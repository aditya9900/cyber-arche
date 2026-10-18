import struct

import pytest
from cyber_arche.btree import local_payload_len
from cyber_arche.errors import ParseError
from cyber_arche.header import parse_header
from cyber_arche.record import parse_record, serial_len
from cyber_arche.varint import read_varint, varint_len
from cyber_arche.wal import parse_wal, wal_checksum

from tests.conftest import OUT


@pytest.mark.parametrize("value", [0, 1, 127, 128, 16383, 16384, 2**31, 2**56 - 1, 2**63])
def test_varint_roundtrip(value):
    # encode like SQLite
    if value > 0x00FFFFFFFFFFFFFF:
        raw = bytearray()
        v = value >> 8
        for _ in range(8):
            raw.insert(0, (v & 0x7F) | 0x80)
            v >>= 7
        raw.append(value & 0xFF)
    else:
        out = [value & 0x7F]
        v = value >> 7
        while v:
            out.insert(0, (v & 0x7F) | 0x80)
            v >>= 7
        raw = bytes(out)
    got, n = read_varint(bytes(raw), 0)
    assert got == value and n == len(raw) == varint_len(value)


def test_varint_bounds():
    with pytest.raises(ParseError):
        read_varint(b"\x81\x81", 0)


def test_serial_lengths():
    assert [serial_len(t) for t in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13, 14, 15)] == [
        0,
        1,
        2,
        3,
        4,
        6,
        8,
        8,
        0,
        0,
        0,
        0,
        1,
        1,
    ]
    assert serial_len(10) is None and serial_len(11) is None


def test_record_decode():
    # header: size 4, types: NULL, int8, text len 2
    rec = parse_record(bytes([4, 0, 1, 17, 0x2A]) + b"hi", 0, 7)
    assert [v.value for v in rec.values] == [None, 42, "hi"]


def test_header_and_overflow_math():
    h = parse_header((OUT / "f1_rollback" / "messages.db").read_bytes())
    assert h.page_size == 4096 and h.encoding == "utf-8" and not h.wal_mode
    assert local_payload_len(100, 4096) == 100
    assert local_payload_len(10000, 4096) < 4096


def test_wal_checksums_and_corruption():
    data = (OUT / "f2_wal" / "messages.db-wal").read_bytes()
    wal = parse_wal(data)
    assert wal.header_checksum_ok and wal.commits and wal.valid_frames == len(wal.frames)
    # flip one byte in frame 5's page: validity stops at frame 4
    bad = bytearray(data)
    off = 32 + 4 * (24 + wal.page_size) + 24 + 100
    bad[off] ^= 0xFF
    wal2 = parse_wal(bytes(bad))
    assert wal2.valid_frames == 4
    assert all(c.last_frame <= 4 for c in wal2.commits)


def test_wal_checksum_word_order():
    data = struct.pack(">4I", 1, 2, 3, 4)
    assert wal_checksum(data, 0, 0, True) != wal_checksum(data, 0, 0, False)
