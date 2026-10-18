import { useState } from "react";
import type { Change, ChatMapping, Commit, Finding, RecoveredRecord, Summary, Value } from "../contracts";
import { api } from "../api";
import { bracketText, classNames, displayValue, formatHM, hex } from "../lib/format";
import { useHex } from "./HexViewer";
import { ErrorBox, Spinner, Tag } from "./ui";

// ---------------- Timeline ----------------
function ValuesInline({ values, tsCol, struck }: { values: Value[]; tsCol: string | null; struck?: boolean }) {
  return (
    <span className={classNames("break-words", struck && "text-slate-500 line-through")}>
      {values.map((v, i) => (
        <span key={v.column}>
          {i > 0 && <span className="text-slate-400"> · </span>}
          <span className="font-mono text-[11px] text-slate-500">{v.column}=</span>
          {displayValue(v)}
          {v.column === tsCol && typeof v.value === "number" && <span className="text-slate-500"> ({formatHM(v.value)})</span>}
        </span>
      ))}
    </span>
  );
}

export function TimelineTab({
  commits,
  changes,
  records,
  chat,
}: {
  commits: Commit[];
  changes: Change[];
  records: RecoveredRecord[];
  chat: ChatMapping | null;
}) {
  const hexOpen = useHex();
  const byId = new Map(records.map((r) => [r.id, r]));
  const open = (id: string | undefined) => {
    const r = id ? byId.get(id) : undefined;
    if (r) hexOpen({ kind: "record", record: r });
  };
  const tsCol = chat?.timestamp ?? null;
  return (
    <div>
      <p className="mb-3 text-xs text-slate-500">
        WAL commits are ordered, not timestamped. Each change is bracketed using the application's own timestamps.
      </p>
      <ol className="relative space-y-3 border-l border-slate-300 pl-5 dark:border-slate-700">
        {commits.map((c) => {
          const cs = changes.filter((x) => x.commit === c.index);
          return (
            <li key={c.index} className="relative">
              <span
                className={classNames(
                  "absolute -left-[27px] top-3 h-3 w-3 rounded-full border-2 border-white dark:border-[#0b0f17]",
                  cs.some((x) => x.kind === "delete")
                    ? "bg-red-500"
                    : cs.some((x) => x.kind === "update")
                      ? "bg-amber-500"
                      : cs.length
                        ? "bg-emerald-500"
                        : "bg-slate-400",
                )}
              />
              <div className="card p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm font-semibold">commit {c.index}</span>
                  <span className="font-mono text-xs text-slate-500">
                    {c.index === 0 ? "main file baseline" : `WAL frames ${c.first_frame}–${c.last_frame}`} · db{" "}
                    {c.db_size_pages} pages
                  </span>
                  {c.approximate && <Tag tone="amber">approximate snapshot</Tag>}
                </div>
                {cs.length === 0 ? (
                  <p className="mt-1 text-xs text-slate-500">
                    {c.index === 0 ? "Starting state: rows already checkpointed into the main file." : "No row-level changes."}
                  </p>
                ) : (
                  <ul className="mt-2 space-y-2">
                    {cs.map((x, i) => (
                      <li key={i} className="rounded border border-slate-200 p-2 text-sm dark:border-slate-800">
                        <div className="mb-1 flex flex-wrap items-center gap-1.5">
                          <Tag tone={x.kind === "delete" ? "red" : x.kind === "update" ? "amber" : "green"}>{x.kind}</Tag>
                          <span className="font-mono text-xs">
                            {x.table} · rowid {x.rowid}
                          </span>
                          {[x.before, x.after].map(
                            (ref, j) =>
                              ref && (
                                <button
                                  key={j}
                                  onClick={() => open(ref.record_id)}
                                  className="pill border border-sky-500/50 font-mono text-sky-700 hover:bg-sky-50 dark:text-sky-300 dark:hover:bg-sky-500/10"
                                >
                                  {ref.record_id}
                                </button>
                              ),
                          )}
                        </div>
                        {x.before && (
                          <div className="text-xs">
                            <span className="mr-1 font-mono text-[10px] uppercase text-slate-400">before</span>
                            <ValuesInline values={x.before.values} tsCol={tsCol} struck={x.kind !== "insert"} />
                          </div>
                        )}
                        {x.after && (
                          <div className="text-xs">
                            <span className="mr-1 font-mono text-[10px] uppercase text-slate-400">after</span>
                            <ValuesInline values={x.after.values} tsCol={tsCol} />
                          </div>
                        )}
                        {x.bracket && (
                          <div className="mt-1 text-xs text-slate-500">
                            {bracketText(x.bracket, x.kind === "delete" ? "Deleted" : x.kind === "update" ? "Edited" : "Inserted")}.
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ---------------- Findings ----------------
const SEV: Record<Finding["severity"], { card: string; tag: string }> = {
  high: {
    card: "border-l-red-500",
    tag: "bg-red-600 text-white",
  },
  medium: {
    card: "border-l-amber-500",
    tag: "bg-amber-500 text-black",
  },
  info: {
    card: "border-l-sky-500",
    tag: "bg-sky-600 text-white",
  },
};

export function FindingsTab({ findings }: { findings: Finding[] }) {
  const hexOpen = useHex();
  if (findings.length === 0)
    return <div className="card p-6 text-center text-sm text-slate-500">No cover-up indicators found.</div>;
  const order = { high: 0, medium: 1, info: 2 };
  return (
    <div>
      <p className="mb-3 text-xs text-slate-500">Findings are indicators, never proof. Each cites the bytes or counts behind it.</p>
      <div className="grid gap-3 md:grid-cols-2">
        {[...findings]
          .sort((a, b) => order[a.severity] - order[b.severity])
          .map((f) => (
            <div key={f.id} className={classNames("card border-l-4 p-4", SEV[f.severity].card)}>
              <div className="flex items-center gap-2">
                <span className={classNames("pill uppercase", SEV[f.severity].tag)}>{f.severity}</span>
                <span className="font-mono text-xs text-slate-500">{f.id}</span>
              </div>
              <h3 className="mt-2 font-semibold">{f.title}</h3>
              <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">{f.detail}</p>
              {f.evidence.length > 0 && (
                <div className="mt-3">
                  <div className="label mb-1">Evidence</div>
                  <div className="flex flex-wrap gap-1.5">
                    {f.evidence.map((e, i) => (
                      <button
                        key={i}
                        onClick={() =>
                          hexOpen({
                            kind: "raw",
                            file: e.file,
                            offset: e.file_offset,
                            length: e.length,
                            title: `${f.id} evidence · ${e.file} @ ${hex(e.file_offset)}`,
                          })
                        }
                        className="rounded border border-slate-300 px-2 py-0.5 font-mono text-xs hover:border-sky-500 hover:text-sky-700 dark:border-slate-700 dark:hover:text-sky-300"
                      >
                        {e.file} {hex(e.file_offset)} +{e.length}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
      </div>
    </div>
  );
}

// ---------------- AI summary ----------------
export function SummaryTab({ caseId, records }: { caseId: string; records: RecoveredRecord[] }) {
  const hexOpen = useHex();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const byId = new Map(records.map((r) => [r.id, r]));
  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      setSummary(await api.summary(caseId));
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="max-w-3xl">
      <div className="flex flex-wrap items-center gap-3">
        <button className="btn-primary" onClick={run} disabled={busy}>
          {busy && <Spinner />} {summary ? "Regenerate summary" : "Generate summary"}
        </button>
        {busy && <span className="text-sm text-slate-500">Asking the model; this can take several seconds…</span>}
      </div>
      {err ? (
        <div className="mt-3">
          <ErrorBox error={err} />
        </div>
      ) : null}
      {summary && (
        <div className="card mt-4 p-4">
          <div className="mb-3 rounded border border-violet-400/50 bg-violet-50 px-2.5 py-1.5 text-xs font-medium text-violet-900 dark:bg-violet-500/10 dark:text-violet-200">
            AI-generated summary. Verify against the cited records.
            {summary.model && <span className="ml-2 font-mono font-normal opacity-70">{summary.model}{summary.cached ? " · cached" : ""}</span>}
          </div>
          {summary.error && <ErrorBox error={summary.error} />}
          {!summary.available && !summary.error && <p className="text-sm text-slate-500">Summary not available.</p>}
          <div className="space-y-2 text-[15px] leading-relaxed">
            {summary.sentences.map((s, i) => (
              <p key={i}>
                {s.text}{" "}
                {s.citations.map((c) => {
                  const r = byId.get(c);
                  return (
                    <button
                      key={c}
                      disabled={!r}
                      onClick={() => r && hexOpen({ kind: "record", record: r })}
                      className="mx-0.5 rounded bg-sky-100 px-1.5 py-0.5 align-middle font-mono text-[11px] text-sky-800 hover:bg-sky-200 disabled:opacity-50 dark:bg-sky-500/15 dark:text-sky-300"
                      title={r ? "Open bytes" : "Record not found"}
                    >
                      {c}
                    </button>
                  );
                })}
              </p>
            ))}
          </div>
          {summary.dropped > 0 && (
            <p className="mt-3 text-xs text-amber-700 dark:text-amber-400">
              {summary.dropped} sentence{summary.dropped === 1 ? "" : "s"} dropped (missing/invalid citations).
            </p>
          )}
        </div>
      )}
    </div>
  );
}
