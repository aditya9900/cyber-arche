// Mock case builder. It simulates a WAL-mode SQLite "messages" database byte by byte
// (table-leaf page 2, freeblocks overwriting the first 4 bytes of deleted cells, one WAL
// frame per commit), so the hex view, segments and decoded values are all mutually consistent.
// Synthetic, fictional data only.

import type {
  CaseDetail,
  CaseManifest,
  Change,
  Commit,
  Finding,
  HexChunk,
  Provenance,
  RecordRef,
  RecoveredRecord,
  Segment,
  SnapshotView,
  Source,
  Summary,
  Value,
} from "../contracts";

const PAGE = 4096;
const WAL_HDR = 32;
const FRAME_HDR = 24;
const FRAME = FRAME_HDR + PAGE;
const enc = new TextEncoder();

// ---------- encoding helpers ----------
function varint(v: number): number[] {
  // SQLite varint for non-negative values < 2^56 (enough here)
  if (v < 0x80) return [v];
  const out: number[] = [];
  let x = v;
  const tmp: number[] = [];
  while (x > 0) {
    tmp.push(x & 0x7f);
    x = Math.floor(x / 128);
  }
  for (let i = tmp.length - 1; i >= 0; i--) out.push(tmp[i] | (i > 0 ? 0x80 : 0));
  return out;
}

type Col = { name: string; v: string | number | null };
interface CellLayout {
  bytes: Uint8Array;
  payloadLen: number; // bytes in payload-size varint
  rowidLen: number;
  headerLen: number; // record header incl. header-size varint
  serials: number[];
  cols: { name: string; rel: number; len: number; value: string | number | null; type: Value["type"] }[];
  payloadSize: number;
}

function intSerial(n: number): [number, number[]] {
  if (n === 0) return [8, []];
  if (n === 1) return [9, []];
  const sizes: [number, number][] = [
    [1, 1],
    [2, 2],
    [3, 3],
    [4, 4],
    [5, 6],
    [6, 8],
  ];
  for (const [st, sz] of sizes) {
    const lim = 2 ** (sz * 8 - 1);
    if (n >= -lim && n < lim) {
      const b: number[] = [];
      let x = n < 0 ? 2 ** (sz * 8) + n : n;
      for (let i = 0; i < sz; i++) {
        b.unshift(x % 256);
        x = Math.floor(x / 256);
      }
      return [st, b];
    }
  }
  throw new Error("int too large");
}

function encodeCell(rowid: number, cols: Col[], rowidAlias: string | null): CellLayout {
  const serials: number[] = [];
  const bodies: number[][] = [];
  const types: Value["type"][] = [];
  for (const c of cols) {
    if (c.name === rowidAlias || c.v === null) {
      serials.push(0);
      bodies.push([]);
      types.push(c.name === rowidAlias ? "integer" : "null");
    } else if (typeof c.v === "number") {
      const [st, b] = intSerial(c.v);
      serials.push(st);
      bodies.push(b);
      types.push("integer");
    } else {
      const b = Array.from(enc.encode(c.v));
      serials.push(b.length * 2 + 13);
      bodies.push(b);
      types.push("text");
    }
  }
  const stBytes = serials.flatMap(varint);
  let hdrSize = stBytes.length + 1;
  if (varint(hdrSize).length > 1) hdrSize += 1;
  const header = [...varint(hdrSize), ...stBytes];
  const body = bodies.flat();
  const payload = [...header, ...body];
  const pv = varint(payload.length);
  const rv = varint(rowid);
  const bytes = new Uint8Array([...pv, ...rv, ...payload]);
  let rel = pv.length + rv.length + header.length;
  const colInfo = cols.map((c, i) => {
    const info = {
      name: c.name,
      rel,
      len: bodies[i].length,
      value: c.name === rowidAlias ? rowid : c.v,
      type: types[i],
    };
    rel += bodies[i].length;
    return info;
  });
  return {
    bytes,
    payloadLen: pv.length,
    rowidLen: rv.length,
    headerLen: header.length,
    serials,
    cols: colInfo,
    payloadSize: payload.length,
  };
}

function segmentsFor(layout: CellLayout, base: number, rowid: number, damaged: boolean): Segment[] {
  const segs: Segment[] = [];
  let o = base;
  segs.push({ start: o, length: layout.payloadLen, kind: "payload_size", column: null, value: layout.payloadSize });
  o += layout.payloadLen;
  segs.push({ start: o, length: layout.rowidLen, kind: "rowid", column: null, value: rowid });
  o += layout.rowidLen;
  segs.push({
    start: o,
    length: layout.headerLen,
    kind: "record_header",
    column: null,
    value: { header_size: layout.headerLen, serial_types: layout.serials },
  });
  for (const c of layout.cols) {
    if (c.len === 0) continue;
    segs.push({ start: base + c.rel, length: c.len, kind: "column", column: c.name, value: c.value });
  }
  if (!damaged) return segs;
  // First 4 bytes were overwritten by the freeblock header: clip everything that overlaps them.
  const dmgEnd = base + 4;
  const out: Segment[] = [{ start: base, length: 4, kind: "damaged", column: null, value: null }];
  for (const s of segs) {
    const end = s.start + s.length;
    if (end <= dmgEnd) continue;
    if (s.start < dmgEnd) out.push({ ...s, start: dmgEnd, length: end - dmgEnd });
    else out.push(s);
  }
  return out;
}

// ---------- page simulator ----------
class LeafPage {
  buf = new Uint8Array(PAGE);
  cells = new Map<string, { rowid: number; off: number; len: number }>();
  free: { off: number; size: number }[] = [];
  contentStart = PAGE;
  constructor(private hdrOff = 0) {
    this.buf[hdrOff] = 0x0d;
    this.writeHeader();
  }
  private u16(at: number, v: number) {
    this.buf[at] = (v >> 8) & 0xff;
    this.buf[at + 1] = v & 0xff;
  }
  private writeHeader() {
    const h = this.hdrOff;
    const sorted = [...this.free].sort((a, b) => a.off - b.off);
    this.u16(h + 1, sorted.length ? sorted[0].off : 0);
    const cells = [...this.cells.values()].sort((a, b) => a.rowid - b.rowid);
    this.u16(h + 3, cells.length);
    this.u16(h + 5, this.contentStart === PAGE ? 0 : this.contentStart);
    this.buf[h + 7] = 0;
    cells.forEach((c, i) => this.u16(h + 8 + i * 2, c.off));
    sorted.forEach((f, i) => {
      this.u16(f.off, i + 1 < sorted.length ? sorted[i + 1].off : 0);
      this.u16(f.off + 2, f.size);
    });
  }
  insert(key: string, rowid: number, bytes: Uint8Array): number {
    const off = this.contentStart - bytes.length;
    this.buf.set(bytes, off);
    this.contentStart = off;
    this.cells.set(key, { rowid, off, len: bytes.length });
    this.writeHeader();
    return off;
  }
  remove(key: string, zero = false): { off: number; len: number } {
    const c = this.cells.get(key);
    if (!c) throw new Error("no cell " + key);
    this.cells.delete(key);
    if (zero) this.buf.fill(0, c.off + 4, c.off + c.len);
    this.free.push({ off: c.off, size: c.len });
    this.writeHeader();
    return { off: c.off, len: c.len };
  }
}

// ---------- the story ----------
const T0 = Date.UTC(2026, 9, 17, 18, 0, 0) / 1000; // 2026-10-17, local display only
const m = (min: number, sec = 0) => T0 + min * 60 + sec;

interface Msg {
  rowid: number;
  sender: string;
  body: string;
  ts: number;
}
const MSG: Record<number, Msg> = {};
const add = (rowid: number, sender: string, body: string, ts: number) => (MSG[rowid] = { rowid, sender, body, ts });
add(1, "Mara", "Theo. Code red. There is exactly ONE box of donuts left at the snack table.", m(0));
add(2, "Theo", "Glazed or sprinkles?", m(0, 40));
add(3, "Mara", "Sprinkles. Team 12 has been circling it for ten minutes.", m(1, 10));
add(4, "Theo", "Should we just ask the organizers for another box?", m(1, 50));
add(5, "Theo", "Ok. You distract them, I walk past casually.", m(2, 30));
add(6, "Mara", "On it. I'll ask them whether REST is dead. That buys us 20 minutes minimum.", m(3, 5));
add(7, "Theo", "Meet behind the sponsor booth at 2:15", m(3, 40));
add(8, "Mara", "Copy. Bringing the big tote bag.", m(4, 20));
add(9, "Theo", "If anyone asks, the donuts were 'for the judges'.", m(5, 10));
add(10, "Mara", "THEO. Delete that. This chat is literally evidence.", m(5, 45));
add(11, "Theo", "Deleted. We were never here.", m(9, 0));
add(12, "Mara", "Phase two: the cold brew urn. Same plan, I'll grab the cups.", m(11, 0));
add(13, "Theo", "Box acquired. 11 donuts. Leaving one for Team 12, I'm not a monster.", m(12, 10));
add(14, "Mara", "Nice. Eating the evidence now.", m(14, 0));
add(15, "Theo", "Best hackathon ever.", m(15, 20));
const EDITED_BODY_7 = "Meet behind the sponsor booth at 2:20 instead. Bring napkins.";

type Op = { ins: number } | { upd: number; body: string } | { del: number };
const OPS: Op[] = [
  { ins: 5 },
  { ins: 6 },
  { ins: 7 },
  { ins: 8 },
  { ins: 9 },
  { ins: 10 },
  { upd: 7, body: EDITED_BODY_7 },
  { del: 9 },
  { ins: 11 },
  { ins: 12 },
  { ins: 13 },
  { del: 12 },
  { ins: 14 },
  { ins: 15 },
];
const N_COMMITS = OPS.length;

interface Version {
  key: string;
  rowid: number;
  sender: string;
  body: string;
  ts: number;
  first: number;
  last: number | null; // last commit where live; null = still live
  layout: CellLayout;
  offByFrame: Map<number, number>; // frame -> page offset where it is a live cell (frame 0 = main)
  freedAt: number | null; // page offset of its freeblock after removal
  removedKind: "delete" | "update" | null;
}

const COLS = ["id", "sender", "body", "sent_at"];
const cellFor = (rowid: number, sender: string, body: string, ts: number) =>
  encodeCell(
    rowid,
    [
      { name: "id", v: null },
      { name: "sender", v: sender },
      { name: "body", v: body },
      { name: "sent_at", v: ts },
    ],
    "id",
  );

function buildWorld() {
  const versions: Version[] = [];
  const liveByRowid = new Map<number, Version>();
  const page = new LeafPage();
  const mkVersion = (mm: Msg, body: string, first: number, vIdx: number): Version => ({
    key: `${mm.rowid}.${vIdx}`,
    rowid: mm.rowid,
    sender: mm.sender,
    body,
    ts: mm.ts,
    first,
    last: null,
    layout: cellFor(mm.rowid, mm.sender, body, mm.ts),
    offByFrame: new Map(),
    freedAt: null,
    removedKind: null,
  });

  // Baseline (already checkpointed into the main file): rows 1..4, then row 4 deleted with
  // secure_delete on (freeblock zero-filled) -> CU1 + CU2.
  for (const r of [1, 2, 3, 4]) {
    const v = mkVersion(MSG[r], MSG[r].body, 0, 0);
    page.insert(v.key, r, v.layout.bytes);
    if (r !== 4) {
      versions.push(v);
      liveByRowid.set(r, v);
    }
  }
  const zeroed = page.remove("4.0", true);
  for (const v of liveByRowid.values()) v.offByFrame.set(0, page.cells.get(v.key)!.off);

  // Main file: page 1 (header + sqlite_master), page 2 (messages leaf).
  const main = new Uint8Array(PAGE * 2);
  main.set(enc.encode("SQLite format 3\0"), 0);
  main.set([0x10, 0x00, 0x02, 0x02, 0x00, 0x40, 0x20, 0x20], 16);
  main.set([0, 0, 0, 1, 0, 0, 0, 2], 24); // change counter, db size in pages
  main.set([0, 0, 0, 1], 44); // schema format
  main.set([0, 0, 0, 1], 56); // UTF-8
  main.set([0, 0x2e, 0x89, 0x70], 96);
  const master = new LeafPage(100);
  const sql =
    "CREATE TABLE messages (\n  id INTEGER PRIMARY KEY AUTOINCREMENT,\n  sender TEXT NOT NULL,\n  body TEXT NOT NULL,\n  sent_at INTEGER NOT NULL\n)";
  const mcell = encodeCell(
    1,
    [
      { name: "type", v: "table" },
      { name: "name", v: "messages" },
      { name: "tbl_name", v: "messages" },
      { name: "rootpage", v: 2 },
      { name: "sql", v: sql },
    ],
    null,
  );
  master.insert("m", 1, mcell.bytes);
  const seqCell = encodeCell(
    2,
    [
      { name: "type", v: "table" },
      { name: "name", v: "sqlite_sequence" },
      { name: "tbl_name", v: "sqlite_sequence" },
      { name: "rootpage", v: 3 },
      { name: "sql", v: "CREATE TABLE sqlite_sequence(name,seq)" },
    ],
    null,
  );
  master.insert("s", 2, seqCell.bytes);
  main.set(master.buf.subarray(100), 100);
  main.set(page.buf, PAGE);

  // WAL: header + one frame (page 2) per commit.
  const wal = new Uint8Array(WAL_HDR + FRAME * N_COMMITS);
  wal.set([0x37, 0x7f, 0x06, 0x82, 0x00, 0x2d, 0xe2, 0x18, 0, 0, 0x10, 0, 0, 0, 0, 0], 0);
  const salt = [0x5a, 0x1d, 0x0b, 0x7e, 0x3c, 0x91, 0x44, 0xd2];
  wal.set(salt, 16);
  wal.set([0x9e, 0x13, 0x2b, 0x40, 0x71, 0xaa, 0x0c, 0x5f], 24);

  const opInfo: { commit: number; kind: Change["kind"]; rowid: number; before: Version | null; after: Version | null }[] =
    [];
  OPS.forEach((op, i) => {
    const k = i + 1;
    if ("ins" in op) {
      const v = mkVersion(MSG[op.ins], MSG[op.ins].body, k, 0);
      page.insert(v.key, v.rowid, v.layout.bytes);
      versions.push(v);
      liveByRowid.set(v.rowid, v);
      opInfo.push({ commit: k, kind: "insert", rowid: v.rowid, before: null, after: v });
    } else if ("upd" in op) {
      const old = liveByRowid.get(op.upd)!;
      old.freedAt = page.remove(old.key).off;
      old.last = k - 1;
      old.removedKind = "update";
      const v = mkVersion(MSG[op.upd], op.body, k, 1);
      page.insert(v.key, v.rowid, v.layout.bytes);
      versions.push(v);
      liveByRowid.set(v.rowid, v);
      opInfo.push({ commit: k, kind: "update", rowid: v.rowid, before: old, after: v });
    } else {
      const old = liveByRowid.get(op.del)!;
      old.freedAt = page.remove(old.key).off;
      old.last = k - 1;
      old.removedKind = "delete";
      liveByRowid.delete(op.del);
      opInfo.push({ commit: k, kind: "delete", rowid: old.rowid, before: old, after: null });
    }
    for (const v of liveByRowid.values()) v.offByFrame.set(k, page.cells.get(v.key)!.off);
    const fo = WAL_HDR + (k - 1) * FRAME;
    wal.set([0, 0, 0, 2, 0, 0, 0, 2], fo); // pgno 2, commit: db size 2 pages
    wal.set(salt, fo + 8);
    const ck = (k * 2654435761) >>> 0;
    wal.set([(ck >>> 24) & 255, (ck >>> 16) & 255, (ck >>> 8) & 255, ck & 255, 0x13, k, 0x7a, 0x21], fo + 16);
    wal.set(page.buf, fo + FRAME_HDR);
  });

  return { versions, main, wal, opInfo, zeroed };
}

const W = buildWorld();
const walPageBase = (frame: number) => WAL_HDR + (frame - 1) * FRAME + FRAME_HDR;

// ---------- records ----------
function valuesOf(v: Version, reconstructedCols: string[] = []): Value[] {
  return v.layout.cols.map((c) => ({
    column: c.name,
    type: c.type,
    value: c.value,
    reconstructed: reconstructedCols.includes(c.name),
  }));
}

function prov(v: Version, source: Source, frame: number | null, pageOff: number, damaged: boolean): Provenance {
  const base = frame === null ? PAGE : walPageBase(frame);
  const fileOff = base + pageOff;
  return {
    source,
    file: frame === null ? "main" : "wal",
    page: 2,
    page_offset: pageOff,
    file_offset: fileOff,
    length: v.layout.bytes.length,
    wal_frame: frame,
    commit: frame,
    segments: segmentsFor(v.layout, fileOff, v.rowid, damaged),
  };
}

const RECORDS: RecoveredRecord[] = [];
const idByKey = new Map<string, string>();
{
  const sorted = [...W.versions].sort((a, b) => a.rowid - b.rowid || a.first - b.first);
  let n = 1;
  const nextId = () => `R-${String(n++).padStart(3, "0")}`;
  for (const v of sorted) {
    const id = nextId();
    idByKey.set(v.key, id);
    if (v.last === null) {
      const p = [prov(v, "btree_live", N_COMMITS, v.offByFrame.get(N_COMMITS)!, false)];
      if (v.first === 0) p.push(prov(v, "btree_live", null, v.offByFrame.get(0)!, false));
      RECORDS.push({
        id,
        table: "messages",
        rowid: v.rowid,
        rowid_inferred: false,
        status: "live",
        values: valuesOf(v),
        confidence: 1,
        confidence_reasons: ["live b-tree cell in the latest committed snapshot", "column count matches schema"],
        provenance: p,
        first_seen_commit: v.first,
        last_seen_commit: N_COMMITS,
        reconstructed: false,
      });
    } else if (v.rowid === 12) {
      // Reconstructed from its damaged freeblock (F7 style).
      RECORDS.push({
        id,
        table: "messages",
        rowid: v.rowid,
        rowid_inferred: true,
        status: "deleted",
        values: valuesOf(v, ["id"]),
        confidence: 0.58,
        confidence_reasons: [
          "freeblock: first 4 bytes overwritten by freeblock header",
          "layout hypothesis: payload varint 1 byte, rowid varint 1 byte, header 5 bytes",
          "valid UTF-8, 100% printable",
          "sent_at is a plausible epoch timestamp",
          "rowid inferred from neighbours' timestamps (between 11 and 13)",
        ],
        provenance: [prov(v, "freeblock", N_COMMITS, v.freedAt!, true)],
        first_seen_commit: v.first,
        last_seen_commit: v.last,
        reconstructed: true,
      });
    } else {
      const lastFrame = v.last;
      const isPrior = v.removedKind === "update";
      const p: Provenance[] = [];
      if (lastFrame >= 1) p.push(prov(v, "wal_frame", lastFrame, v.offByFrame.get(lastFrame)!, false));
      p.push(prov(v, "freeblock", N_COMMITS, v.freedAt!, true));
      RECORDS.push({
        id,
        table: "messages",
        rowid: v.rowid,
        rowid_inferred: false,
        status: isPrior ? "prior_version" : "deleted",
        values: valuesOf(v),
        confidence: 0.95,
        confidence_reasons: [
          `valid committed WAL frame ${lastFrame} (base 0.95)`,
          "column count matches schema",
          "valid UTF-8",
          `also found as freeblock residue in WAL frame ${N_COMMITS}`,
        ],
        provenance: p,
        first_seen_commit: v.first,
        last_seen_commit: v.last,
        reconstructed: false,
      });
    }
  }
  // A stale copy: row 5 as it sat in WAL frame 1, byte-identical to the live row.
  const v5 = W.versions.find((v) => v.rowid === 5)!;
  RECORDS.push({
    id: `R-${String(n++).padStart(3, "0")}`,
    table: "messages",
    rowid: 5,
    rowid_inferred: false,
    status: "stale_copy",
    values: valuesOf(v5),
    confidence: 0.95,
    confidence_reasons: ["valid committed WAL frame 1", "identical to live row 5 (stale copy, not a deletion)"],
    provenance: [prov(v5, "wal_frame", 1, v5.offByFrame.get(1)!, false)],
    first_seen_commit: 1,
    last_seen_commit: 1,
    reconstructed: false,
  });
}

const refOf = (v: Version | null): RecordRef | null =>
  v ? { record_id: idByKey.get(v.key)!, values: valuesOf(v) } : null;

const CHANGES: Change[] = W.opInfo.map((o) => {
  const k = o.commit;
  const earlier = W.versions.filter((v) => v.first <= k - 1).map((v) => v.ts);
  const later = W.versions.filter((v) => v.first >= k + 1).map((v) => v.ts);
  return {
    commit: k,
    table: "messages",
    kind: o.kind,
    rowid: o.rowid,
    before: refOf(o.before),
    after: refOf(o.after),
    bracket: {
      after_ts: earlier.length ? Math.max(...earlier) : null,
      before_ts: later.length ? Math.min(...later) : null,
    },
  };
});

const COMMITS: Commit[] = [
  { index: 0, first_frame: 0, last_frame: 0, db_size_pages: 2, approximate: false },
  ...OPS.map((_, i) => ({
    index: i + 1,
    first_frame: i + 1,
    last_frame: i + 1,
    db_size_pages: 2,
    approximate: false,
  })),
];

function snapshot(k: number): SnapshotView {
  const ids = W.versions
    .filter((v) => v.first <= k && (v.last === null || v.last >= k))
    .sort((a, b) => a.rowid - b.rowid)
    .map((v) => idByKey.get(v.key)!);
  return { commit: k, approximate: false, tables: { messages: ids } };
}

const sha = (seed: string) => {
  let h = 0x811c9dc5;
  let out = "";
  for (let r = 0; r < 8; r++) {
    for (let i = 0; i < seed.length; i++) h = Math.imul(h ^ seed.charCodeAt(i), 16777619) >>> 0;
    h = Math.imul(h ^ r, 2246822519) >>> 0;
    out += h.toString(16).padStart(8, "0");
  }
  return out;
};

export function manifestFor(caseId: string, label: string, created: string): CaseManifest {
  return {
    case_id: caseId,
    created,
    label,
    files: [
      { role: "main", name: "chat.db", bytes: W.main.length, sha256: sha(caseId + "main") },
      { role: "wal", name: "chat.db-wal", bytes: W.wal.length, sha256: sha(caseId + "wal") },
      { role: "shm", name: "chat.db-shm", bytes: 32768, sha256: sha(caseId + "shm") },
    ],
  };
}

export function caseDetail(manifest: CaseManifest): CaseDetail {
  const status_counts: Record<string, number> = {};
  const source_counts: Record<string, number> = {};
  for (const r of RECORDS) {
    status_counts[r.status] = (status_counts[r.status] ?? 0) + 1;
    const s = r.provenance[0].source;
    source_counts[s] = (source_counts[s] ?? 0) + 1;
  }
  return {
    manifest,
    hashes_verified: true,
    encrypted: false,
    header: {
      page_size: 4096,
      text_encoding: "UTF-8",
      journal_mode: "wal",
      freelist_trunk: 0,
      freelist_count: 0,
      auto_vacuum: false,
      schema_format: 4,
    },
    tables: [
      {
        name: "messages",
        root: 2,
        sql: "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT NOT NULL, body TEXT NOT NULL, sent_at INTEGER NOT NULL)",
        without_rowid: false,
        columns: COLS.map((name, i) => ({
          name,
          decl_type: name === "sender" || name === "body" ? "TEXT" : "INTEGER",
          affinity: name === "sender" || name === "body" ? "TEXT" : "INTEGER",
          notnull: i > 0,
          pk: i === 0 ? 1 : 0,
          is_rowid_alias: i === 0,
        })),
      },
      {
        name: "sqlite_sequence",
        root: 3,
        sql: "CREATE TABLE sqlite_sequence(name,seq)",
        without_rowid: false,
        columns: [
          { name: "name", decl_type: "", affinity: "BLOB", notnull: false, pk: 0, is_rowid_alias: false },
          { name: "seq", decl_type: "", affinity: "BLOB", notnull: false, pk: 0, is_rowid_alias: false },
        ],
      },
    ],
    stats: {
      status_counts,
      source_counts,
      pages_main: 2,
      pages_final: 2,
      freelist_pages: 0,
      checkpointed_through_frame: 0,
    },
    wal: { frames: N_COMMITS, valid_frames: N_COMMITS, commits: N_COMMITS, salt_ok: true, checkpoint_seq: 0 },
    journal: null,
    commits: N_COMMITS,
    chat: { table: "messages", sender: "sender", body: "body", timestamp: "sent_at" },
    errors: [],
  };
}

export const FINDINGS: Finding[] = [
  {
    id: "CU1",
    severity: "high",
    title: "Secure-delete pattern",
    detail: `1 freeblock on page 2 (main file, offset 0x${W.zeroed.off.toString(16).toUpperCase()}) is zero-filled after its 4-byte header. This is consistent with secure_delete being enabled when that row was removed.`,
    evidence: [{ file: "main", file_offset: PAGE + W.zeroed.off, length: W.zeroed.len }],
  },
  {
    id: "CU2",
    severity: "high",
    title: "Deletions with no residue",
    detail:
      "Rowid 4 is missing between rowids 1 and 15 in table messages, and no copy was recovered from the main file, WAL frames or freeblocks. This is consistent with a deletion whose bytes were overwritten.",
    evidence: [
      { file: "main", file_offset: PAGE + W.zeroed.off, length: W.zeroed.len },
      { file: "main", file_offset: PAGE, length: 64 },
    ],
  },
  {
    id: "CU6",
    severity: "info",
    title: "WAL history present",
    detail: `The WAL holds ${N_COMMITS} committed transactions that were never checkpointed into the main file. Each one is a recoverable snapshot.`,
    evidence: [{ file: "wal", file_offset: 0, length: 32 }],
  },
];

const rid = (rowid: number, removed?: "delete" | "update") => {
  const v = W.versions.find((x) => x.rowid === rowid && (removed ? x.removedKind === removed : x.last === null))!;
  return idByKey.get(v.key)!;
};

export const SUMMARY: Summary = {
  available: true,
  model: "mock-model",
  cached: false,
  sentences: [
    {
      text: "Mara and Theo discussed taking the last box of donuts from the snack table.",
      citations: [rid(1), rid(3)],
    },
    {
      text: "A message reading \"If anyone asks, the donuts were 'for the judges'\" was deleted; it survives intact in WAL frame 7.",
      citations: [rid(9, "delete")],
    },
    {
      text: "The meeting time in one message was edited from 2:15 to 2:20, and the earlier wording is preserved as a prior version.",
      citations: [rid(7, "update"), rid(7)],
    },
    {
      text: "A second deleted message about the cold brew urn was reconstructed from a damaged freeblock with moderate confidence.",
      citations: [rid(12, "delete")],
    },
    {
      text: "A later message states that the box was acquired with 11 donuts.",
      citations: [rid(13)],
    },
  ],
  dropped: 1,
  error: null,
};

export function mockRecords(table?: string | null, status?: string | null): RecoveredRecord[] {
  return RECORDS.filter((r) => (!table || r.table === table) && (!status || r.status === status));
}
export function mockCommits(): Commit[] {
  return COMMITS;
}
export function mockSnapshot(k: number): SnapshotView {
  return snapshot(Math.max(0, Math.min(N_COMMITS, k)));
}
export function mockChanges(commit?: number | null): Change[] {
  return commit === null || commit === undefined ? CHANGES : CHANGES.filter((c) => c.commit === commit);
}
export function mockHex(file: string, offset: number, length: number): HexChunk {
  const buf = file === "wal" ? W.wal : file === "main" ? W.main : new Uint8Array(0);
  const start = Math.max(0, Math.min(offset, buf.length));
  const end = Math.max(start, Math.min(buf.length, start + Math.min(512, length)));
  let hexStr = "";
  for (let i = start; i < end; i++) hexStr += buf[i].toString(16).padStart(2, "0");
  return { file, offset: start, length: end - start, hex: hexStr, file_size: buf.length };
}
