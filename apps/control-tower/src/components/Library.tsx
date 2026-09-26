"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArchiveRestore, Archive, Droplet, Plus, Search } from "lucide-react";
import type { PocSummary, TodaySummary } from "@/lib/types";
import { filterCounts, selectPocs, type LibraryFilter } from "@/lib/aggregate";
import { fmtClock, fmtDateFull, fmtDuration } from "@/lib/format";
import { useSessions } from "@/lib/sessions";
import { useSessionBusy } from "@/lib/useSessionStatus";
import { Card, CardTitle, StatusPill } from "./ui";
import { ToastProvider, useToast } from "./Toasts";
import ThemeToggle from "./ThemeToggle";

const POLL_MS = 10_000;

const FILTERS: { key: LibraryFilter; label: string }[] = [
  { key: "active", label: "Active" },
  { key: "deployed", label: "Deployed" },
  { key: "torn_down", label: "Torn down" },
  { key: "archived", label: "Archived" },
];

// "s3 · c1" — the version summary in the table.
function versionsLabel(v: PocSummary["versions"]): string {
  const n = (s?: string) => {
    if (!s) return "—";
    const digits = s.replace(/\D/g, "");
    return digits ? String(parseInt(digits, 10)) : s;
  };
  return `s${n(v.spec)} · c${n(v.code)}`;
}

function LibraryInner() {
  const [pocs, setPocs] = useState<PocSummary[]>([]);
  const [today, setToday] = useState<TodaySummary | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<LibraryFilter>("active");
  const [busyId, setBusyId] = useState("");
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const [pRes, sRes] = await Promise.all([
        fetch("/api/pocs", { cache: "no-store" }),
        fetch("/api/summary", { cache: "no-store" }),
      ]);
      const pData = await pRes.json();
      if (!pRes.ok) throw new Error(pData.error || `HTTP ${pRes.status}`);
      setPocs(pData.pocs as PocSummary[]);
      if (sRes.ok) setToday((await sRes.json()).today as TodaySummary);
    } catch (err) {
      toast.error(`Library load failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, [toast]);

  useEffect(() => {
    load();
    const timer = setInterval(() => {
      if (!document.hidden) load();
    }, POLL_MS);
    const onVis = () => {
      if (!document.hidden) load();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [load]);

  const counts = useMemo(() => filterCounts(pocs), [pocs]);
  const rows = useMemo(() => selectPocs(pocs, filter, query), [pocs, filter, query]);

  const toggleArchive = async (poc: PocSummary) => {
    const archived = !poc.ui_archived;
    setBusyId(poc.poc_id);
    // Optimistic update, then confirm with the server + reload.
    setPocs((cur) => cur.map((p) => (p.poc_id === poc.poc_id ? { ...p, ui_archived: archived } : p)));
    try {
      const res = await fetch(`/api/pocs/${poc.poc_id}/archive`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived }),
      });
      if (!res.ok) throw new Error((await res.json()).error || `HTTP ${res.status}`);
      await load();
    } catch (err) {
      toast.error(`Archive failed: ${err instanceof Error ? err.message : String(err)}`);
      await load();
    } finally {
      setBusyId("");
    }
  };

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      {/* Top bar */}
      <header className="flex h-16 shrink-0 items-center gap-5 border-b border-line px-6">
        <div className="flex items-center gap-3">
          <div className="flex h-[34px] w-[34px] items-center justify-center rounded-[10px] bg-green">
            <Droplet className="h-[18px] w-[18px] text-greenInk" fill="currentColor" strokeWidth={0} />
          </div>
          <div className="flex flex-col gap-px leading-none">
            <div className="font-sora text-[15px] font-semibold tracking-tight">POC Builder</div>
            <div className="text-[11px] uppercase tracking-[0.08em] text-faint">Control Tower</div>
          </div>
        </div>
        <div className="font-sora text-base font-semibold">POC library</div>
        <div className="ml-auto flex items-center gap-2.5">
          <Link href="/" className="flex h-10 items-center rounded-[10px] border border-line2 px-3.5 font-medium text-content hover:bg-surface">
            Back to workspace
          </Link>
          <Link href="/" className="flex h-10 items-center gap-2 rounded-[10px] bg-green px-4 font-semibold text-greenInk hover:opacity-90">
            <Plus className="h-4 w-4" strokeWidth={2.2} /> New POC
          </Link>
          <ThemeToggle />
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-12 overflow-hidden">
        {/* Left: search + filters + table */}
        <section className="col-span-12 flex min-h-0 flex-col gap-3.5 overflow-y-auto border-r border-line bg-panel p-6 lg:col-span-9">
          <div className="flex flex-wrap items-center gap-2.5">
            <div className="flex h-10 w-80 items-center gap-2.5 rounded-[10px] border border-line2 bg-surface px-3">
              <Search className="h-4 w-4 text-faint" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search title or POC id"
                aria-label="Search POCs"
                className="flex-1 bg-transparent text-[13px] text-content outline-none placeholder:text-faint"
              />
            </div>
            {FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={`h-[34px] rounded-full px-3.5 text-xs font-semibold ${
                  filter === f.key ? "bg-green text-greenInk" : "border border-line2 bg-surface text-content"
                }`}
              >
                {f.label} · {counts[f.key]}
              </button>
            ))}
            <span className="flex-1" />
            <span className="text-xs text-faint">Sorted by created · newest first</span>
          </div>

          <Card className="flex flex-col overflow-hidden p-0">
            <div className="grid grid-cols-12 gap-3 border-b border-line px-[18px] py-3 text-[11px] uppercase tracking-[0.06em] text-faint">
              <span className="col-span-5">POC</span>
              <span className="col-span-2">Status</span>
              <span className="col-span-2">Created</span>
              <span className="col-span-1">Versions</span>
              <span className="col-span-2 text-right">Actions</span>
            </div>
            {rows.length === 0 && <div className="px-[18px] py-6 text-sm text-faint">No POCs match.</div>}
            {rows.map((p) => (
              <div key={p.poc_id} className="grid grid-cols-12 items-center gap-3 border-b border-line px-[18px] py-3.5 last:border-0">
                <div className="col-span-5 flex flex-col gap-0.5">
                  <div className={`truncate font-semibold ${p.ui_archived ? "text-muted" : ""}`}>{p.title}</div>
                  <div className="truncate font-mono text-xs text-faint">{p.poc_id}</div>
                </div>
                <div className="col-span-2">
                  <StatusPill status={p.status} />
                </div>
                <div className="col-span-2 text-muted">{fmtDateFull(p.created_at)}</div>
                <div className="col-span-1 font-mono text-xs text-muted">{versionsLabel(p.versions)}</div>
                <div className="col-span-2 flex justify-end gap-2">
                  <Link
                    href={`/?poc=${encodeURIComponent(p.poc_id)}`}
                    className="rounded-lg bg-green px-3 py-1.5 text-xs font-semibold text-greenInk hover:opacity-90"
                  >
                    Open
                  </Link>
                  <button
                    disabled={busyId === p.poc_id}
                    onClick={() => toggleArchive(p)}
                    className="flex items-center gap-1 rounded-lg border border-line2 px-3 py-1.5 text-xs text-muted hover:text-content disabled:opacity-50"
                  >
                    {p.ui_archived ? <ArchiveRestore className="h-3.5 w-3.5" /> : <Archive className="h-3.5 w-3.5" />}
                    {p.ui_archived ? "Restore" : "Archive"}
                  </button>
                </div>
              </div>
            ))}
          </Card>

          <p className="text-xs text-faint">
            Archiving hides a POC from the workspace selector. Runs, spec and code stay in S3 and can be restored from the Archived filter.
          </p>
        </section>

        {/* Right: sessions + today */}
        <aside className="col-span-12 flex min-h-0 flex-col gap-4 overflow-y-auto p-5 lg:col-span-3">
          <SessionsCard />
          <TodayCard today={today} />
        </aside>
      </div>
    </div>
  );
}

function SessionsCard() {
  const { sessions, activeId, selectSession, renameSession, closeSession } = useSessions();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [stoppingId, setStoppingId] = useState("");
  const active = sessions.find((s) => s.id === activeId);
  const toast = useToast();
  const busy = useSessionBusy(sessions.map((s) => s.id));

  const startRename = () => {
    setDraft(active?.name ?? "");
    setEditing(true);
  };
  const commit = () => {
    if (activeId && draft.trim()) renameSession(activeId, draft.trim());
    setEditing(false);
  };

  // Stop a busy session's running turn (item 3). This only ever stops the current user's own sessions.
  const stopSession = async (id: string) => {
    if (!window.confirm("Stop the running turn on this session?")) return;
    setStoppingId(id);
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(id)}/stop`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) toast.show({ message: data.error || "Couldn’t stop the turn.", tone: "error", detail: data.detail });
      else toast.show({ message: "Turn cancelled.", tone: "success" });
    } catch {
      toast.error("Couldn’t stop the turn.");
    } finally {
      setStoppingId("");
    }
  };

  return (
    <Card className="flex flex-col gap-3 p-[18px]">
      <CardTitle>Sessions</CardTitle>
      <div className="flex flex-col gap-2">
        {sessions.map((s) => {
          const isActive = s.id === activeId;
          const isBusy = !!busy[s.id];
          return (
            <div
              key={s.id}
              className={`flex items-center gap-2 rounded-[10px] px-3 py-2.5 ${
                isActive ? "border border-line2 bg-elevated" : "border border-line"
              }`}
            >
              <button onClick={() => selectSession(s.id)} className="flex min-w-0 flex-1 flex-col gap-0.5 text-left">
                <div className="flex items-center gap-2">
                  {isBusy ? (
                    <span className="h-2 w-2 rounded-full bg-run" title="Running a turn" />
                  ) : isActive ? (
                    <span className="h-2 w-2 rounded-full bg-green" />
                  ) : null}
                  <span className={`truncate text-[13px] font-semibold ${isActive ? "" : "text-muted"}`}>{s.name}</span>
                </div>
                <span className="truncate font-mono text-[11px] text-faint">
                  {s.id} · {s.turns} {s.turns === 1 ? "turn" : "turns"} · {fmtClock(new Date(s.updatedAt).toISOString())}
                </span>
              </button>
              {isBusy && (
                <button
                  onClick={() => stopSession(s.id)}
                  disabled={stoppingId === s.id}
                  className="shrink-0 rounded-md border border-fail/50 px-2 py-1 text-[11px] font-semibold text-fail hover:bg-failBg disabled:opacity-50"
                >
                  {stoppingId === s.id ? "…" : "Stop"}
                </button>
              )}
            </div>
          );
        })}
      </div>
      {editing ? (
        <div className="flex gap-2">
          <input
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && commit()}
            className="h-[34px] flex-1 rounded-lg border border-line2 bg-ink px-2.5 text-xs text-content outline-none"
          />
          <button onClick={commit} className="h-[34px] rounded-lg bg-green px-3 text-xs font-semibold text-greenInk">
            Save
          </button>
        </div>
      ) : (
        <div className="flex gap-2">
          <button onClick={startRename} className="h-[34px] flex-1 rounded-lg border border-line2 text-xs text-content hover:bg-surface">
            Rename
          </button>
          <button
            onClick={() => activeId && closeSession(activeId)}
            className="h-[34px] flex-1 rounded-lg border border-line2 text-xs text-content hover:bg-surface"
          >
            Close
          </button>
        </div>
      )}
    </Card>
  );
}

function TodayCard({ today }: { today: TodaySummary | null }) {
  const transcript = today?.transcriptToTestedMs;
  const cells: { value: string; label: string; accent?: boolean }[] = [
    { value: today ? String(today.drafted) : "—", label: "POCs drafted" },
    { value: today ? String(today.deployedTested) : "—", label: "deployed & tested" },
    { value: today ? String(today.cloudLive) : "—", label: "cloud resources live", accent: (today?.cloudLive ?? 0) === 0 },
    {
      value: transcript == null ? "—" : `${Math.max(1, Math.round(transcript / 60000))}m`,
      label: "transcript → tested",
    },
  ];
  return (
    <Card className="flex flex-col gap-2.5 p-[18px]">
      <CardTitle>Today</CardTitle>
      <div className="grid grid-cols-2 gap-2.5">
        {cells.map((c) => (
          <div key={c.label} className="rounded-[10px] border border-line bg-ink p-2.5">
            <div className={`font-sora text-[22px] font-bold ${c.accent ? "text-success" : ""}`}>{c.value}</div>
            <div className="text-[11px] text-faint">{c.label}</div>
          </div>
        ))}
      </div>
    </Card>
  );
}

export default function Library() {
  return (
    <ToastProvider>
      <LibraryInner />
    </ToastProvider>
  );
}
