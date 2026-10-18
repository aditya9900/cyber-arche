"""B-tree page headers, cell pointers, freeblocks and unallocated space.

BLUEPRINT "Pages and b-trees" and "What deletion leaves behind".
"""

import struct
from dataclasses import dataclass

from .errors import ParseError

TABLE_LEAF = 0x0D
TABLE_INTERIOR = 0x05
INDEX_LEAF = 0x0A
INDEX_INTERIOR = 0x02
PAGE_TYPES = {TABLE_LEAF, TABLE_INTERIOR, INDEX_LEAF, INDEX_INTERIOR}
MAX_CHAIN = 10_000


@dataclass
class PageHeader:
    hdr_off: int  # offset of the b-tree header within the page (100 on page 1)
    page_type: int
    first_freeblock: int
    ncells: int
    content_start: int
    frag_bytes: int
    right_child: int | None

    @property
    def header_len(self) -> int:
        return 12 if self.page_type in (TABLE_INTERIOR, INDEX_INTERIOR) else 8

    @property
    def ptr_array_end(self) -> int:
        return self.hdr_off + self.header_len + 2 * self.ncells


def hdr_offset(page_no: int) -> int:
    return 100 if page_no == 1 else 0


def parse_page_header(page: bytes, page_no: int) -> PageHeader:
    off = hdr_offset(page_no)
    if len(page) < off + 8:
        raise ParseError(off, "page too short for a header")
    ptype = page[off]
    if ptype not in PAGE_TYPES:
        raise ParseError(off, f"not a b-tree page (type 0x{ptype:02x})")
    first_fb, ncells, content = struct.unpack(">HHH", page[off + 1 : off + 7])
    right = None
    if ptype in (TABLE_INTERIOR, INDEX_INTERIOR):
        right = struct.unpack(">I", page[off + 8 : off + 12])[0]
    ph = PageHeader(off, ptype, first_fb, ncells, content or 65536, page[off + 7], right)
    if ph.ptr_array_end > len(page):
        raise ParseError(off, "cell pointer array runs past page end")
    return ph


def cell_pointers(page: bytes, ph: PageHeader) -> list[int]:
    base = ph.hdr_off + ph.header_len
    return [struct.unpack(">H", page[base + 2 * i : base + 2 * i + 2])[0] for i in range(ph.ncells)]


def freeblocks(page: bytes, ph: PageHeader, usable: int) -> list[tuple[int, int]]:
    """Follow the freeblock chain; returns [(offset, size_including_4_byte_header)]."""
    out: list[tuple[int, int]] = []
    seen: set[int] = set()
    off = ph.first_freeblock
    while off and len(out) < MAX_CHAIN:
        if off in seen or off < ph.ptr_array_end or off + 4 > usable:
            break
        seen.add(off)
        nxt, size = struct.unpack(">HH", page[off : off + 4])
        if size < 4 or off + size > usable:
            break
        out.append((off, size))
        off = nxt
    return out


def unallocated(ph: PageHeader, usable: int) -> tuple[int, int]:
    """The gap between the cell pointer array and the cell content area."""
    return ph.ptr_array_end, min(ph.content_start, usable)
