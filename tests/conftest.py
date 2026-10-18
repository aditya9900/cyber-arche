import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures" / "out"


@pytest.fixture(scope="session", autouse=True)
def built_fixtures() -> Path:
    subprocess.run(
        [sys.executable, str(ROOT / "fixtures" / "make_fixtures.py")],
        check=True,
        capture_output=True,
    )
    return OUT


def load(name: str):
    from cyber_arche.analyze import analyze

    d = OUT / name
    exp = json.loads((d / "expected.json").read_text())
    files = exp["files"]

    def rd(role: str):
        return (d / files[role]).read_bytes() if role in files else None

    return analyze(rd("main"), rd("wal"), rd("journal")), exp


def values(rec) -> list:
    return [v.value for v in rec.values]
