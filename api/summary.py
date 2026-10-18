"""F6: grounded AI summary where every sentence cites recovered record IDs.

Only a compact JSON list of records and timeline facts is sent; never raw hex.
Sentences with no citation, or a citation that does not exist, are dropped.
"""

import json
import os

from cyber_arche.analyze import CaseAnalysis

SYSTEM = """You are assisting a digital forensic examiner. You will receive JSON describing records \
recovered from a SQLite database (live, deleted, earlier versions of edited rows), the change \
timeline, and cover-up indicators. Write a short factual summary (3 to 7 sentences) of what the \
recovered data shows.

Rules:
- Use ONLY the provided records and facts. Do not speculate beyond them.
- Every sentence must cite at least one record id (like "R-003") or finding id (like "CU2") in its citations list, and only ids that appear in the input.
- Never state or guess identity, motive, intent or guilt. Describe what the data shows, not why.
- The WAL has no clock: never invent wall-clock times for when a change happened. You may use the bracket timestamps provided ("after the message sent at X, before the one sent at Y").
- Cover-up findings are indicators: say "consistent with", never "proves"."""

SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "citations": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "citations"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["sentences"],
    "additionalProperties": False,
}


def model_name() -> str:
    return os.environ.get("CYBER_ARCHE_MODEL", "").strip()


def ai_available() -> bool:
    """The summary panel shows only when both a key and a model are configured."""
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    return has_key and bool(model_name())


def build_input(a: CaseAnalysis, fmt_ts) -> dict:
    recs = []
    for r in a.records:
        if r.table.startswith("sqlite_"):
            continue
        if r.status == "stale_copy":
            continue
        vals = {}
        for v in r.values:
            val = v.value
            if isinstance(val, int) and "at" in v.column.lower():
                val = fmt_ts(val)
            vals[v.column] = val
        recs.append(
            {
                "id": r.id,
                "table": r.table,
                "rowid": r.rowid,
                "status": r.status,
                "values": vals,
                "confidence": r.confidence,
                "source": r.provenance[0].source if r.provenance else None,
                "first_seen_commit": r.first_seen_commit,
                "last_seen_commit": r.last_seen_commit,
            }
        )
    changes = []
    for c in a.changes:
        if c.table.startswith("sqlite_"):
            continue
        b = c.bracket or {}
        changes.append(
            {
                "commit": c.commit,
                "kind": c.kind,
                "table": c.table,
                "rowid": c.rowid,
                "before": c.before.record_id if c.before else None,
                "after": c.after.record_id if c.after else None,
                "after_message_sent_at": fmt_ts(b["after_ts"]) if b.get("after_ts") else None,
                "before_message_sent_at": fmt_ts(b["before_ts"]) if b.get("before_ts") else None,
            }
        )
    findings = [
        {"id": f.id, "severity": f.severity, "title": f.title, "detail": f.detail}
        for f in a.findings
    ]
    return {"records": recs, "changes": changes[-80:], "findings": findings}


def validate(sentences: list[dict], valid_ids: set[str]) -> tuple[list[dict], int]:
    kept = []
    dropped = 0
    for s in sentences:
        text = str(s.get("text", "")).strip()
        cites = [c for c in s.get("citations", []) if isinstance(c, str)]
        if not text or not cites or any(c not in valid_ids for c in cites):
            dropped += 1
            continue
        kept.append({"text": text, "citations": list(dict.fromkeys(cites))})
    return kept, dropped


def generate(a: CaseAnalysis, fmt_ts) -> dict:
    import anthropic

    payload = build_input(a, fmt_ts)
    client = anthropic.Anthropic()
    kwargs = dict(
        model=model_name(),
        max_tokens=4000,
        system=SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    try:
        resp = client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
        )
    except TypeError:  # older SDK without the fallbacks parameter
        resp = client.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        raise RuntimeError("the model declined to summarize this case")
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    data = json.loads(text)
    valid = {r.id for r in a.records} | {f.id for f in a.findings}
    kept, dropped = validate(data.get("sentences", []), valid)
    return {"sentences": kept, "dropped": dropped, "model": resp.model}
