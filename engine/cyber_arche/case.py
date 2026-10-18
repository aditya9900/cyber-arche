"""Evidence intake: copy, SHA-256, read-only, manifest (BLUEPRINT "Forensic soundness rules")."""

import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

from .models import CaseManifest, ManifestFile

ROLE_SUFFIX = {"main": "", "wal": "-wal", "shm": "-shm", "journal": "-journal"}


def cases_dir() -> Path:
    d = Path(os.environ.get("CASES_DIR", Path(__file__).resolve().parents[2] / "cases"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def new_case_id() -> str:
    day = datetime.now(UTC).strftime("%Y%m%d")
    n = 1
    while (cases_dir() / f"c-{day}-{n:02d}").exists():
        n += 1
    return f"c-{day}-{n:02d}"


def create_case(files: dict[str, tuple[str, bytes]], label: str = "") -> CaseManifest:
    """files: role -> (original name, bytes). Writes cases/<id>/evidence/ read-only."""
    if "main" not in files:
        raise ValueError("a main database file is required")
    cid = new_case_id()
    ev = cases_dir() / cid / "evidence"
    ev.mkdir(parents=True)
    base = Path(files["main"][0]).name or "evidence.db"
    out: list[ManifestFile] = []
    for role in ("main", "wal", "shm", "journal"):
        if role not in files:
            continue
        _name, data = files[role]
        name = base + ROLE_SUFFIX[role]
        p = ev / name
        p.write_bytes(data)
        p.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        out.append(ManifestFile(role, name, len(data), sha256(data)))
    m = CaseManifest(cid, datetime.now(UTC).isoformat(timespec="seconds"), out, label)
    (cases_dir() / cid / "manifest.json").write_text(json.dumps(_manifest_dict(m), indent=2))
    return m


def _manifest_dict(m: CaseManifest) -> dict:
    return {
        "case_id": m.case_id,
        "created": m.created,
        "label": m.label,
        "files": [f.__dict__ for f in m.files],
    }


def load_manifest(case_id: str) -> CaseManifest:
    if "/" in case_id or ".." in case_id:
        raise FileNotFoundError(case_id)
    d = json.loads((cases_dir() / case_id / "manifest.json").read_text())
    return CaseManifest(
        d["case_id"], d["created"], [ManifestFile(**f) for f in d["files"]], d.get("label", "")
    )


def list_cases() -> list[CaseManifest]:
    out = []
    for p in cases_dir().glob("*/manifest.json"):
        try:
            out.append(load_manifest(p.parent.name))
        except (OSError, KeyError, json.JSONDecodeError):
            continue
    return sorted(out, key=lambda m: m.created, reverse=True)


def read_evidence(m: CaseManifest) -> dict[str, bytes]:
    ev = cases_dir() / m.case_id / "evidence"
    return {f.role: (ev / f.name).read_bytes() for f in m.files}


def verify_hashes(m: CaseManifest) -> list[str]:
    """Re-hash every evidence file; return a list of mismatches (empty = verified)."""
    bad = []
    ev = cases_dir() / m.case_id / "evidence"
    for f in m.files:
        try:
            h = sha256((ev / f.name).read_bytes())
        except OSError as e:
            bad.append(f"{f.name}: {e}")
            continue
        if h != f.sha256:
            bad.append(f"{f.name}: expected {f.sha256}, got {h}")
    return bad


def case_path(case_id: str) -> Path:
    return cases_dir() / case_id
