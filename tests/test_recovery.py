"""BLUEPRINT F1/F2/F4/F7 "Done when" checks against generated fixtures."""

from tests.conftest import load, values


def by_status(a, status, table="messages"):
    return [r for r in a.records if r.table == table and r.status == status]


def test_f1_live_and_deleted():
    a, exp = load("f1_rollback")
    live = by_status(a, "live")
    assert sorted(values(r) for r in live) == sorted(exp["live"])
    deleted = {tuple(values(r)[1:]) for r in by_status(a, "deleted")}
    for row in exp["deleted"]:
        assert tuple(row[1:]) in deleted, row
    live_vals = {tuple(r) for r in exp["live"]}
    for r in by_status(a, "deleted"):
        assert tuple(values(r)) not in live_vals


def test_every_record_has_provenance():
    for name in ("f1_rollback", "f2_wal", "f4_middle", "f5_journal"):
        a, _ = load(name)
        for r in a.records:
            assert r.provenance, r
            for p in r.provenance:
                assert p.segments and p.length > 0
                cols = [s for s in p.segments if s.kind == "column"]
                assert cols


def test_f2_wal_time_machine():
    a, exp = load("f2_wal")
    assert len(a.commits) - 1 >= exp["commits_min"]
    kinds = [(c.kind, c.rowid) for c in a.changes if c.table == "messages"]
    assert ("update", 4) in kinds and ("delete", 10) in kinds and ("delete", 6) in kinds
    # each a separate commit
    commits = {c.commit for c in a.changes if c.table == "messages" and c.kind != "insert"}
    assert len(commits) == 3
    # scrubbing to the commit before the delete shows the message as live
    d = next(c for c in a.changes if c.kind == "delete" and c.rowid == 10)
    ids = a.snapshots[d.commit - 1]["messages"]
    assert d.before.record_id in ids
    assert d.before.record_id not in a.snapshots[d.commit].get("messages", [])
    assert d.bracket and d.bracket["after_ts"]
    prior = by_status(a, "prior_version")
    assert [values(r) for r in prior] == [exp["updated"][0]["before"]]


def test_stale_copies_never_deleted():
    a, exp = load("f2_wal")
    live = {tuple(r) for r in exp["live"]}
    for r in a.records:
        if r.table == "messages" and r.status == "deleted":
            assert tuple(values(r)) not in live


def test_f4_secure_delete_findings():
    a, _ = load("f3_secure")
    ids = {f.id for f in a.findings}
    assert {"CU1", "CU2"} <= ids
    for f in a.findings:
        assert "proves" not in f.detail.lower()
    clean, _ = load("f2_wal")
    assert not {"CU1", "CU2"} & {f.id for f in clean.findings}


def test_f7_middle_delete_without_wal():
    a, exp = load("f4_middle")
    rec = by_status(a, "deleted")
    assert len(rec) == 1
    assert values(rec[0])[1:] == exp["deleted"][0][1:]
    assert rec[0].reconstructed and 0.4 <= rec[0].confidence <= 0.7
    assert any(s.kind == "damaged" for s in rec[0].provenance[0].segments)


def test_journal_recovery():
    a, exp = load("f5_journal")
    rec = by_status(a, "deleted")
    assert [values(r)[1:] for r in rec] == [exp["deleted"][0][1:]]
    assert any(p.source == "journal" for p in rec[0].provenance)


def test_segments_match_bytes():
    """Hex view contract: each column segment decodes to the shown value."""
    from tests.conftest import OUT

    a, _ = load("f2_wal")
    wal = (OUT / "f2_wal" / "messages.db-wal").read_bytes()
    for r in by_status(a, "deleted"):
        for p in r.provenance:
            if p.file != "wal":
                continue
            for s in p.segments:
                if s.kind == "column" and isinstance(s.value, str) and s.column == "body":
                    assert wal[s.start : s.start + s.length].decode() == s.value


def test_corrupt_input_never_crashes():
    import random

    from cyber_arche.analyze import analyze

    from tests.conftest import OUT

    data = bytearray((OUT / "f1_rollback" / "messages.db").read_bytes())
    rnd = random.Random(7)
    for _ in range(30):
        b = bytearray(data)
        for _ in range(50):
            b[rnd.randrange(100, len(b))] = rnd.randrange(256)
        analyze(bytes(b))
    analyze(b"not a database at all" * 10)
