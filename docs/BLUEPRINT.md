## Cyber Arche — SQLite Deleted-Record Recovery Blueprint

Oct 17, 2026 · @Gaurav

### Overview

Cyber Arche recovers deleted and edited messages from SQLite databases, replays the write-ahead log as a scrubbable history, and proves every recovered record with byte-level provenance.

One-line pitch: "Delete a message, and we'll show you what it said, when it changed, and the exact bytes on disk that prove it."

The problem. Nearly every chat app, browser and phone stores its data in SQLite: WhatsApp, iMessage, Chrome history, Android contacts. When a row is deleted, SQLite usually marks its space as free instead of erasing it, so the old bytes survive until something overwrites them. Recovery is still unreliable: in a 2019 DFRWS evaluation, the best tool (bring2lite) recovered only about 53% of deleted records, and that beat eight competing programs.

Why investigators care. Deleted messages are often the most important evidence on a seized device. An investigator needs the content, when it changed, and proof it was not fabricated. A basic carver delivers only the first, as rows in a table.

Prize fit. Cipher Tech builds production tools for two missions, digital forensics and malware reverse engineering, and its job listings ask for database forensics experience. Cyber Arche is a forensic tool built by reverse-engineering a binary file format from its specification, so it hits both missions.

Competition. Established tools include undark, FQLite, bring2lite and DC3's sqlite_dissect (DC3 is the DoD Cyber Crime Center). Judges may know these, so never pitch "we recover deleted rows" alone. Lead with what the table below adds.

| Capability | A basic carver | Cyber Arche |
|---|---|---|
| Deleted content | Rows dumped to a table or CSV | Messages put back into the conversation, in order, flagged as deleted |
| History | Latest state only | Commit-by-commit replay of the WAL with diffs: inserts, edits, deletes |
| Proof | Little or none | Page, byte offset, source structure, confidence score, and a hex view with the bytes highlighted |
| Cover-ups | Not reported | Indicators of secure_delete, VACUUM and rowid gaps with no residue |
| Damaged records | Usually skipped | Schema-guided reconstruction of overwritten record headers (stretch goal) |
| Summary | None | AI summary where every sentence cites recovered record IDs |

Demo story. A judge types a message into our fake chat app, deletes it, edits another, and clicks "Seize device." Seconds later Cyber Arche shows the deleted message back in the conversation, the edit as a before-and-after on a timeline, and the exact bytes on disk.

### Scope

The MVP must be integrated and demoable by hour 6; everything after that is optional and gets cut first. The recovery benchmark (twist #4) is excluded by decision.

Must-have (MVP, done by hour 6)

- ☐ Pure-Python parser for the database header, pages, cells, records and varints, including overflow pages for live records
- ☐ Schema read from sqlite_master, and live rows read by walking each table's b-tree
- ☐ Carver for deleted records in freeblocks, unallocated page space and freelist leaf pages
- ☐ De-duplication: a carved record identical to a live row is labeled a stale copy, never "deleted"
- ☐ WAL parser with salt and checksum validation, a commit list, per-commit snapshots, and per-commit diffs (insert, update, delete)
- ☐ Provenance and a confidence score on every recovered record
- ☐ Web UI: case overview, conversation view with recovered messages in place, timeline slider, hex viewer
- ☐ Demo chat app with a "Seize device" button that copies the database files and hashes them
- ☐ A pre-built backup case file, in case the live demo fails
Should-have (hours 6 to 9)

- ☐ Cover-up detector (secure_delete, VACUUM and auto_vacuum indicators, rowid gaps)
- ☐ Grounded AI summary with record-ID citations
- ☐ Exportable case report (HTML plus JSON) that includes the evidence hashes
- ☐ Rollback journal parsing for databases not in WAL mode
- ☐ Old-generation WAL frames (stale salts) surfaced as a separate source
Stretch (only if the MVP is solid at hour 8)

- ☐ Damaged-record reconstruction for records whose first 4 bytes were overwritten
- ☐ Partial recovery of deleted records that spilled onto overflow pages
- ☐ Schema adapters that render synthetic databases shaped like real apps (a WhatsApp-style message table, Chrome-style history)
Out of scope

- Recovery-rate benchmarking against other tools (dropped)
- Encrypted databases (SQLCipher): detect a missing "SQLite format 3" header and report it, nothing more
- Recovering deleted -wal or -journal files from the file system
- Index b-trees and WITHOUT ROWID tables (table b-trees only)
- Any real personal data: every database in this project is synthetic or typed by a consenting judge
### SQLite internals reference

Everything the parser needs is public: the database file format and the WAL design notes in wal.c. All multi-byte integers in the database and WAL files are big-endian; use struct.unpack('>H', ...) and '>I'.

#### Files on disk

| File | What it holds | Forensic value |
|---|---|---|
| X.db (any name) | The main database, as fixed-size pages | Live rows plus residue of deleted rows |
| X.db-wal | Write-ahead log: new versions of pages, appended per transaction | Older and newer versions of pages, including rows deleted since |
| X.db-shm | Shared-memory index into the WAL | Not needed; can be rebuilt from the WAL |
| X.db-journal | Rollback journal (non-WAL mode): original pages before a transaction | Pre-change copies of pages |

#### Varints

A varint is 1 to 9 bytes, big-endian. In the first 8 bytes, the high bit means "another byte follows" and the low 7 bits are data. A 9th byte contributes all 8 bits.

```
def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    """Return (value, number_of_bytes_consumed)."""
    value = 0
    for i in range(8):
        b = buf[pos + i]
        value = (value << 7) | (b & 0x7F)
        if b < 0x80:
            return value, i + 1
    return (value << 8) | buf[pos + 8], 9
```

#### Database header (first 100 bytes of the file)

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 16 | Magic | SQLite format 3\0; absent = encrypted or not SQLite |
| 16 | 2 | Page size | Value 1 means 65536 |
| 18 / 19 | 1 / 1 | Write / read format version | Both 2 = WAL mode; 1 = rollback journal |
| 20 | 1 | Reserved bytes per page | Usable size U = page size minus this |
| 24 | 4 | File change counter | Incremented on commits in rollback mode |
| 28 | 4 | Database size in pages |  |
| 32 | 4 | First freelist trunk page | 0 = no freelist |
| 36 | 4 | Total freelist pages |  |
| 52 | 4 | Largest root b-tree page | Non-zero = auto_vacuum is enabled |
| 56 | 4 | Text encoding | 1 UTF-8, 2 UTF-16le, 3 UTF-16be |
| 64 | 4 | Incremental vacuum flag | Non-zero = incremental mode |
| 92 | 4 | Version-valid-for number |  |
| 96 | 4 | SQLite version that last wrote the file |  |

#### Pages and b-trees

Page N starts at byte offset (N - 1) x page size; pages are numbered from 1. Page 1 begins with the 100-byte database header, so its b-tree header starts at offset 100. Every table is a b-tree whose root page number is in sqlite_master, and page 1 is the root of sqlite_master itself.

| Page-header offset | Size | Field |
|---|---|---|
| 0 | 1 | Page type: 0x0D table leaf, 0x05 table interior, 0x0A index leaf, 0x02 index interior |
| 1 | 2 | Offset of the first freeblock (0 = none) |
| 3 | 2 | Number of cells |
| 5 | 2 | Start of the cell content area (0 means 65536) |
| 7 | 1 | Fragmented free bytes (gaps under 4 bytes) |
| 8 | 4 | Right-most child pointer (interior pages only; their header is 12 bytes, leaves are 8) |

After the page header comes the cell pointer array: one 2-byte offset per cell. The gap between the end of that array and the start of the cell content area is unallocated space. Cells are packed from the end of the page toward the front.

Table interior cells (0x05) are a 4-byte left-child page number plus a varint rowid; walk them, plus the right-most pointer, to reach every leaf.

#### Table leaf cells and records

A table leaf cell (0x0D) is: varint payload size, varint rowid, the payload bytes stored on this page, then a 4-byte first-overflow-page number if the payload overflows.

The payload is a record: a varint header size (counting itself), one varint serial type per column, then the column values in order.

| Serial type | Meaning | Body bytes |
|---|---|---|
| 0 | NULL | 0 |
| 1 to 6 | Signed big-endian integer | 1, 2, 3, 4, 6, 8 |
| 7 | IEEE 754 64-bit float | 8 |
| 8 / 9 | Integer constant 0 / 1 | 0 |
| 10, 11 | Reserved | (treat as invalid) |
| N >= 12, even | BLOB | (N - 12) / 2 |
| N >= 13, odd | TEXT in the database encoding | (N - 13) / 2 |

A column declared INTEGER PRIMARY KEY is an alias for the rowid. It is stored as serial type 0 (NULL) in the record; take its value from the cell's rowid.

To get column names and types without writing a SQL parser, execute the CREATE TABLE text from sqlite_master against an in-memory database (sqlite3.connect(':memory:')) and run PRAGMA table_info. This never touches the evidence file.

#### Overflow pages

Let U be the usable page size and P the payload size. For table leaves, X = U - 35. If P <= X, the whole payload is on the page. Otherwise M = ((U - 12) x 32 / 255) - 23 and K = M + ((P - M) mod (U - 4)), with integer division. The page stores K bytes if K <= X, otherwise M bytes, and the rest continues on overflow pages. Each overflow page holds a 4-byte next-page number (0 = last) and then U - 4 bytes of payload.

Keep demo messages short, well under one page (about 4,000 bytes at the default 4,096-byte page size), so deleted messages never spill onto overflow pages.

#### What deletion leaves behind

- Freeblocks. A deleted cell's space joins the page's freeblock chain, sorted by offset. A freeblock starts with a 2-byte offset of the next freeblock (0 = last) and a 2-byte size that includes this 4-byte header. Everything after those 4 bytes is the old record, intact.
- The 4-byte overwrite. That freeblock header overwrites the first 4 bytes of the old cell: the payload-size varint, the rowid varint, and usually the record-header-size varint and the first serial type or two. This is why naive carvers miss records, and it is the target of the stretch goal.
- Merging. Adjacent freeblocks are merged into one, so a single freeblock can hold several deleted records back to back. Each one still has its own damaged first 4 bytes.
- Unallocated space. If the deleted cell sits at the very start of the cell content area, SQLite moves the content-start pointer instead of creating a freeblock. The bytes become unallocated space but stay on disk until reused. Stale 2-byte cell pointers can also linger just past the end of the cell pointer array.
- Freelist pages. When a page empties (or a table is dropped), it goes on the freelist. Trunk pages hold a 4-byte next-trunk number, a 4-byte leaf count, then 4-byte leaf page numbers. Freelist leaf pages are usually not rewritten, so a former table leaf still carries its old 0x0D header and cells; parse it like a normal page.
- Destroyers. Page defragmentation (when SQLite repacks a page to make room) can overwrite residue, and so can any new insert that reuses the space.
#### Settings that change what survives

| Setting | Effect on residue | Where to see it |
|---|---|---|
| PRAGMA secure_delete=ON | Deleted content is overwritten with zeros, including freelist pages | Zero-filled freeblocks and freelist pages |
| PRAGMA secure_delete=FAST | Zeroes in-page deleted content; freelist pages are not overwritten | Zeroed freeblocks, intact freelist pages |
| auto_vacuum=FULL | Freelist pages are removed and the file is truncated at each commit | Header offset 52 non-zero, freelist count 0 |
| VACUUM | Rebuilds the whole file; all residue is gone | No freeblocks, no freelist, tightly packed pages |
| journal_mode=WAL | Old page versions live on in the -wal file until it is reset | Header bytes 18 and 19 both equal 2 |

Secure delete is off in a default SQLite build, but an app or platform can turn it on.

#### WAL format

The WAL starts with a 32-byte header, then frames. Each frame is a 24-byte frame header plus one full page.

| WAL header offset | Field |
|---|---|
| 0 | Magic: 0x377f0682 (checksum words little-endian) or 0x377f0683 (big-endian) |
| 4 | Format version, 3007000 |
| 8 | Page size |
| 12 | Checkpoint sequence number |
| 16 / 20 | Salt-1 / Salt-2 |
| 24 / 28 | Checksum-1 / Checksum-2 over the first 24 header bytes |

| Frame header offset | Field |
|---|---|
| 0 | Database page number this frame replaces |
| 4 | For a commit frame, the database size in pages after the commit; 0 otherwise |
| 8 / 12 | Salt-1 / Salt-2 copied from the WAL header |
| 16 / 20 | Cumulative checksum-1 / checksum-2 |

A frame is valid only if its page number is non-zero, its salts match the WAL header, and its checksum matches. The checksum is cumulative: it runs over the WAL header, then each frame's first 8 header bytes and its page data, carried forward from frame to frame. Values are always stored big-endian; the magic's low bit only picks how the input words are read.

```
def wal_checksum(data: bytes, s0: int, s1: int, big_endian: bool) -> tuple[int, int]:
    fmt = '>' if big_endian else '<'
    words = struct.unpack(f'{fmt}{len(data) // 4}I', data)
    for i in range(0, len(words), 2):
        s0 = (s0 + words[i] + s1) & 0xFFFFFFFF
        s1 = (s1 + words[i + 1] + s0) & 0xFFFFFFFF
    return s0, s1

# header: s = wal_checksum(hdr[:24], 0, 0, be)
# each frame: s = wal_checksum(frame_hdr[:8], *s, be); s = wal_checksum(page, *s, be)
```

SQLite's own recovery stops at the first invalid frame. Frames after the last commit frame were never committed. After a checkpoint resets the WAL, salt-1 is incremented and salt-2 randomized, and new frames overwrite the file from the start. Frames past the new end that carry the old salts are leftovers from a previous generation. SQLite ignores them; Cyber Arche surfaces them as a separate, lower-confidence source.

Snapshot rule. The database as of commit k = the main file's pages, with each page replaced by its latest valid frame at or before commit k. Caveat: if a checkpoint already copied later frames into the main file, early snapshots are approximate. Label them so in the UI.

Gotchas that will bite the demo.

- When the last connection closes, SQLite checkpoints and deletes the -wal file. Copy the files while the app is still running.
- Opening evidence with the sqlite3 library can checkpoint, rewrite or create files. Never do it; parse raw bytes only.
- In the demo app, set PRAGMA wal_autocheckpoint=0 so every transaction stays in the WAL.
#### Rollback journal (should-have)

In non-WAL mode, the -journal file holds original copies of pages before a transaction changes them. It starts with the 8-byte magic d9 d5 05 f9 20 a1 63 d7, then a page count, a checksum nonce, the original database size, the sector size and the page size. Each record is a 4-byte page number, the page data and a 4-byte checksum. With journal_mode=PERSIST, only the journal header is zeroed after commit, so the old pages often remain.

### Feature specs

Seven features ship in priority order: the recovery engine and WAL time machine carry the demo, and everything else layers on top. Each spec ends with a "Done when" line that doubles as its test.

#### F1. Recovery engine (must-have)

- Read the evidence file into memory as bytes. Parse the header: page size, usable size, encoding, journal mode, freelist head and count.
- Walk sqlite_master from page 1 to get every table's name, root page and CREATE TABLE text. Derive columns with the in-memory PRAGMA table_info trick.
- Walk each table's b-tree to collect live rows and build a map of which leaf pages belong to which table.
- On every table leaf page (owned pages, freelist leaf pages, and every page image inside the WAL), carve three regions:
- Freeblocks: follow the chain from page-header offset 1. Guard against loops with a visited set and against offsets past the page end.
- Unallocated space: the gap between the cell pointer array and the cell content area.
- Freelist leaf pages: parse them as ordinary pages, live cells included, since their content was never cleared.
- Inside each region, scan for record candidates and keep only those that pass the validator below.
- De-duplicate: group candidates by (table, rowid, values). A candidate identical to a live row is a stale copy, not a deletion. One logical record can appear in many places; keep all its locations as a provenance list.
Candidate validator (shared by the carver and the time machine):

- The number of serial types equals the table's column count.
- No serial type is 10 or 11, and the header size matches the bytes the serial types used.
- The body length (from serial types) fits inside the region.
- TEXT decodes cleanly in the database encoding, and most characters are printable.
- Serial types fit each column's declared affinity. SQLite allows any type in any column, so treat this as a score, not a hard rule.
- If the payload-size varint is intact, it equals header size plus body size.
A cell deleted from the top of the cell content area becomes unallocated space with its header intact, so it parses cleanly. A cell deleted from the middle becomes a freeblock with its first 4 bytes destroyed; the MVP recovers it only when the record header starts past byte 4 (larger payloads or rowids). The WAL time machine catches most of the rest, and F7 attacks it directly.

Done when: a fixture with 20 inserted and 5 deleted rows returns all 15 live rows, recovers every deleted row whose bytes survive, and labels nothing "deleted" that is still live.

#### F2. WAL time machine (must-have, headline feature)

- Parse and verify the WAL header checksum. Read the magic's low bit to choose word order.
- Walk frames in order with the running checksum. Mark frames valid up to the first failure. Record each commit frame (non-zero size field) as commit k with its last frame number and database size.
- Past the valid range, collect frames whose salts differ from the header as old-generation frames. Their checksum chain cannot be fully verified, so tag them "unverified".
- Build snapshot k: start from the main file, then overlay, for each page, the latest valid frame at or before commit k. Pages past the end of the main file exist only in the WAL; a fresh WAL-mode database may hold little more than page 1 in the main file.
- Parse every snapshot's tables into a map keyed by (table, rowid).
- Diff each snapshot against the previous one: inserts, updates (same rowid, new values) and deletes.
- Run the F1 carver over every WAL page image too, because old frames carry their own freeblock residue.
The WAL has no clock. Commits are ordered, not timestamped, so never print a wall-clock time for a commit. Instead, bracket each change using the app's own timestamp column: "deleted after the message sent at 14:02 and before the one sent at 14:05." Investigators value this a lot, and it is honest.

Done when: in the live demo, an insert, an edit and a delete each appear as a separate commit, and scrubbing to the commit before the delete shows the message as live.

#### F3. Provenance and hex view (must-have)

Every recovered record carries: source kind, file (main, wal or journal), page number, offset within the page, absolute file offset, length, WAL frame and commit (if any), confidence and the reasons behind it. See the data contract in Architecture.

The engine also emits a segment map for each record: byte ranges labeled as damaged bytes, payload size, rowid, record header, and one range per column. The hex viewer shows 64 bytes of context on each side. It colors each segment, shows the decoded value on hover, and labels the source, for example "Page 3, offset 0x0F2A, freeblock" or "WAL frame 12 (commit 7)".

Done when: clicking any recovered message highlights exactly the bytes it was decoded from, and the decoded column values match what the hex shows.

#### F4. Cover-up detector (should-have)

Findings are indicators, never proof. Every finding is worded "consistent with" and cites the bytes or counts behind it.

| ID | Finding | Signal | Severity |
|---|---|---|---|
| CU1 | Secure-delete pattern | Freeblocks whose bytes after the 4-byte header are all zero; zero-filled freelist pages | High |
| CU2 | Deletions with no residue | Rowids missing between the table's min and max with no recovered record from any source; list the exact rowids | High |
| CU3 | Newest rows removed | sqlite_sequence.seq greater than the table's max rowid (AUTOINCREMENT tables) | Medium |
| CU4 | Possible VACUUM | Rowid gaps, but zero freelist pages, zero freeblocks and zero fragmented bytes across all pages | Medium |
| CU5 | auto_vacuum enabled | Header offset 52 non-zero | Info |
| CU6 | WAL history | Old-generation frames present, or a non-zero checkpoint sequence | Info |

Fresh pages are zero-filled, so zeros in unallocated space are normal. Only zeroed freeblocks and freelist pages count toward CU1.

Done when: the backup case file (which switches secure_delete on partway through) triggers CU1 and CU2, and the clean live-demo database triggers neither.

#### F5. Live judge demo and conversation view (must-have)

Demo chat app. A two-person messenger between fictional characters, where the judge can send, edit and delete messages. Schema:

CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sender TEXT NOT NULL,
  body TEXT NOT NULL,
  sent_at INTEGER NOT NULL  -- Unix epoch seconds
);

- Open one long-lived connection with journal_mode=WAL, wal_autocheckpoint=0 and secure_delete=OFF. Commit after every action so each one is its own WAL commit.
- Seed about 10 messages of a lighthearted fictional story, such as two characters plotting to take the last box of donuts from the hackathon.
- Seize device copies the .db, -wal and -shm files byte for byte into cases/<case_id>/evidence/ while the app is still running. It then hashes them with SHA-256, marks them read-only, writes a manifest and opens the analyzer.
- Reset closes the connection, deletes the files and reseeds for the next judge.
Conversation view.

- Auto-detect which columns hold the sender, body and timestamp by name (sender/from/author, body/text/message, *_at/time/date). Fall back to a column-mapping dropdown.
- Merge live and recovered rows, ordered by timestamp, then rowid.
- Deleted messages get a red outline, a "Deleted" badge, a confidence pill and a source icon. Edited messages show the current text with an "Edited" badge that expands to the earlier versions, struck through, each with its commit number.
- A timeline slider above the view sets the commit. The view then shows that snapshot, with badges for what the commit inserted, edited or deleted.
- A toggle switches between "as of this commit" and "everything recovered." Non-chat tables fall back to a plain table view with the same badges.
Done when: a judge's freshly deleted message reappears, in place, within 10 seconds of clicking Seize device.

#### F6. Grounded AI summary (should-have)

- Give each recovered or changed record a short ID (R-001, R-002...). Send the model a compact JSON list of those records and the timeline facts, never raw hex.
- Instruct the model to use only the provided records, cite record IDs after every sentence, and never state identity, motive or guilt. It returns JSON: {"sentences":[{"text":"...","citations":["R-003"]}]}.
- Validate before display: drop any sentence with no citation or a citation that does not exist.
- Render citations as chips that open the hex view, under a label: "AI-generated summary. Verify against the cited records."
- Default to the Anthropic API with the model set by the CYBER_ARCHE_MODEL environment variable. With no API key, hide the panel.
This sends recovered text to an outside service. That is fine for synthetic demo data, but say in the pitch that real casework would need the provider approved or a local model.

Done when: every displayed sentence carries at least one citation, and each citation opens a real record.

#### F7. Damaged-record reconstruction (stretch)

The goal is to recover freeblock records whose first 4 bytes were overwritten.

- Inputs: the freeblock bytes (offset o, size s), the candidate schemas (the owning table, or every table for freelist pages) and the usable page size.
- Enumerate hypotheses about the original layout: payload-size varint length a (1 to 3 bytes), rowid varint length b (1 to 4) and header-size varint length c (1 or 2). Serial types begin at a + b + c. Any serial types inside the first 4 bytes are lost.
- Fill each lost serial type from the column's affinity. An INTEGER PRIMARY KEY alias is always 0; an integer column tries 0 to 6, 8 and 9. For one lost TEXT or BLOB length, solve it from the remaining space: the cell must end at the freeblock's end or exactly where the next record begins.
- Score each hypothesis: clean UTF-8, printable ratio, affinity fit, exact fit and plausible values (epoch timestamps between 2000 and 2035 in *_at or time columns).
- Keep the best hypothesis above a threshold. Mark the record as reconstructed, its rowid as unknown, the first 4 bytes as damaged, and its confidence between 0.4 and 0.7.
- Repeat for each record packed inside a merged freeblock, since each one has its own damaged first 4 bytes.
Optionally infer a rowid range from neighbors' timestamps, shown as "between 6 and 9 (inferred)."

Done when: deleting a middle message (not the newest one) from a fixture with the WAL removed still recovers its text.

### Architecture

One Python engine parses raw bytes and never imports sqlite3 for evidence; a FastAPI server exposes it as JSON; a React app renders it. The JSON contracts below are fixed in hour 1, so all four people can build against them in parallel.

System architecture · 6 components, one-way evidence flow

Evidence only moves forward: the demo app is the one place sqlite3 touches a database, and everything after the case store reads read-only copies as raw bytes.

#### Stack

| Layer | Choice | Why |
|---|---|---|
| Engine | Python 3.11+, standard library only (struct, hashlib, dataclasses) | Writing the parser from the spec is the point; no third-party SQLite parsers |
| API | FastAPI, uvicorn, pydantic v2 | Typed contracts, automatic docs at /docs for the frontend |
| Web | React 18, Vite, TypeScript, Tailwind | Fast to build; mock data from the contracts on day one |
| Demo app | Routes in the same FastAPI app plus a page in the web app; uses sqlite3 | It plays the suspect's app, so using the library here is correct |
| AI | anthropic Python SDK, model from an environment variable | Optional; panel hides without a key |
| Tests | pytest, with fixture databases generated by a script | Ground truth for every feature |

#### Repository layout

```
cyber-arche/
  AGENTS.md              # agent instructions
  README.md
  engine/cyber_arche/
    varint.py            # read_varint
    header.py            # 100-byte database header
    page.py              # page header, cell pointers, freeblocks, unallocated range
    record.py            # record header, serial types, value decoding, segment map
    btree.py             # table b-tree walk, overflow chains, page->table map
    schema.py            # sqlite_master + PRAGMA table_info trick
    carver.py            # candidate scan + validator + de-duplication
    wal.py               # WAL header, frames, checksums, commits, old generations
    snapshot.py          # PageSource overlay and snapshot k
    timeline.py          # per-commit diffs and time bracketing
    coverup.py           # CU1-CU6 findings
    reconstruct.py       # F7 stretch
    case.py              # evidence intake, hashing, manifest
    models.py            # dataclasses mirroring the JSON contracts
  api/main.py            # FastAPI app: case routes + demo routes
  api/summary.py         # F6 LLM summary with citation validation
  web/                   # React app
  demo/seed.py           # seeds the demo chat database
  fixtures/make_fixtures.py   # builds test databases with known deletions
  fixtures/cases/        # the pre-built backup case
  tests/                 # pytest
  cases/                 # runtime case folders (gitignored)
```

Route every page read through one PageSource interface (main file, WAL frame N, snapshot k). The carver and b-tree walker then work identically on the main file, on any WAL frame, and on any snapshot.

#### Data contracts

```
{
  "RecoveredRecord": {
    "id": "R-001",
    "table": "messages",
    "rowid": 7,
    "rowid_inferred": false,
    "status": "live | deleted | prior_version | stale_copy",
    "values": [{"column": "body", "type": "text", "value": "meet at 9", "reconstructed": false}],
    "confidence": 0.85,
    "confidence_reasons": ["column count matches schema", "valid UTF-8"],
    "provenance": ["Provenance"],
    "first_seen_commit": 3,
    "last_seen_commit": 5
  },
  "Provenance": {
    "source": "btree_live | freeblock | unallocated | freelist_leaf | wal_frame | wal_old_generation | journal",
    "file": "main | wal | journal",
    "page": 3,
    "page_offset": 3882,
    "file_offset": 12074,
    "length": 41,
    "wal_frame": 12,
    "commit": 7,
    "segments": [{"start": 12074, "length": 4, "kind": "damaged | payload_size | rowid | record_header | column", "column": null}]
  },
  "Commit": {"index": 7, "first_frame": 11, "last_frame": 12, "db_size_pages": 4, "approximate": false},
  "Change": {"commit": 7, "table": "messages", "kind": "insert | update | delete", "rowid": 7, "before": "RecordRef | null", "after": "RecordRef | null", "bracket": {"after_ts": 1760709720, "before_ts": 1760709900}},
  "Finding": {"id": "CU2", "severity": "info | medium | high", "title": "Deletions with no residue", "detail": "Rowids 8, 9 are missing and no copy was recovered.", "evidence": [{"file": "main", "file_offset": 4096, "length": 4096}]},
  "CaseManifest": {"case_id": "c-20261017-01", "created": "ISO-8601", "files": [{"role": "main | wal | shm | journal", "name": "messages.db", "bytes": 16384, "sha256": "..."}]}
}
```

#### API

| Method and path | Returns |
|---|---|
| POST /api/cases (multipart: db, optional wal, journal) | CaseManifest |
| GET /api/cases/{id} | Header fields, schema, counts per source, manifest |
| GET /api/cases/{id}/records?table=&status= | RecoveredRecord[] |
| GET /api/cases/{id}/commits | Commit[] |
| GET /api/cases/{id}/snapshot/{k}?table= | Rows as of commit k |
| GET /api/cases/{id}/changes?commit= | Change[] |
| GET /api/cases/{id}/hex?file=&offset=&length= | Bytes as hex, capped at 512 per request |
| GET /api/cases/{id}/findings | Finding[] |
| POST /api/cases/{id}/summary | Validated summary sentences with citations |
| GET /api/cases/{id}/report | Exportable HTML report with hashes re-verified |
| POST /demo/messages, PATCH /demo/messages/{id}, DELETE /demo/messages/{id} | Demo chat actions, one commit each |
| POST /demo/seize / POST /demo/reset | Copy, hash and open a case / reseed |

#### Confidence scoring

Start from the source's base score, then add or subtract for the validator checks; clamp to 0 to 1 and store a reason for every adjustment.

| Source | Base |
|---|---|
| Live b-tree cell | 1.00 |
| Valid, committed WAL frame | 0.95 |
| Freelist leaf page with a valid page header | 0.85 |
| Freeblock, header intact | 0.80 |
| Unallocated space | 0.70 |
| Uncommitted or old-generation WAL frame | 0.60 |
| Reconstructed (F7) | 0.40 to 0.70 |

Adjustments: +0.05 for an exact fit to the region's end; -0.15 for an affinity mismatch; -0.25 for unprintable or invalid text; -0.10 if the payload-size varint disagrees.

### Project guidelines

The forensic rules are what expert judges will probe, so they are non-negotiable; the engineering rules keep four people and their AI agents from colliding.

#### Forensic soundness rules

- Evidence is read-only. On intake, copy every file, hash it with SHA-256, set it read-only and record it in the manifest. Re-hash when exporting a report; a mismatch is a loud error.
- Never open evidence with sqlite3 or any tool that might write. The engine reads bytes only.
- Every claim has provenance. No record, finding or summary sentence appears without the bytes behind it.
- "Deleted" means absent from the current live state. A copy of a live row is a stale copy, never a deletion.
- Confidence is always visible, with the reasons behind it.
- Indicators, not verdicts. Cover-up findings say "consistent with", never "proves".
- No wall-clock times for WAL commits. Bracket changes between the app's own timestamps instead.
- Synthetic data only. Every database is generated by our scripts or typed by a consenting judge.
#### Engineering conventions

- Python: 3.11+, type hints everywhere, dataclasses for records. Write small pure functions that take bytes plus an offset; no global state.
- Tolerate damage. Corrupt input never crashes the engine. Raise ParseError(file_offset, reason) internally, then log it and keep going.
- Bounds and loops. Check bounds before every read. Every chain (freeblocks, freelist trunks, overflow pages, b-tree children) gets a visited set and an iteration cap.
- Quality gates: ruff format, ruff check and pytest -q must pass. Every engine module has tests against generated fixtures; unit tests may use small byte literals.
- Frontend: TypeScript strict mode. Mirror the contracts in web/src/contracts.ts. Serve mock JSON from web/src/mocks/ when VITE_USE_MOCKS=1, until the API is ready.
- Config: use a .env file (ANTHROPIC_API_KEY, CYBER_ARCHE_MODEL, CASES_DIR). Commit .env.example, never .env.
- Scale: target databases up to about 50 MB; cache parsed pages per PageSource.
#### Git workflow

- main always runs. Each person works on one branch: engine-core, engine-wal, api-demo or web.
- Prefix commit messages by area (engine:, wal:, api:, web:, demo:, docs:). Commit small and often.
- Merge to main at the hour-3, hour-6 and hour-9 checkpoints, only after tests pass and the web app builds.
- models.py and contracts.ts are the shared interface. Changing either requires telling all four people first.
- Code freeze at hour 11: bug fixes and README only.
#### Definition of done (every task)

- ☐ pytest -q and ruff check pass, and npm run build succeeds
- ☐ Works on the backup case file and on a fresh live-demo seizure
- ☐ Anything new on screen shows its provenance
- ☐ README updated if a command or setup step changed
### Team plan and 12-hour schedule

Four roles, one branch each, merging at gates at hours 3, 6 and 9; the MVP must work end to end at hour 6, and code freezes at hour 11.

| Role | Branch | Owns | Stretch if ahead |
|---|---|---|---|
| P1 Format engineer | engine-core | varint, header, page, record, btree, schema, carver, segment maps | F7 damaged-record reconstruction |
| P2 WAL engineer | engine-wal | wal, snapshot, timeline, coverup, journal parsing | Old-generation frames |
| P3 Backend and demo | api-demo | Case intake and hashing, FastAPI routes, demo chat app, seize and reset, fixtures, backup case, AI summary, report export | Schema adapters |
| P4 Frontend and pitch | web | All React views, the hex viewer, the demo script, the Devpost page, the backup video | Visual polish |

Pick P1 and P2 from whoever is most comfortable reading byte layouts; P4 should be the strongest presenter.

12-hour build plan · 4 lanes, 5 gates

Each lane is one person's branch; merges happen only at the dashed gates, and nothing new starts after hour 9.

#### Gate checklists

Hour 1: skeleton

- ☐ Repo, AGENTS.md, contracts and .env.example committed
- ☐ make_fixtures.py produces at least three fixture databases
- ☐ Web app renders mock records end to end
Hour 3: real data flowing

- ☐ Live rows from a real fixture reach the UI through the API
- ☐ WAL commits listed for the demo database
- ☐ Demo chat app sends, edits and deletes
Hour 6: MVP (must-haves frozen after this)

- ☐ Seize device works, and deleted messages reappear in the conversation view
- ☐ Timeline slider scrubs commits and shows diffs
- ☐ Hex viewer highlights the source bytes
- ☐ Backup case file loads
- ☐ Full demo run-through number 1
Hour 9: should-haves in

- ☐ Cover-up findings fire on the backup case
- ☐ AI summary with validated citations, or the panel is cut
- ☐ Demo run-through number 2, and a recorded backup video
Hour 11: code freeze

- ☐ README, Devpost text and screenshots done
- ☐ Three timed pitch rehearsals before submitting
Check the Technica schedule for the exact submission deadline and judging slot, and work backward from those times rather than the clock hours above.

### Demo, judge Q&A and risks

The demo is a 3-minute live story with a judge as the suspect, backed by a pre-built case file and a recorded video if anything breaks.

#### Demo script (3 minutes)

| Time | On screen | Say |
|---|---|---|
| 0:00 | Title slide | "The best published tool recovers about half of deleted SQLite records, and hands investigators a table with no proof of where it came from." |
| 0:20 | Demo chat app | Ask a judge to send a message, edit an earlier one, then delete their new message. Click Seize device. |
| 0:50 | Conversation view | The deleted message is back, in place, flagged red. The edit shows its old text struck through. |
| 1:15 | Timeline slider | Scrub to the commit before the delete: the message is live. Point out the time bracket: "deleted after 14:02, before 14:05." |
| 1:40 | Hex viewer | Click the recovered message: "WAL frame 12, commit 7, these exact bytes." Show the SHA-256 hashes in the case header. |
| 2:05 | Backup case, findings tab | "Here the suspect turned on secure delete." Show CU1 and CU2: rowids 8 and 9 deleted with no trace. |
| 2:25 | AI summary | One cited sentence; click a citation chip to land on its bytes. |
| 2:40 | Closing slide | "A from-spec parser, nothing opened with SQLite itself, every claim traceable to bytes." Name one next step. |

If the live demo stalls for more than 10 seconds, switch to the backup case without comment and continue the same script.

#### Judge Q&A prep

| Likely question | Answer |
|---|---|
| How do you know a recovered record isn't a false positive? | Each one passes a schema validator, shows its confidence and reasons, and links to its exact bytes. Copies of live rows are labeled stale, not deleted. |
| Did your tool change the evidence? | No. We never open evidence with the SQLite library; we hash every file on intake and re-verify the hashes when exporting a report. |
| What if the suspect used secure_delete or VACUUM? | Zeroed bytes can't be recovered, but we detect the pattern and the rowid gaps it leaves, and that is evidence in itself. |
| How is this different from FQLite, bring2lite or sqlite_dissect? | Those recover rows. We add commit-by-commit history with diffs, byte-level provenance in the interface, and cover-up indicators. |
| When exactly was the message deleted? | The WAL has no clock, so we never invent one. We bracket each change between the app's own timestamps. |
| What about WAL checkpoints? | Snapshots before a checkpoint are marked approximate, and frames from earlier WAL generations appear as a separate, lower-confidence source. |
| Why write your own parser instead of using sqlite3? | The library only shows live rows and can modify the files. Parsing raw pages is the only way to reach freeblocks, unallocated space and old WAL frames. |
| Encrypted databases? | We detect them (no SQLite header) and say so; decryption is out of scope. |
| Does it work on real apps? | The format is the same for every app; schema adapters are the next step. We tested only on synthetic data for privacy. |
| Can the AI summary hallucinate? | It sees only recovered records, every sentence must cite record IDs, and we drop any sentence whose citations don't check out. |

#### Risks and fallbacks

| Risk | Fallback |
|---|---|
| The demo app's WAL gets checkpointed or deleted | Long-lived connection, wal_autocheckpoint=0, and seize while the app is running; test at every gate |
| A new insert overwrites the deleted message's space | The WAL still holds the earlier page version; ask the judge to delete and then seize without sending more |
| A judge types emoji or a very long message | Cap demo messages at 500 characters; include emoji in a fixture |
| Venue Wi-Fi fails | Everything runs locally; AI is optional; the backup case has a cached summary |
| A laptop dies | Second laptop with the repo cloned and the backup case ready |
| F7 or the AI summary isn't finished | Cut it from the demo and name it as the next step |
| Late merge conflicts | Gates at hours 3, 6 and 9; contracts change only with all four people informed |

### Resume and Devpost framing

The resume line that lands is the from-spec parser plus provenance; claim only what each person actually built, and add the award line if you place.

#### Resume bullets (pick two or three)

- Co-built Cyber Arche, a forensic tool that recovers deleted and edited records from SQLite databases and write-ahead logs with byte-level provenance for every artifact (Python, FastAPI, React); Technica 2026.
- Implemented a parser for the SQLite file format from its specification: b-tree pages, record serial types, freelists, and WAL frames with checksum validation, without using the SQLite library on evidence.
- Reconstructed commit-by-commit database history from WAL frames, surfacing edits and deletions with time windows bracketed by application timestamps.
- Designed anti-forensics heuristics that flag secure-delete and VACUUM activity and deletions that left no residue.
- Built a schema-guided method that reconstructs deleted records whose headers were overwritten (only if F7 ships).
#### Devpost page

| Section | What to write |
|---|---|
| Tagline | "Delete a message. We'll show you what it said, when it changed, and the bytes that prove it." |
| Inspiration | Deleted chats are key evidence; the best published tool recovers about half, with little proof attached. |
| What it does | Recovers deleted and edited records, replays the WAL as a timeline, proves each record in a hex view, flags cover-ups. |
| How we built it | From-spec parser in pure Python, FastAPI, React; the architecture diagram; the forensic rules. |
| Challenges | The 4-byte freeblock overwrite, WAL checkpoint behavior, keeping evidence untouched. |
| Accomplishments | The live judge demo; zero evidence modification, verified by hashes. |
| What we learned | How SQLite actually deletes data, and why provenance matters more than recall. |
| What's next | Schema adapters for real app layouts, deleted overflow chains, a command-line mode for batch casework. |

#### After the hackathon

- Keep the repo public with screenshots, a 60-second demo GIF, the architecture diagram and a one-command setup.
- Write a short post explaining how SQLite deletion leaves data behind; that post is what recruiters and forensics engineers will read and share.
- Mention Cipher Tech's prize, if you win it, when applying there; they hire for exactly this work.
### AI coding agent instructions

Write the agent instructions once in AGENTS.md, put this blueprint in the repo, then give each person's coding agent the kickoff prompts for their role.

#### Setup (5 minutes, one person)

- Export this doc as Markdown and save it in the repo as docs/BLUEPRINT.md, so agents can read the full spec.
- Save the block below as AGENTS.md at the repo root.
- If your coding agent reads a different instructions file name, point that file at AGENTS.md so every tool shares one source of truth.
- Confirm once that each agent has loaded the instructions.
#### AGENTS.md (copy as-is)

```
# Cyber Arche - agent instructions

## Mission
Forensic tool that recovers deleted and edited records from SQLite databases
(main file, -wal, -journal) with byte-level provenance for every record.
Built by 4 people in 12 hours at Technica 2026. Full spec: docs/BLUEPRINT.md.
Before any task, read the BLUEPRINT section named in the prompt.

## Hard rules (never break)
1. Never open evidence with sqlite3 or any library that can write. The engine
   parses raw bytes only. sqlite3 is allowed ONLY in demo/, fixtures/, tests that
   build databases, and schema.py's ':memory:' PRAGMA table_info trick.
2. Files under cases/*/evidence/ are read-only. Never write, move or re-save them.
3. Every RecoveredRecord carries Provenance (source, file, page, offsets, length,
   WAL frame/commit, segments). No provenance, no output.
4. A record identical to a live row is status "stale_copy", never "deleted".
5. Cover-up findings say "consistent with", never "proves".
6. Never invent wall-clock times for WAL commits; bracket with app timestamps.
7. Synthetic data only. No real personal data anywhere.
8. Do not edit engine/cyber_arche/models.py or web/src/contracts.ts without
   asking the human first. They are the shared interface.
9. Stay inside your role's paths (see Roles). Ask before touching others.

## Stack and commands
- Engine: Python 3.11+, standard library only. API: FastAPI, uvicorn, pydantic v2.
- Web: React 18, Vite, TypeScript (strict), Tailwind.
- Setup: python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
         cd web && npm install
- Fixtures: python fixtures/make_fixtures.py   (writes fixtures/out/)
- Tests: pytest -q        Lint: ruff format . && ruff check .
- API: uvicorn api.main:app --reload --port 8000
- Web: cd web && npm run dev   (mock data: VITE_USE_MOCKS=1 npm run dev)
- Demo: python demo/seed.py

## Roles
- P1 engine-core: engine/cyber_arche/{varint,header,page,record,btree,schema,carver,reconstruct}.py
- P2 engine-wal:  engine/cyber_arche/{wal,snapshot,timeline,coverup}.py
- P3 api-demo:    api/, demo/, fixtures/, engine/cyber_arche/case.py
- P4 web:         web/

## Conventions
- Big-endian everywhere, except WAL checksum input words when magic is 0x377f0682.
- Pure functions over (bytes, offset). Bounds-check every read. Every chain
  (freeblocks, freelist, overflow, b-tree) gets a visited set and an iteration cap.
- Corrupt input never crashes: raise ParseError(file_offset, reason), log, continue.
- All page reads go through one PageSource interface (main file, WAL frame, snapshot k).
- Type hints and dataclasses; docstrings cite the BLUEPRINT section implemented.
- Tests first: extend fixtures/make_fixtures.py, write a failing test, implement.
- Commit after each green run, prefixed engine:, wal:, api:, web:, demo: or docs:.

## Format facts (details in BLUEPRINT "SQLite internals reference")
- Page N starts at (N-1)*page_size; page 1's b-tree header starts at offset 100.
- Table leaf 0x0D (8-byte header); table interior 0x05 (12-byte header).
- Freeblock = [next u16][size u16 incl. header]; it overwrites the first 4 bytes
  of the deleted cell.
- Record = varint header size, varint serial types, body. INTEGER PRIMARY KEY is
  stored as NULL; use the cell's rowid.
- WAL = 32-byte header + frames (24-byte header + page). Valid frame: salts match,
  pgno > 0, cumulative checksum matches. Commit frame: non-zero db size field.
- Demo app: journal_mode=WAL, wal_autocheckpoint=0, one long-lived connection;
  seize files while it is still open.

## Definition of done
- pytest -q and ruff check pass; npm run build succeeds.
- Works on fixtures/cases/backup and on a fresh demo seizure.
- Anything new on screen shows provenance. README updated if commands changed.
- End every task with: what changed, test output, what's next.
```

#### Standing suffix for every prompt

Append this to every prompt you give an agent:

Work in small steps. Write or extend tests first, then implement. Run pytest -q (and npm run build for web work) and show me the output. Do not modify the contracts. Finish with a summary of what changed and the next step, then commit with the area prefix.

#### Hour-0 bootstrap prompt (one person runs it once)

Read AGENTS.md and docs/BLUEPRINT.md, sections "Architecture" and "Project guidelines". Create the repository skeleton exactly as in "Repository layout": a pyproject.toml that packages cyber_arche from engine/ with a dev extra (pytest, ruff, fastapi, uvicorn, pydantic, python-multipart, anthropic); a .gitignore (cases/, .env, .venv, node_modules, fixtures/out/); .env.example; stub modules with docstrings; engine/cyber_arche/models.py with dataclasses mirroring "Data contracts"; and api/main.py with a /api/health route. In web/, create a Vite + React + TypeScript + Tailwind app with web/src/contracts.ts mirroring the contracts and realistic mock JSON in web/src/mocks/.

Then write fixtures/make_fixtures.py that builds three databases in fixtures/out/, each with an expected.json listing live rows and deleted rows with their values:

- Rollback-journal mode, a messages table, 20 inserts and 5 deletes (the newest row plus middle rows).
- WAL mode with wal_autocheckpoint=0: inserts, one update and two deletes, with the .db and -wal copied while the connection is still open.
- secure_delete=ON with several deletions.
Add pytest smoke tests and run them. Stop and show me the tree and the test output.

#### P1 (format engineer) prompts, in order

- Read BLUEPRINT "SQLite internals reference". Implement varint.py, header.py, page.py and record.py: header fields, page headers, cell pointer arrays, loop-guarded freeblock chains, the unallocated range, every serial type, and a segment map per record. Test against fixtures/out/.
- Implement btree.py and schema.py: walk table b-trees (0x05 and 0x0D) with overflow chains, build the page-to-table map, read sqlite_master, and derive columns with the in-memory PRAGMA table_info trick. Return live rows as RecoveredRecord with status live and source btree_live. Check against expected.json.
- Implement carver.py per BLUEPRINT F1: candidate scan in freeblocks, unallocated space and freelist leaf pages; the validator; confidence scoring from "Confidence scoring"; de-duplication with stale_copy. Accept any PageSource so it also runs on WAL page images. Test: every recoverable deleted row in fixture 1 is found, and no live row is labeled deleted.
- Stretch, only if the hour-6 gate passed: implement reconstruct.py per F7, with a new fixture that deletes a middle row and removes the WAL.
#### P2 (WAL engineer) prompts, in order

- Read BLUEPRINT "WAL format". Implement wal.py: header parsing and checksum verification for both byte orders, frame iteration with the cumulative checksum, the valid range, the commit list, and old-generation frames tagged unverified. Test on fixture 2 and on a copy with one corrupted byte.
- Implement snapshot.py (a PageSource overlay for commit k, with the approximate flag) and timeline.py (per-commit diffs keyed by table and rowid, plus time bracketing from timestamp columns). Reuse P1's b-tree and schema functions through PageSource. Test that commits and diffs match expected.json.
- Implement coverup.py findings CU1 to CU6 per F4, with "consistent with" wording and evidence offsets. Test: fixture 3 fires CU1 and CU2; fixture 2 fires neither.
#### P3 (backend and demo) prompts, in order

- Implement case.py intake (copy, SHA-256, read-only, manifest) and every route in BLUEPRINT "API". Return engine output where it exists and mock data elsewhere. Enable CORS for the Vite dev server.
- Build the demo backend per F5: demo/seed.py and the /demo routes with one long-lived connection (journal_mode=WAL, wal_autocheckpoint=0, secure_delete=OFF), one commit per action, a 500-character cap, /demo/seize copying .db, -wal and -shm while the connection is open, and /demo/reset. Test: seizing after a delete yields a WAL whose earlier frame still contains the deleted text.
- Build fixtures/cases/backup: a scripted storyline database that switches secure_delete on partway through, with one edit and several deletes. Cache an AI summary JSON for offline use.
- Implement api/summary.py per F6 (citation validation, CYBER_ARCHE_MODEL environment variable) and the /report HTML export that re-verifies hashes.
#### P4 (frontend and pitch) prompts, in order

- Read BLUEPRINT F3 and F5. Using mocks, build the case header (files and SHA-256), the conversation view with deleted and edited rendering, the plain-table fallback, and a hex viewer that colors segments and shows decoded values on hover.
- Add the timeline slider (commits, snapshot and changes per commit), the "as of commit" versus "everything recovered" toggle, a findings tab, and a summary tab whose citation chips open the hex viewer. Switch to the live API with VITE_USE_MOCKS=0.
- Build the /demo chat page with send, edit and delete, plus Seize device and Reset buttons; after seizing, open the new case.
#### Working with the agents

- One agent session per person, on that person's branch, so agents never edit the same files.
- For any task bigger than one module, ask the agent for a plan first, approve it, then let it implement.
- Read every diff. Byte-offset bugs are subtle, so insist on tests against real fixtures, not invented bytes.
- If an agent spends more than 15 minutes on one bug, stop it, have it write a minimal failing test, and cut scope if needed.
- Paste error output and file paths into prompts; name the BLUEPRINT section every time.
