// BLUEPRINT F5 conversation view + F2 timeline slider.
import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { Change, ChatMapping, Commit, RecoveredRecord, SnapshotView } from "../contracts";
import { api } from "../api";
import { bracketText, classNames, compareByTsRowid, formatHM, numValue, rawValue } from "../lib/format";
import { useHex } from "./HexViewer";
import { ErrorBox, RecordBadges, Spinner, Tag } from "./ui";

interface Props {
  caseId: string;
  chat: ChatMapping;
  records: RecoveredRecord[];
  commits: Commit[];
  changes: Change[];
}

type Mode = "asof" | "all";

interface Item {
  rec: RecoveredRecord;
  ghost?: Change; // deleted by the selected commit
  inserted?: Change;
  edited?: Change;
  priors: RecoveredRecord[];
}

export default function ConversationView({ caseId, chat, records, commits, changes }: Props) {
  const lastCommit = commits.length ? commits[commits.length - 1].index : 0;
  const [mode, setMode] = useState<Mode>("asof");
  const [k, setK] = useState(lastCommit);
  useEffect(() => setK(lastCommit), [lastCommit]);

  const [snaps, setSnaps] = useState<Record<number, SnapshotView>>({});
  const [snapErr, setSnapErr] = useState<unknown>(null);
  useEffect(() => {
    if (mode !== "asof" || snaps[k]) return;
    let cancelled = false;
    setSnapErr(null);
    api
      .snapshot(caseId, k)
      .then((s) => !cancelled && setSnaps((m) => ({ ...m, [k]: s })))
      .catch((e) => !cancelled && setSnapErr(e));
    return () => {
      cancelled = true;
    };
  }, [caseId, k, mode, snaps]);

  const table = chat.table;
  const tableRecs = useMemo(() => records.filter((r) => r.table === table), [records, table]);
  const byId = useMemo(() => new Map(records.map((r) => [r.id, r])), [records]);
  const cmp = useMemo(() => compareByTsRowid(chat.timestamp), [chat.timestamp]);
  const chatChanges = useMemo(() => changes.filter((c) => c.table === table), [changes, table]);

  const senders = useMemo(() => {
    const seen: string[] = [];
    for (const r of [...tableRecs].sort(cmp)) {
      const s = String(rawValue(r, chat.sender) ?? "");
      if (s && !seen.includes(s)) seen.push(s);
    }
    return seen;
  }, [tableRecs, chat.sender, cmp]);

  const priorsFor = (rec: RecoveredRecord, maxLastSeen: number | null): RecoveredRecord[] =>
    rec.rowid === null
      ? []
      : tableRecs
          .filter(
            (p) =>
              p.status === "prior_version" &&
              p.rowid === rec.rowid &&
              p.id !== rec.id &&
              (maxLastSeen === null || (p.last_seen_commit !== null && p.last_seen_commit < maxLastSeen)),
          )
          .sort((a, b) => (a.first_seen_commit ?? -1) - (b.first_seen_commit ?? -1));

  const snap = snaps[k];
  const items: Item[] | null = useMemo(() => {
    if (mode === "all") {
      const main = tableRecs.filter((r) => r.status === "live" || r.status === "deleted");
      const mainRowids = new Set(main.map((r) => r.rowid));
      const orphans = tableRecs.filter((r) => r.status === "prior_version" && !mainRowids.has(r.rowid));
      const out: Item[] = main.map((r) => {
        // attach priors to the live row; to a deleted row only if no live row shares the rowid
        const hasLive = main.some((x) => x.status === "live" && x.rowid === r.rowid);
        return { rec: r, priors: r.status === "live" || !hasLive ? priorsFor(r, null) : [] };
      });
      // orphan priors with the same rowid collapse onto the newest one
      const orphanByRow = new Map<string, RecoveredRecord[]>();
      for (const o of orphans) {
        const key = String(o.rowid ?? o.id);
        orphanByRow.set(key, [...(orphanByRow.get(key) ?? []), o]);
      }
      for (const group of orphanByRow.values()) {
        group.sort((a, b) => (a.last_seen_commit ?? -1) - (b.last_seen_commit ?? -1));
        const head = group[group.length - 1];
        out.push({ rec: head, priors: group.slice(0, -1) });
      }
      return out.sort((a, b) => cmp(a.rec, b.rec));
    }
    if (!snap) return null;
    const ids = snap.tables[table] ?? [];
    const atK = chatChanges.filter((c) => c.commit === k);
    const out: Item[] = [];
    for (const id of ids) {
      const rec = byId.get(id);
      if (!rec) continue;
      out.push({
        rec,
        priors: priorsFor(rec, k),
        inserted: atK.find((c) => c.kind === "insert" && c.after?.record_id === id),
        edited: atK.find((c) => c.kind === "update" && c.after?.record_id === id),
      });
    }
    for (const c of atK) {
      if (c.kind !== "delete" || !c.before) continue;
      const rec: RecoveredRecord =
        byId.get(c.before.record_id) ??
        ({
          id: c.before.record_id,
          table,
          rowid: c.rowid,
          rowid_inferred: false,
          status: "deleted",
          values: c.before.values,
          confidence: 0,
          confidence_reasons: [],
          provenance: [],
          first_seen_commit: null,
          last_seen_commit: k - 1,
          reconstructed: false,
        } satisfies RecoveredRecord);
      out.push({ rec, ghost: c, priors: [] });
    }
    return out.sort((a, b) => cmp(a.rec, b.rec));
  }, [mode, snap, tableRecs, chatChanges, byId, k, cmp, table]);

  const commit = commits.find((c) => c.index === k);
  const approximate = !!(commit?.approximate || (mode === "asof" && snap?.approximate));
  const atK = chatChanges.filter((c) => c.commit === k);

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
      <div className="min-w-0">
        <div className="card mb-3 p-3">
          <div className="flex flex-wrap items-center gap-3">
            <div className="inline-flex rounded-md border border-slate-300 p-0.5 text-sm dark:border-slate-700">
              {(
                [
                  ["asof", "As of commit"],
                  ["all", "Everything recovered"],
                ] as const
              ).map(([m, label]) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={classNames(
                    "rounded px-3 py-1 font-medium",
                    mode === m
                      ? "bg-slate-900 text-white dark:bg-slate-200 dark:text-slate-900"
                      : "text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            <span className="text-xs text-slate-500">
              {mode === "asof"
                ? "Exactly the rows live in snapshot k (main file + WAL frames through commit k)."
                : "Live, deleted and earlier versions merged. Stale copies hidden."}
            </span>
          </div>
          {mode === "asof" && (
            <TimelineSlider commits={commits} k={k} setK={setK} changes={chatChanges} />
          )}
          {mode === "asof" && approximate && (
            <div className="mt-2 rounded border border-amber-400/60 bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
              Approximate snapshot: some pages for this commit could not be fully verified.
            </div>
          )}
        </div>

        {snapErr ? <ErrorBox error={snapErr} /> : null}
        {!items ? (
          <div className="flex items-center gap-2 p-6 text-sm text-slate-500">
            <Spinner /> Loading snapshot {k}…
          </div>
        ) : items.length === 0 ? (
          <div className="card p-6 text-center text-sm text-slate-500">No messages in this view.</div>
        ) : (
          <div className="card space-y-2.5 p-3 sm:p-5">
            {items.map((it) => (
              <Bubble
                key={it.rec.id + (it.ghost ? "-ghost" : "")}
                item={it}
                chat={chat}
                side={senders.indexOf(String(rawValue(it.rec, chat.sender) ?? "")) <= 0 ? "left" : "right"}
                mode={mode}
                k={k}
              />
            ))}
          </div>
        )}
      </div>

      <aside className="space-y-3">
        {mode === "asof" && (
          <div className="card p-3">
            <div className="label mb-2">Commit {k} changes</div>
            {k === 0 && <p className="mb-2 text-xs text-slate-500">Commit 0 is the main database file alone, before any WAL commit.</p>}
            {atK.length === 0 ? (
              <p className="text-xs text-slate-500">No changes to {table} in this commit.</p>
            ) : (
              <ul className="space-y-2">
                {atK.map((c, i) => (
                  <ChangeLine key={i} c={c} chat={chat} byId={byId} />
                ))}
              </ul>
            )}
            {commit && k > 0 && (
              <p className="mt-2 font-mono text-[11px] text-slate-500">
                WAL frames {commit.first_frame}–{commit.last_frame} · db {commit.db_size_pages} pages
              </p>
            )}
          </div>
        )}
        <div className="card p-3 text-xs text-slate-600 dark:text-slate-400">
          <div className="label mb-2">Reading this view</div>
          <ul className="space-y-1.5">
            <li>Click any message to see the exact bytes it was decoded from.</li>
            <li>
              <span className="rounded border border-red-500 px-1 text-red-600 dark:text-red-400">red outline</span> = deleted,
              recovered from residue.
            </li>
            <li>WAL commits are ordered, not timestamped. Changes are bracketed by the app's own message times.</li>
          </ul>
        </div>
      </aside>
    </div>
  );
}

function ChangeLine({ c, chat, byId }: { c: Change; chat: ChatMapping; byId: Map<string, RecoveredRecord> }) {
  const hexOpen = useHex();
  const body = (vals: { values: RecoveredRecord["values"] } | null) => (vals ? String(rawValue(vals, chat.body) ?? "") : "");
  const tone = c.kind === "delete" ? "red" : c.kind === "update" ? "amber" : "green";
  return (
    <li className="text-xs">
      <div className="flex items-center gap-1.5">
        <Tag tone={tone}>{c.kind}</Tag>
        <span className="font-mono text-slate-500">rowid {c.rowid}</span>
      </div>
      {c.kind === "update" ? (
        <div className="mt-1">
          <span className="text-slate-500 line-through">{body(c.before)}</span>
          <span className="mx-1 text-slate-400">→</span>
          <span>{body(c.after)}</span>
        </div>
      ) : (
        <div className="mt-1 line-clamp-2">{body(c.after ?? c.before)}</div>
      )}
      {c.kind !== "insert" && <div className="mt-1 text-slate-500">{bracketText(c.bracket, c.kind === "delete" ? "Deleted" : "Edited")}.</div>}
      {(c.after ?? c.before) && (
        <button
          className="mt-1 font-mono text-[10px] text-sky-600 hover:underline dark:text-sky-400"
          onClick={() => {
            const ref = c.after ?? c.before;
            const r = ref ? byId.get(ref.record_id) : undefined;
            if (r) hexOpen({ kind: "record", record: r });
          }}
        >
          {(c.after ?? c.before)?.record_id} bytes →
        </button>
      )}
    </li>
  );
}

function TimelineSlider({
  commits,
  k,
  setK,
  changes,
}: {
  commits: Commit[];
  k: number;
  setK: (n: number) => void;
  changes: Change[];
}) {
  const max = commits.length ? commits[commits.length - 1].index : 0;
  const kinds = (i: number) => new Set(changes.filter((c) => c.commit === i).map((c) => c.kind));
  return (
    <div className="mt-3">
      <div className="flex items-center gap-2">
        <button className="btn-ghost px-2 py-1" onClick={() => setK(Math.max(0, k - 1))} disabled={k <= 0} aria-label="Previous commit">
          ◀
        </button>
        <div className="relative flex-1 px-1">
          <input
            type="range"
            min={0}
            max={max}
            step={1}
            value={k}
            onChange={(e) => setK(Number(e.target.value))}
            className="w-full accent-sky-500"
            aria-label="Commit"
          />
          <div className="relative mt-0.5 h-3">
            {commits.map((c) => {
              const ks = kinds(c.index);
              const color = ks.has("delete")
                ? "bg-red-500"
                : ks.has("update")
                  ? "bg-amber-500"
                  : ks.has("insert")
                    ? "bg-emerald-500"
                    : "bg-slate-400";
              const left = max === 0 ? 0 : (c.index / max) * 100;
              return (
                <button
                  key={c.index}
                  title={`Commit ${c.index}${ks.size ? ": " + [...ks].join(", ") : ""}`}
                  onClick={() => setK(c.index)}
                  className={classNames(
                    "absolute top-0 h-2 w-2 -translate-x-1/2 rounded-full",
                    color,
                    c.index === k && "ring-2 ring-sky-400 ring-offset-1 ring-offset-white dark:ring-offset-slate-900",
                  )}
                  style={{ left: `calc(8px + (100% - 16px) * ${left / 100})` }}
                />
              );
            })}
          </div>
        </div>
        <button className="btn-ghost px-2 py-1" onClick={() => setK(Math.min(max, k + 1))} disabled={k >= max} aria-label="Next commit">
          ▶
        </button>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 font-mono text-xs">
        <span className="font-semibold">
          commit {k} <span className="font-normal text-slate-500">/ {max}</span>
        </span>
        <span className="text-slate-500">{k === 0 ? "main file baseline" : k === max ? "latest committed state" : ""}</span>
        <span className="ml-auto flex gap-2 text-[10px] text-slate-500">
          <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-emerald-500" />insert</span>
          <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-amber-500" />edit</span>
          <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-red-500" />delete</span>
        </span>
      </div>
    </div>
  );
}

function Bubble({ item, chat, side, mode, k }: { item: Item; chat: ChatMapping; side: "left" | "right"; mode: Mode; k: number }) {
  const hexOpen = useHex();
  const [open, setOpen] = useState(false);
  const { rec, ghost, inserted, edited, priors } = item;
  const sender = String(rawValue(rec, chat.sender) ?? "unknown");
  const body = rawValue(rec, chat.body);
  const ts = numValue(rec, chat.timestamp);
  const deleted = rec.status === "deleted" && (mode === "all" || !!ghost);
  const isPrior = rec.status === "prior_version";
  const laterDeleted =
    mode === "asof" && !ghost && rec.status === "deleted" && rec.last_seen_commit !== null && rec.last_seen_commit >= k;
  const laterEdited = mode === "asof" && !ghost && isPrior;

  const tags: ReactNode[] = [];
  if (ghost) tags.push(<Tag key="g" tone="red">Deleted @{ghost.commit}</Tag>);
  else if (deleted) tags.push(<Tag key="d" tone="red">Deleted</Tag>);
  if (inserted) tags.push(<Tag key="i" tone="green">Inserted @{inserted.commit}</Tag>);
  if (edited) tags.push(<Tag key="e" tone="amber">Edited @{edited.commit}</Tag>);
  if (mode === "all" && isPrior) tags.push(<Tag key="p" tone="amber">Prior version</Tag>);
  if (laterDeleted) tags.push(<Tag key="ld" title="This row is gone in a later commit">deleted after commit {rec.last_seen_commit}</Tag>);
  if (laterEdited) tags.push(<Tag key="le" title="This text is replaced in a later commit">edited after commit {rec.last_seen_commit}</Tag>);

  const showEvidence = deleted || ghost || rec.status !== "live" || rec.reconstructed;

  return (
    <div className={classNames("flex", side === "right" ? "justify-end" : "justify-start")}>
      <div className={classNames("flex max-w-[85%] flex-col sm:max-w-[72%]", side === "right" ? "items-end" : "items-start")}>
        <div className="mb-0.5 flex items-center gap-2 px-1 text-[11px] text-slate-500">
          <span className="font-semibold text-slate-700 dark:text-slate-300">{sender}</span>
          <span className="font-mono">{formatHM(ts)}</span>
          <span className="font-mono text-slate-400">#{rec.rowid ?? "?"}</span>
        </div>
        <button
          onClick={() => rec.provenance.length && hexOpen({ kind: "record", record: rec })}
          title="Show bytes"
          className={classNames(
            "group rounded-2xl px-3.5 py-2 text-left text-[14px] leading-snug transition-shadow hover:shadow-md",
            side === "right"
              ? "rounded-br-sm bg-sky-600 text-white dark:bg-sky-700"
              : "rounded-bl-sm bg-slate-100 text-slate-900 dark:bg-slate-800 dark:text-slate-100",
            (deleted || ghost) &&
              "!bg-red-50 !text-red-950 outline outline-2 outline-red-500 dark:!bg-red-950/40 dark:!text-red-100",
            ghost && "opacity-75",
            isPrior && mode === "all" && "outline outline-1 outline-amber-500",
          )}
        >
          <span className={classNames("whitespace-pre-wrap break-words", ghost && "line-through decoration-red-500/70")}>
            {body === null ? <i className="opacity-60">(no body)</i> : String(body)}
          </span>
          {edited?.before && (
            <span className="mt-1 block text-[12px] opacity-80">
              was: <span className="line-through">{String(rawValue(edited.before, chat.body) ?? "")}</span>
            </span>
          )}
        </button>
        <div className={classNames("mt-1 flex flex-wrap items-center gap-1 px-1", side === "right" && "justify-end")}>
          {tags}
          {priors.length > 0 && (
            <button onClick={() => setOpen((o) => !o)} className="pill bg-amber-100 text-amber-800 hover:bg-amber-200 dark:bg-amber-500/15 dark:text-amber-300">
              Edited {open ? "▾" : "▸"} {priors.length} earlier
            </button>
          )}
          {showEvidence && <RecordBadges r={rec} showStatus={false} />}
        </div>
        {ghost && (
          <div className="mt-1 px-1 text-[11px] text-red-700 dark:text-red-300">{bracketText(ghost.bracket, "Deleted")}.</div>
        )}
        {open && priors.length > 0 && (
          <ul className="mt-1.5 space-y-1 border-l-2 border-amber-400 pl-2">
            {priors.map((p) => (
              <li key={p.id}>
                <button
                  onClick={() => hexOpen({ kind: "record", record: p })}
                  className="text-left text-[13px] text-slate-500 line-through hover:text-slate-800 dark:hover:text-slate-200"
                >
                  {String(rawValue(p, chat.body) ?? "")}
                </button>
                <div className="font-mono text-[10px] text-slate-500">
                  {p.id} · commits {p.first_seen_commit ?? "?"}–{p.last_seen_commit ?? "?"} ·{" "}
                  {p.provenance[0]?.source ?? ""}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
