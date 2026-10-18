"""The demo chat app: plays the suspect's phone (BLUEPRINT F5).

This is the ONE place sqlite3 touches a database: it is the app, not the analyzer.
One long-lived connection, journal_mode=WAL, wal_autocheckpoint=0, secure_delete=OFF,
one commit per action, so every action stays in the WAL as its own commit.
"""

import fcntl
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

LIVE_DIR = Path(os.environ.get("DEMO_DIR", Path(__file__).resolve().parent / "live"))
DB = LIVE_DIR / "messages.db"
PARTICIPANTS = ["Mara", "Theo"]
MAX_LEN = 500

SCHEMA = """CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sender TEXT NOT NULL,
  body TEXT NOT NULL,
  sent_at INTEGER NOT NULL
)"""

STORY = [
    ("Mara", "Did you see the donut table? There is ONE box left."),
    ("Theo", "Glazed or the fancy maple ones?"),
    ("Mara", "Maple. With sprinkles. It's a sign."),
    ("Theo", "Judges' table is right next to it though."),
    ("Mara", "We go during the 2pm workshop when everyone leaves."),
    ("Theo", "I'll distract the mentor with a question about pointers."),
    ("Mara", "Perfect. I'll grab the box and walk like I belong."),
    ("Theo", "What if someone asks?"),
    ("Mara", "Say it's for our 'user research'. 🍩"),
    ("Theo", "Bold. Meet at the east stairs, 2:05."),
]


class DemoApp:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.con: sqlite3.Connection | None = None
        # Two servers sharing one demo database would let one checkpoint the other's
        # WAL and wipe the history the demo depends on, so only one may own it.
        LIVE_DIR.parent.mkdir(parents=True, exist_ok=True)
        self._lockfile = open(LIVE_DIR.parent / ".demo.lock", "w")  # noqa: SIM115
        try:
            fcntl.flock(self._lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            self._lockfile.close()
            raise RuntimeError(
                "Another Cyber Arche server is already running the demo chat. "
                "Stop it (Ctrl+C in its terminal) or open the one that is running."
            ) from e
        self.open()

    def open(self) -> None:
        with self.lock:
            LIVE_DIR.mkdir(parents=True, exist_ok=True)
            fresh = not DB.exists()
            self.con = sqlite3.connect(DB, isolation_level=None, check_same_thread=False)
            self.con.execute("PRAGMA journal_mode=WAL")
            self.con.execute("PRAGMA wal_autocheckpoint=0")
            self.con.execute("PRAGMA secure_delete=OFF")
            if fresh:
                self.seed()

    def seed(self) -> None:
        assert self.con is not None
        self.con.execute(SCHEMA)
        t0 = int(time.time()) - 60 * 60
        for i, (s, b) in enumerate(STORY):
            self.con.execute(
                "INSERT INTO messages(sender, body, sent_at) VALUES (?,?,?)", (s, b, t0 + i * 180)
            )

    def messages(self) -> list[dict]:
        with self.lock:
            assert self.con is not None
            rows = self.con.execute(
                "SELECT id, sender, body, sent_at FROM messages ORDER BY sent_at, id"
            ).fetchall()
        return [dict(id=r[0], sender=r[1], body=r[2], sent_at=r[3]) for r in rows]

    def send(self, sender: str, body: str) -> dict:
        body = body.strip()[:MAX_LEN]
        if not body:
            raise ValueError("empty message")
        if sender not in PARTICIPANTS:
            raise ValueError("unknown sender")
        with self.lock:
            assert self.con is not None
            now = int(time.time())
            cur = self.con.execute(
                "INSERT INTO messages(sender, body, sent_at) VALUES (?,?,?)", (sender, body, now)
            )
            return dict(id=cur.lastrowid, sender=sender, body=body, sent_at=now)

    def edit(self, mid: int, body: str) -> None:
        body = body.strip()[:MAX_LEN]
        if not body:
            raise ValueError("empty message")
        with self.lock:
            assert self.con is not None
            if self.con.execute("UPDATE messages SET body=? WHERE id=?", (body, mid)).rowcount == 0:
                raise KeyError(mid)

    def delete(self, mid: int) -> None:
        with self.lock:
            assert self.con is not None
            if self.con.execute("DELETE FROM messages WHERE id=?", (mid,)).rowcount == 0:
                raise KeyError(mid)

    def seize(self) -> dict[str, tuple[str, bytes]]:
        """Byte-for-byte copy of .db, -wal and -shm WHILE the connection is open
        (closing would checkpoint and delete the WAL)."""
        with self.lock:
            out = {}
            for role, suffix in (("main", ""), ("wal", "-wal"), ("shm", "-shm")):
                p = Path(str(DB) + suffix)
                if p.exists():
                    out[role] = (p.name, p.read_bytes())
            return out

    def reset(self) -> None:
        with self.lock:
            if self.con is not None:
                self.con.close()
                self.con = None
            shutil.rmtree(LIVE_DIR, ignore_errors=True)
            self.open()


if __name__ == "__main__":
    app = DemoApp()
    print(f"seeded {len(app.messages())} messages in {DB}")
