# Sample evidence files

Synthetic SQLite databases to upload into Cyber Arche. Every person, message and URL is fictional.

- **`test/`** has 14 scenarios that exercise each recovery path and cover-up finding. Use them to check that the tool works.
- **`demo/`** has 4 story cases built for presenting to judges.

Each folder has an `ABOUT.txt` that says which files to upload and what you should see.

## How to upload

1. Start the app (`./run.sh`) and open http://localhost:8000.
2. Under **New case from files**:
   - **Database (.db):** the main file, e.g. `messages.db` (or `History`).
   - **WAL (-wal):** the `messages.db-wal` file, if the folder has one.
   - **Rollback journal:** the `messages.db-journal` file, if the folder has one.
   - **Label:** the folder name.
3. Click **Hash and analyze**. The case opens.

## Test files: what each should show

| Folder | Upload | Expected result |
|---|---|---|
| `01_rollback_freeblocks` | db | 12 live, 4 deleted. Rowid 16 from unallocated space; 5, 9, 10 rebuilt from freeblocks, rowids inferred. CU3 (info). |
| `02_wal_timeline` | db + wal | 14 commits. 8 live, 2 deleted, rowid 4's old text as a prior version. CU3 (info). |
| `03_wal_left_behind` | db only | Nothing. Every message was in the `-wal`, which wasn't uploaded. |
| `04_rollback_journal` | db + journal | 7 live, rowid 3 recovered from the journal. |
| `05_secure_delete` | db | 13 live, nothing recovered. CU1 + CU2 (rowids 8, 9, 12). |
| `06_vacuum` | db | 14 live, nothing recovered. CU2 (rowids 4, 11) + CU4 (possible VACUUM). |
| `07_newest_rows_removed` | db | 13 live. CU3 (16 issued, 13 live, none recovered) + CU2. |
| `08_auto_vacuum` | db | 15 live, rowid 6 rebuilt. CU5 (auto_vacuum on). |
| `09_long_messages_overflow` | db | A ~6 KB message read in full across overflow pages; rowid 2 recovered. |
| `10_browser_history_non_chat` | `History` | Not a chat: use **Records**. 2 deleted searches recovered. |
| `11_encrypted_or_not_sqlite` | `encrypted.db` | "No SQLite header" finding, no records, no crash. |
| `12_corrupted` | db | 60 bytes flipped. No crash; undamaged rows still recovered. |
| `13_utf16_encoding` | db | UTF-16 database. Deleted "Ça marche. Ünïcödé ✓ 🍩" decoded correctly. |
| `14_wal_reset_old_frames` | db + wal | CU6 (WAL history). Old-generation frames listed as a lower-confidence source. |

## Demo files: for presenting

| Folder | Upload | The story | Best for |
|---|---|---|---|
| `1_the_donut_heist` | db + wal | Two friends plot to take the last box of donuts; one turns on secure delete mid-way. | The main demo: WAL recovery, an edit, a rebuilt freeblock, CU1/CU2. |
| `2_the_edited_alibi` | db + wal | Jonah rewrites his alibi three times, then deletes a confession. | The timeline slider and "Edited ▸ 2 earlier" version history. |
| `3_the_clean_sweep` | db | Messages deleted, then the file VACUUMed so nothing survives. | "Absence of evidence is evidence": CU2 + CU4. |
| `4_the_browser_trail` | `History` | Two "how to delete chat history" searches removed from browser history. | Showing it works on any SQLite file, not just chats. |

Suggested order when presenting: the live demo chat first, then **1** if live fails, **2** for the timeline, **3** for cover-up detection, **4** to close on generality.

## Regenerate or check

```bash
.venv/bin/python samples/make_samples.py    # rebuild every folder
.venv/bin/python samples/check_samples.py   # print what the engine finds in each
```
