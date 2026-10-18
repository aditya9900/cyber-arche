"""Per-commit table state, diffs and time bracketing (BLUEPRINT F2).

The WAL has no clock: changes are bracketed between the app's own timestamps.
"""

import re
from dataclasses import dataclass, field

from .btree import walk_table
from .carver import Candidate, live_candidate
from .page import TABLE_LEAF
from .schema import TableInfo, read_schema
from .source import PageSource

TS_NAME = re.compile(r"(_at$|^at$|time|date|^ts$|timestamp)", re.I)


@dataclass
class SnapshotState:
    k: int
    tables: list[TableInfo]
    rows: dict[tuple[str, int], Candidate] = field(default_factory=dict)
    page_owner: dict[int, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def read_state(src: PageSource, k: int, final: bool, cache: dict) -> SnapshotState:
    tables, errors = read_schema(src)
    st = SnapshotState(k, tables, errors=errors)
    for t in tables:
        walk = walk_table(src, t.root)
        st.errors += walk.errors
        for p in walk.leaf_pages | walk.interior_pages | walk.overflow_pages:
            st.page_owner[p] = t.name
        for cell in walk.cells:
            loc = src.locate(cell.page_no)
            if loc is None:
                continue
            source = "btree_live" if (final or loc.file == "main") else "wal_frame"
            # only the final state's cells are certainly live; a main-file page that
            # later WAL frames replace is an older version, scored like a WAL frame
            base = 1.0 if final else 0.95
            key = (loc.file, loc.file_offset, cell.cell_off, t.name, source)
            cand = cache.get(key)
            if cand is None:
                cand = live_candidate(cell, t, loc, src.encoding, source, base)
                if cand is not None and not final and loc.file == "main":
                    cand.reasons.append(
                        "allocated cell in a main-file page that later WAL frames replace"
                    )
                cache[key] = cand
            if cand is not None:
                st.rows[(t.name, cell.rowid)] = cand
    return st


def leaf_pages(src: PageSource) -> list[int]:
    out = []
    for n in range(1, src.page_count() + 1):
        p = src.page(n)
        off = 100 if n == 1 else 0
        if p is not None and len(p) > off and p[off] == TABLE_LEAF:
            out.append(n)
    return out


def timestamp_column(t: TableInfo) -> int | None:
    for i, c in enumerate(t.columns):
        if TS_NAME.search(c.name) and c.affinity in ("INTEGER", "NUMERIC", "REAL"):
            return i
    return None


def same_values(a: Candidate, b: Candidate) -> bool:
    return a.key_values == b.key_values


@dataclass
class RawChange:
    commit: int
    table: str
    kind: str
    rowid: int
    before: Candidate | None
    after: Candidate | None


def diff_states(prev: SnapshotState, cur: SnapshotState) -> list[RawChange]:
    out: list[RawChange] = []
    for key, c in cur.rows.items():
        p = prev.rows.get(key)
        if p is None:
            out.append(RawChange(cur.k, key[0], "insert", key[1], None, c))
        elif not same_values(p, c):
            out.append(RawChange(cur.k, key[0], "update", key[1], p, c))
    for key, p in prev.rows.items():
        if key not in cur.rows:
            out.append(RawChange(cur.k, key[0], "delete", key[1], p, None))
    out.sort(key=lambda ch: (ch.table, ch.rowid))
    return out


def first_seen_timestamps(
    states: list[SnapshotState], ts_cols: dict[str, int]
) -> dict[str, list[tuple[int, float]]]:
    """Per table: (first commit a row appeared, its app timestamp)."""
    seen: dict[str, dict[tuple[int, object], int]] = {}
    for st in states:
        for (tname, rowid), c in st.rows.items():
            idx = ts_cols.get(tname)
            if idx is None or idx >= len(c.values):
                continue
            ts = c.values[idx].value
            if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                seen.setdefault(tname, {}).setdefault((rowid, ts), st.k)
    return {t: [(k, ts) for (_r, ts), k in m.items()] for t, m in seen.items()}


def bracket(change: RawChange, first_seen: dict[str, list[tuple[int, float]]]) -> dict | None:
    """after_ts = latest app timestamp known at or before this commit;
    before_ts = earliest app timestamp that first appears after it."""
    rows = first_seen.get(change.table)
    if not rows:
        return None
    after = [ts for k, ts in rows if k <= change.commit]
    before = [ts for k, ts in rows if k > change.commit]
    return {"after_ts": max(after) if after else None, "before_ts": min(before) if before else None}
