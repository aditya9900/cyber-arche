"""F7: reconstruct freeblock records whose first 4 bytes were overwritten.

BLUEPRINT "F7. Damaged-record reconstruction". The freeblock header (next
offset u16, size u16) destroys the payload-size varint, the rowid varint and
usually the header-size varint and the first serial type. We enumerate the
original layout, fill lost serial types from the schema, and keep the best
hypothesis that decodes cleanly and fits the freeblock exactly.
"""

import itertools
import re

from .carver import Candidate, build_segments, build_values, page_mapper, validate
from .errors import ParseError
from .models import Provenance
from .page import TABLE_LEAF, freeblocks, parse_page_header
from .record import DecodedValue, ParsedRecord, decode_value, serial_kind, serial_len
from .schema import Column, TableInfo
from .source import PageLoc
from .varint import read_varint, varint_len

DAMAGED = 4
TS_COL = re.compile(r"(_at$|time|date|^ts$)", re.I)
TS_MIN, TS_MAX = 946684800, 2082758400  # 2000-01-01 .. 2036-01-01
INT_TYPES = [0, 1, 2, 3, 4, 5, 6, 8, 9]


def _lost_type_options(col: Column, nbytes: int) -> list[int | str]:
    """Possible serial types for a lost column; 'TEXT'/'BLOB' = solve length later."""
    if col.is_rowid_alias:
        return [0] if nbytes == 1 else []
    opts: list[int | str] = []
    if nbytes == 1:
        if col.affinity in ("INTEGER", "NUMERIC"):
            opts += INT_TYPES
        elif col.affinity == "REAL":
            opts += [7, *INT_TYPES]
        elif col.affinity == "BLOB":
            opts += INT_TYPES + [7]
        if not col.notnull and 0 not in opts:
            opts.append(0)
    if col.affinity in ("TEXT", "NUMERIC", "BLOB"):
        opts.append("TEXT")
    if col.affinity == "BLOB":
        opts.append("BLOB")
    if col.notnull:
        opts = [o for o in opts if o != 0]
    return opts


def _plausibility(table: TableInfo, rec: ParsedRecord) -> tuple[float, list[str]]:
    adj = 0.0
    reasons = []
    for col, v in zip(table.columns, rec.values, strict=False):
        if TS_COL.search(col.name) and v.kind == "integer" and not col.is_rowid_alias:
            if TS_MIN <= v.value <= TS_MAX or TS_MIN * 1000 <= v.value <= TS_MAX * 1000:
                adj += 0.05
                reasons.append(f"{col.name} is a plausible timestamp")
            else:
                adj -= 0.15
    return adj, reasons


def _hypotheses(page: bytes, o: int, end: int, table: TableInfo, encoding: str):
    """Yield (ParsedRecord, header_start, damaged_len, notes) for one table."""
    ncols = len(table.columns)
    for a, b, c in itertools.product((1, 2, 3), (1, 2, 3, 4), (1, 2)):
        h = o + a + b  # where the record header (size varint) started
        types_start = h + c
        lost = o + DAMAGED - types_start
        if lost < 0:
            continue  # header-size varint survives in part; the plain scanner covers that
        # split `lost` bytes among the first j columns (1 byte each, or one 2-byte TEXT/BLOB)
        for j in range(0, min(lost, ncols) + 1):
            widths_opts = []
            if j == lost:
                widths_opts.append([1] * j)
            if j >= 1 and j + 1 == lost:
                for t in range(j):
                    w = [1] * j
                    w[t] = 2
                    widths_opts.append(w)
            if j == 0 and lost == 0:
                widths_opts = [[]]
            for widths in widths_opts:
                opts = [_lost_type_options(table.columns[i], widths[i]) for i in range(j)]
                if any(not op for op in opts):
                    continue
                # parse the surviving serial types
                p = o + DAMAGED
                rest: list[int] = []
                try:
                    while len(rest) < ncols - j:
                        st, k = read_varint(page, p, end)
                        rest.append(st)
                        p += k
                except ParseError:
                    continue
                hsize = c + lost + (p - (o + DAMAGED))
                if varint_len(hsize) != c:
                    continue
                if any(serial_len(st) is None for st in rest):
                    continue
                body_start = p
                known_rest = sum(serial_len(st) for st in rest)
                for combo in itertools.product(*opts):
                    unknown = [i for i, x in enumerate(combo) if isinstance(x, str)]
                    if len(unknown) > 1:
                        continue
                    known = sum(serial_len(x) for x in combo if not isinstance(x, str)) + known_rest
                    types = list(combo)
                    if unknown:
                        i = unknown[0]
                        ln = end - body_start - known  # exact fit to freeblock end
                        if ln < 0:
                            continue
                        st = ln * 2 + (13 if combo[i] == "TEXT" else 12)
                        if varint_len(st) != widths[i]:
                            continue
                        types[i] = st
                    types = types + rest
                    body_len = sum(serial_len(t) for t in types)
                    rec_end = body_start + body_len
                    if rec_end > end:
                        continue
                    payload = hsize + body_len
                    if varint_len(payload) != a:
                        continue
                    vals = []
                    q = body_start
                    for t in types:
                        ln = serial_len(t)
                        v, ok = decode_value(t, page[q : q + ln], encoding)
                        vals.append(DecodedValue(serial_kind(t), v, q, ln, ok))
                        q += ln
                    rec = ParsedRecord(h, hsize, types, vals, rec_end, c)
                    yield rec, set(range(j)), (a, b, c)


def reconstruct_region(
    page: bytes,
    page_no: int,
    o: int,
    end: int,
    tables: list[TableInfo],
    loc: PageLoc,
    encoding: str,
    source: str,
) -> list[Candidate]:
    """Reconstruct back-to-back damaged records inside one (possibly merged) freeblock."""
    out: list[Candidate] = []
    guard = 0
    while o + DAMAGED + 2 < end and guard < 64:
        guard += 1
        best = None
        for t in tables:
            for rec, lost_cols, layout in _hypotheses(page, o, end, t, encoding):
                ok, adj, reasons = validate(rec, t, end)
                if not ok:
                    continue
                p_adj, p_reasons = _plausibility(t, rec)
                score = adj + p_adj
                # prefer hypotheses that explain more of the freeblock
                if best is None or (score, rec.end) > (best[0], best[1].end):
                    best = (score, rec, t, lost_cols, layout, reasons + p_reasons)
        if best is None:
            break
        score, rec, t, lost_cols, layout, reasons = best
        if any(rec.serial_types[i] >= 12 for i in lost_cols if i < len(rec.serial_types)):
            # a TEXT/BLOB length solved from the leftover space is a weaker guess:
            # a wrong boundary silently merges neighbouring columns
            score -= 0.10
            reasons = [*reasons, "a lost text length was solved from the remaining space"]
        if score < 0:
            break  # not convincing enough to present as recovered data
        conf = max(0.40, min(0.70, 0.55 + score))
        lost_names = [t.columns[i].name for i in sorted(lost_cols)]
        why = [
            "reconstructed: first 4 bytes overwritten by the freeblock header",
            f"layout hypothesis: payload-size varint {layout[0]}B, rowid varint {layout[1]}B, header-size varint {layout[2]}B",
        ]
        if lost_names:
            why.append("serial types inferred from schema for: " + ", ".join(lost_names))
        segs = build_segments(rec, t, page_mapper(loc), None, damaged_span=(o, DAMAGED))
        prov = Provenance(
            source,
            loc.file,
            page_no,
            o,
            loc.file_offset + o,
            rec.end - o,
            loc.wal_frame,
            loc.commit,
            segs,
        )
        alias_cols = {i for i, c in enumerate(t.columns) if c.is_rowid_alias}
        values = build_values(rec, t, None, (lost_cols - alias_cols) or None)
        out.append(
            Candidate(t.name, None, values, prov, round(conf, 2), why + reasons, reconstructed=True)
        )
        o = rec.end
    return out


def reconstruct_freeblocks(
    page: bytes,
    page_no: int,
    tables: list[TableInfo],
    loc: PageLoc,
    encoding: str,
    usable: int,
    in_freelist: bool,
) -> list[Candidate]:
    try:
        ph = parse_page_header(page, page_no)
    except ParseError:
        return []
    if ph.page_type != TABLE_LEAF:
        return []
    out: list[Candidate] = []
    for off, size in freeblocks(page, ph, usable):
        if not any(page[off + DAMAGED : off + size]):
            continue  # zeroed (secure_delete): nothing to rebuild
        out += reconstruct_region(
            page,
            page_no,
            off,
            off + size,
            tables,
            loc,
            encoding,
            "freelist_leaf" if in_freelist else "freeblock",
        )
    return out
