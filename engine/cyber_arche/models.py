"""Dataclasses mirroring the JSON contracts (BLUEPRINT "Data contracts").

Shared interface with web/src/contracts.ts: change only with the whole team.
"""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Segment:
    start: int  # absolute offset in `file`
    length: int
    kind: str  # damaged | payload_size | rowid | record_header | column
    column: str | None = None
    value: Any = None  # decoded value, for hover in the hex viewer


@dataclass
class Provenance:
    source: str  # btree_live | freeblock | unallocated | freelist_leaf | wal_frame | wal_old_generation | journal
    file: str  # main | wal | journal
    page: int
    page_offset: int
    file_offset: int
    length: int
    wal_frame: int | None = None
    commit: int | None = None
    segments: list[Segment] = field(default_factory=list)


@dataclass
class Value:
    column: str
    type: str  # null | integer | real | text | blob
    value: Any
    reconstructed: bool = False


@dataclass
class RecoveredRecord:
    id: str
    table: str
    rowid: int | None
    rowid_inferred: bool
    status: str  # live | deleted | prior_version | stale_copy
    values: list[Value]
    confidence: float
    confidence_reasons: list[str]
    provenance: list[Provenance]
    first_seen_commit: int | None = None
    last_seen_commit: int | None = None
    reconstructed: bool = False


@dataclass
class Commit:
    index: int
    first_frame: int
    last_frame: int
    db_size_pages: int
    approximate: bool = False


@dataclass
class RecordRef:
    record_id: str
    values: list[Value]


@dataclass
class Change:
    commit: int
    table: str
    kind: str  # insert | update | delete
    rowid: int
    before: RecordRef | None
    after: RecordRef | None
    bracket: dict | None = None  # {"after_ts": int|None, "before_ts": int|None}


@dataclass
class EvidenceRef:
    file: str
    file_offset: int
    length: int


@dataclass
class Finding:
    id: str
    severity: str  # info | medium | high
    title: str
    detail: str
    evidence: list[EvidenceRef] = field(default_factory=list)


@dataclass
class ManifestFile:
    role: str  # main | wal | shm | journal
    name: str
    bytes: int
    sha256: str


@dataclass
class CaseManifest:
    case_id: str
    created: str
    files: list[ManifestFile]
    label: str = ""


def to_json(obj: Any) -> Any:
    """Dataclass (or list of them) to plain JSON-able structures."""
    if isinstance(obj, list):
        return [to_json(o) for o in obj]
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    return obj
