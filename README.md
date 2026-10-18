# Cyber Arche

> Delete a message. We'll show you what it said, when it changed, and the bytes that prove it.

Cyber Arche recovers deleted and edited records from SQLite databases, replays the write-ahead
log as a scrubbable commit-by-commit history, and proves every recovered record with byte-level
provenance. The parser is written from the SQLite file-format spec in pure Python; evidence is
never opened with the SQLite library. Full spec: [docs/BLUEPRINT.md](docs/BLUEPRINT.md).

**Live preview:** [cyber-arche.netlify.app](https://cyber-arche.netlify.app/) (demo mode with sample data;
run it locally for real analysis).

## Run it

```bash
./run.sh
```

Then open http://127.0.0.1:8000. The script creates `.venv`, installs dependencies, builds the
fixtures and the backup case, builds the web UI and starts the server. For the AI summary, copy
`.env.example` to `.env` and set `ANTHROPIC_API_KEY` and `CYBER_ARCHE_MODEL` (the panel hides without them).

Phone mode (judges use the demo chat on their own phones, same Wi-Fi):

```bash
HOST=0.0.0.0 ./run.sh
```

It prints the address to open, e.g. `http://192.168.1.20:8000/demo`.

Online preview: [cyber-arche.netlify.app](https://cyber-arche.netlify.app/), deployed to Netlify from this repo
(`netlify.toml`). That build runs in demo mode, in the browser on a built-in sample case; real
analysis needs the server above.

Demo flow: open **Demo chat**, send / edit / delete a message, click **Seize device**. The case
opens with the deleted message back in the conversation, the edit on the timeline, and the hex
view of the exact bytes. The **Backup case** (loaded automatically) shows secure_delete cover-up
findings.

## What it recovers

| Source | How |
|---|---|
| Live rows | Table b-tree walk (interior + leaf pages, overflow chains) |
| Freeblocks | Record scan after the 4-byte freeblock header, plus F7 reconstruction of the overwritten first 4 bytes from the schema |
| Unallocated space | Intact cells between the cell-pointer array and the content area |
| Freelist leaf pages | Parsed as ordinary pages |
| WAL | Salt + cumulative checksum validation, commit list, snapshot per commit, per-commit diffs, old-generation and uncommitted frames as lower-confidence sources |
| Rollback journal | Pre-change page images (including `journal_mode=PERSIST`) |

Every record carries a confidence score with reasons, and every location a segment map
(damaged bytes, payload size, rowid, record header, one range per column) for the hex viewer.
Cover-up indicators CU1–CU6 (secure_delete, rowid gaps with no residue, sqlite_sequence ahead of
max rowid, possible VACUUM, auto_vacuum, WAL resets) are worded "consistent with", never "proves".

## Develop

```bash
.venv/bin/pytest -q                      # engine + API tests against generated fixtures
.venv/bin/ruff format . && .venv/bin/ruff check .
.venv/bin/uvicorn api.main:app --reload --port 8000
cd web && npm run dev                    # Vite on :5173, proxies /api and /demo
cd web && VITE_USE_MOCKS=1 npm run dev   # UI against mock JSON
```

Layout: `engine/cyber_arche/` (parser, carver, WAL, snapshots, timeline, cover-up, F7),
`api/` (FastAPI + summary), `demo/` (the "suspect's" chat app, the only sqlite3 user),
`fixtures/` (fixture and backup-case builders), `web/` (React + Vite + Tailwind), `tests/`.

## Forensic rules

Evidence files are copied, SHA-256 hashed and set read-only on intake; hashes are re-verified on
report export. A carved copy of a live row is a `stale_copy`, never `deleted`. WAL commits are
never given wall-clock times; changes are bracketed between the app's own timestamps.
All data in this repo is synthetic.

## Development Notes

AI-assisted development tools, such as Claude Code, were used to support
implementation, debugging, and development.
