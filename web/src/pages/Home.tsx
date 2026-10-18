import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { CaseManifest } from "../contracts";
import { api } from "../api";
import { formatBytes, formatDateTime, shortSha } from "../lib/format";
import { ErrorBox, Loading, Spinner, TopBar } from "../components/ui";

export default function Home() {
  const [cases, setCases] = useState<CaseManifest[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    api.listCases().then(setCases).catch(setErr);
  }, []);

  return (
    <>
      <TopBar />
      <main className="mx-auto max-w-6xl px-4 pb-16 pt-8">
        <section className="mb-8 grid gap-6 md:grid-cols-[1.4fr_1fr] md:items-center">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
              Deleted isn't gone.
            </h1>
            <p className="mt-3 max-w-xl text-slate-600 dark:text-slate-400">
              Cyber Arche recovers deleted and edited SQLite records from the main file, WAL and journal, and shows the
              exact bytes every record was decoded from. Evidence is parsed read-only and hashed on intake.
            </p>
          </div>
          <Link
            to="/demo"
            className="group block rounded-2xl bg-gradient-to-br from-fuchsia-500 via-pink-500 to-orange-400 p-[2px] shadow-lg transition-transform hover:-translate-y-0.5"
          >
            <div className="rounded-[14px] bg-white/95 p-5 dark:bg-slate-950/90">
              <div className="font-chat text-xs font-extrabold uppercase tracking-wider text-pink-600">Live judge demo</div>
              <div className="mt-1 font-chat text-xl font-extrabold">Open the suspect's chat app →</div>
              <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
                Send, edit and delete messages, then seize the device and watch them come back.
              </p>
            </div>
          </Link>
        </section>

        <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
          <section>
            <h2 className="label mb-2">Cases</h2>
            {err ? <ErrorBox error={err} /> : null}
            {!cases && !err && <Loading label="Loading cases" />}
            {cases && cases.length === 0 && (
              <div className="card p-6 text-sm text-slate-500">No cases yet. Upload evidence or seize the demo device.</div>
            )}
            <div className="space-y-2">
              {cases?.map((c) => (
                <Link
                  key={c.case_id}
                  to={`/cases/${encodeURIComponent(c.case_id)}`}
                  className="card block p-4 transition-colors hover:border-sky-500/60"
                >
                  <div className="flex flex-wrap items-baseline gap-x-3">
                    <span className="font-semibold">{c.label || "Untitled case"}</span>
                    <span className="font-mono text-xs text-slate-500">{c.case_id}</span>
                    <span className="ml-auto text-xs text-slate-500">{formatDateTime(c.created)}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {c.files.map((f) => (
                      <span
                        key={f.role + f.name}
                        className="rounded border border-slate-200 px-2 py-0.5 font-mono text-[11px] text-slate-600 dark:border-slate-800 dark:text-slate-400"
                        title={f.sha256}
                      >
                        <span className="uppercase text-slate-400">{f.role}</span> {f.name} · {formatBytes(f.bytes)} ·{" "}
                        {shortSha(f.sha256)}…
                      </span>
                    ))}
                  </div>
                </Link>
              ))}
            </div>
          </section>
          <UploadForm onCreated={(c) => setCases((cs) => [c, ...(cs ?? [])])} />
        </div>
      </main>
    </>
  );
}

function FileField({ label, required, onChange, file }: { label: string; required?: boolean; onChange: (f: File | null) => void; file: File | null }) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-600 dark:text-slate-400">
        {label} {required ? <span className="text-red-500">*</span> : <span className="text-slate-400">(optional)</span>}
      </span>
      <input
        type="file"
        required={required}
        onChange={(e) => onChange(e.target.files?.[0] ?? null)}
        className="mt-1 block w-full text-xs text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-slate-200 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-slate-800 hover:file:bg-slate-300 dark:text-slate-400 dark:file:bg-slate-800 dark:file:text-slate-200"
      />
      {file && <span className="mt-0.5 block font-mono text-[11px] text-slate-500">{formatBytes(file.size)}</span>}
    </label>
  );
}

function UploadForm({ onCreated }: { onCreated: (c: CaseManifest) => void }) {
  const nav = useNavigate();
  const [db, setDb] = useState<File | null>(null);
  const [wal, setWal] = useState<File | null>(null);
  const [journal, setJournal] = useState<File | null>(null);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!db) return;
    setBusy(true);
    setErr(null);
    try {
      const c = await api.uploadCase({ db, wal, journal, label: label.trim() });
      onCreated(c);
      nav(`/cases/${encodeURIComponent(c.case_id)}`);
    } catch (ex) {
      setErr(ex);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="card h-fit space-y-3 p-4">
      <h2 className="label">New case from files</h2>
      <label className="block">
        <span className="text-xs font-medium text-slate-600 dark:text-slate-400">Label</span>
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="e.g. Exhibit 4: chat.db"
          className="mt-1 w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
        />
      </label>
      <FileField label="Database (.db)" required file={db} onChange={setDb} />
      <FileField label="WAL (-wal)" file={wal} onChange={setWal} />
      <FileField label="Rollback journal (-journal)" file={journal} onChange={setJournal} />
      {err ? <ErrorBox error={err} /> : null}
      <button className="btn-primary w-full justify-center" disabled={!db || busy}>
        {busy && <Spinner />} Hash and analyze
      </button>
      <p className="text-[11px] text-slate-500">Files are hashed with SHA-256 on intake and stored read-only.</p>
    </form>
  );
}
