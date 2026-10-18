"""Build upload-ready sample databases: samples/test/* (coverage) and samples/demo/* (stories).

sqlite3 is allowed here: this script *creates* synthetic evidence. Every person,
message and URL is fictional. Each scenario lands in its own folder; upload the
files in that folder through "New case from files" on the home page.

    .venv/bin/python samples/make_samples.py
"""

import os
import random
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
TEST = ROOT / "test"
DEMO = ROOT / "demo"

CHAT = """CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sender TEXT NOT NULL,
  body TEXT NOT NULL,
  sent_at INTEGER NOT NULL
)"""
INS = "INSERT INTO messages(sender, body, sent_at) VALUES (?,?,?)"


def at(h: int, m: int) -> int:
    """Epoch seconds for 17 Oct 2026, local time, so the UI shows h:m."""
    return int(datetime(2026, 10, 17, h, m).timestamp())


DONUT = [
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
    ("Mara", "Bring napkins. Evidence must be minimal."),
    ("Theo", "Delete this chat after, obviously."),
    ("Mara", "Obviously."),
    ("Theo", "Coffee run first?"),
    ("Mara", "Always. Oat latte, double shot."),
    ("Theo", "Ok workshop is starting, go go go"),
]


def fresh(base: Path, name: str) -> Path:
    d = base / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def note(d: Path, text: str) -> None:
    (d / "ABOUT.txt").write_text(text.strip() + "\n")


def seed_chat(con: sqlite3.Connection, rows=DONUT, start=(13, 40), step_min=2) -> None:
    con.execute(CHAT)
    t = at(*start)
    for i, (s, b) in enumerate(rows):
        con.execute(INS, (s, b, t + i * step_min * 60))


def wal_open(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("PRAGMA secure_delete=OFF")
    return con


def seize(work: Path, dest: Path, name: str = "messages.db", roles=("", "-wal")) -> None:
    """Copy the database files while the connection is still open."""
    for suffix in roles:
        src = work / f"{name}{suffix}"
        if src.exists():
            shutil.copyfile(src, dest / f"{name}{suffix}")


# --------------------------------------------------------------------------- test files


def t01_rollback_basic() -> None:
    d = fresh(TEST, "01_rollback_freeblocks")
    con = sqlite3.connect(d / "messages.db")
    con.execute("PRAGMA journal_mode=DELETE")
    seed_chat(con)
    con.commit()
    for rid in (16, 5, 9, 10):  # newest (top of content area) plus middle rows
        con.execute("DELETE FROM messages WHERE id=?", (rid,))
        con.commit()
    con.close()
    note(
        d,
        """
Upload: messages.db
Rollback-journal mode (no WAL). Deleted rowids 16, 5, 9 and 10.
Expect: 12 live, 4 deleted. Rowid 16 comes from unallocated space; 5, 9 and 10
come from freeblocks (rebuilt: their first 4 bytes were overwritten), with
rowids inferred from neighbouring timestamps. Finding: CU3 (info: the
newest row was deleted, and it was recovered).
""",
    )


def t02_wal_history() -> None:
    d = fresh(TEST, "02_wal_timeline")
    work = d / "work"
    work.mkdir()
    con = wal_open(work / "messages.db")
    seed_chat(con, DONUT[:10])
    con.execute(
        "UPDATE messages SET body=? WHERE id=4", ("Judges' table is far away, we're fine.",)
    )
    con.execute("DELETE FROM messages WHERE id=10")
    con.execute("DELETE FROM messages WHERE id=6")
    seize(work, d)
    con.close()
    shutil.rmtree(work)
    note(
        d,
        """
Upload: messages.db AND messages.db-wal
WAL mode, nothing checkpointed. Every insert, the edit of rowid 4 and the
deletes of rowids 10 and 6 are separate commits.
Expect: 8 live, 2 deleted, 1 prior version (rowid 4's old text). The timeline
slider steps through every commit; scrub back before the delete to see it live.
Finding: CU3 (info: the newest row was deleted, and it was recovered).
""",
    )


def t03_wal_missing() -> None:
    src = TEST / "02_wal_timeline" / "messages.db"
    d = fresh(TEST, "03_wal_left_behind")
    shutil.copyfile(src, d / "messages.db")
    note(
        d,
        """
Upload: messages.db ONLY (same database as test 02, without its -wal file)
Expect: almost nothing. In WAL mode the main file is a 1-page stub until a
checkpoint, so every message lives in the -wal file. Shows why investigators
must seize the -wal alongside the database.
""",
    )


def t04_persist_journal() -> None:
    d = fresh(TEST, "04_rollback_journal")
    con = sqlite3.connect(d / "messages.db")
    con.execute("PRAGMA journal_mode=PERSIST")
    seed_chat(con, DONUT[:8])
    con.commit()
    con.execute("DELETE FROM messages WHERE id=3")
    con.commit()
    con.close()
    note(
        d,
        """
Upload: messages.db AND messages.db-journal
journal_mode=PERSIST keeps the pre-change page in the journal after commit.
Expect: 7 live, rowid 3 deleted, found in the journal (and rebuilt from a
freeblock in the main file). Copies of live rows in the journal are
labelled stale copies, never "deleted".
""",
    )


def t05_secure_delete() -> None:
    d = fresh(TEST, "05_secure_delete")
    con = sqlite3.connect(d / "messages.db")
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("PRAGMA secure_delete=ON")
    seed_chat(con)
    con.commit()
    for rid in (8, 9, 12):
        con.execute("DELETE FROM messages WHERE id=?", (rid,))
    con.commit()
    con.close()
    note(
        d,
        """
Upload: messages.db
secure_delete=ON zeroes deleted bytes.
Expect: 13 live, nothing recovered. Findings CU1 (secure-delete pattern:
zeroed freeblocks) and CU2 (rowids 8, 9, 12 missing with no residue).
""",
    )


def t06_vacuum() -> None:
    d = fresh(TEST, "06_vacuum")
    con = sqlite3.connect(d / "messages.db")
    con.execute("PRAGMA journal_mode=DELETE")
    seed_chat(con)
    con.commit()
    con.execute("DELETE FROM messages WHERE id IN (4, 11)")
    con.commit()
    con.execute("VACUUM")
    con.close()
    note(
        d,
        """
Upload: messages.db
Rows deleted, then VACUUM rebuilt the file.
Expect: 14 live, nothing recovered. Findings CU2 (rowids 4 and 11 missing)
and CU4 (possible VACUUM: gaps but no freeblocks, freelist or fragments).
""",
    )


def t07_newest_removed() -> None:
    d = fresh(TEST, "07_newest_rows_removed")
    con = sqlite3.connect(d / "messages.db")
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("PRAGMA secure_delete=ON")
    seed_chat(con)
    con.commit()
    con.execute("DELETE FROM messages WHERE id >= 14")
    con.commit()
    con.close()
    note(
        d,
        """
Upload: messages.db
AUTOINCREMENT table; the three newest messages deleted with secure_delete on.
Expect: 13 live, nothing recovered. Findings CU3 (sqlite_sequence says 16
rowids were issued, highest live is 13, none recovered) and CU2. No CU1: rows
deleted from the top of a page zero unallocated space, not a freeblock.
""",
    )


def t08_auto_vacuum() -> None:
    d = fresh(TEST, "08_auto_vacuum")
    con = sqlite3.connect(d / "messages.db")
    con.execute("PRAGMA auto_vacuum=FULL")
    con.execute("PRAGMA journal_mode=DELETE")
    seed_chat(con)
    con.commit()
    con.execute("DELETE FROM messages WHERE id=6")
    con.commit()
    con.close()
    note(
        d,
        """
Upload: messages.db
auto_vacuum=FULL (header offset 52 is non-zero).
Expect: 15 live, rowid 6 rebuilt from a freeblock. Finding CU5 (auto_vacuum
enabled, info).
""",
    )


def t09_overflow() -> None:
    d = fresh(TEST, "09_long_messages_overflow")
    con = sqlite3.connect(d / "messages.db")
    con.execute("PRAGMA journal_mode=DELETE")
    long_rows = DONUT[:5] + [
        ("Theo", "Meeting notes. " + "The plan has many steps and each one is important. " * 120),
        ("Mara", "tl;dr?"),
    ]
    seed_chat(con, long_rows)
    con.commit()
    con.execute("DELETE FROM messages WHERE id=2")
    con.commit()
    con.close()
    note(
        d,
        """
Upload: messages.db
Rowid 6 is a ~6 KB message that spills onto overflow pages.
Expect: 6 live; rowid 6 is read in full by following its overflow chain.
Rowid 2 deleted and recovered. Tests the overflow-page math.
""",
    )


def t10_browser_history() -> None:
    d = fresh(TEST, "10_browser_history_non_chat")
    con = sqlite3.connect(d / "History")
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute(
        "CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT NOT NULL, title TEXT, "
        "visit_count INTEGER NOT NULL DEFAULT 0, last_visit_time INTEGER NOT NULL)"
    )
    sites = [
        ("https://docs.example.edu/sqlite-file-format", "Database File Format"),
        ("https://news.example.com/hackathon-recap", "Hackathon recap"),
        ("https://maps.example.com/?q=east+stairs", "East stairs - Maps"),
        ("https://shop.example.com/donuts/maple", "Maple donuts, box of 12"),
        ("https://search.example.com/?q=how+to+delete+chat+history", "how to delete chat history"),
        (
            "https://search.example.com/?q=does+deleting+messages+really+delete+them",
            "does deleting messages really delete them",
        ),
        ("https://video.example.com/watch?v=pointers101", "Pointers in 10 minutes"),
        ("https://mail.example.com/inbox", "Inbox"),
    ]
    t = at(12, 0)
    for i, (u, title) in enumerate(sites):
        con.execute(
            "INSERT INTO urls(url, title, visit_count, last_visit_time) VALUES (?,?,?,?)",
            (u, title, 1 + i % 3, t + i * 600),
        )
    con.commit()
    con.execute("DELETE FROM urls WHERE id IN (5, 6)")
    con.commit()
    con.close()
    note(
        d,
        """
Upload: History
A browser-history style table, not a chat. No Conversation tab: use Records.
Expect: 6 live, 2 deleted (the two "how to delete chat history" searches).
""",
    )


def t11_not_sqlite() -> None:
    d = fresh(TEST, "11_encrypted_or_not_sqlite")
    rnd = random.Random(42)
    (d / "encrypted.db").write_bytes(bytes(rnd.randrange(256) for _ in range(8192)))
    note(
        d,
        """
Upload: encrypted.db (random bytes, like a SQLCipher database)
Expect: a "No SQLite header" finding and no records. Decryption is out of
scope; the tool says so instead of guessing.
""",
    )


def t12_corrupted() -> None:
    src = TEST / "01_rollback_freeblocks" / "messages.db"
    d = fresh(TEST, "12_corrupted")
    data = bytearray(src.read_bytes())
    rnd = random.Random(7)
    for _ in range(60):
        data[rnd.randrange(4096 + 8, len(data))] ^= 0xFF  # damage page 2+, keep the header
    (d / "messages.db").write_bytes(bytes(data))
    note(
        d,
        """
Upload: messages.db (test 01 with 60 random bytes flipped in the data pages)
Expect: no crash. Some rows decode, damaged ones are skipped or scored lower.
""",
    )


def t13_utf16() -> None:
    d = fresh(TEST, "13_utf16_encoding")
    con = sqlite3.connect(d / "messages.db")
    con.execute("PRAGMA encoding='UTF-16le'")
    con.execute("PRAGMA journal_mode=DELETE")
    seed_chat(con, DONUT[:8] + [("Theo", "Ça marche. Ünïcödé ✓ 🍩")])
    con.commit()
    con.execute("DELETE FROM messages WHERE id=9")
    con.commit()
    con.close()
    note(
        d,
        """
Upload: messages.db
Database text encoding is UTF-16le instead of UTF-8.
Expect: 8 live, rowid 9 ("Ça marche. Ünïcödé ✓ 🍩") recovered and decoded correctly.
Finding: CU3 (info: the newest row was deleted, and it was recovered).
""",
    )


def t14_wal_old_generation() -> None:
    d = fresh(TEST, "14_wal_reset_old_frames")
    work = d / "work"
    work.mkdir()
    con = wal_open(work / "messages.db")
    seed_chat(con)
    con.execute("DELETE FROM messages WHERE id=7")
    con.execute("PRAGMA wal_checkpoint(RESTART)")
    con.execute(INS, ("Theo", "Fresh start after the checkpoint.", at(15, 0)))
    seize(work, d)
    con.close()
    shutil.rmtree(work)
    note(
        d,
        """
Upload: messages.db AND messages.db-wal
The WAL was checkpointed and restarted, then one new message written. New
frames overwrite the start of the file; frames after them carry the old salts.
Expect: finding CU6 (WAL history: checkpoint sequence and earlier-generation
frames). Old-generation frames appear as a lower-confidence source.
""",
    )


# --------------------------------------------------------------------------- demo files


def d1_donut_heist() -> None:
    d = fresh(DEMO, "1_the_donut_heist")
    work = d / "work"
    work.mkdir()
    con = wal_open(work / "messages.db")
    seed_chat(con, DONUT[:10])
    con.execute("DELETE FROM messages WHERE id=3")
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.execute("PRAGMA secure_delete=ON")
    con.execute("DELETE FROM messages WHERE id=8")
    con.execute("DELETE FROM messages WHERE id=9")
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    t = at(14, 2)
    con.execute(INS, ("Mara", "Ok it's 2:02, workshop just started. Going now.", t))
    con.execute(INS, ("Theo", "Mentor is distracted. Pointers to pointers. Go go go.", t + 60))
    con.execute(INS, ("Mara", "Got the box. Meet at the east stairs.", t + 120))
    con.execute(
        "UPDATE messages SET body=? WHERE id=6",
        ("I'll ask the mentor a question about the workshop.",),
    )
    con.execute("DELETE FROM messages WHERE id=13")
    con.execute(INS, ("Theo", "On my way.", t + 180))
    seize(work, d)
    con.close()
    shutil.rmtree(work)
    note(
        d,
        """
THE DONUT HEIST (the main demo case)
Upload: messages.db AND messages.db-wal

Story: Mara and Theo plan to take the last box of donuts at a hackathon.
Halfway through, Theo turns on secure delete and wipes two messages.

What to show:
1. Conversation: "Got the box. Meet at the east stairs." is back, outlined
   red, recovered from a WAL frame. Bracket: deleted after 14:04, before 14:05.
2. Theo's message 6 was edited: "I'll distract the mentor..." became an
   innocent "I'll ask the mentor a question about the workshop."
3. "Maple. With sprinkles. It's a sign." was deleted before secure delete,
   and is rebuilt from a freeblock even though its first 4 bytes were overwritten.
4. Findings: CU1 secure-delete pattern, CU2 rowids 8 and 9 deleted with no
   trace. "Zeroed bytes are evidence too."
5. Click any message for the hex view.
""",
    )


def d2_edited_alibi() -> None:
    d = fresh(DEMO, "2_the_edited_alibi")
    work = d / "work"
    work.mkdir()
    con = wal_open(work / "messages.db")
    rows = [
        ("Priya", "Where were you last night? The server room door was propped open."),
        ("Jonah", "Home all night. Watched a movie."),
        ("Priya", "Which movie?"),
        ("Jonah", "The new space one. Fell asleep halfway."),
        ("Priya", "Badge log says someone swiped in at 11:48pm."),
        ("Jonah", "Wasn't me. Probably the cleaning crew."),
        ("Priya", "Cleaning crew doesn't have server room access."),
        ("Jonah", "Then I don't know. Can we talk tomorrow?"),
    ]
    seed_chat(con, rows, start=(9, 15), step_min=3)
    # Jonah rewrites his alibi three times, then deletes a slip
    con.execute("UPDATE messages SET body=? WHERE id=2", ("Home all night. Studying.",))
    con.execute(
        "UPDATE messages SET body=? WHERE id=4",
        ("Didn't watch anything, I was studying for the exam.",),
    )
    con.execute(INS, ("Jonah", "ok fine I went back for my charger, 5 minutes max", at(9, 40)))
    con.execute("DELETE FROM messages WHERE id=9")
    con.execute(
        "UPDATE messages SET body=? WHERE id=2", ("Home all night. Studying, then asleep by 11.",)
    )
    con.execute(INS, ("Priya", "Ok. Let's talk tomorrow.", at(9, 44)))
    seize(work, d)
    con.close()
    shutil.rmtree(work)
    note(
        d,
        """
THE EDITED ALIBI (best for the timeline slider)
Upload: messages.db AND messages.db-wal

Story: Priya asks Jonah where he was when the server room was opened.
Jonah keeps rewriting his alibi, then sends a confession and deletes it.

What to show:
1. Conversation: Jonah's first message shows "Edited". Expand it: three
   versions, "Watched a movie" -> "Studying" -> "Studying, then asleep by 11",
   each with the commit it was live in.
2. The deleted slip: "ok fine I went back for my charger, 5 minutes max",
   bracketed between 09:40 and 09:44.
3. Drag the slider back commit by commit and watch the story change.
""",
    )


def d3_clean_sweep() -> None:
    d = fresh(DEMO, "3_the_clean_sweep")
    con = sqlite3.connect(d / "messages.db")
    con.execute("PRAGMA journal_mode=DELETE")
    rows = [
        ("Sam", "Did you send the files?"),
        ("Rhea", "Sending now."),
        ("Sam", "Use the shared drive, not email."),
        ("Rhea", "Done. Folder is called 'quarterly'."),
        ("Sam", "Great. Delete the old copies."),
        ("Rhea", "Already did."),
        ("Sam", "And clear this chat."),
        ("Rhea", "On it."),
        ("Sam", "Thanks. See you Monday."),
        ("Rhea", "See you."),
    ]
    seed_chat(con, rows, start=(17, 30), step_min=1)
    con.commit()
    con.execute("DELETE FROM messages WHERE id IN (3, 4, 5, 7)")
    con.commit()
    con.execute("VACUUM")
    con.close()
    note(
        d,
        """
THE CLEAN SWEEP (absence of evidence is evidence)
Upload: messages.db

Story: Sam and Rhea delete the incriminating messages, then rebuild the
database with VACUUM so no residue survives.

What to show:
1. Nothing is recoverable: VACUUM rewrote every page.
2. Findings: CU2 says rowids 3, 4, 5 and 7 are missing with no residue;
   CU4 says the file is consistent with a VACUUM (gaps, yet no freeblocks,
   freelist pages or fragments anywhere).
3. Point: "We can't show what they said, but we can show that someone
   removed it and then scrubbed the file."
""",
    )


def d4_browser_trail() -> None:
    src = TEST / "10_browser_history_non_chat"
    d = fresh(DEMO, "4_the_browser_trail")
    shutil.copyfile(src / "History", d / "History")
    note(
        d,
        """
THE BROWSER TRAIL (it's not just chat apps)
Upload: History

Story: a Chrome-style browser history. Two searches were deleted from it.

What to show:
1. No Conversation tab (it's not a chat). Open Records, filter "deleted".
2. The two deleted searches come back: "how to delete chat history" and
   "does deleting messages really delete them".
3. Point: "Same engine, any SQLite file: browsers, phones, chat apps."
""",
    )


def main() -> None:
    os.umask(0o022)
    for f in (
        t01_rollback_basic,
        t02_wal_history,
        t03_wal_missing,
        t04_persist_journal,
        t05_secure_delete,
        t06_vacuum,
        t07_newest_removed,
        t08_auto_vacuum,
        t09_overflow,
        t10_browser_history,
        t11_not_sqlite,
        t12_corrupted,
        t13_utf16,
        t14_wal_old_generation,
        d1_donut_heist,
        d2_edited_alibi,
        d3_clean_sweep,
        d4_browser_trail,
    ):
        f()
        print("built", f.__name__)


if __name__ == "__main__":
    main()
