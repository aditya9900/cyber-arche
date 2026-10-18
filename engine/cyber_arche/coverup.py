"""Cover-up indicators CU1-CU6 (BLUEPRINT F4). Indicators, never proof."""

from dataclasses import dataclass, field

from .errors import ParseError
from .models import EvidenceRef, Finding, RecoveredRecord
from .page import TABLE_LEAF, freeblocks, parse_page_header
from .schema import TableInfo
from .source import PageLoc


@dataclass
class PageImage:
    page_no: int
    data: bytes
    loc: PageLoc
    in_freelist: bool = False


@dataclass
class CoverupInputs:
    images: list[PageImage]
    tables: list[TableInfo]
    live_rowids: dict[str, set[int]]
    recovered_rowids: dict[str, set[int]]
    unknown_rowid_deleted: dict[str, int]
    sequence: dict[str, int]
    usable: int
    freelist_count: int
    auto_vacuum: bool
    incremental_vacuum: bool
    wal_checkpoint_seq: int | None = None
    wal_old_frames: list[int] = field(default_factory=list)
    wal_old_offsets: list[tuple[int, int]] = field(default_factory=list)


def fmt_ids(ids: list[int], cap: int = 40) -> str:
    s = ", ".join(str(i) for i in ids[:cap])
    return s + (f" and {len(ids) - cap} more" if len(ids) > cap else "")


def detect(inp: CoverupInputs) -> list[Finding]:
    out: list[Finding] = []
    zero_fb: list[EvidenceRef] = []
    zero_free: list[EvidenceRef] = []
    any_freeblock = False
    any_frag = False
    for img in inp.images:
        if img.in_freelist:
            if not any(img.data[: inp.usable]):
                zero_free.append(EvidenceRef(img.loc.file, img.loc.file_offset, len(img.data)))
            continue
        try:
            ph = parse_page_header(img.data, img.page_no)
        except ParseError:
            continue
        if ph.page_type != TABLE_LEAF:
            continue
        if ph.frag_bytes:
            any_frag = True
        for off, size in freeblocks(img.data, ph, inp.usable):
            any_freeblock = True
            if size > 4 and not any(img.data[off + 4 : off + size]):
                zero_fb.append(EvidenceRef(img.loc.file, img.loc.file_offset + off, size))
    if zero_fb or zero_free:
        parts = []
        if zero_fb:
            parts.append(
                f"{len(zero_fb)} freeblock(s) whose contents after the 4-byte header are all zero"
            )
        if zero_free:
            parts.append(f"{len(zero_free)} zero-filled freelist page(s)")
        out.append(
            Finding(
                "CU1",
                "high",
                "Secure-delete pattern",
                "Consistent with PRAGMA secure_delete: "
                + " and ".join(parts)
                + ". Default SQLite leaves deleted bytes in place; zeroed space means they were deliberately overwritten.",
                (zero_fb + zero_free)[:50],
            )
        )

    gaps_by_table: dict[str, list[int]] = {}
    for t in inp.tables:
        if t.name.startswith("sqlite_"):
            continue
        live = inp.live_rowids.get(t.name, set())
        rec = inp.recovered_rowids.get(t.name, set())
        known = live | rec
        if not known:
            continue
        lo, hi = min(known), max(known)
        hi = max(hi, inp.sequence.get(t.name, hi))
        gaps = [r for r in range(lo, hi + 1) if r not in known]
        if len(gaps) > 100_000:
            gaps = gaps[:100_000]
        all_gaps = [r for r in range(min(live or known), max(live or known) + 1) if r not in live]
        gaps_by_table[t.name] = all_gaps
        if gaps:
            unk = inp.unknown_rowid_deleted.get(t.name, 0)
            extra = (
                f" ({unk} recovered record(s) with an unknown rowid may account for some of them)"
                if unk
                else ""
            )
            out.append(
                Finding(
                    "CU2",
                    "high",
                    "Deletions with no residue",
                    f"Table {t.name}: rowid(s) {fmt_ids(gaps)} are missing and no copy was recovered from any source{extra}. Consistent with rows deleted and their bytes overwritten or zeroed.",
                    [],
                )
            )
        seq = inp.sequence.get(t.name)
        if seq is not None and live and seq > max(live):
            newest = list(range(max(live) + 1, seq + 1))
            got = [r for r in newest if r in rec]
            if not got:
                tail = " None of them was recovered."
            elif len(got) == len(newest):
                tail = f" All of them were recovered (rowid {fmt_ids(got)})."
            else:
                tail = f" Recovered: rowid {fmt_ids(got)}; no trace of the rest."
            out.append(
                Finding(
                    "CU3",
                    "medium" if len(got) < len(newest) else "info",
                    "Newest rows removed",
                    f"Table {t.name}: sqlite_sequence records rowid {seq} as issued, but the highest live rowid is {max(live)}. Consistent with the newest {len(newest)} row(s) having been deleted.{tail}",
                    [],
                )
            )
    if (
        any(gaps_by_table.values())
        and inp.freelist_count == 0
        and not any_freeblock
        and not any_frag
    ):
        tbls = ", ".join(t for t, g in gaps_by_table.items() if g)
        out.append(
            Finding(
                "CU4",
                "medium",
                "Possible VACUUM",
                f"Rowid gaps in {tbls}, yet zero freelist pages, zero freeblocks and zero fragmented bytes on every page. Consistent with the file having been rebuilt by VACUUM, which removes all deletion residue.",
                [],
            )
        )
    if inp.auto_vacuum:
        out.append(
            Finding(
                "CU5",
                "info",
                "auto_vacuum enabled",
                "Header offset 52 is non-zero: auto_vacuum is "
                + ("INCREMENTAL" if inp.incremental_vacuum else "FULL")
                + ". Consistent with freed pages being removed from the file, which limits recovery.",
                [EvidenceRef("main", 52, 4)],
            )
        )
    if inp.wal_old_frames or (inp.wal_checkpoint_seq or 0) > 0:
        bits = []
        if inp.wal_checkpoint_seq:
            bits.append(f"checkpoint sequence number {inp.wal_checkpoint_seq}")
        if inp.wal_old_frames:
            bits.append(f"{len(inp.wal_old_frames)} frame(s) from an earlier WAL generation")
        out.append(
            Finding(
                "CU6",
                "info",
                "WAL history",
                "The WAL shows "
                + " and ".join(bits)
                + ". Consistent with the log having been checkpointed and reset at least once; older history may be partly lost.",
                [EvidenceRef("wal", o, n) for o, n in inp.wal_old_offsets[:20]]
                or [EvidenceRef("wal", 12, 4)],
            )
        )
    return out


def recovered_rowids(records: list[RecoveredRecord]) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for r in records:
        if r.rowid is not None and r.status != "live":
            out.setdefault(r.table, set()).add(r.rowid)
    return out
