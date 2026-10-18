// Mirrors engine/cyber_arche/models.py and the API in docs/BLUEPRINT.md ("Data contracts", "API").
// Shared interface: change only with the whole team.

export type Status = "live" | "deleted" | "prior_version" | "stale_copy";
export type Source =
  | "btree_live"
  | "freeblock"
  | "unallocated"
  | "freelist_leaf"
  | "wal_frame"
  | "wal_old_generation"
  | "journal";
export type FileRole = "main" | "wal" | "journal" | "shm";

export interface Segment {
  start: number; // absolute offset within `file`
  length: number;
  kind: "damaged" | "payload_size" | "rowid" | "record_header" | "column";
  column: string | null;
  value: unknown; // decoded value (for hover)
}

export interface Provenance {
  source: Source;
  file: "main" | "wal" | "journal";
  page: number;
  page_offset: number;
  file_offset: number;
  length: number;
  wal_frame: number | null;
  commit: number | null;
  segments: Segment[];
}

export interface Value {
  column: string;
  type: "null" | "integer" | "real" | "text" | "blob";
  value: string | number | null;
  reconstructed: boolean;
}

export interface RecoveredRecord {
  id: string; // R-001
  table: string;
  rowid: number | null;
  rowid_inferred: boolean;
  status: Status;
  values: Value[];
  confidence: number; // 0..1
  confidence_reasons: string[];
  provenance: Provenance[]; // provenance[0] is the strongest location
  first_seen_commit: number | null;
  last_seen_commit: number | null;
  reconstructed: boolean;
}

export interface Commit {
  index: number; // 0 = main file alone (before any WAL commit)
  first_frame: number;
  last_frame: number;
  db_size_pages: number;
  approximate: boolean;
}

export interface RecordRef {
  record_id: string;
  values: Value[];
}

export interface Change {
  commit: number;
  table: string;
  kind: "insert" | "update" | "delete";
  rowid: number;
  before: RecordRef | null;
  after: RecordRef | null;
  bracket: { after_ts: number | null; before_ts: number | null } | null; // app timestamps (epoch s)
}

export interface Finding {
  id: string; // CU1..CU6 (or ENC)
  severity: "info" | "medium" | "high";
  title: string;
  detail: string;
  evidence: { file: string; file_offset: number; length: number }[];
}

export interface ManifestFile {
  role: FileRole;
  name: string;
  bytes: number;
  sha256: string;
}

export interface CaseManifest {
  case_id: string;
  created: string; // ISO-8601
  files: ManifestFile[];
  label: string;
}

export interface Column {
  name: string;
  decl_type: string;
  affinity: string;
  notnull: boolean;
  pk: number;
  is_rowid_alias: boolean;
}

export interface TableInfo {
  name: string;
  root: number;
  sql: string;
  without_rowid: boolean;
  columns: Column[];
}

export interface ChatMapping {
  table: string;
  sender: string | null;
  body: string;
  timestamp: string | null;
}

// GET /api/cases/{id}
export interface CaseDetail {
  manifest: CaseManifest;
  hashes_verified: boolean;
  encrypted: boolean;
  header: Record<string, number | string | boolean> | null;
  tables: TableInfo[];
  stats: {
    status_counts: Partial<Record<Status, number>>;
    source_counts: Partial<Record<Source, number>>;
    pages_main: number;
    pages_final: number;
    freelist_pages: number;
    checkpointed_through_frame: number;
  };
  wal: Record<string, unknown> | null;
  journal: Record<string, unknown> | null;
  commits: number; // number of WAL commits (snapshots are 0..commits)
  chat: ChatMapping | null; // auto-detected conversation columns
  errors: string[];
}

// GET /api/cases/{id}/snapshot/{k}
export interface SnapshotView {
  commit: number;
  approximate: boolean;
  tables: Record<string, string[]>; // table -> record ids live as of commit k
}

// GET /api/cases/{id}/hex?file=&offset=&length=   (length capped at 512)
export interface HexChunk {
  file: string;
  offset: number;
  length: number;
  hex: string; // lowercase, 2 chars per byte
  file_size: number;
}

// POST /api/cases/{id}/summary
export interface SummarySentence {
  text: string;
  citations: string[]; // record ids
}
export interface Summary {
  available: boolean;
  model: string | null;
  cached: boolean;
  sentences: SummarySentence[];
  dropped: number; // sentences removed for missing/invalid citations
  error: string | null;
}

// GET /api/config
export interface AppConfig {
  ai_available: boolean;
  model: string | null;
}

// GET /api/cases
export type CaseList = CaseManifest[];

// Demo chat app (/demo/*)
export interface DemoMessage {
  id: number;
  sender: string;
  body: string;
  sent_at: number;
}
