"""Whole-case analysis: live state, WAL history, carving, grouping, findings.

Reads raw bytes only. Inputs are the bytes of the main file and, optionally,
the -wal and -journal files.
"""

from dataclasses import dataclass, field

from .btree import freelist_pages
from .carver import BASE, Candidate, carve_page
from .coverup import CoverupInputs, PageImage, detect
from .errors import ParseError, log
from .header import DbHeader, is_sqlite, parse_header
from .journal import Journal, parse_journal
from .models import (
    Change,
    Commit,
    Finding,
    Provenance,
    RecordRef,
    RecoveredRecord,
    Value,
)
from .reconstruct import reconstruct_freeblocks
from .schema import SQLITE_MASTER, TableInfo
from .snapshot import Snapshot, checkpointed_through
from .source import MainFile, PageLoc
from .timeline import (
    SnapshotState,
    bracket,
    diff_states,
    first_seen_timestamps,
    read_state,
    timestamp_column,
)
from .wal import Wal, parse_wal

STATUS_ORDER = {"live": 0, "prior_version": 1, "deleted": 2, "stale_copy": 3}


@dataclass
class CaseAnalysis:
    header: DbHeader | None
    encrypted: bool
    tables: list[TableInfo]
    records: list[RecoveredRecord]
    commits: list[Commit]
    changes: list[Change]
    snapshots: dict[int, dict[str, list[str]]]  # commit -> table -> record ids
    findings: list[Finding]
    wal: Wal | None = None
    journal: Journal | None = None
    errors: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def record(self, rid: str) -> RecoveredRecord | None:
        return self._by_id.get(rid)

    def __post_init__(self) -> None:
        self._by_id = {r.id: r for r in self.records}


def _prov_key(p: Provenance) -> tuple:
    return (p.file, p.file_offset, p.source)


def _values_key(values: list[Value]) -> tuple:
    return tuple(round(v.value, 9) if isinstance(v.value, float) else v.value for v in values)


def analyze(
    main: bytes, wal_bytes: bytes | None = None, journal_bytes: bytes | None = None
) -> CaseAnalysis:
    errors: list[str] = []
    wal: Wal | None = None
    if wal_bytes:
        try:
            wal = parse_wal(wal_bytes)
        except ParseError as e:
            errors.append(f"WAL: {e.reason}")

    # ---- header (the main file may be a stub; fall back to WAL page 1)
    header: DbHeader | None = None
    if len(main) >= 100 and is_sqlite(main):
        header = parse_header(main)
    elif wal is not None and wal.commits:
        for f in reversed(wal.frames):
            if f.valid and f.page_no == 1:
                pg = wal.frame_page(f)
                if is_sqlite(pg):
                    header = parse_header(pg)
                break
    if header is None:
        encrypted = len(main) >= 100
        msg = (
            "No 'SQLite format 3' header: the file is encrypted (e.g. SQLCipher) or not SQLite. Decryption is out of scope."
            if encrypted
            else "File too short to be a SQLite database."
        )
        return CaseAnalysis(
            None,
            encrypted,
            [],
            [],
            [],
            [],
            {},
            [Finding("ENC", "high", "No SQLite header", msg, [])],
            wal,
            None,
            errors + [msg],
        )

    page_size = header.page_size
    usable = header.usable_size
    mainsrc = MainFile(main, page_size, usable, header.encoding)

    # ---- per-commit states
    ncommits = len(wal.commits) if wal else 0
    cache: dict = {}
    states: list[SnapshotState] = []
    snaps: list[Snapshot] = []
    for k in range(ncommits + 1):
        snap = Snapshot(mainsrc, wal, k)
        snaps.append(snap)
        states.append(read_state(snap, k, final=(k == ncommits), cache=cache))
    final = states[-1]
    final_snap = snaps[-1]
    for st in states:
        errors += [f"commit {st.k}: {e}" for e in st.errors[:20]]

    # ---- commits (0 = main file alone)
    ckpt = checkpointed_through(mainsrc, wal) if wal else 0
    commits = [Commit(0, 0, 0, mainsrc.page_count(), approximate=ckpt > 0)]
    if wal:
        for c in wal.commits:
            commits.append(
                Commit(
                    c.index,
                    c.first_frame,
                    c.last_frame,
                    c.db_size_pages,
                    approximate=c.last_frame < ckpt,
                )
            )

    all_tables: dict[str, TableInfo] = {"sqlite_master": SQLITE_MASTER}
    for st in states:
        for t in st.tables:
            all_tables[t.name] = t
    table_list = list(all_tables.values())

    # ---- carving over every physical page image
    carved: list[Candidate] = []
    images: list[PageImage] = []

    def freelist_of(src) -> set[int]:
        p1 = src.page(1)
        if p1 is None or not is_sqlite(p1):
            return set()
        h = parse_header(p1)
        _trunks, leaves = freelist_pages(src, h.freelist_trunk)
        return set(leaves)

    def owners_for(st: SnapshotState, pno: int, free: set[int]) -> tuple[list[TableInfo], bool]:
        if pno in free:
            return table_list, True
        owner = st.page_owner.get(pno)
        if owner and owner in all_tables:
            return [all_tables[owner]], False
        return table_list, False

    free0 = freelist_of(snaps[0])
    for pno in range(1, mainsrc.page_count() + 1):
        pg = mainsrc.page(pno)
        loc = mainsrc.locate(pno)
        if pg is None or loc is None:
            continue
        tabs, in_free = owners_for(states[0], pno, free0)
        images.append(PageImage(pno, pg, loc, in_free))
        carved += carve_page(pg, pno, tabs, loc, header.encoding, usable, in_freelist=in_free)
        carved += reconstruct_freeblocks(pg, pno, tabs, loc, header.encoding, usable, in_free)

    old_offsets: list[tuple[int, int]] = []
    if wal:
        frees = {k: freelist_of(snaps[k]) for k in range(1, ncommits + 1)}
        for f in wal.frames:
            pg = wal.frame_page(f)
            if len(pg) < page_size:
                continue
            if f.valid and f.committed:
                loc = PageLoc("wal", f.page_offset, f.index, f.commit)
                st = states[f.commit]
                tabs, in_free = owners_for(st, f.page_no, frees[f.commit])
                images.append(PageImage(f.page_no, pg, loc, in_free))
                carved += carve_page(
                    pg, f.page_no, tabs, loc, header.encoding, usable, in_freelist=in_free
                )
                carved += reconstruct_freeblocks(
                    pg, f.page_no, tabs, loc, header.encoding, usable, in_free
                )
            elif f.page_no:
                old = f.old_generation
                if old:
                    old_offsets.append((f.file_offset, 24 + page_size))
                elif not f.valid:
                    continue  # past the valid range, same salts: torn/garbage frames
                loc = PageLoc(
                    "wal", f.page_offset, f.index, None, old_generation=old, committed=False
                )
                src_name = "wal_old_generation" if old else "wal_frame"
                carved += carve_page(
                    pg,
                    f.page_no,
                    table_list,
                    loc,
                    header.encoding,
                    usable,
                    base_override={src_name: BASE["uncommitted"]},
                    source_override=src_name,
                    include_live_cells=True,
                )

    journal: Journal | None = None
    if journal_bytes:
        journal = parse_journal(journal_bytes, page_size, mainsrc.page_count())
        for jp in journal.pages:
            pg = journal_bytes[jp.file_offset : jp.file_offset + page_size]
            if len(pg) < page_size:
                continue
            loc = PageLoc("journal", jp.file_offset)
            carved += carve_page(
                pg,
                jp.page_no,
                table_list,
                loc,
                header.encoding,
                usable,
                source_override="journal",
                include_live_cells=True,
            )

    # ---- group everything by (table, rowid, values)
    groups: dict[tuple, list[Candidate]] = {}
    seen_in: dict[tuple, list[int]] = {}
    for st in states:
        for (tname, rowid), c in st.rows.items():
            key = (tname, rowid, c.key_values)
            seen_in.setdefault(key, []).append(st.k)
            groups.setdefault(key, [])
            if all(_prov_key(x.prov) != _prov_key(c.prov) for x in groups[key]):
                groups[key].append(c)

    def match_key(tname: str, values: list[Value]) -> tuple:
        t = all_tables.get(tname)
        alias = {i for i, col in enumerate(t.columns) if col.is_rowid_alias} if t else set()
        return (tname, tuple(v for i, v in enumerate(_values_key(values)) if i not in alias))

    by_values: dict[tuple, set] = {}
    for key, cands in groups.items():
        by_values.setdefault(match_key(key[0], cands[0].values), set()).add(key[1])
    inferred: set[tuple] = set()
    _infer_rowids(
        carved,
        by_values,
        match_key,
        final,
        all_tables,
        ts_index={t.name: i for t in table_list if (i := timestamp_column(t)) is not None},
    )
    for c in carved:
        rowid = c.rowid
        was_inferred = "rowid inferred" in " ".join(c.reasons)
        if rowid is None:
            matches = by_values.get(match_key(c.table, c.values), set())
            if len(matches) == 1:
                rowid = next(iter(matches))
                c.reasons.append("rowid matched by identical values elsewhere in the case")
        if rowid is not None:
            t = all_tables.get(c.table)
            if t:
                vals = list(c.values)
                for i, col in enumerate(t.columns):
                    if col.is_rowid_alias and i < len(vals):
                        vals[i] = Value(col.name, "integer", rowid, vals[i].reconstructed)
                c.values = vals
        c.rowid = rowid
        key = (c.table, rowid, c.key_values)
        if was_inferred:
            inferred.add(key)
        groups.setdefault(key, [])
        if all(_prov_key(x.prov) != _prov_key(c.prov) for x in groups[key]):
            groups[key].append(c)

    final_keys = {(t, r): (t, r, c.key_values) for (t, r), c in final.rows.items()}
    final_prov = {k: _prov_key(c.prov) for k, c in final.rows.items()}

    pending: list[tuple[str, tuple, list[Candidate], bool]] = []
    for key, cands in groups.items():
        tname, rowid, _vals = key
        live_key = final_keys.get((tname, rowid)) if rowid is not None else None
        if live_key == key:
            live_c = [c for c in cands if _prov_key(c.prov) == final_prov[(tname, rowid)]]
            others = [c for c in cands if _prov_key(c.prov) != final_prov[(tname, rowid)]]
            pending.append(("live", key, live_c or cands[:1], False))
            if others:
                pending.append(("stale_copy", key, others, False))
        elif live_key is not None:
            pending.append(("prior_version", key, cands, key in inferred))
        else:
            # a carved copy with unknown rowid whose values equal a live row is stale
            status = "deleted"
            if rowid is None:
                for (t2, _r2), c2 in final.rows.items():
                    if t2 == tname and c2.key_values == key[2]:
                        status = "stale_copy"
                        break
            pending.append((status, key, cands, key in inferred))

    def sort_key(p):
        status, key, _c, _i = p
        rid = key[1] if key[1] is not None else 1 << 62
        return (
            key[0] != "sqlite_master",
            key[0].startswith("sqlite_"),
            key[0],
            rid,
            STATUS_ORDER[status],
        )

    pending.sort(key=sort_key)
    records: list[RecoveredRecord] = []
    key_to_id: dict[tuple, str] = {}
    for n, (status, key, cands, was_inferred) in enumerate(pending, 1):
        cands = sorted(cands, key=lambda c: -c.confidence)
        best = cands[0]
        reasons = list(dict.fromkeys(best.reasons))
        if len(cands) > 1:
            reasons.append(f"found in {len(cands)} locations")
        commits_seen = seen_in.get(key, [])
        rid = f"R-{n:03d}"
        if status != "stale_copy" or key not in key_to_id:
            key_to_id.setdefault(key, rid)
        rec = RecoveredRecord(
            rid,
            key[0],
            key[1],
            was_inferred,
            status,
            best.values,
            round(best.confidence, 2),
            reasons,
            [c.prov for c in cands],
            min(commits_seen) if commits_seen else None,
            max(commits_seen) if commits_seen else None,
            any(c.reconstructed for c in cands),
        )
        records.append(rec)

    # ---- changes and snapshot views
    ts_cols = {t.name: i for t in table_list if (i := timestamp_column(t)) is not None}
    first_seen = first_seen_timestamps(states, ts_cols)
    changes: list[Change] = []

    def ref(c: Candidate | None, tname: str, rowid: int) -> RecordRef | None:
        if c is None:
            return None
        return RecordRef(key_to_id.get((tname, rowid, c.key_values), ""), c.values)

    for k in range(1, len(states)):
        for ch in diff_states(states[k - 1], states[k]):
            changes.append(
                Change(
                    ch.commit,
                    ch.table,
                    ch.kind,
                    ch.rowid,
                    ref(ch.before, ch.table, ch.rowid),
                    ref(ch.after, ch.table, ch.rowid),
                    bracket(ch, first_seen),
                )
            )
    snapshots: dict[int, dict[str, list[str]]] = {}
    for st in states:
        view: dict[str, list[str]] = {}
        for (tname, rowid), c in sorted(st.rows.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            rid = key_to_id.get((tname, rowid, c.key_values))
            if rid:
                view.setdefault(tname, []).append(rid)
        snapshots[st.k] = view

    # ---- cover-up indicators (on the final state)
    live_rowids: dict[str, set[int]] = {}
    for tname, rowid in final.rows:
        live_rowids.setdefault(tname, set()).add(rowid)
    rec_rowids: dict[str, set[int]] = {}
    unknown: dict[str, int] = {}
    for r in records:
        if r.status == "live":
            continue
        if r.rowid is None:
            unknown[r.table] = unknown.get(r.table, 0) + 1
        else:
            rec_rowids.setdefault(r.table, set()).add(r.rowid)
    sequence: dict[str, int] = {}
    for (tname, _rowid), c in final.rows.items():
        if tname == "sqlite_sequence" and len(c.values) >= 2:
            name, seq = c.values[0].value, c.values[1].value
            if isinstance(name, str) and isinstance(seq, int):
                sequence[name] = seq
    p1 = final_snap.page(1)
    fh = parse_header(p1) if p1 is not None and is_sqlite(p1) else header
    final_free = freelist_of(final_snap)
    final_images = []
    for pno in range(1, final_snap.page_count() + 1):
        pg, loc = final_snap.page(pno), final_snap.locate(pno)
        if pg is not None and loc is not None:
            final_images.append(PageImage(pno, pg, loc, pno in final_free))
    findings = detect(
        CoverupInputs(
            final_images,
            final.tables,
            live_rowids,
            rec_rowids,
            unknown,
            sequence,
            usable,
            fh.freelist_count,
            fh.auto_vacuum,
            bool(fh.incremental_vacuum),
            wal.checkpoint_seq if wal else None,
            [f.index for f in wal.old_generation_frames] if wal else [],
            old_offsets,
        )
    )

    counts: dict[str, int] = {}
    user_records = [r for r in records if not r.table.startswith("sqlite_")]
    for r in user_records:
        counts[r.status] = counts.get(r.status, 0) + 1
    sources: dict[str, int] = {}
    for r in user_records:
        if r.status == "live":
            continue
        for p in r.provenance:
            sources[p.source] = sources.get(p.source, 0) + 1
    stats = {
        "status_counts": counts,
        "source_counts": sources,
        "pages_main": mainsrc.page_count(),
        "pages_final": final_snap.page_count(),
        "freelist_pages": len(final_free),
        "checkpointed_through_frame": ckpt,
    }
    log.info("analysis: %s", stats)
    return CaseAnalysis(
        header,
        False,
        final.tables,
        records,
        commits,
        changes,
        snapshots,
        findings,
        wal,
        journal,
        errors,
        stats,
    )


def _infer_rowids(carved, by_values, match_key, final, all_tables, ts_index) -> None:
    """Infer rowids for carved records that lost them, from neighbouring timestamps.

    Assumes rowids grow with the app's timestamp (true for append-only chat
    tables). Every inferred rowid is labelled as such; ambiguous cases get a
    range in the reasons instead of a number.
    """
    by_table: dict[str, list[Candidate]] = {}
    for c in carved:
        if c.rowid is None and not by_values.get(match_key(c.table, c.values)):
            by_table.setdefault(c.table, []).append(c)
    for tname, cands in by_table.items():
        idx = ts_index.get(tname)
        if idx is None:
            continue
        known: list[tuple[float, int]] = []
        for (t2, rowid), c in final.rows.items():
            ts = c.values[idx].value if idx < len(c.values) else None
            if t2 == tname and isinstance(ts, (int, float)):
                known.append((ts, rowid))
        known.sort()
        used = {r for _ts, r in known}
        # bucket unknown records into the gap between their timestamp neighbours
        gaps: dict[tuple, list[Candidate]] = {}
        uniq: dict[tuple, Candidate] = {}
        for c in cands:
            uniq.setdefault(match_key(tname, c.values), c)
        for c in uniq.values():
            ts = c.values[idx].value if idx < len(c.values) else None
            if not isinstance(ts, (int, float)):
                continue
            lo = max((r for t, r in known if t <= ts), default=0)
            hi = min((r for t, r in known if t >= ts), default=None)
            gaps.setdefault((lo, hi), []).append(c)
        for (lo, hi), members in gaps.items():
            members.sort(key=lambda c: c.values[idx].value)
            if hi is None:
                free = [lo + 1 + i for i in range(len(members))]
                exact = False
            else:
                free = [r for r in range(lo + 1, hi) if r not in used]
                exact = len(free) == len(members)
            for c, r in zip(members, free, strict=False):
                group = [
                    x for x in cands if match_key(tname, x.values) == match_key(tname, c.values)
                ]
                for x in group:
                    if exact or hi is None:
                        x.rowid = r
                        x.reasons.append(
                            f"rowid inferred as {r} from neighbouring timestamps (between rowids {lo} and {hi if hi is not None else 'end'})"
                        )
                    else:
                        x.reasons.append(
                            f"rowid unknown; between {lo} and {hi} (inferred from timestamps)"
                        )
