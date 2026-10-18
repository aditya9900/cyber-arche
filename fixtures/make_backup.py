"""Build fixtures/cases/backup: the scripted storyline used if the live demo fails.

Phase A (secure_delete OFF): seed the chat, then delete one message -> residue survives.
Phase B (secure_delete ON):  delete rowids 8 and 9, checkpoint -> zeroed, no trace (CU1, CU2).
Phase C (WAL kept):          new messages, one edit, one delete -> full commit history.
"""

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

OUT = Path(__file__).parent / "cases" / "backup"
SCHEMA = """CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sender TEXT NOT NULL,
  body TEXT NOT NULL,
  sent_at INTEGER NOT NULL
)"""
T0 = int(datetime(2026, 10, 17, 13, 40).timestamp())  # local time, so the UI reads 13:40...

SEED = [
    ("Mara", "Did you see the donut table? There is ONE box left."),
    ("Theo", "Glazed or the fancy maple ones?"),
    ("Mara", "Maple. With sprinkles. It's a sign."),
    ("Theo", "Judges' table is right next to it though."),
    ("Mara", "We go during the 2pm workshop when everyone leaves."),
    ("Theo", "I'll distract the mentor with a question about pointers."),
    ("Mara", "Perfect. I'll grab the box and walk like I belong."),
    ("Theo", "Plan B: we tell them it's for 'user research'."),
    ("Mara", "If anyone asks, we were never at the snack table."),
    ("Theo", "Bring napkins. Evidence must be minimal. 🍩"),
]


def main() -> None:
    if OUT.exists():
        for p in OUT.glob("messages.db*"):
            p.unlink()
    OUT.mkdir(parents=True, exist_ok=True)
    work = OUT / "work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    db = work / "messages.db"
    con = sqlite3.connect(db, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("PRAGMA secure_delete=OFF")
    con.execute(SCHEMA)
    ins = "INSERT INTO messages(sender, body, sent_at) VALUES (?,?,?)"
    for i, (s, b) in enumerate(SEED):
        con.execute(ins, (s, b, T0 + i * 120))
    # Phase A: an ordinary delete, residue survives
    con.execute("DELETE FROM messages WHERE id=3")
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    # Phase B: the suspect turns on secure_delete and removes two messages
    con.execute("PRAGMA secure_delete=ON")
    con.execute("DELETE FROM messages WHERE id=8")
    con.execute("DELETE FROM messages WHERE id=9")
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    # Phase C: activity kept in the WAL
    t = T0 + 22 * 60  # 14:02
    con.execute(ins, ("Mara", "Ok it's 2:02, workshop just started. Going now.", t))
    con.execute(ins, ("Theo", "Mentor is distracted. Pointers to pointers. Go go go.", t + 60))
    con.execute(ins, ("Mara", "Got the box. Meet at the east stairs.", t + 120))
    con.execute(
        "UPDATE messages SET body=? WHERE id=6",
        ("I'll ask the mentor a question about the workshop.",),
    )
    con.execute("DELETE FROM messages WHERE id=13")
    con.execute(ins, ("Theo", "On my way.", t + 180))
    for suffix in ("", "-wal"):
        shutil.copyfile(Path(str(db) + suffix), OUT / f"messages.db{suffix}")
    con.close()
    shutil.rmtree(work)
    print("backup case written to", OUT)


if __name__ == "__main__":
    main()
