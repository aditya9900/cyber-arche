"""SQLite varints (BLUEPRINT "Varints")."""

from .errors import ParseError


def read_varint(buf: bytes, pos: int, end: int | None = None) -> tuple[int, int]:
    """Return (value, number_of_bytes_consumed). Raises ParseError past `end`."""
    limit = len(buf) if end is None else min(end, len(buf))
    value = 0
    for i in range(8):
        if pos + i >= limit:
            raise ParseError(pos, "varint runs past end of region")
        b = buf[pos + i]
        value = (value << 7) | (b & 0x7F)
        if b < 0x80:
            return value, i + 1
    if pos + 8 >= limit:
        raise ParseError(pos, "varint runs past end of region")
    return (value << 8) | buf[pos + 8], 9


def varint_len(value: int) -> int:
    """Number of bytes SQLite uses to encode a non-negative value."""
    if value < 0 or value > 0x00FFFFFFFFFFFFFF:
        return 9
    n = 1
    while value >= 0x80:
        value >>= 7
        n += 1
    return n
