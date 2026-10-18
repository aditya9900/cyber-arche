"""Record headers, serial types, value decoding and segment maps.

BLUEPRINT "Table leaf cells and records".
"""

import struct
from dataclasses import dataclass, field
from typing import Any

from .errors import ParseError
from .varint import read_varint


def serial_len(st: int) -> int | None:
    """Body bytes for a serial type; None for the reserved types 10 and 11."""
    if st in (0, 8, 9):
        return 0
    if 1 <= st <= 4:
        return st
    if st == 5:
        return 6
    if st in (6, 7):
        return 8
    if st in (10, 11):
        return None
    return (st - 12) // 2 if st % 2 == 0 else (st - 13) // 2


def serial_kind(st: int) -> str:
    if st == 0:
        return "null"
    if 1 <= st <= 6 or st in (8, 9):
        return "integer"
    if st == 7:
        return "real"
    if st >= 12:
        return "blob" if st % 2 == 0 else "text"
    return "invalid"


@dataclass
class DecodedValue:
    kind: str
    value: Any
    start: int  # offset in the buffer
    length: int
    text_ok: bool = True


@dataclass
class ParsedRecord:
    start: int  # offset of the record header
    header_size: int
    serial_types: list[int]
    values: list[DecodedValue]
    end: int  # one past the last body byte
    header_varint_len: int = 1
    notes: list[str] = field(default_factory=list)

    @property
    def body_len(self) -> int:
        return sum(v.length for v in self.values)


def decode_value(st: int, raw: bytes, encoding: str) -> tuple[Any, bool]:
    kind = serial_kind(st)
    if kind == "null":
        return None, True
    if st == 8:
        return 0, True
    if st == 9:
        return 1, True
    if kind == "integer":
        return int.from_bytes(raw, "big", signed=True), True
    if kind == "real":
        return struct.unpack(">d", raw)[0], True
    if kind == "text":
        try:
            return raw.decode(encoding), True
        except UnicodeDecodeError:
            return raw.decode(encoding, errors="replace"), False
    return raw.hex(), True


def parse_record(
    buf: bytes,
    pos: int,
    end: int,
    encoding: str = "utf-8",
    max_cols: int = 2000,
) -> ParsedRecord:
    """Parse a record header at `pos` and the body that follows, all before `end`."""
    hsize, n = read_varint(buf, pos, end)
    if hsize < n + 1 or pos + hsize > end:
        raise ParseError(pos, f"bad record header size {hsize}")
    types: list[int] = []
    p = pos + n
    while p < pos + hsize:
        st, k = read_varint(buf, p, pos + hsize)
        types.append(st)
        p += k
        if len(types) > max_cols:
            raise ParseError(pos, "too many columns")
    if p != pos + hsize:
        raise ParseError(pos, "serial types overrun header size")
    values: list[DecodedValue] = []
    q = pos + hsize
    for st in types:
        ln = serial_len(st)
        if ln is None:
            raise ParseError(q, f"reserved serial type {st}")
        if q + ln > end:
            raise ParseError(q, "record body runs past region")
        val, ok = decode_value(st, buf[q : q + ln], encoding)
        values.append(DecodedValue(serial_kind(st), val, q, ln, ok))
        q += ln
    return ParsedRecord(pos, hsize, types, values, q, n)
