"""FastAPI app: case routes + demo routes, and the built web UI (BLUEPRINT "API")."""

import html
import json
import logging
import re
import shutil
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from cyber_arche import case as cases
from cyber_arche.analyze import CaseAnalysis, analyze
from cyber_arche.models import to_json
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api import summary as ai
from demo.seed import PARTICIPANTS, DemoApp

ROOT = Path(__file__).resolve().parents[1]
WEB_DIST = ROOT / "web" / "dist"
BACKUP_DIR = ROOT / "fixtures" / "cases" / "backup"
HEX_CAP = 512

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cyber_arche.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        ensure_backup_case()
    except Exception:
        log.exception("could not load backup case")
    try:
        demo()
    except RuntimeError as e:
        log.error("%s", e)
        raise SystemExit(1) from e
    yield


app = FastAPI(title="Cyber Arche", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_cache: dict[str, CaseAnalysis] = {}
_lock = threading.Lock()
_demo: DemoApp | None = None


def demo() -> DemoApp:
    global _demo
    if _demo is None:
        _demo = DemoApp()
    return _demo


def get_case(case_id: str) -> tuple[cases.CaseManifest, CaseAnalysis]:
    try:
        m = cases.load_manifest(case_id)
    except (OSError, KeyError, ValueError) as e:
        raise HTTPException(404, f"case {case_id} not found") from e
    with _lock:
        a = _cache.get(case_id)
        if a is None:
            ev = cases.read_evidence(m)
            a = analyze(ev["main"], ev.get("wal"), ev.get("journal"))
            _cache[case_id] = a
    return m, a


def fmt_ts(ts: float | int) -> str:
    try:
        if ts > 10**11:
            ts = ts / 1000
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return str(ts)


SENDER = re.compile(r"^(sender|from|author|from_name|user|username|handle)$", re.I)
BODY = re.compile(r"^(body|text|message|msg|content)$", re.I)
TS = re.compile(r"(_at$|time|date|^ts$)", re.I)


def chat_mapping(a: CaseAnalysis) -> dict | None:
    for t in a.tables:
        if t.name.startswith("sqlite_"):
            continue
        names = t.column_names
        body = next((n for n in names if BODY.search(n)), None)
        if not body:
            continue
        return {
            "table": t.name,
            "sender": next((n for n in names if SENDER.search(n)), None),
            "body": body,
            "timestamp": next((n for n in names if TS.search(n)), None),
        }
    return None


# ------------------------------------------------------------------------- case routes


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/config")
def config() -> dict:
    return {
        "ai_available": ai.ai_available(),
        "model": ai.model_name() if ai.ai_available() else None,
    }


@app.get("/api/cases")
def list_cases() -> list:
    return to_json(cases.list_cases())


@app.post("/api/cases")
async def create_case(
    db: UploadFile = File(...),
    wal: UploadFile | None = File(None),
    journal: UploadFile | None = File(None),
    label: str = Form(""),
) -> dict:
    files = {"main": (db.filename or "evidence.db", await db.read())}
    if wal is not None and wal.filename:
        files["wal"] = (wal.filename, await wal.read())
    if journal is not None and journal.filename:
        files["journal"] = (journal.filename, await journal.read())
    m = cases.create_case(files, label or (db.filename or "Uploaded case"))
    return to_json(m)


@app.get("/api/cases/{case_id}")
def case_detail(case_id: str) -> dict:
    m, a = get_case(case_id)
    bad = cases.verify_hashes(m)
    return {
        "manifest": to_json(m),
        "hashes_verified": not bad,
        "hash_errors": bad,
        "encrypted": a.encrypted,
        "header": a.header.to_dict() if a.header else None,
        "tables": [t.to_dict() for t in a.tables],
        "stats": a.stats,
        "wal": a.wal.summary() if a.wal else None,
        "journal": a.journal.summary() if a.journal else None,
        "commits": max(0, len(a.commits) - 1),
        "chat": chat_mapping(a),
        "errors": a.errors[:50],
    }


@app.get("/api/cases/{case_id}/records")
def records(case_id: str, table: str | None = None, status: str | None = None) -> list:
    _m, a = get_case(case_id)
    out = [
        r
        for r in a.records
        if (not table or r.table == table) and (not status or r.status == status)
    ]
    return to_json(out)


@app.get("/api/cases/{case_id}/commits")
def commits(case_id: str) -> list:
    return to_json(get_case(case_id)[1].commits)


@app.get("/api/cases/{case_id}/snapshot/{k}")
def snapshot(case_id: str, k: int, table: str | None = None) -> dict:
    _m, a = get_case(case_id)
    if k not in a.snapshots:
        raise HTTPException(404, f"no commit {k}")
    tables = a.snapshots[k]
    if table:
        tables = {table: tables.get(table, [])}
    return {
        "commit": k,
        "approximate": a.commits[k].approximate if k < len(a.commits) else False,
        "tables": tables,
    }


@app.get("/api/cases/{case_id}/changes")
def changes(case_id: str, commit: int | None = None) -> list:
    _m, a = get_case(case_id)
    return to_json([c for c in a.changes if commit is None or c.commit == commit])


@app.get("/api/cases/{case_id}/hex")
def hexdump(case_id: str, file: str, offset: int, length: int = 256) -> dict:
    m, _a = get_case(case_id)
    f = next((x for x in m.files if x.role == file), None)
    if f is None:
        raise HTTPException(404, f"no {file} file in this case")
    length = max(0, min(length, HEX_CAP))
    offset = max(0, offset)
    p = cases.case_path(case_id) / "evidence" / f.name
    with open(p, "rb") as fh:  # read-only; evidence is never written
        fh.seek(offset)
        data = fh.read(length)
    return {
        "file": file,
        "offset": offset,
        "length": len(data),
        "hex": data.hex(),
        "file_size": f.bytes,
    }


@app.get("/api/cases/{case_id}/findings")
def findings(case_id: str) -> list:
    return to_json(get_case(case_id)[1].findings)


def _summary_path(case_id: str) -> Path:
    return cases.case_path(case_id) / "summary.json"


@app.post("/api/cases/{case_id}/summary")
def summary(case_id: str, refresh: bool = False) -> dict:
    _m, a = get_case(case_id)
    p = _summary_path(case_id)
    if p.exists() and not refresh:
        d = json.loads(p.read_text())
        valid = {r.id for r in a.records} | {f.id for f in a.findings}
        kept, dropped = ai.validate(d.get("sentences", []), valid)
        return {
            "available": True,
            "model": d.get("model"),
            "cached": True,
            "sentences": kept,
            "dropped": dropped + d.get("dropped", 0),
            "error": None,
        }
    if not ai.ai_available():
        return {
            "available": False,
            "model": None,
            "cached": False,
            "sentences": [],
            "dropped": 0,
            "error": "No ANTHROPIC_API_KEY configured",
        }
    try:
        res = ai.generate(a, fmt_ts)
    except Exception as e:  # surface API errors to the UI instead of a 500
        log.exception("summary failed")
        return {
            "available": True,
            "model": ai.model_name(),
            "cached": False,
            "sentences": [],
            "dropped": 0,
            "error": str(e),
        }
    p.write_text(json.dumps(res, indent=2, ensure_ascii=False))
    return {
        "available": True,
        "model": res["model"],
        "cached": False,
        "sentences": res["sentences"],
        "dropped": res["dropped"],
        "error": None,
    }


@app.get("/api/cases/{case_id}/report", response_class=HTMLResponse)
def report(case_id: str) -> HTMLResponse:
    m, a = get_case(case_id)
    bad = cases.verify_hashes(m)
    if bad:
        log.error("HASH MISMATCH for %s: %s", case_id, bad)
    e = html.escape
    rows = []
    for r in a.records:
        if r.table.startswith("sqlite_") or r.status == "live":
            continue
        p = r.provenance[0]
        where = (
            f"{p.file} @ {p.file_offset:#x} (page {p.page}, {p.source}"
            + (f", WAL frame {p.wal_frame}" if p.wal_frame else "")
            + ")"
        )
        vals = "; ".join(f"{v.column}={v.value}" for v in r.values)
        rows.append(
            f"<tr><td>{e(r.id)}</td><td>{e(r.status)}</td><td>{e(r.table)}</td><td>{e(str(r.rowid))}</td><td>{e(vals)}</td><td>{r.confidence:.2f}</td><td>{e(where)}</td></tr>"
        )
    ch = []
    for c in a.changes:
        if c.table.startswith("sqlite_"):
            continue
        b = c.bracket or {}
        br = ""
        if b.get("after_ts") or b.get("before_ts"):
            br = f"after msg at {fmt_ts(b['after_ts']) if b.get('after_ts') else '—'}, before msg at {fmt_ts(b['before_ts']) if b.get('before_ts') else '—'}"
        ch.append(
            f"<tr><td>{c.commit}</td><td>{e(c.kind)}</td><td>{e(c.table)}</td><td>{c.rowid}</td><td>{e(c.before.record_id if c.before else '')}</td><td>{e(c.after.record_id if c.after else '')}</td><td>{e(br)}</td></tr>"
        )
    fi = (
        "".join(
            f"<li><b>{e(f.id)} {e(f.title)}</b> ({e(f.severity)}): {e(f.detail)}</li>"
            for f in a.findings
        )
        or "<li>None</li>"
    )
    files = "".join(
        f"<tr><td>{e(f.role)}</td><td>{e(f.name)}</td><td>{f.bytes}</td><td><code>{e(f.sha256)}</code></td></tr>"
        for f in m.files
    )
    status = (
        "<p class=ok>All evidence hashes re-verified at export time.</p>"
        if not bad
        else "<p class=bad>HASH MISMATCH: " + e("; ".join(bad)) + "</p>"
    )
    sp = _summary_path(case_id)
    summ = ""
    if sp.exists():
        d = json.loads(sp.read_text())
        summ = (
            "<h2>AI-generated summary</h2><p><i>Verify against the cited records.</i></p><ul>"
            + "".join(
                f"<li>{e(s['text'])} [{e(', '.join(s['citations']))}]</li>"
                for s in d.get("sentences", [])
            )
            + "</ul>"
        )
    doc = f"""<!doctype html><html><head><meta charset=utf-8><title>Cyber Arche report {e(m.case_id)}</title>
<style>body{{font:14px/1.5 system-ui,sans-serif;max-width:1100px;margin:2em auto;padding:0 1em;color:#111}}
table{{border-collapse:collapse;width:100%;margin:1em 0}}td,th{{border:1px solid #ccc;padding:4px 6px;text-align:left;vertical-align:top;font-size:13px}}
code{{font-size:12px;word-break:break-all}}.ok{{color:#060;font-weight:600}}.bad{{color:#b00;font-weight:700;font-size:18px}}</style></head><body>
<h1>Cyber Arche case report: {e(m.case_id)}</h1><p>{e(m.label)} · created {e(m.created)} · exported {e(datetime.now().isoformat(timespec="seconds"))}</p>
{status}
<h2>Evidence</h2><table><tr><th>Role</th><th>File</th><th>Bytes</th><th>SHA-256</th></tr>{files}</table>
<h2>Summary</h2><p>{e(json.dumps(a.stats.get("status_counts", {})))}</p>
<h2>Recovered (non-live) records</h2><table><tr><th>ID</th><th>Status</th><th>Table</th><th>Rowid</th><th>Values</th><th>Conf.</th><th>Provenance</th></tr>{"".join(rows)}</table>
<h2>Timeline</h2><p>The WAL has no clock: changes are bracketed between the application's own timestamps.</p>
<table><tr><th>Commit</th><th>Kind</th><th>Table</th><th>Rowid</th><th>Before</th><th>After</th><th>Bracket</th></tr>{"".join(ch)}</table>
<h2>Cover-up indicators</h2><p>Indicators, not proof.</p><ul>{fi}</ul>{summ}
<h2>Machine-readable</h2><p><a href="/api/cases/{e(m.case_id)}/records">records JSON</a> · <a href="/api/cases/{e(m.case_id)}/changes">changes JSON</a> · <a href="/api/cases/{e(m.case_id)}/findings">findings JSON</a></p>
</body></html>"""
    return HTMLResponse(doc, status_code=200 if not bad else 409)


# ------------------------------------------------------------------------- demo routes


class NewMessage(BaseModel):
    sender: str
    body: str = Field(max_length=2000)


class EditMessage(BaseModel):
    body: str = Field(max_length=2000)


@app.get("/demo/state")
def demo_state() -> dict:
    return {"participants": PARTICIPANTS}


@app.get("/demo/messages")
def demo_messages() -> list:
    return demo().messages()


@app.post("/demo/messages")
def demo_send(msg: NewMessage) -> dict:
    try:
        return demo().send(msg.sender, msg.body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.patch("/demo/messages/{mid}")
def demo_edit(mid: int, msg: EditMessage) -> dict:
    try:
        demo().edit(mid, msg.body)
    except KeyError as e:
        raise HTTPException(404, "no such message") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@app.delete("/demo/messages/{mid}")
def demo_delete(mid: int) -> dict:
    try:
        demo().delete(mid)
    except KeyError as e:
        raise HTTPException(404, "no such message") from e
    return {"ok": True}


@app.post("/demo/seize")
def demo_seize() -> dict:
    files = demo().seize()
    label = "Seized device · " + datetime.now().strftime("%H:%M:%S")
    return to_json(cases.create_case(files, label))


@app.post("/demo/reset")
def demo_reset() -> dict:
    demo().reset()
    return {"ok": True}


# ------------------------------------------------------------------------- backup case + web UI


def ensure_backup_case() -> None:
    """Load the pre-built backup case once, so the demo survives a live failure."""
    if not (BACKUP_DIR / "messages.db").exists():
        return
    if any(m.label.startswith("Backup case") for m in cases.list_cases()):
        return
    files = {"main": ("messages.db", (BACKUP_DIR / "messages.db").read_bytes())}
    for role, suffix in (("wal", "-wal"), ("journal", "-journal")):
        p = BACKUP_DIR / f"messages.db{suffix}"
        if p.exists():
            files[role] = (p.name, p.read_bytes())
    m = cases.create_case(files, "Backup case · secure_delete switched on")
    cached = BACKUP_DIR / "summary.json"
    if cached.exists():
        shutil.copyfile(cached, _summary_path(m.case_id))
    log.info("backup case loaded as %s", m.case_id)


if (WEB_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith(("api/", "demo/")):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    f = (WEB_DIST / path).resolve()
    if path and f.is_file() and WEB_DIST.resolve() in f.parents:
        return FileResponse(f)
    index = WEB_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return HTMLResponse(
        "<h1>Cyber Arche API</h1><p>Web UI not built yet: cd web &amp;&amp; npm run build. API docs at <a href=/docs>/docs</a>.</p>"
    )
