"""API + live demo flow (BLUEPRINT F5 "Done when", F3, hash verification)."""

import os
import stat
import sys
import tempfile
import time

import pytest


@pytest.fixture(scope="module")
def client():
    tmp = tempfile.mkdtemp()
    os.environ["CASES_DIR"] = os.path.join(tmp, "cases")
    os.environ["DEMO_DIR"] = os.path.join(tmp, "demo")
    for mod in [m for m in sys.modules if m.startswith(("api", "demo"))]:
        del sys.modules[mod]
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app) as c:
        yield c


def test_seize_recovers_fresh_delete_and_edit(client):
    msgs = client.get("/demo/messages").json()
    assert len(msgs) >= 5
    new = client.post(
        "/demo/messages", json={"sender": "Mara", "body": "secret: the box is in locker 12"}
    ).json()
    client.patch(f"/demo/messages/{msgs[1]['id']}", json={"body": "edited text"})
    client.delete(f"/demo/messages/{new['id']}")
    t0 = time.time()
    m = client.post("/demo/seize").json()
    cid = m["case_id"]
    roles = {f["role"] for f in m["files"]}
    assert {"main", "wal"} <= roles
    detail = client.get(f"/api/cases/{cid}").json()
    assert time.time() - t0 < 10
    assert detail["hashes_verified"] and detail["chat"]["table"] == "messages"
    recs = client.get(f"/api/cases/{cid}/records", params={"table": "messages"}).json()
    deleted = [r for r in recs if r["status"] == "deleted"]
    assert any(
        any(v["value"] == "secret: the box is in locker 12" for v in r["values"]) for r in deleted
    )
    prior = [r for r in recs if r["status"] == "prior_version" and r["rowid"] == msgs[1]["id"]]
    assert prior and any(v["value"] == msgs[1]["body"] for v in prior[0]["values"])
    # evidence is read-only
    from cyber_arche import case as cases

    ev = cases.case_path(cid) / "evidence"
    for p in ev.iterdir():
        assert not (p.stat().st_mode & stat.S_IWUSR)
    # hex view returns the exact bytes of a column segment
    r = deleted[0]
    p = r["provenance"][0]
    seg = next(s for s in p["segments"] if s["column"] == "body")
    hx = client.get(
        f"/api/cases/{cid}/hex",
        params={"file": p["file"], "offset": seg["start"], "length": seg["length"]},
    ).json()
    assert bytes.fromhex(hx["hex"]).decode() == seg["value"]
    # timeline: the commit before the delete has the message live
    changes = client.get(f"/api/cases/{cid}/changes").json()
    dl = next(c for c in changes if c["kind"] == "delete" and c["table"] == "messages")
    snap = client.get(f"/api/cases/{cid}/snapshot/{dl['commit'] - 1}").json()
    assert dl["before"]["record_id"] in snap["tables"]["messages"]
    rep = client.get(f"/api/cases/{cid}/report")
    assert rep.status_code == 200 and "re-verified" in rep.text
    client.post("/demo/reset")
    assert all(x["body"] != "edited text" for x in client.get("/demo/messages").json())


def test_backup_case_loaded(client):
    cs = client.get("/api/cases").json()
    b = next(c for c in cs if c["label"].startswith("Backup case"))
    ids = {f["id"] for f in client.get(f"/api/cases/{b['case_id']}/findings").json()}
    assert {"CU1", "CU2"} <= ids


def test_hex_cap_and_upload(client):
    from tests.conftest import OUT

    with open(OUT / "f1_rollback" / "messages.db", "rb") as f:
        m = client.post("/api/cases", files={"db": ("chat.db", f)}, data={"label": "upload"}).json()
    hx = client.get(
        f"/api/cases/{m['case_id']}/hex", params={"file": "main", "offset": 0, "length": 5000}
    ).json()
    assert hx["length"] == 512
    assert client.get("/api/cases/nope-123").status_code == 404
    assert client.get("/api/cases/..%2F..%2Fetc").status_code == 404
