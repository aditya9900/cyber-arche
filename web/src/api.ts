// Typed fetchers for every endpoint in BLUEPRINT "API". With VITE_USE_MOCKS=1 they are
// answered in-browser by src/mocks instead of the FastAPI server.
import type {
  AppConfig,
  CaseDetail,
  CaseManifest,
  Change,
  Commit,
  DemoMessage,
  Finding,
  HexChunk,
  RecoveredRecord,
  SnapshotView,
  Status,
  Summary,
} from "./contracts";

export const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === "1";
export const HEX_CHUNK_MAX = 512;

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  if (USE_MOCKS) {
    const { mockRequest } = await import("./mocks");
    return (await mockRequest(method, url, body)) as T;
  }
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const res = await fetch(url, init);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (j && j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const qs = (params: Record<string, string | number | null | undefined>): string => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};
const c = (id: string) => `/api/cases/${encodeURIComponent(id)}`;

export const api = {
  config: () => request<AppConfig>("GET", "/api/config"),
  listCases: () => request<CaseManifest[]>("GET", "/api/cases"),
  uploadCase: (files: { db: File; wal?: File | null; journal?: File | null; label?: string }) => {
    const fd = new FormData();
    fd.append("db", files.db);
    if (files.wal) fd.append("wal", files.wal);
    if (files.journal) fd.append("journal", files.journal);
    if (files.label) fd.append("label", files.label);
    return request<CaseManifest>("POST", "/api/cases", fd);
  },
  caseDetail: (id: string) => request<CaseDetail>("GET", c(id)),
  records: (id: string, filters: { table?: string; status?: Status } = {}) =>
    request<RecoveredRecord[]>("GET", `${c(id)}/records${qs(filters)}`),
  commits: (id: string) => request<Commit[]>("GET", `${c(id)}/commits`),
  snapshot: (id: string, k: number) => request<SnapshotView>("GET", `${c(id)}/snapshot/${k}`),
  changes: (id: string, commit?: number) => request<Change[]>("GET", `${c(id)}/changes${qs({ commit })}`),
  hex: (id: string, file: string, offset: number, length: number) =>
    request<HexChunk>("GET", `${c(id)}/hex${qs({ file, offset, length: Math.min(length, HEX_CHUNK_MAX) })}`),
  findings: (id: string) => request<Finding[]>("GET", `${c(id)}/findings`),
  summary: (id: string) => request<Summary>("POST", `${c(id)}/summary`),
  reportUrl: (id: string) => `${c(id)}/report`,

  demo: {
    state: () => request<{ participants: string[] }>("GET", "/demo/state"),
    messages: () => request<DemoMessage[]>("GET", "/demo/messages"),
    send: (sender: string, body: string) => request<DemoMessage>("POST", "/demo/messages", { sender, body }),
    edit: (id: number, body: string) => request<DemoMessage>("PATCH", `/demo/messages/${id}`, { body }),
    remove: (id: number) => request<unknown>("DELETE", `/demo/messages/${id}`),
    seize: () => request<CaseManifest>("POST", "/demo/seize"),
    reset: () => request<{ ok: boolean }>("POST", "/demo/reset"),
  },
};

/** Fetch [offset, offset+length) in <=512-byte chunks and concatenate. */
export async function fetchHexRange(
  caseId: string,
  file: string,
  offset: number,
  length: number,
): Promise<{ offset: number; bytes: Uint8Array; fileSize: number }> {
  const start = Math.max(0, offset);
  const end = offset + length;
  const reqs: Promise<HexChunk>[] = [];
  for (let o = start; o < end; o += HEX_CHUNK_MAX) reqs.push(api.hex(caseId, file, o, Math.min(HEX_CHUNK_MAX, end - o)));
  const chunks = await Promise.all(reqs);
  const total = chunks.reduce((n, ch) => n + ch.length, 0);
  const bytes = new Uint8Array(total);
  let pos = 0;
  let fileSize = 0;
  for (const ch of chunks) {
    fileSize = ch.file_size;
    for (let i = 0; i < ch.length; i++) bytes[pos + i] = parseInt(ch.hex.substr(i * 2, 2), 16);
    pos += ch.length;
    if (ch.length === 0) break;
  }
  return { offset: chunks.length ? chunks[0].offset : start, bytes: bytes.subarray(0, pos), fileSize };
}
