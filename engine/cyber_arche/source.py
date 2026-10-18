"""PageSource: the single interface every page read goes through.

BLUEPRINT "Repository layout": main file, WAL frame N and snapshot k all look
the same to the b-tree walker and the carver.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PageLoc:
    """Where a page image physically lives."""

    file: str  # main | wal | journal
    file_offset: int  # absolute offset of the first byte of the page image
    wal_frame: int | None = None
    commit: int | None = None
    old_generation: bool = False
    committed: bool = True


class PageSource(Protocol):
    page_size: int
    usable: int
    encoding: str

    def page_count(self) -> int: ...

    def page(self, n: int) -> bytes | None: ...

    def locate(self, n: int) -> PageLoc | None: ...


class MainFile:
    """Pages straight from the main database file."""

    def __init__(self, data: bytes, page_size: int, usable: int, encoding: str) -> None:
        self.data = data
        self.page_size = page_size
        self.usable = usable
        self.encoding = encoding

    def page_count(self) -> int:
        return len(self.data) // self.page_size

    def page(self, n: int) -> bytes | None:
        if n < 1 or n > self.page_count():
            return None
        off = (n - 1) * self.page_size
        return self.data[off : off + self.page_size]

    def locate(self, n: int) -> PageLoc | None:
        if n < 1 or n > self.page_count():
            return None
        return PageLoc("main", (n - 1) * self.page_size)
