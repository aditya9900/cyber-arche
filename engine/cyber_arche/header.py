"""The 100-byte database header (BLUEPRINT "Database header")."""

import struct
from dataclasses import dataclass

from .errors import ParseError

MAGIC = b"SQLite format 3\x00"
ENCODINGS = {1: "utf-8", 2: "utf-16-le", 3: "utf-16-be"}


@dataclass
class DbHeader:
    page_size: int
    write_version: int
    read_version: int
    reserved: int
    change_counter: int
    page_count: int
    freelist_trunk: int
    freelist_count: int
    largest_root: int
    encoding: str
    incremental_vacuum: int
    version_valid_for: int
    sqlite_version: int

    @property
    def usable_size(self) -> int:
        return self.page_size - self.reserved

    @property
    def wal_mode(self) -> bool:
        return self.write_version == 2 and self.read_version == 2

    @property
    def auto_vacuum(self) -> bool:
        return self.largest_root != 0

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d.update(usable_size=self.usable_size, wal_mode=self.wal_mode, auto_vacuum=self.auto_vacuum)
        return d


def is_sqlite(data: bytes) -> bool:
    return data[:16] == MAGIC


def parse_header(data: bytes) -> DbHeader:
    if len(data) < 100:
        raise ParseError(0, "file shorter than the 100-byte header")
    if not is_sqlite(data):
        raise ParseError(0, "missing 'SQLite format 3' magic: encrypted or not SQLite")
    (ps,) = struct.unpack(">H", data[16:18])
    page_size = 65536 if ps == 1 else ps
    if page_size < 512 or page_size & (page_size - 1):
        raise ParseError(16, f"invalid page size {page_size}")
    enc = struct.unpack(">I", data[56:60])[0]
    return DbHeader(
        page_size=page_size,
        write_version=data[18],
        read_version=data[19],
        reserved=data[20],
        change_counter=struct.unpack(">I", data[24:28])[0],
        page_count=struct.unpack(">I", data[28:32])[0],
        freelist_trunk=struct.unpack(">I", data[32:36])[0],
        freelist_count=struct.unpack(">I", data[36:40])[0],
        largest_root=struct.unpack(">I", data[52:56])[0],
        encoding=ENCODINGS.get(enc, "utf-8"),
        incremental_vacuum=struct.unpack(">I", data[64:68])[0],
        version_valid_for=struct.unpack(">I", data[92:96])[0],
        sqlite_version=struct.unpack(">I", data[96:100])[0],
    )
