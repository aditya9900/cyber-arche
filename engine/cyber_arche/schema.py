"""Schema from sqlite_master plus the in-memory PRAGMA table_info trick.

BLUEPRINT F1. sqlite3 is used ONLY on ':memory:' here; evidence is never opened.
"""

import re
import sqlite3
from dataclasses import dataclass, field

from .btree import walk_table
from .errors import ParseError, log
from .record import parse_record
from .source import PageSource


@dataclass
class Column:
    name: str
    decl_type: str
    affinity: str  # INTEGER | TEXT | BLOB | REAL | NUMERIC
    notnull: bool = False
    pk: int = 0
    is_rowid_alias: bool = False


@dataclass
class TableInfo:
    name: str
    root: int
    sql: str
    columns: list[Column] = field(default_factory=list)
    without_rowid: bool = False
    autoincrement: bool = False

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "root": self.root,
            "sql": self.sql,
            "without_rowid": self.without_rowid,
            "columns": [c.__dict__ for c in self.columns],
        }


def affinity(decl: str) -> str:
    """SQLite's column affinity rules (datatype3 section 3.1)."""
    d = decl.upper()
    if "INT" in d:
        return "INTEGER"
    if any(k in d for k in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in d or not d:
        return "BLOB"
    if any(k in d for k in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


SQLITE_MASTER = TableInfo(
    "sqlite_master",
    1,
    "CREATE TABLE sqlite_master(type text, name text, tbl_name text, rootpage integer, sql text)",
    [
        Column("type", "text", "TEXT"),
        Column("name", "text", "TEXT"),
        Column("tbl_name", "text", "TEXT"),
        Column("rootpage", "integer", "INTEGER"),
        Column("sql", "text", "TEXT"),
    ],
)


RESERVED = {
    "sqlite_sequence": [Column("name", "", "BLOB"), Column("seq", "", "BLOB")],
    "sqlite_stat1": [
        Column("tbl", "", "BLOB"),
        Column("idx", "", "BLOB"),
        Column("stat", "", "BLOB"),
    ],
}


def columns_from_sql(name: str, sql: str) -> list[Column]:
    if name in RESERVED:
        return [Column(**c.__dict__) for c in RESERVED[name]]
    if name.lower().startswith("sqlite_"):
        # internal names cannot be created by users; rename in the scratch copy
        sql = re.sub(re.escape(name), "x_" + name, sql, count=1, flags=re.I)
        name = "x_" + name
    con = sqlite3.connect(":memory:")
    try:
        con.execute(sql)
        rows = con.execute(f'PRAGMA table_info("{name.replace(chr(34), chr(34) * 2)}")').fetchall()
    finally:
        con.close()
    cols = [Column(r[1], r[2] or "", affinity(r[2] or ""), bool(r[3]), int(r[5])) for r in rows]
    pks = [c for c in cols if c.pk]
    if len(pks) == 1 and pks[0].decl_type.strip().upper() == "INTEGER":
        pks[0].is_rowid_alias = True
    return cols


def read_schema(src: PageSource) -> tuple[list[TableInfo], list[str]]:
    """Every rowid table in sqlite_master (plus sqlite_master itself)."""
    walk = walk_table(src, 1)
    errors = list(walk.errors)
    tables: list[TableInfo] = []
    for cell in walk.cells:
        try:
            rec = parse_record(cell.payload, 0, len(cell.payload), src.encoding)
        except ParseError as e:
            errors.append(f"sqlite_master row {cell.rowid}: {e.reason}")
            continue
        vals = [v.value for v in rec.values] + [None] * 5
        typ, name, _tbl, root, sql = vals[:5]
        if typ != "table" or not isinstance(root, int) or root <= 0 or not sql:
            continue
        without = bool(re.search(r"\)\s*WITHOUT\s+ROWID\s*;?\s*$", sql, re.I))
        try:
            cols = columns_from_sql(name, sql)
        except sqlite3.Error as e:
            log.warning("cannot derive columns for %s: %s", name, e)
            errors.append(f"table {name}: {e}")
            continue
        tables.append(TableInfo(name, root, sql, cols, without, "AUTOINCREMENT" in sql.upper()))
    return tables, errors
