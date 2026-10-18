import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { RecoveredRecord, Source, Status } from "../contracts";
import { SOURCE_GLYPH, SOURCE_LABEL, STATUS_LABEL, classNames, pct } from "../lib/format";
import { USE_MOCKS } from "../api";

export function Spinner({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg className={classNames("animate-spin", className)} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 p-6 text-sm text-slate-500">
      <Spinner /> {label}…
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
      {msg}
    </div>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => setDone(false), 1400);
    return () => clearTimeout(t);
  }, [done]);
  return (
    <button
      type="button"
      className="rounded border border-slate-300 px-1.5 py-0.5 text-[11px] text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => setDone(true));
      }}
      title="Copy to clipboard"
    >
      {done ? "Copied" : label}
    </button>
  );
}

const STATUS_STYLE: Record<Status, string> = {
  live: "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300",
  deleted: "bg-red-100 text-red-800 dark:bg-red-500/20 dark:text-red-300",
  prior_version: "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300",
  stale_copy: "bg-slate-200 text-slate-700 dark:bg-slate-700/60 dark:text-slate-300",
};

export function StatusBadge({ status }: { status: Status }) {
  return <span className={classNames("pill", STATUS_STYLE[status])}>{STATUS_LABEL[status]}</span>;
}

export function ConfidencePill({ value }: { value: number }) {
  const tone =
    value >= 0.9
      ? "border-emerald-500/50 text-emerald-700 dark:text-emerald-300"
      : value >= 0.7
        ? "border-sky-500/50 text-sky-700 dark:text-sky-300"
        : value >= 0.5
          ? "border-amber-500/60 text-amber-700 dark:text-amber-300"
          : "border-red-500/60 text-red-700 dark:text-red-300";
  return (
    <span className={classNames("pill border font-mono", tone)} title="Confidence">
      {pct(value)}
    </span>
  );
}

export function SourceTag({ source }: { source: Source }) {
  return (
    <span
      className="pill border border-slate-300 font-mono text-slate-600 dark:border-slate-700 dark:text-slate-400"
      title={`Recovered from: ${source}`}
    >
      <span aria-hidden>{SOURCE_GLYPH[source]}</span>
      {SOURCE_LABEL[source]}
    </span>
  );
}

export function Tag({ children, tone = "slate", title }: { children: ReactNode; tone?: string; title?: string }) {
  const tones: Record<string, string> = {
    slate: "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
    violet: "bg-violet-100 text-violet-800 dark:bg-violet-500/15 dark:text-violet-300",
    amber: "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300",
    red: "bg-red-100 text-red-800 dark:bg-red-500/20 dark:text-red-300",
    green: "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300",
    sky: "bg-sky-100 text-sky-800 dark:bg-sky-500/15 dark:text-sky-300",
  };
  return (
    <span className={classNames("pill", tones[tone] ?? tones.slate)} title={title}>
      {children}
    </span>
  );
}

/** The standard badge cluster for a recovered record. */
export function RecordBadges({ r, showStatus = true }: { r: RecoveredRecord; showStatus?: boolean }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {showStatus && <StatusBadge status={r.status} />}
      <ConfidencePill value={r.confidence} />
      {r.provenance[0] && <SourceTag source={r.provenance[0].source} />}
      {r.reconstructed && (
        <Tag tone="violet" title="Damaged bytes were reconstructed from a layout hypothesis">
          reconstructed
        </Tag>
      )}
      {r.rowid_inferred && (
        <Tag tone="amber" title="Rowid bytes were lost; the rowid is inferred">
          rowid inferred
        </Tag>
      )}
    </span>
  );
}

export function TopBar({ children }: { children?: ReactNode }) {
  return (
    <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-[#0b0f17]/90">
      <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-2.5">
        <Link to="/" className="flex items-center gap-2">
          <img src="/favicon.svg" alt="" className="h-6 w-6" />
          <span className="font-mono text-sm font-semibold tracking-tight">cyber arche</span>
        </Link>
        {USE_MOCKS && <Tag tone="amber">mock data</Tag>}
        <div className="ml-auto flex items-center gap-2">{children}</div>
      </div>
    </header>
  );
}
