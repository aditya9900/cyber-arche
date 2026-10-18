// In-browser mock backend used when VITE_USE_MOCKS=1.
import type { AppConfig, CaseManifest, DemoMessage } from "../contracts";
import {
  FINDINGS,
  SUMMARY,
  caseDetail,
  manifestFor,
  mockChanges,
  mockCommits,
  mockHex,
  mockRecords,
  mockSnapshot,
} from "./case";

const cases: CaseManifest[] = [
  manifestFor("c-20261017-02", "Donut heist: Theo's phone (seized at table 14)", "2026-10-17T14:31:05Z"),
  manifestFor("c-20261017-01", "Backup fixture (secure_delete switched on midway)", "2026-10-17T11:02:44Z"),
];

const base = Math.floor(Date.now() / 1000) - 600;
let demoMessages: DemoMessage[] = [];
let nextId = 1;
function seedDemo() {
  const seed: [string, string][] = [
    ["Mara", "Theo. Code red. There is exactly ONE box of donuts left at the snack table."],
    ["Theo", "Glazed or sprinkles?"],
    ["Mara", "Sprinkles. Team 12 has been circling it for ten minutes."],
    ["Theo", "Ok. You distract them, I walk past casually."],
    ["Mara", "On it. I'll ask them whether REST is dead. That buys us 20 minutes minimum."],
    ["Theo", "Meet behind the sponsor booth at 2:15"],
  ];
  nextId = 1;
  demoMessages = seed.map(([sender, body], i) => ({ id: nextId++, sender, body, sent_at: base + i * 45 }));
}
seedDemo();

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

export class MockHttpError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function mockRequest(method: string, url: string, body?: unknown): Promise<unknown> {
  await delay(method === "POST" && url.endsWith("/summary") ? 1800 : 120);
  const u = new URL(url, "http://mock.local");
  const path = u.pathname;
  const q = u.searchParams;
  let mm: RegExpMatchArray | null;

  if (path === "/api/config") return { ai_available: true, model: "mock-model" } satisfies AppConfig;
  if (path === "/api/cases" && method === "GET") return cases;
  if (path === "/api/cases" && method === "POST") {
    const fd = body as FormData;
    const label = String(fd.get("label") ?? "") || "Uploaded evidence";
    const id = `c-20261017-${String(cases.length + 1).padStart(2, "0")}`;
    const man = manifestFor(id, label, new Date().toISOString());
    cases.unshift(man);
    return man;
  }
  if ((mm = path.match(/^\/api\/cases\/([^/]+)(\/.*)?$/))) {
    const id = decodeURIComponent(mm[1]);
    const rest = mm[2] ?? "";
    const man = cases.find((c) => c.case_id === id) ?? manifestFor(id, "Mock case", new Date().toISOString());
    if (rest === "") return caseDetail(man);
    if (rest === "/records") return mockRecords(q.get("table"), q.get("status"));
    if (rest === "/commits") return mockCommits();
    if ((mm = rest.match(/^\/snapshot\/(\d+)$/))) return mockSnapshot(Number(mm[1]));
    if (rest === "/changes") return mockChanges(q.has("commit") ? Number(q.get("commit")) : null);
    if (rest === "/hex")
      return mockHex(q.get("file") ?? "main", Number(q.get("offset") ?? 0), Number(q.get("length") ?? 256));
    if (rest === "/findings") return FINDINGS;
    if (rest === "/summary" && method === "POST") return SUMMARY;
  }

  if (path === "/demo/state") return { participants: ["Mara", "Theo"] };
  if (path === "/demo/messages" && method === "GET") return demoMessages;
  if (path === "/demo/messages" && method === "POST") {
    const b = body as { sender: string; body: string };
    const msg: DemoMessage = { id: nextId++, sender: b.sender, body: b.body, sent_at: Math.floor(Date.now() / 1000) };
    demoMessages = [...demoMessages, msg];
    return msg;
  }
  if ((mm = path.match(/^\/demo\/messages\/(\d+)$/))) {
    const mid = Number(mm[1]);
    if (method === "PATCH") {
      const b = body as { body: string };
      demoMessages = demoMessages.map((x) => (x.id === mid ? { ...x, body: b.body } : x));
      return demoMessages.find((x) => x.id === mid);
    }
    if (method === "DELETE") {
      demoMessages = demoMessages.filter((x) => x.id !== mid);
      return { ok: true };
    }
  }
  if (path === "/demo/seize" && method === "POST") {
    const id = `c-20261017-${String(cases.length + 1).padStart(2, "0")}`;
    const man = manifestFor(id, "Live demo seizure", new Date().toISOString());
    cases.unshift(man);
    return man;
  }
  if (path === "/demo/reset" && method === "POST") {
    seedDemo();
    return { ok: true };
  }
  throw new MockHttpError(404, `mock: no route for ${method} ${path}`);
}
