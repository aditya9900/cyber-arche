"""Candidate scan, validator and confidence scoring (BLUEPRINT F1, "Confidence scoring").

Works on any page image, so it runs identically on main-file pages, freelist
leaves, WAL frames and journal pages.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from .btree import RawCell
from .errors import ParseError
from .models import Provenance, Segment, Value
from .page import TABLE_LEAF, cell_pointers, freeblocks, parse_page_header, unallocated
from .record import ParsedRecord, parse_record, serial_kind
from .schema import TableInfo
from .source import PageLoc
from .varint import read_varint

BASE = {
    "btree_live": 1.00,
    "wal_frame": 0.95,
    "journal": 0.90,
    "freelist_leaf": 0.85,
    "freeblock": 0.80,
    "unallocated": 0.70,
    "wal_old_generation": 0.60,
    "uncommitted": 0.60,
}
MIN_CONFIDENCE = 0.45


@dataclass
class Candidate:
    table: str
    rowid: int | None
    values: list[Value]
    prov: Provenance
    confidence: float
    reasons: list[str] = field(default_factory=list)
    reconstructed: bool = False

    @property
    def key_values(self) -> tuple:
        return tuple(
            (v.value if not isinstance(v.value, float) else round(v.value, 9)) for v in self.values
        )


# --------------------------------------------------------------------------- validator


def printable_ratio(s: str) -> float:
    if not s:
        return 1.0
    ok = sum(1 for ch in s if ch.isprintable() or ch in "\n\r\t")
    return ok / len(s)


def affinity_fits(aff: str, kind: str) -> bool:
    if kind == "null":
        return True
    if aff == "INTEGER":
        return kind in ("integer", "real")
    if aff == "REAL":
        return kind in ("real", "integer")
    if aff == "NUMERIC":
        return kind in ("integer", "real", "text")
    if aff == "TEXT":
        return kind in ("text", "blob")
    return True


def validate(
    rec: ParsedRecord, table: TableInfo, region_end: int | None
) -> tuple[bool, float, list[str]]:
    """Return (accept, adjustment, reasons) for a record decoded against `table`."""
    reasons: list[str] = []
    adj = 0.0
    ncols = len(table.columns)
    if len(rec.serial_types) != ncols:
        # ALTER TABLE ADD COLUMN leaves older rows short; accept only that case
        if not (0 < len(rec.serial_types) < ncols):
            return False, 0, ["column count mismatch"]
        adj -= 0.10
        reasons.append(f"{len(rec.serial_types)} of {ncols} columns (older row layout?)")
    else:
        reasons.append("column count matches schema")
    non_null = 0
    for col, st, v in zip(table.columns, rec.serial_types, rec.values, strict=False):
        kind = serial_kind(st)
        if col.is_rowid_alias:
            if st != 0:
                return False, 0, ["rowid alias column is not NULL"]
            continue
        if kind == "null" and col.notnull:
            return False, 0, [f"NULL in NOT NULL column {col.name}"]
        if kind != "null":
            non_null += 1
        if not affinity_fits(col.affinity, kind):
            adj -= 0.15
            reasons.append(f"affinity mismatch in {col.name} ({kind} in {col.affinity})")
        if kind == "text":
            if not v.text_ok:
                adj -= 0.25
                reasons.append(f"invalid text encoding in {col.name}")
            elif printable_ratio(v.value) < 0.9:
                adj -= 0.25
                reasons.append(f"unprintable text in {col.name}")
    if non_null == 0:
        return False, 0, ["every column is NULL"]
    if adj > -0.01 and any(serial_kind(s) == "text" for s in rec.serial_types):
        reasons.append("valid, printable text")
    if region_end is not None and rec.end == region_end:
        adj += 0.05
        reasons.append("exact fit to region end")
    return True, adj, reasons


# --------------------------------------------------------------------------- candidates


def build_values(
    rec: ParsedRecord, table: TableInfo, rowid: int | None, reconstructed: set[int] | None = None
) -> list[Value]:
    out = []
    for i, col in enumerate(table.columns):
        rc = bool(reconstructed and i in reconstructed)
        if i >= len(rec.values):
            out.append(Value(col.name, "null", None, rc))
            continue
        v = rec.values[i]
        if col.is_rowid_alias and v.kind == "null":
            out.append(Value(col.name, "integer" if rowid is not None else "null", rowid, rc))
        else:
            out.append(Value(col.name, v.kind, v.value, rc))
    return out


def build_segments(
    rec: ParsedRecord,
    table: TableInfo,
    to_file: Callable[[int], int | None],
    rowid: int | None,
    payload_span: tuple[int, int] | None = None,
    rowid_span: tuple[int, int] | None = None,
    damaged_span: tuple[int, int] | None = None,
    limit: int | None = None,
) -> list[Segment]:
    """Byte ranges (absolute file offsets) for the hex viewer. Positions in
    `rec` are converted with `to_file`; anything at or past `limit` is dropped."""
    segs: list[Segment] = []

    def add(start: int, length: int, kind: str, column: str | None = None, value=None) -> None:
        if length <= 0:
            return
        if limit is not None:
            if start >= limit:
                return
            length = min(length, limit - start)
        fo = to_file(start)
        if fo is not None:
            segs.append(Segment(fo, length, kind, column, value))

    if damaged_span:
        add(damaged_span[0], damaged_span[1], "damaged")
    if payload_span:
        add(payload_span[0], payload_span[1], "payload_size")
    if rowid_span:
        add(rowid_span[0], rowid_span[1], "rowid", None, rowid)
    hstart = rec.start
    if damaged_span:
        dend = damaged_span[0] + damaged_span[1]
        if dend > hstart:
            hstart = dend
    add(hstart, rec.start + rec.header_size - hstart, "record_header")
    for col, v in zip(table.columns, rec.values, strict=False):
        add(
            v.start,
            v.length,
            "column",
            col.name,
            v.value if v.kind != "blob" else f"blob {v.length} bytes",
        )
    return segs


def page_mapper(loc: PageLoc) -> Callable[[int], int | None]:
    return lambda off: loc.file_offset + off


def live_candidate(
    cell: RawCell, table: TableInfo, loc: PageLoc, encoding: str, source: str, base: float
) -> Candidate | None:
    """Decode a b-tree cell (allocated, not carved) into a Candidate."""
    try:
        rec = parse_record(cell.payload, 0, len(cell.payload), encoding)
    except ParseError:
        return None
    po = cell.payload_off

    def to_file(p: int) -> int | None:
        return loc.file_offset + p

    # payload positions -> page offsets
    shifted = ParsedRecord(
        rec.start + po,
        rec.header_size,
        rec.serial_types,
        [type(v)(v.kind, v.value, v.start + po, v.length, v.text_ok) for v in rec.values],
        rec.end + po,
        rec.header_varint_len,
    )
    segs = build_segments(
        shifted,
        table,
        to_file,
        cell.rowid,
        (cell.cell_off, cell.payload_size_len),
        (cell.cell_off + cell.payload_size_len, cell.rowid_len),
        limit=po + cell.local_len,
    )
    reasons = ["allocated b-tree cell"]
    if cell.overflow_pages:
        reasons.append(f"payload continues on overflow pages {cell.overflow_pages}")
    if cell.truncated:
        reasons.append("overflow chain truncated")
    prov = Provenance(
        source,
        loc.file,
        cell.page_no,
        cell.cell_off,
        loc.file_offset + cell.cell_off,
        cell.cell_len,
        loc.wal_frame,
        loc.commit,
        segs,
    )
    return Candidate(
        table.name, cell.rowid, build_values(rec, table, cell.rowid), prov, base, reasons
    )


def _try_at(
    page: bytes,
    i: int,
    end: int,
    tables: list[TableInfo],
    encoding: str,
    intact_cell: bool,
) -> tuple[ParsedRecord, TableInfo, float, list[str], int | None, tuple | None] | None:
    """Try a record at offset i, either as a full cell (payload, rowid, record)
    or as a bare record header. Returns the best-scoring table interpretation."""
    payload_size = rowid = None
    spans = None
    rpos = i
    if intact_cell:
        try:
            payload_size, n1 = read_varint(page, i, end)
            rowid, n2 = read_varint(page, i + n1, end)
        except ParseError:
            return None
        if payload_size < 2 or payload_size > end - i:
            return None
        rpos = i + n1 + n2
        spans = ((i, n1), (i + n1, n2))
    if rpos >= end or page[rpos] == 0:
        return None
    try:
        rec = parse_record(page, rpos, end, encoding)
    except ParseError:
        return None
    if intact_cell and payload_size != rec.header_size + rec.body_len:
        return None
    best = None
    for t in tables:
        ok, adj, reasons = validate(rec, t, end)
        if ok and (best is None or adj > best[2]):
            best = (rec, t, adj, reasons, rowid, spans)
    return best


def carve_region(
    page: bytes,
    page_no: int,
    start: int,
    end: int,
    tables: list[TableInfo],
    loc: PageLoc,
    encoding: str,
    source: str,
    base: float,
    damaged_prefix: int = 0,
) -> list[Candidate]:
    """Scan [start, end) of a page for deleted records.

    damaged_prefix > 0 marks a freeblock: its first 4 bytes were overwritten by
    the freeblock header, so records there are tried as bare record headers.
    """
    out: list[Candidate] = []
    i = start + damaged_prefix
    to_file = page_mapper(loc)
    while i < end - 2:
        if page[i] == 0:
            i += 1
            continue
        hit = _try_at(page, i, end, tables, encoding, True)
        intact = hit is not None
        if hit is None:
            hit = _try_at(page, i, end, tables, encoding, False)
        if hit is None:
            i += 1
            continue
        rec, table, adj, reasons, rowid, spans = hit
        damaged = None
        if damaged_prefix and i == start + damaged_prefix:
            damaged = (start, damaged_prefix)
        if rowid is not None and rowid >= 1 << 63:
            rowid -= 1 << 64
        reasons = list(reasons)
        if intact:
            reasons.insert(0, "cell header intact (payload size and rowid)")
        else:
            reasons.insert(0, "cell header overwritten; rowid unknown")
            adj -= 0.05
        conf = max(0.0, min(1.0, base + adj))
        cell_start = i if intact else rec.start
        if damaged:
            cell_start = damaged[0]
        segs = build_segments(
            rec,
            table,
            to_file,
            rowid,
            spans[0] if spans else None,
            spans[1] if spans else None,
            damaged,
        )
        prov = Provenance(
            source,
            loc.file,
            page_no,
            cell_start,
            loc.file_offset + cell_start,
            rec.end - cell_start,
            loc.wal_frame,
            loc.commit,
            segs,
        )
        if conf >= MIN_CONFIDENCE:
            out.append(
                Candidate(table.name, rowid, build_values(rec, table, rowid), prov, conf, reasons)
            )
        i = rec.end
    return out


def carve_page(
    page: bytes,
    page_no: int,
    tables: list[TableInfo],
    loc: PageLoc,
    encoding: str,
    usable: int,
    in_freelist: bool = False,
    base_override: dict[str, float] | None = None,
    source_override: str | None = None,
    include_live_cells: bool = False,
) -> list[Candidate]:
    """Carve freeblocks and unallocated space of one table-leaf page image.

    For freelist leaves (and page images we cannot attribute to the live tree),
    the allocated cells are decoded too, since their content was never cleared.
    """
    try:
        ph = parse_page_header(page, page_no)
    except ParseError:
        return []
    if ph.page_type != TABLE_LEAF:
        return []
    bases = dict(BASE)
    if base_override:
        bases.update(base_override)
    out: list[Candidate] = []

    def src(kind: str) -> str:
        if source_override:
            return source_override
        return "freelist_leaf" if in_freelist else kind

    def base(kind: str) -> float:
        return bases[src(kind)] if source_override or in_freelist else bases[kind]

    fbs = freeblocks(page, ph, usable)
    for off, size in fbs:
        out += carve_region(
            page,
            page_no,
            off,
            off + size,
            tables,
            loc,
            encoding,
            src("freeblock"),
            base("freeblock"),
            4,
        )
    ua_start, ua_end = unallocated(ph, usable)
    if ua_end > ua_start:
        out += carve_region(
            page,
            page_no,
            ua_start,
            ua_end,
            tables,
            loc,
            encoding,
            src("unallocated"),
            base("unallocated"),
        )
    if in_freelist or include_live_cells:
        for ptr in cell_pointers(page, ph):
            hit = None
            if ptr < usable:
                hit = _try_at(page, ptr, usable, tables, encoding, True)
            if hit is None:
                continue
            rec, table, adj, reasons, rowid, spans = hit
            cell_src = source_override or ("freelist_leaf" if in_freelist else "btree_live")
            conf = max(0.0, min(1.0, bases.get(cell_src, 0.85) + adj))
            segs = build_segments(rec, table, page_mapper(loc), rowid, spans[0], spans[1])
            prov = Provenance(
                cell_src,
                loc.file,
                page_no,
                ptr,
                loc.file_offset + ptr,
                rec.end - ptr,
                loc.wal_frame,
                loc.commit,
                segs,
            )
            if conf >= MIN_CONFIDENCE:
                out.append(
                    Candidate(
                        table.name,
                        rowid,
                        build_values(rec, table, rowid),
                        prov,
                        conf,
                        ["allocated cell on a page outside the live tree", *reasons],
                    )
                )
    return out
