"""Run every sample folder through the engine and print what it finds."""

import sys
from pathlib import Path

from cyber_arche.analyze import analyze

ROOT = Path(__file__).parent


def roles(d: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for p in sorted(d.iterdir()):
        if p.name == "ABOUT.txt" or p.name.endswith("-shm"):
            continue
        if p.name.endswith("-wal"):
            out["wal"] = p.read_bytes()
        elif p.name.endswith("-journal"):
            out["journal"] = p.read_bytes()
        else:
            out["main"] = p.read_bytes()
    return out


def main() -> None:
    for group in ("test", "demo"):
        for d in sorted((ROOT / group).iterdir()):
            if not d.is_dir():
                continue
            f = roles(d)
            a = analyze(f["main"], f.get("wal"), f.get("journal"))
            user = [r for r in a.records if not r.table.startswith("sqlite_")]
            counts: dict[str, int] = {}
            for r in user:
                counts[r.status] = counts.get(r.status, 0) + 1
            gone = [r for r in user if r.status in ("deleted", "prior_version")]
            print(
                f"{group}/{d.name}: commits={len(a.commits) - 1} {counts} findings={[x.id for x in a.findings]}"
            )
            for r in gone:
                text = next(
                    (v.value for v in r.values if isinstance(v.value, str) and len(v.value) > 12),
                    r.values[-1].value,
                )
                src = r.provenance[0].source
                print(
                    f"    {r.status:13} rowid={r.rowid} conf={r.confidence:.2f} {src:18} {str(text)[:70]}"
                )


if __name__ == "__main__":
    sys.exit(main())
