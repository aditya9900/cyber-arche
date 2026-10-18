"""Snapshot k: the main file overlaid with WAL frames up to commit k (BLUEPRINT F2)."""

from .source import MainFile, PageLoc
from .wal import Wal, WalFrame


class Snapshot:
    """PageSource for the database as of commit k (k = 0 is the main file alone)."""

    def __init__(self, main: MainFile, wal: Wal | None, k: int) -> None:
        self.main = main
        self.wal = wal
        self.k = k
        self.page_size = main.page_size
        self.usable = main.usable
        self.encoding = main.encoding
        self.frames: dict[int, WalFrame] = {}
        self.db_size = main.page_count()
        if wal is not None and k > 0:
            c = wal.commits[k - 1]
            self.db_size = c.db_size_pages
            for f in wal.frames:
                if f.valid and f.committed and f.index <= c.last_frame:
                    self.frames[f.page_no] = f

    def page_count(self) -> int:
        return self.db_size

    def page(self, n: int) -> bytes | None:
        if n < 1 or n > self.db_size:
            return None
        f = self.frames.get(n)
        if f is not None and self.wal is not None:
            return self.wal.frame_page(f)
        p = self.main.page(n)
        if p is None:  # page exists only in later WAL frames or was never written
            return bytes(self.page_size)
        return p

    def locate(self, n: int) -> PageLoc | None:
        if n < 1 or n > self.db_size:
            return None
        f = self.frames.get(n)
        if f is not None:
            return PageLoc("wal", f.page_offset, f.index, f.commit)
        return self.main.locate(n)


def checkpointed_through(main: MainFile, wal: Wal) -> int:
    """Highest WAL frame whose page image already equals the main file's page.

    If a checkpoint copied frame f into the main file, every snapshot before
    f's commit is only approximate: the main file no longer holds the older page.
    """
    latest = 0
    for f in wal.frames:
        if not f.valid:
            continue
        mp = main.page(f.page_no)
        if mp is not None and mp == wal.frame_page(f):
            # only meaningful if a different, earlier version of this page existed
            latest = max(latest, f.index)
    return latest
