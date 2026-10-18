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
