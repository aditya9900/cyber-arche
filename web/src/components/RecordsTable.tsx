import { useMemo, useState } from "react";
import type { RecoveredRecord, Status, TableInfo } from "../contracts";
import { STATUS_LABEL, classNames, displayValue, formatHM, valueOf } from "../lib/format";
import { useHex } from "./HexViewer";
import { RecordBadges } from "./ui";

const STATUSES: Status[] = ["live", "deleted", "prior_version", "stale_copy"];

export default function RecordsTable({ tables, records, timestampCol }: { tables: TableInfo[]; records: RecoveredRecord[]; timestampCol: string | null }) {
  const hexOpen = useHex();
  const names = useMemo(() => {
    const s = tables.map((t) => t.name);
    for (const r of records) if (!s.includes(r.table)) s.push(r.table);
    return s;
  }, [tables, records]);
  const [table, setTable] = useState<string>(() => {
    const counts = new Map<string, number>();
    for (const r of records) counts.set(r.table, (counts.get(r.table) ?? 0) + 1);
    return [...names].sort((a, b) => (counts.get(b) ?? 0) - (counts.get(a) ?? 0))[0] ?? "";
  });
  const [filter, setFilter] = useState<Set<Status>>(new Set(["live", "deleted", "prior_version"]));

  const inTable = records.filter((r) => r.table === table);
  const rows = inTable.filter((r) => filter.has(r.status));
  const columns = useMemo(() => {
    const info = tables.find((t) => t.name === table);
    const cols = info ? info.columns.map((c) => c.name) : [];
    for (const r of inTable) for (const v of r.values) if (!cols.includes(v.column)) cols.push(v.column);
    return cols;
  }, [tables, table, inTable]);

  const toggle = (s: Status) =>
    setFilter((f) => {
      const n = new Set(f);
      if (n.has(s)) n.delete(s);
      else n.add(s);
      return n;
    });

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="label">Table</span>
        {names.map((n) => (
          <button
            key={n}
            onClick={() => setTable(n)}
            className={classNames(
              "rounded-md border px-2.5 py-1 font-mono text-xs",
              n === table
                ? "border-sky-500 bg-sky-50 text-sky-900 dark:bg-sky-500/10 dark:text-sky-200"
                : "border-slate-300 hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800",
            )}
          >
            {n} <span className="text-slate-500">{records.filter((r) => r.table === n).length}</span>
          </button>
        ))}
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="label">Status</span>
        {STATUSES.map((s) => {
          const n = inTable.filter((r) => r.status === s).length;
          const on = filter.has(s);
          return (
            <button
              key={s}
              onClick={() => toggle(s)}
              className={classNames(
                "rounded-full border px-2.5 py-0.5 text-xs",
                on
                  ? "border-slate-800 bg-slate-800 text-white dark:border-slate-200 dark:bg-slate-200 dark:text-slate-900"
                  : "border-slate-300 text-slate-500 dark:border-slate-700",
              )}
            >
              {STATUS_LABEL[s]} · {n}
            </button>
          );
        })}
      </div>
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-xs dark:border-slate-800 dark:bg-slate-900/60">
            <tr>
              <th className="px-3 py-2 font-mono font-medium text-slate-500">id</th>
              <th className="px-3 py-2 font-mono font-medium text-slate-500">rowid</th>
              {columns.map((c) => (
                <th key={c} className="px-3 py-2 font-mono font-medium text-slate-500">
                  {c}
                </th>
              ))}
              <th className="px-3 py-2 font-medium text-slate-500">evidence</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={columns.length + 3} className="px-3 py-6 text-center text-slate-500">
                  No records match.
                </td>
              </tr>
            )}
            {rows.map((r) => (
              <tr
                key={r.id}
                onClick={() => hexOpen({ kind: "record", record: r })}
                className={classNames(
                  "cursor-pointer border-b border-slate-100 align-top last:border-0 hover:bg-sky-50/60 dark:border-slate-800/80 dark:hover:bg-sky-500/5",
                  r.status === "deleted" && "bg-red-50/60 dark:bg-red-500/5",
                  r.status === "stale_copy" && "opacity-60",
                )}
              >
                <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-slate-500">{r.id}</td>
                <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">
                  {r.rowid ?? "?"}
                  {r.rowid_inferred && <span className="text-amber-600">*</span>}
                </td>
                {columns.map((c) => {
                  const v = valueOf(r, c);
                  const isTs = c === timestampCol && typeof v?.value === "number";
                  return (
                    <td key={c} className={classNames("max-w-[360px] px-3 py-2", v?.type !== "text" && "font-mono text-xs")}>
                      <span className="line-clamp-3 break-words">
                        {displayValue(v)}
                        {isTs && <span className="ml-1 text-slate-500">({formatHM(v!.value as number)})</span>}
                      </span>
                      {v?.reconstructed && <span className="text-[10px] text-violet-500">reconstructed</span>}
                    </td>
                  );
                })}
                <td className="px-3 py-2">
                  <RecordBadges r={r} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-slate-500">Click a row to open its bytes. * rowid inferred.</p>
    </div>
  );
}
