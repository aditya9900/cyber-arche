"""WAL header, frames, checksums, commits and old generations (BLUEPRINT "WAL format", F2)."""

import struct
from dataclasses import dataclass, field

from .errors import ParseError

WAL_MAGIC_LE = 0x377F0682
WAL_MAGIC_BE = 0x377F0683
WAL_HDR = 32
FRAME_HDR = 24


def wal_checksum(data: bytes, s0: int, s1: int, big_endian: bool) -> tuple[int, int]:
    fmt = ">" if big_endian else "<"
    words = struct.unpack(f"{fmt}{len(data) // 4}I", data[: len(data) // 4 * 4])
    for i in range(0, len(words) - 1, 2):
        s0 = (s0 + words[i] + s1) & 0xFFFFFFFF
        s1 = (s1 + words[i + 1] + s0) & 0xFFFFFFFF
    return s0, s1


@dataclass
class WalFrame:
    index: int  # 1-based frame number
    page_no: int
    db_size: int  # non-zero on commit frames
    salt1: int
    salt2: int
    file_offset: int  # offset of the frame header
    valid: bool = False
    committed: bool = False
    commit: int | None = None
    old_generation: bool = False

    @property
    def page_offset(self) -> int:
        return self.file_offset + FRAME_HDR


@dataclass
class WalCommit:
    index: int  # 1-based
    first_frame: int
    last_frame: int
    db_size_pages: int


@dataclass
class Wal:
    data: bytes
    magic: int
    big_endian: bool
    version: int
    page_size: int
    checkpoint_seq: int
    salt1: int
    salt2: int
    header_checksum_ok: bool
    frames: list[WalFrame] = field(default_factory=list)
    commits: list[WalCommit] = field(default_factory=list)
    valid_frames: int = 0
    errors: list[str] = field(default_factory=list)

    def frame_page(self, f: WalFrame) -> bytes:
        return self.data[f.page_offset : f.page_offset + self.page_size]

    @property
    def old_generation_frames(self) -> list[WalFrame]:
        return [f for f in self.frames if f.old_generation]

    @property
    def uncommitted_frames(self) -> list[WalFrame]:
        return [f for f in self.frames if f.valid and not f.committed]

    def summary(self) -> dict:
        return {
            "magic": hex(self.magic),
            "big_endian": self.big_endian,
            "version": self.version,
            "page_size": self.page_size,
            "checkpoint_seq": self.checkpoint_seq,
            "salt1": self.salt1,
            "salt2": self.salt2,
            "header_checksum_ok": self.header_checksum_ok,
            "frames_total": len(self.frames),
            "frames_valid": self.valid_frames,
            "frames_uncommitted": len(self.uncommitted_frames),
            "frames_old_generation": len(self.old_generation_frames),
            "commits": len(self.commits),
            "errors": self.errors,
        }


def parse_wal(data: bytes) -> Wal:
    if len(data) < WAL_HDR:
        raise ParseError(0, "WAL shorter than its 32-byte header")
    magic, version, page_size, ckpt, salt1, salt2, c1, c2 = struct.unpack(">8I", data[:32])
    if magic not in (WAL_MAGIC_LE, WAL_MAGIC_BE):
        raise ParseError(0, f"bad WAL magic {magic:#x}")
    be = bool(magic & 1)
    if page_size < 512 or page_size > 65536 or page_size & (page_size - 1):
        raise ParseError(8, f"bad WAL page size {page_size}")
    s = wal_checksum(data[:24], 0, 0, be)
    wal = Wal(data, magic, be, version, page_size, ckpt, salt1, salt2, s == (c1, c2))
    if not wal.header_checksum_ok:
        wal.errors.append("WAL header checksum mismatch: no frame can be trusted")
    frame_size = FRAME_HDR + page_size
    nframes = (len(data) - WAL_HDR) // frame_size
    chain_ok = wal.header_checksum_ok
    pending: list[WalFrame] = []
    for i in range(nframes):
        off = WAL_HDR + i * frame_size
        pno, dbsize, fs1, fs2, fc1, fc2 = struct.unpack(">6I", data[off : off + 24])
        fr = WalFrame(i + 1, pno, dbsize, fs1, fs2, off)
        if chain_ok:
            s = wal_checksum(data[off : off + 8], *s, be)
            s = wal_checksum(data[off + 24 : off + frame_size], *s, be)
            if pno and (fs1, fs2) == (salt1, salt2) and s == (fc1, fc2):
                fr.valid = True
                wal.valid_frames = i + 1
                pending.append(fr)
                if dbsize:
                    c = WalCommit(len(wal.commits) + 1, pending[0].index, fr.index, dbsize)
                    wal.commits.append(c)
                    for p in pending:
                        p.committed = True
                        p.commit = c.index
                    pending = []
            else:
                chain_ok = False
        if not fr.valid and pno and (fs1, fs2) != (salt1, salt2):
            # leftovers from a previous WAL generation (before a checkpoint reset)
            fr.old_generation = True
        wal.frames.append(fr)
    return wal
