"""Table b-tree walk with overflow chains and a page->table map.

BLUEPRINT "Pages and b-trees", "Table leaf cells and records", "Overflow pages".
"""

import struct
from dataclasses import dataclass, field

from .errors import ParseError, log
from .page import (
    MAX_CHAIN,
    TABLE_INTERIOR,
    TABLE_LEAF,
    cell_pointers,
    parse_page_header,
)
from .source import PageSource
from .varint import read_varint


@dataclass
class RawCell:
    page_no: int
    cell_off: int  # offset of the cell within its page
    payload_size: int
    payload_size_len: int
    rowid: int
    rowid_len: int
    payload_off: int  # offset of the local payload within the page
    local_len: int
    payload: bytes  # full payload (local + overflow)
    overflow_pages: list[int] = field(default_factory=list)
    truncated: bool = False

    @property
    def cell_len(self) -> int:
        return (
            (self.payload_off - self.cell_off) + self.local_len + (4 if self.overflow_pages else 0)
        )


@dataclass
class TableWalk:
    cells: list[RawCell] = field(default_factory=list)
    leaf_pages: set[int] = field(default_factory=set)
    interior_pages: set[int] = field(default_factory=set)
    overflow_pages: set[int] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


def local_payload_len(payload_size: int, usable: int) -> int:
    """Bytes of a table-leaf payload stored on the page itself."""
    x = usable - 35
    if payload_size <= x:
        return payload_size
    m = ((usable - 12) * 32 // 255) - 23
    k = m + ((payload_size - m) % (usable - 4))
    return k if k <= x else m


def read_leaf_cell(page: bytes, page_no: int, off: int, src: PageSource) -> RawCell:
    usable = src.usable
    if off >= usable:
        raise ParseError(off, "cell pointer past usable size")
    psize, n1 = read_varint(page, off, usable)
    rowid, n2 = read_varint(page, off + n1, usable)
    if rowid >= 1 << 63:
        rowid -= 1 << 64
    pstart = off + n1 + n2
    local = local_payload_len(psize, usable)
    if pstart + local > usable:
        raise ParseError(off, "cell payload runs past page")
    payload = bytearray(page[pstart : pstart + local])
    ovf: list[int] = []
    truncated = False
    if local < psize:
        if pstart + local + 4 > usable:
            raise ParseError(off, "overflow pointer past page")
        nxt = struct.unpack(">I", page[pstart + local : pstart + local + 4])[0]
        seen: set[int] = set()
        while nxt and len(payload) < psize and len(seen) < MAX_CHAIN:
            if nxt in seen:
                break
            seen.add(nxt)
            op = src.page(nxt)
            if op is None:
                break
            ovf.append(nxt)
            take = min(usable - 4, psize - len(payload))
            payload += op[4 : 4 + take]
            nxt = struct.unpack(">I", op[:4])[0]
        truncated = len(payload) < psize
    return RawCell(
        page_no, off, psize, n1, rowid, n2, pstart, local, bytes(payload), ovf, truncated
    )


def walk_table(src: PageSource, root: int) -> TableWalk:
    """Collect every live cell of the table b-tree rooted at `root`."""
    out = TableWalk()
    stack = [root]
    seen: set[int] = set()
    while stack and len(seen) < MAX_CHAIN * 10:
        pno = stack.pop()
        if pno in seen:
            out.errors.append(f"b-tree loop at page {pno}")
            continue
        seen.add(pno)
        page = src.page(pno)
        if page is None:
            out.errors.append(f"page {pno} missing")
            continue
        try:
            ph = parse_page_header(page, pno)
        except ParseError as e:
            out.errors.append(f"page {pno}: {e.reason}")
            continue
        if ph.page_type == TABLE_INTERIOR:
            out.interior_pages.add(pno)
            children = []
            for ptr in cell_pointers(page, ph):
                if ptr + 4 <= len(page):
                    children.append(struct.unpack(">I", page[ptr : ptr + 4])[0])
            if ph.right_child:
                children.append(ph.right_child)
            stack.extend(reversed(children))
        elif ph.page_type == TABLE_LEAF:
            out.leaf_pages.add(pno)
            for ptr in cell_pointers(page, ph):
                try:
                    cell = read_leaf_cell(page, pno, ptr, src)
                except ParseError as e:
                    log.debug("page %d cell @%d: %s", pno, ptr, e.reason)
                    out.errors.append(f"page {pno} cell {ptr}: {e.reason}")
                    continue
                out.overflow_pages.update(cell.overflow_pages)
                out.cells.append(cell)
        else:
            out.errors.append(f"page {pno} is an index page (type 0x{ph.page_type:02x}); skipped")
    out.cells.sort(key=lambda c: c.rowid)
    return out


def freelist_pages(src: PageSource, trunk: int) -> tuple[list[int], list[int]]:
    """Return (trunk_pages, leaf_pages) of the freelist starting at `trunk`."""
    trunks: list[int] = []
    leaves: list[int] = []
    seen: set[int] = set()
    while trunk and trunk not in seen and len(seen) < MAX_CHAIN:
        seen.add(trunk)
        page = src.page(trunk)
        if page is None:
            break
        trunks.append(trunk)
        nxt, count = struct.unpack(">II", page[:8])
        count = min(count, (src.usable - 8) // 4)
        for i in range(count):
            leaves.append(struct.unpack(">I", page[8 + 4 * i : 12 + 4 * i])[0])
        trunk = nxt
    return trunks, leaves
