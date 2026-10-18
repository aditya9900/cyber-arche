import type { Change, Provenance, RecoveredRecord, Segment, Source, Status, Value } from "../contracts";

export const hex = (n: number, width = 4): string => "0x" + n.toString(16).toUpperCase().padStart(width, "0");

export const hexPlain = (n: number, width = 8): string => n.toString(16).toUpperCase().padStart(width, "0");

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KiB`;
  return `${(n / 1024 / 1024).toFixed(1)} MiB`;
}

export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Epoch seconds -> local HH:MM. */
export function formatHM(epoch: number | null | undefined): string {
  if (epoch === null || epoch === undefined || !Number.isFinite(epoch)) return "--:--";
  const d = new Date(epoch * 1000);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
}

export const SOURCE_LABEL: Record<Source, string> = {
  btree_live: "live b-tree",
  freeblock: "freeblock",
  unallocated: "unallocated",
  freelist_leaf: "freelist page",
  wal_frame: "WAL frame",
  wal_old_generation: "old WAL generation",
  journal: "rollback journal",
};

export const SOURCE_GLYPH: Record<Source, string> = {
  btree_live: "◆",
  freeblock: "◌",
  unallocated: "▢",
  freelist_leaf: "▤",
  wal_frame: "≋",
  wal_old_generation: "≈",
  journal: "↺",
};

export const STATUS_LABEL: Record<Status, string> = {
  live: "Live",
  deleted: "Deleted",
  prior_version: "Prior version",
  stale_copy: "Stale copy",
};

/** "Page 3, offset 0x0F2A, freeblock" or "WAL frame 12 (commit 7), page 2, offset 0x0E10, wal_frame". */
export function provenanceLabel(p: Provenance): string {
  const parts: string[] = [];
  if (p.wal_frame !== null) {
    parts.push(`WAL frame ${p.wal_frame}${p.commit !== null ? ` (commit ${p.commit})` : ""}`);
    parts.push(`page ${p.page}`);
  } else {
    parts.push(`${p.file === "journal" ? "Journal page" : "Page"} ${p.page}`);
  }
  parts.push(`offset ${hex(p.page_offset)}`);
  parts.push(p.source);
  return parts.join(", ");
}

export function valueOf(r: { values: Value[] }, column: string | null | undefined): Value | undefined {
  if (!column) return undefined;
  return r.values.find((v) => v.column === column);
}

export function rawValue(r: { values: Value[] }, column: string | null | undefined): string | number | null {
  return valueOf(r, column)?.value ?? null;
}

export function numValue(r: { values: Value[] }, column: string | null | undefined): number | null {
  const v = rawValue(r, column);
  if (typeof v === "number") return v;
  if (typeof v === "string" && v.trim() !== "" && !Number.isNaN(Number(v))) return Number(v);
  return null;
}

export function displayValue(v: Value | undefined): string {
  if (!v) return "";
  if (v.value === null) return "NULL";
  if (v.type === "blob") return `<blob ${String(v.value).slice(0, 24)}${String(v.value).length > 24 ? "…" : ""}>`;
  return String(v.value);
}

export function segmentValueText(s: Segment): string {
  if (s.value === undefined || s.value === null) return s.kind === "damaged" ? "overwritten bytes" : "";
  if (typeof s.value === "string") return JSON.stringify(s.value.length > 80 ? s.value.slice(0, 80) + "…" : s.value);
  try {
    return JSON.stringify(s.value);
  } catch {
    return String(s.value);
  }
}

export function pct(conf: number): string {
  return `${Math.round(conf * 100)}%`;
}

export function compareByTsRowid(tsCol: string | null) {
  return (a: RecoveredRecord, b: RecoveredRecord): number => {
    const ta = numValue(a, tsCol);
    const tb = numValue(b, tsCol);
    if (ta !== null && tb !== null && ta !== tb) return ta - tb;
    if (ta === null && tb !== null) return 1;
    if (tb === null && ta !== null) return -1;
    const ra = a.rowid ?? Number.MAX_SAFE_INTEGER;
    const rb = b.rowid ?? Number.MAX_SAFE_INTEGER;
    if (ra !== rb) return ra - rb;
    return a.id.localeCompare(b.id);
  };
}

/** Honest bracket for a change, using the app's own timestamps (never a commit wall-clock). */
export function bracketText(bracket: Change["bracket"], verb = "changed"): string {
  if (!bracket || (bracket.after_ts === null && bracket.before_ts === null)) {
    return `${verb}; no app timestamps available to bracket this commit`;
  }
  const { after_ts, before_ts } = bracket;
  if (after_ts !== null && before_ts !== null) {
    return `${verb} after the message sent at ${formatHM(after_ts)} and before the one sent at ${formatHM(before_ts)}`;
  }
  if (after_ts !== null) return `${verb} after the message sent at ${formatHM(after_ts)} (no later message to bound it)`;
  return `${verb} before the message sent at ${formatHM(before_ts)} (no earlier message to bound it)`;
}

export function shortSha(sha: string): string {
  return sha.slice(0, 12);
}

export function classNames(...xs: (string | false | null | undefined)[]): string {
  return xs.filter(Boolean).join(" ");
}
