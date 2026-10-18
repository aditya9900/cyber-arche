// BLUEPRINT F3: hex view with 64 bytes of context, segment coloring, hover-decoded values.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Provenance, RecoveredRecord, Segment } from "../contracts";
import { fetchHexRange } from "../api";
import { classNames, displayValue, hex, hexPlain, provenanceLabel, segmentValueText } from "../lib/format";
import { ConfidencePill, ErrorBox, RecordBadges, Spinner, StatusBadge } from "./ui";

export type HexTarget =
  | { kind: "record"; record: RecoveredRecord; prov?: number }
  | { kind: "raw"; file: string; offset: number; length: number; title?: string };

const HexCtx = createContext<(t: HexTarget) => void>(() => {});
export const useHex = () => useContext(HexCtx);

export function HexProvider({ caseId, children }: { caseId: string; children: ReactNode }) {
  const [target, setTarget] = useState<HexTarget | null>(null);
  const open = useCallback((t: HexTarget) => setTarget(t), []);
  return (
    <HexCtx.Provider value={open}>
      {children}
      {target && <HexDrawer caseId={caseId} target={target} onClose={() => setTarget(null)} />}
    </HexCtx.Provider>
  );
}

const CONTEXT = 64;
const RAW_MAX = 2048;

// rgb triplets; used with alpha so they read on light and dark backgrounds
const KIND_RGB = {
  payload_size: "148,163,184",
  rowid: "245,158,11",
  record_header: "167,139,250",
} as const;
const COLUMN_RGB = [
  "56,189,248",
  "16,185,129",
  "236,72,153",
  "249,115,22",
  "20,184,166",
  "132,204,22",
  "217,70,239",
  "99,102,241",
];

function segRgb(s: Segment, colIndex: Map<string, number>): string | null {
  if (s.kind === "damaged") return null;
  if (s.kind === "column") return COLUMN_RGB[(colIndex.get(s.column ?? "") ?? 0) % COLUMN_RGB.length];
  return KIND_RGB[s.kind];
}

function segName(s: Segment): string {
  switch (s.kind) {
    case "damaged":
      return "damaged (overwritten)";
    case "payload_size":
      return "payload size varint";
    case "rowid":
      return "rowid varint";
    case "record_header":
      return "record header";
    case "column":
      return `column ${s.column ?? "?"}`;
  }
}

interface Hover {
  off: number;
  x: number;
  y: number; // bottom of the byte
  top: number; // top of the byte
  above: boolean;
}

function HexDrawer({ caseId, target, onClose }: { caseId: string; target: HexTarget; onClose: () => void }) {
  const record = target.kind === "record" ? target.record : null;
  const [provIdx, setProvIdx] = useState(target.kind === "record" ? (target.prov ?? 0) : 0);
  useEffect(() => setProvIdx(target.kind === "record" ? (target.prov ?? 0) : 0), [target]);
  const prov: Provenance | null = record ? (record.provenance[provIdx] ?? record.provenance[0] ?? null) : null;

  const file = prov ? prov.file : target.kind === "raw" ? target.file : "main";
  const focusStart = prov ? prov.file_offset : target.kind === "raw" ? target.offset : 0;
  const focusLen = prov
    ? prov.length
    : target.kind === "raw"
      ? Math.min(target.length, RAW_MAX)
      : 0;
  const truncated = target.kind === "raw" && target.length > RAW_MAX;
  const ctxStart = Math.max(0, focusStart - CONTEXT);
  const ctxEnd = focusStart + focusLen + CONTEXT;

  const [data, setData] = useState<{ offset: number; bytes: Uint8Array; fileSize: number } | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    let cancelled = false;
    setData(null);
    setErr(null);
    fetchHexRange(caseId, file, ctxStart, ctxEnd - ctxStart)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setErr(e));
    return () => {
      cancelled = true;
    };
  }, [caseId, file, ctxStart, ctxEnd]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const segments = prov?.segments ?? [];
  const colIndex = useMemo(() => {
    const m = new Map<string, number>();
    (record?.values ?? []).forEach((v, i) => m.set(v.column, i));
    for (const s of segments) if (s.kind === "column" && s.column && !m.has(s.column)) m.set(s.column, m.size);
    return m;
  }, [record, segments]);
  const segAt = useMemo(() => {
    const m = new Map<number, number>();
    segments.forEach((s, i) => {
      for (let o = s.start; o < s.start + s.length; o++) m.set(o, i);
    });
    return m;
  }, [segments]);

  const [hover, setHover] = useState<Hover | null>(null);
  const [hoverCol, setHoverCol] = useState<string | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const hoverSegIdx = hover ? segAt.get(hover.off) : undefined;
  const activeSeg =
    hoverSegIdx !== undefined
      ? hoverSegIdx
      : hoverCol !== null
        ? segments.findIndex((s) => s.kind === "column" && s.column === hoverCol)
        : -1;

  const rows: number[] = [];
  if (data) {
    const first = Math.floor(data.offset / 16) * 16;
    const last = data.offset + data.bytes.length;
    for (let r = first; r < last; r += 16) rows.push(r);
  }
  const byteAt = (off: number): number | null => {
    if (!data) return null;
    const i = off - data.offset;
    return i >= 0 && i < data.bytes.length ? data.bytes[i] : null;
  };
  const inFocus = (off: number) => off >= focusStart && off < focusStart + focusLen;

  const byteStyle = (off: number): { cls: string; style?: React.CSSProperties } => {
    const si = segAt.get(off);
    const focus = inFocus(off);
    const dim = !focus ? "opacity-35" : "";
    if (si === undefined) {
      if (focus && target.kind === "raw") return { cls: dim, style: { backgroundColor: "rgba(56,189,248,0.22)" } };
      return { cls: classNames(dim, focus && "outline outline-1 -outline-offset-1 outline-slate-400/40") };
    }
    const s = segments[si];
    const rgb = segRgb(s, colIndex);
    const active = si === activeSeg;
    return {
      cls: classNames(dim, s.kind === "damaged" && "hatch text-red-900 dark:text-red-100", active && "ring-2 ring-inset ring-slate-900 dark:ring-white"),
      style: rgb ? { backgroundColor: `rgba(${rgb},${active ? 0.6 : 0.32})` } : undefined,
    };
  };

  const onEnter = (off: number) => (e: React.MouseEvent<HTMLSpanElement>) => {
    const el = e.currentTarget;
    const grid = gridRef.current;
    if (!grid) return;
    const r = el.getBoundingClientRect();
    const g = grid.getBoundingClientRect();
    const top = r.top - g.top + grid.scrollTop;
    setHover({
      off,
      x: r.left - g.left + grid.scrollLeft + r.width / 2,
      y: r.bottom - g.top + grid.scrollTop,
      top,
      above: top > grid.scrollHeight / 2,
    });
  };

  const hovered = hover ? segAt.get(hover.off) : undefined;
  const hoverSeg = hovered !== undefined ? segments[hovered] : null;
  const hoverByte = hover ? byteAt(hover.off) : null;

  const legend: { label: string; style?: React.CSSProperties; cls?: string }[] = [];
  if (segments.some((s) => s.kind === "damaged")) legend.push({ label: "damaged", cls: "hatch" });
  (["payload_size", "rowid", "record_header"] as const).forEach((k) => {
    if (segments.some((s) => s.kind === k))
      legend.push({ label: k.replace("_", " "), style: { backgroundColor: `rgba(${KIND_RGB[k]},0.45)` } });
  });
  for (const [col, i] of colIndex)
    if (segments.some((s) => s.kind === "column" && s.column === col))
      legend.push({ label: col, style: { backgroundColor: `rgba(${COLUMN_RGB[i % COLUMN_RGB.length]},0.45)` } });

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-[1px]" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-[780px] flex-col border-l border-slate-200 bg-white shadow-2xl dark:border-slate-800 dark:bg-[#0d121c]">
        <div className="flex items-start gap-3 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
          <div className="min-w-0 flex-1">
            <div className="label">Hex provenance</div>
            {record ? (
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm font-semibold">{record.id}</span>
                <span className="font-mono text-xs text-slate-500">
                  {record.table} · rowid {record.rowid ?? "?"}
                  {record.rowid_inferred ? " (inferred)" : ""}
                </span>
                <RecordBadges r={record} />
              </div>
            ) : (
              <div className="mt-1 font-mono text-sm font-semibold">
                {target.kind === "raw" && (target.title ?? "Evidence bytes")}
              </div>
            )}
          </div>
          <button className="btn-ghost px-2 py-1" onClick={onClose} aria-label="Close hex viewer">
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-3">
          {record && record.provenance.length > 1 && (
            <div className="mb-3">
              <div className="label mb-1">Locations ({record.provenance.length})</div>
              <div className="flex flex-col gap-1">
                {record.provenance.map((p, i) => (
                  <button
                    key={i}
                    onClick={() => setProvIdx(i)}
                    className={classNames(
                      "rounded border px-2 py-1 text-left font-mono text-xs",
                      i === provIdx
                        ? "border-sky-500 bg-sky-50 text-sky-900 dark:bg-sky-500/10 dark:text-sky-200"
                        : "border-slate-200 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50",
                    )}
                  >
                    {i === 0 && <span className="mr-1 text-[10px] uppercase text-slate-500">strongest</span>}
                    {p.file} · {provenanceLabel(p)}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="font-mono text-sm font-semibold text-sky-700 dark:text-sky-300">
              {prov
                ? provenanceLabel(prov)
                : `${file} file, offset ${hex(focusStart)}, ${target.kind === "raw" ? target.length : 0} bytes`}
            </span>
            <span className="font-mono text-xs text-slate-500">
              file <b>{file}</b> · abs {hex(focusStart, 6)}–{hex(focusStart + focusLen, 6)} · {focusLen} bytes
              {data ? ` · file size ${data.fileSize}` : ""}
            </span>
          </div>
          {truncated && (
            <p className="mb-2 text-xs text-amber-600">Showing the first {RAW_MAX} of {target.length} bytes.</p>
          )}

          {err ? <ErrorBox error={err} /> : null}
          {!data && !err && (
            <div className="flex items-center gap-2 py-8 text-sm text-slate-500">
              <Spinner /> Reading bytes…
            </div>
          )}
          {data && (
            <div
              ref={gridRef}
              className="hexgrid relative overflow-x-auto rounded-md border border-slate-200 bg-slate-50 p-2 font-mono text-[12px] leading-[1.55] dark:border-slate-800 dark:bg-[#080b12]"
              onMouseLeave={() => setHover(null)}
            >
              <div className="w-max">
                {rows.map((rs) => (
                  <div key={rs} className="flex whitespace-pre">
                    <span className="mr-3 select-none text-slate-400 dark:text-slate-600">{hexPlain(rs, 8)}</span>
                    <span className="flex">
                      {Array.from({ length: 16 }, (_, i) => {
                        const off = rs + i;
                        const b = byteAt(off);
                        const st = b === null ? { cls: "" } : byteStyle(off);
                        return (
                          <span
                            key={i}
                            className={classNames("inline-block w-[2.6ch] text-center", i === 8 && "ml-[1ch]", st.cls)}
                            style={st.style}
                            onMouseEnter={b === null ? undefined : onEnter(off)}
                          >
                            {b === null ? "  " : b.toString(16).padStart(2, "0")}
                          </span>
                        );
                      })}
                    </span>
                    <span className="ml-3 flex border-l border-slate-300 pl-3 dark:border-slate-700">
                      {Array.from({ length: 16 }, (_, i) => {
                        const off = rs + i;
                        const b = byteAt(off);
                        const st = b === null ? { cls: "" } : byteStyle(off);
                        const ch = b === null ? " " : b >= 0x20 && b < 0x7f ? String.fromCharCode(b) : "·";
                        return (
                          <span
                            key={i}
                            className={classNames("inline-block w-[1ch] text-center", st.cls, b !== null && (b < 0x20 || b >= 0x7f) && "text-slate-400 dark:text-slate-600")}
                            style={st.style}
                            onMouseEnter={b === null ? undefined : onEnter(off)}
                          >
                            {ch}
                          </span>
                        );
                      })}
                    </span>
                  </div>
                ))}
              </div>
              {hover && hoverByte !== null && (
                <div
                  className="pointer-events-none absolute z-10 w-max max-w-[320px] -translate-x-1/2 rounded-md border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-[11px] text-slate-100 shadow-lg"
                  style={
                    hover.above
                      ? { left: Math.max(150, hover.x), top: hover.top - 4, transform: "translate(-50%, -100%)" }
                      : { left: Math.max(150, hover.x), top: hover.y + 4 }
                  }
                >
                  <div className="text-slate-400">
                    {hex(hover.off, 6)} = 0x{hoverByte.toString(16).padStart(2, "0")} ({hoverByte})
                    {!inFocus(hover.off) && " · context"}
                  </div>
                  {hoverSeg ? (
                    <>
                      <div className="font-semibold">{segName(hoverSeg)}</div>
                      <div className="text-slate-300">
                        bytes {hex(hoverSeg.start, 6)}+{hoverSeg.length}
                      </div>
                      {segmentValueText(hoverSeg) && (
                        <div className="mt-0.5 break-words font-mono text-emerald-300">{segmentValueText(hoverSeg)}</div>
                      )}
                    </>
                  ) : (
                    inFocus(hover.off) && <div className="text-slate-300">inside range (no segment)</div>
                  )}
                </div>
              )}
            </div>
          )}

          {legend.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
              {legend.map((l) => (
                <span key={l.label} className="inline-flex items-center gap-1 font-mono text-slate-600 dark:text-slate-400">
                  <span className={classNames("inline-block h-3 w-3 rounded-sm", l.cls)} style={l.style} />
                  {l.label}
                </span>
              ))}
              <span className="inline-flex items-center gap-1 font-mono text-slate-500">
                <span className="inline-block h-3 w-3 rounded-sm bg-slate-400 opacity-35" />
                64-byte context
              </span>
            </div>
          )}

          {record && (
            <div className="mt-5 grid gap-5 md:grid-cols-[1fr_1fr]">
              <section>
                <div className="label mb-1.5">Decoded values</div>
                <table className="w-full text-left text-xs">
                  <tbody>
                    {record.values.map((v) => {
                      const ci = colIndex.get(v.column) ?? 0;
                      return (
                        <tr
                          key={v.column}
                          className="border-b border-slate-100 align-top last:border-0 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/40"
                          onMouseEnter={() => setHoverCol(v.column)}
                          onMouseLeave={() => setHoverCol(null)}
                        >
                          <td className="whitespace-nowrap py-1 pr-2 font-mono">
                            <span
                              className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm"
                              style={{ backgroundColor: `rgba(${COLUMN_RGB[ci % COLUMN_RGB.length]},0.6)` }}
                            />
                            {v.column}
                          </td>
                          <td className="py-1 pr-2 font-mono text-slate-500">{v.type}</td>
                          <td className="break-all py-1">
                            {displayValue(v)}
                            {v.reconstructed && <span className="ml-1 text-[10px] text-violet-500">(reconstructed)</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </section>
              <section>
                <div className="label mb-1.5 flex items-center gap-2">
                  Confidence <ConfidencePill value={record.confidence} />
                  <StatusBadge status={record.status} />
                </div>
                <ul className="space-y-1 text-xs text-slate-600 dark:text-slate-400">
                  {record.confidence_reasons.map((r, i) => (
                    <li key={i} className="flex gap-1.5">
                      <span className="text-slate-400">•</span>
                      {r}
                    </li>
                  ))}
                </ul>
                <div className="mt-3 font-mono text-[11px] text-slate-500">
                  first seen commit {record.first_seen_commit ?? "–"} · last seen commit {record.last_seen_commit ?? "–"}
                </div>
              </section>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
