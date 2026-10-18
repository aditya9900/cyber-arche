"""Build synthetic fixture databases with known deletions (BLUEPRINT "Hour-0 bootstrap").

sqlite3 is allowed here: these scripts *create* test databases; they never read evidence.
Each fixture lands in fixtures/out/<name>/ with an expected.json of live and deleted rows.
"""

import json
import shutil
import sqlite3
import sys
from pathlib import Path

OUT = Path(__file__).parent / "out"
SCHEMA = """CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sender TEXT NOT NULL,
  body TEXT NOT NULL,
  sent_at INTEGER NOT NULL
)"""
T0 = 1792224000  # 2026-10-17 08:00 UTC
LINES = [
    ("Mara", "Did you see the donut table? There is ONE box left."),
    ("Theo", "Glazed or the fancy maple ones?"),
    ("Mara", "Maple. With sprinkles. It's a sign."),
    ("Theo", "Judges' table is right next to it though."),
    ("Mara", "We go during the 2pm workshop when everyone leaves."),
    ("Theo", "I'll distract the mentor with a question about pointers."),
    ("Mara", "Perfect. I'll grab the box and walk like I belong."),
    ("Theo", "What if someone asks?"),
    ("Mara", "Say it's for our 'user research'."),
    ("Theo", "Bold. I respect it. 🍩"),
    ("Mara", "Meet at the east stairs, 2:05."),
    ("Theo", "Bring napkins. Evidence must be minimal."),
    ("Mara", "Delete this chat after, obviously."),
    ("Theo", "Obviously."),
    ("Mara", "Coffee run first?"),
    (
        "Theo",
        "Always. Oat latte, double shot, the usual negotiation with the barista about foam density.",
    ),
    (
        "Mara",
        "Also, the sprinkle ratio on the maple ones is objectively superior to every other option on that table and I will defend this in front of the judges if I have to.",
    ),
    ("Theo", "Noted for the record."),
    ("Mara", "Ok workshop is starting, go go go"),
    ("Theo", "On my way."),
]


def rows(con: sqlite3.Connection) -> list[list]:
    return [
        list(r) for r in con.execute("SELECT id, sender, body, sent_at FROM messages ORDER BY id")
    ]


def fresh(name: str) -> Path:
    d = OUT / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def write_expected(d: Path, **kw) -> None:
    (d / "expected.json").write_text(json.dumps(kw, indent=2, ensure_ascii=False))


def fixture_rollback() -> None:
    """1. Rollback-journal mode: 20 inserts, 5 deletes (newest plus middle rows)."""
    d = fresh("f1_rollback")
    con = sqlite3.connect(d / "messages.db")
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("PRAGMA secure_delete=OFF")
    con.execute(SCHEMA)
    for i, (s, b) in enumerate(LINES):
        con.execute(
            "INSERT INTO messages(sender, body, sent_at) VALUES (?,?,?)", (s, b, T0 + 60 * i)
        )
    con.commit()
    before = {r[0]: r for r in rows(con)}
    gone = [20, 13, 7, 16, 17]
    for rid in gone:
        con.execute("DELETE FROM messages WHERE id=?", (rid,))
        con.commit()
    live = rows(con)
    con.close()
    write_expected(d, files={"main": "messages.db"}, live=live, deleted=[before[r] for r in gone])


def fixture_wal() -> None:
    """2. WAL mode, autocheckpoint off: inserts, one update, two deletes; copied while open."""
    d = fresh("f2_wal")
    work = d / "work"
    work.mkdir()
    con = sqlite3.connect(work / "messages.db", isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("PRAGMA secure_delete=OFF")
    con.execute(SCHEMA)
    for i, (s, b) in enumerate(LINES[:10]):
        con.execute(
            "INSERT INTO messages(sender, body, sent_at) VALUES (?,?,?)", (s, b, T0 + 60 * i)
        )
    before = {r[0]: r for r in rows(con)}
    con.execute(
        "UPDATE messages SET body=? WHERE id=4", ("Judges' table is far away, we're fine.",)
    )
    con.execute("DELETE FROM messages WHERE id=10")
    con.execute("DELETE FROM messages WHERE id=6")
    live = rows(con)
    for suffix in ("", "-wal", "-shm"):
        shutil.copyfile(work / f"messages.db{suffix}", d / f"messages.db{suffix}")
    con.close()
    shutil.rmtree(work)
    write_expected(
        d,
        files={"main": "messages.db", "wal": "messages.db-wal"},
        live=live,
        deleted=[before[10], before[6]],
        updated=[{"before": before[4], "after": [r for r in live if r[0] == 4][0]}],
        commits_min=13,
    )


def fixture_secure() -> None:
    """3. secure_delete=ON with several deletions."""
    d = fresh("f3_secure")
    con = sqlite3.connect(d / "messages.db")
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("PRAGMA secure_delete=ON")
    con.execute(SCHEMA)
    for i, (s, b) in enumerate(LINES):
        con.execute(
            "INSERT INTO messages(sender, body, sent_at) VALUES (?,?,?)", (s, b, T0 + 60 * i)
        )
    con.commit()
    before = {r[0]: r for r in rows(con)}
    gone = [8, 9, 12, 13]
    for rid in gone:
        con.execute("DELETE FROM messages WHERE id=?", (rid,))
    con.commit()
    live = rows(con)
    con.close()
    write_expected(
        d,
        files={"main": "messages.db"},
        live=live,
        deleted=[before[r] for r in gone],
        unrecoverable=gone,
    )


def fixture_middle_no_wal() -> None:
    """4. F7: delete a middle message, then checkpoint so only the main file remains."""
    d = fresh("f4_middle")
    con = sqlite3.connect(d / "messages.db")
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute(SCHEMA)
    for i, (s, b) in enumerate(LINES[:12]):
        con.execute(
            "INSERT INTO messages(sender, body, sent_at) VALUES (?,?,?)", (s, b, T0 + 60 * i)
        )
    con.commit()
    before = {r[0]: r for r in rows(con)}
    con.execute("DELETE FROM messages WHERE id=6")
    con.commit()
    live = rows(con)
    con.close()
    write_expected(d, files={"main": "messages.db"}, live=live, deleted=[before[6]])


def fixture_persist_journal() -> None:
    """5. journal_mode=PERSIST: the journal keeps pre-change page images."""
    d = fresh("f5_journal")
    con = sqlite3.connect(d / "messages.db")
    con.execute("PRAGMA journal_mode=PERSIST")
    con.execute(SCHEMA)
    for i, (s, b) in enumerate(LINES[:8]):
        con.execute(
            "INSERT INTO messages(sender, body, sent_at) VALUES (?,?,?)", (s, b, T0 + 60 * i)
        )
    con.commit()
    before = {r[0]: r for r in rows(con)}
    con.execute("DELETE FROM messages WHERE id=3")
    con.commit()
    live = rows(con)
    con.close()
    write_expected(
        d,
        files={"main": "messages.db", "journal": "messages.db-journal"},
        live=live,
        deleted=[before[3]],
    )


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for f in (
        fixture_rollback,
        fixture_wal,
        fixture_secure,
        fixture_middle_no_wal,
        fixture_persist_journal,
    ):
        f()
        print("built", f.__doc__.splitlines()[0])
    print("fixtures written to", OUT, file=sys.stderr)


if __name__ == "__main__":
    main()
