// BLUEPRINT F5 demo chat app: deliberately styled as a friendly consumer messenger (the "suspect's phone").
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { DemoMessage } from "../contracts";
import { api, USE_MOCKS } from "../api";
import { classNames, formatHM } from "../lib/format";
import { Spinner } from "../components/ui";

const MAX = 500;
const AVATAR = ["from-pink-500 to-orange-400", "from-indigo-500 to-cyan-400", "from-emerald-500 to-lime-400"];

export default function DemoChat() {
  const nav = useNavigate();
  const [participants, setParticipants] = useState<string[]>([]);
  const [me, setMe] = useState<string>("");
  const [msgs, setMsgs] = useState<DemoMessage[] | null>(null);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const lastCount = useRef(0);

  const refresh = useCallback(async () => {
    try {
      setMsgs(await api.demo.messages());
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    api.demo
      .state()
      .then((s) => {
        setParticipants(s.participants);
        setMe((m) => m || s.participants[0] || "");
      })
      .catch(() => {
        setParticipants(["Mara", "Theo"]);
        setMe((m) => m || "Mara");
      });
    void refresh();
    const t = setInterval(() => void refresh(), 3000);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    if (msgs && msgs.length !== lastCount.current) {
      lastCount.current = msgs.length;
      endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [msgs]);

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setErr(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const send = (e: FormEvent) => {
    e.preventDefault();
    const body = draft.trim();
    if (!body || !me) return;
    void act("send", async () => {
      await api.demo.send(me, body.slice(0, MAX));
      setDraft("");
    });
  };

  const saveEdit = () => {
    if (!editing) return;
    const body = editing.text.trim();
    if (!body) return;
    void act("edit", async () => {
      await api.demo.edit(editing.id, body.slice(0, MAX));
      setEditing(null);
    });
  };

  const seize = async () => {
    setBusy("seize");
    setErr(null);
    try {
      const c = await api.demo.seize();
      nav(`/cases/${encodeURIComponent(c.case_id)}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  };

  const reset = () => {
    if (!window.confirm("Reset the chat? This wipes the demo database and reseeds the conversation.")) return;
    void act("reset", () => api.demo.reset());
  };

  const other = participants.find((p) => p !== me) ?? "";
  const avatar = (name: string) => AVATAR[Math.max(0, participants.indexOf(name)) % AVATAR.length];

  return (
    <div className="min-h-screen bg-gradient-to-br from-rose-100 via-amber-50 to-sky-100 font-chat text-slate-900 dark:from-[#1d1530] dark:via-[#131826] dark:to-[#0c1f2b] dark:text-slate-100">
      <div className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-6 md:flex-row md:items-start md:py-10">
        {/* Phone */}
        <div className="mx-auto w-full max-w-[420px] overflow-hidden rounded-[2rem] border border-white/60 bg-white shadow-2xl ring-8 ring-slate-900/90 dark:border-slate-700 dark:bg-slate-900">
          <div className="flex items-center gap-3 bg-gradient-to-r from-pink-500 to-orange-400 px-4 pb-3 pt-4 text-white">
            <div className="flex -space-x-2">
              {participants.map((p) => (
                <div key={p} className={classNames("flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br text-sm font-extrabold ring-2 ring-white", avatar(p))}>
                  {p.slice(0, 1)}
                </div>
              ))}
            </div>
            <div className="min-w-0">
              <div className="text-base font-extrabold leading-tight">Operation Donut 🍩</div>
              <div className="text-xs opacity-90">{participants.join(", ")}</div>
            </div>
          </div>

          <div className="h-[56vh] min-h-[360px] space-y-2 overflow-y-auto bg-[radial-gradient(circle_at_1px_1px,rgba(0,0,0,0.05)_1px,transparent_0)] bg-[length:18px_18px] px-3 py-4 dark:bg-[radial-gradient(circle_at_1px_1px,rgba(255,255,255,0.05)_1px,transparent_0)]">
            {!msgs && (
              <div className="flex justify-center pt-10 text-slate-400">
                <Spinner className="h-6 w-6" />
              </div>
            )}
            {msgs?.map((m) => {
              const mine = m.sender === me;
              const isEditing = editing?.id === m.id;
              return (
                <div key={m.id} className={classNames("group flex items-end gap-2", mine ? "justify-end" : "justify-start")}>
                  {!mine && (
                    <div className={classNames("flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br text-xs font-extrabold text-white", avatar(m.sender))}>
                      {m.sender.slice(0, 1)}
                    </div>
                  )}
                  <div className={classNames("flex max-w-[78%] flex-col", mine ? "items-end" : "items-start")}>
                    {isEditing ? (
                      <div className="w-64 max-w-full rounded-2xl border-2 border-pink-400 bg-white p-2 dark:bg-slate-800">
                        <textarea
                          autoFocus
                          value={editing.text}
                          maxLength={MAX}
                          onChange={(e) => setEditing({ id: m.id, text: e.target.value })}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" && !e.shiftKey) {
                              e.preventDefault();
                              saveEdit();
                            }
                            if (e.key === "Escape") setEditing(null);
                          }}
                          rows={3}
                          className="w-full resize-none bg-transparent text-sm outline-none"
                        />
                        <div className="flex items-center justify-end gap-2 text-xs">
                          <span className="mr-auto text-slate-400">{editing.text.length}/{MAX}</span>
                          <button className="rounded-full px-2 py-1 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700" onClick={() => setEditing(null)}>
                            Cancel
                          </button>
                          <button className="rounded-full bg-pink-500 px-3 py-1 font-bold text-white hover:bg-pink-600" onClick={saveEdit} disabled={busy === "edit"}>
                            Save
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div
                        className={classNames(
                          "whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-[15px] leading-snug shadow-sm",
                          mine
                            ? "rounded-br-md bg-gradient-to-br from-pink-500 to-orange-400 text-white"
                            : "rounded-bl-md bg-white text-slate-900 dark:bg-slate-800 dark:text-slate-100",
                        )}
                      >
                        {!mine && <div className="mb-0.5 text-[11px] font-extrabold text-pink-600 dark:text-pink-400">{m.sender}</div>}
                        {m.body}
                      </div>
                    )}
                    <div className="mt-0.5 flex items-center gap-2 px-1 text-[11px] text-slate-400">
                      <span>{formatHM(m.sent_at)}</span>
                      {!isEditing && (
                        <span className="flex gap-2 opacity-100 transition-opacity md:opacity-0 md:group-hover:opacity-100">
                          <button className="hover:text-pink-600" onClick={() => setEditing({ id: m.id, text: m.body })}>
                            Edit
                          </button>
                          <button className="hover:text-red-600" onClick={() => void act("delete", () => api.demo.remove(m.id))}>
                            Delete
                          </button>
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
            <div ref={endRef} />
          </div>

          <div className="border-t border-slate-100 bg-white px-3 pb-3 pt-2 dark:border-slate-800 dark:bg-slate-900">
            <div className="mb-2 flex items-center gap-2 text-xs">
              <span className="text-slate-400">Sending as</span>
              {participants.map((p) => (
                <button
                  key={p}
                  onClick={() => setMe(p)}
                  className={classNames(
                    "rounded-full px-3 py-1 font-bold transition-colors",
                    p === me ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300",
                  )}
                >
                  {p}
                </button>
              ))}
            </div>
            <form onSubmit={send} className="flex items-end gap-2">
              <div className="relative flex-1">
                <textarea
                  value={draft}
                  maxLength={MAX}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send(e);
                    }
                  }}
                  rows={1}
                  placeholder={other ? `Message ${other}…` : "Message…"}
                  className="max-h-28 w-full resize-none rounded-3xl border border-slate-200 bg-slate-50 px-4 py-2.5 pr-14 text-[15px] outline-none focus:border-pink-400 dark:border-slate-700 dark:bg-slate-800"
                />
                {draft.length > MAX * 0.8 && (
                  <span className="absolute bottom-3 right-3 text-[10px] text-slate-400">{draft.length}/{MAX}</span>
                )}
              </div>
              <button
                disabled={!draft.trim() || busy === "send"}
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-pink-500 to-orange-400 text-white shadow disabled:opacity-40"
                aria-label="Send"
              >
                {busy === "send" ? <Spinner /> : <svg viewBox="0 0 24 24" className="h-5 w-5" fill="currentColor"><path d="M3.4 20.4 21 12 3.4 3.6 3.4 10l12.6 2-12.6 2z" /></svg>}
              </button>
            </form>
          </div>
        </div>

        {/* Investigator controls (outside the "phone") */}
        <aside className="w-full rounded-xl border border-slate-800 bg-slate-950 p-5 font-sans text-slate-200 shadow-xl md:w-80">
          <Link to="/" className="font-mono text-xs text-slate-400 hover:text-slate-200">
            ← cyber arche
          </Link>
          <h2 className="mt-3 text-lg font-semibold">Investigator controls</h2>
          <p className="mt-1 text-sm text-slate-400">
            Every send, edit and delete is its own WAL commit. Seizing copies the .db, -wal and -shm byte for byte while
            the app is still running, hashes them and opens the case.
          </p>
          <button
            onClick={() => void seize()}
            disabled={busy !== null}
            className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg bg-red-600 px-4 py-3 font-semibold text-white shadow hover:bg-red-500 disabled:opacity-60"
          >
            {busy === "seize" ? <Spinner /> : <span aria-hidden>⦿</span>} Seize device
          </button>
          <button
            onClick={reset}
            disabled={busy !== null}
            className="mt-2 w-full rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-900 disabled:opacity-60"
          >
            {busy === "reset" ? "Resetting…" : "Reset demo"}
          </button>
          {err && <p className="mt-3 rounded border border-red-900 bg-red-950/50 p-2 text-xs text-red-300">{err}</p>}
          <p className="mt-4 text-xs text-slate-500">
            {msgs ? `${msgs.length} messages on device` : "…"}
            {USE_MOCKS && " · mock backend"}
          </p>
        </aside>
      </div>
    </div>
  );
}
