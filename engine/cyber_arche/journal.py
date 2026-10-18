"""Rollback journal parsing (BLUEPRINT "Rollback journal")."""

import struct
from dataclasses import dataclass, field

JOURNAL_MAGIC = bytes.fromhex("d9d505f920a163d7")


@dataclass
class JournalPage:
    page_no: int
    file_offset: int  # offset of the page image within the journal
    record_offset: int


@dataclass
class Journal:
    header_intact: bool
    page_count: int
    nonce: int
    initial_db_size: int
    sector_size: int
    page_size: int
    pages: list[JournalPage] = field(default_factory=list)

    def summary(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "pages"} | {
            "pages_found": len(self.pages)
        }


def parse_journal(data: bytes, db_page_size: int, db_pages: int) -> Journal:
    """Parse a -journal file. With journal_mode=PERSIST the header may be zeroed;
    then fall back to the database page size and a 512-byte sector."""
    intact = data[:8] == JOURNAL_MAGIC
    if intact and len(data) >= 28:
        cnt, nonce, init, sector, psz = struct.unpack(">5I", data[8:28])
    else:
        cnt, nonce, init, sector, psz = 0xFFFFFFFF, 0, 0, 512, db_page_size
    if psz < 512 or psz > 65536 or psz & (psz - 1):
        psz = db_page_size
    if sector < 512 or sector > 65536 or sector & (sector - 1):
        sector = 512
    j = Journal(intact, cnt, nonce, init, sector, psz)
    rec = 4 + psz + 4
    off = sector
    limit = max(db_pages, init, 1) + 1024
    seen: set[int] = set()
    while off + rec <= len(data):
        (pno,) = struct.unpack(">I", data[off : off + 4])
        if pno == 0 or pno > limit:
            # a zero page number may also mark a new journal header; try the next sector
            if data[off : off + 8] == JOURNAL_MAGIC:
                off += sector
                continue
            break
        if pno not in seen:
            seen.add(pno)
            j.pages.append(JournalPage(pno, off + 4, off))
        off += rec
    return j
