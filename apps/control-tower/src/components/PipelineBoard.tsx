"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { HelpCircle, RefreshCw } from "lucide-react";
import type { PocDetail, StageCell } from "@/lib/types";
import { fmtCountdown, fmtDuration } from "@/lib/format";
import { pollIntervalMs, IDLE_POLL_MS } from "@/lib/live";
import { useToast } from "./Toasts";
import { Stepper } from "./Stepper";
import CodeRun from "./CodeRun";
import RunHistory from "./RunHistory";
import { Card, CardTitle, CopyId, Skeleton, VersionChip } from "./ui";

const UPDATED_FLASH_MS = 1_400;
const STAGE_FLASH_MS = 2_000;

function isTerminal(status: StageCell["status"]): boolean {
  return status === "succeeded" || status === "failed";
}

export default function PipelineBoard({
  pocId,
  refreshSignal,
}: {
  pocId: string;
  refreshSignal: number;
}) {
  const [detail, setDetail] = useState<PocDetail | null>(null);
  const [error, setError] = useState("");
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const [intervalMs, setIntervalMs] = useState<number>(IDLE_POLL_MS);
  const [justUpdated, setJustUpdated] = useState(false);
  const [flashKeys, setFlashKeys] = useState<Set<string>>(new Set());

  const pocRef = useRef(pocId);
  pocRef.current = pocId;
  const nextPollAtRef = useRef<number>(0);
  const intervalRef = useRef<number>(IDLE_POLL_MS);
  const prevStagesRef = useRef<Record<string, StageCell["status"]>>({});
  const updatedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const toast = useToast();

  // Compare the freshly-fetched stages to the previous poll: any stage that just reached a terminal state
  // flashes its node, and a freshly-succeeded deploy surfaces the App link in a toast.
  const detectTransitions = useCallback(
    (data: PocDetail) => {
      const prev = prevStagesRef.current;
      const flashed: string[] = [];
      for (const s of data.stages) {
        const before = prev[s.key];
        if (before !== undefined && !isTerminal(before) && isTerminal(s.status)) {
          flashed.push(s.key);
          if (s.key === "deploy" && s.status === "succeeded" && data.deployment?.app) {
            toast.show({
              message: `Deploy finished${data.poc.title ? ` — ${data.poc.title}` : ""}. The app is live.`,
              tone: "success",
              href: data.deployment.app,
              linkLabel: "Open the app",
              ttlMs: 12_000,
            });
          }
        }
      }
      prevStagesRef.current = Object.fromEntries(data.stages.map((s) => [s.key, s.status]));
      if (flashed.length) {
        setFlashKeys(new Set(flashed));
        if (flashTimer.current) clearTimeout(flashTimer.current);
        flashTimer.current = setTimeout(() => setFlashKeys(new Set()), STAGE_FLASH_MS);
      }
    },
    [toast],
  );

  const load = useCallback(async () => {
    const id = pocRef.current;
    if (!id) {
      setDetail(null);
      return;
    }
    try {
      const res = await fetch(`/api/pocs/${id}`, { cache: "no-store" });
      const data = await res.json();
      if (pocRef.current !== id) return; // selection changed mid-flight
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      const detailData = data as PocDetail;
      detectTransitions(detailData);
      setDetail(detailData);
      setError("");
      // Adapt the cadence from the fresh data and schedule the next poll.
      const next = pollIntervalMs(detailData);
      intervalRef.current = next;
      setIntervalMs(next);
      nextPollAtRef.current = Date.now() + next;
      setJustUpdated(true);
      if (updatedTimer.current) clearTimeout(updatedTimer.current);
      updatedTimer.current = setTimeout(() => setJustUpdated(false), UPDATED_FLASH_MS);
    } catch (err) {
      if (pocRef.current !== id) return;
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      // Back off to the idle cadence on error but keep trying.
      intervalRef.current = IDLE_POLL_MS;
      nextPollAtRef.current = Date.now() + IDLE_POLL_MS;
      toast.error(`Pipeline update failed: ${msg}`);
    }
  }, [toast, detectTransitions]);

  // Reset per-POC state and fetch immediately when the selection changes.
  useEffect(() => {
    setDetail(null);
    setError("");
    prevStagesRef.current = {};
    setFlashKeys(new Set());
    load();
  }, [pocId, load]);

  useEffect(() => {
    if (refreshSignal > 0) load();
  }, [refreshSignal, load]);

  // 1 s ticker: advances running-run elapsed + TTL countdowns, drives the poll-countdown ring, AND fires
  // the next poll when it is due (self-scheduling adaptive interval; paused while the tab is hidden).
  useEffect(() => {
    const t = setInterval(() => {
      setNowMs(Date.now());
      if (!document.hidden && nextPollAtRef.current && Date.now() >= nextPollAtRef.current) {
        nextPollAtRef.current = Date.now() + intervalRef.current; // guard against overlapping fires
        load();
      }
    }, 1000);
    const onVis = () => {
      if (!document.hidden) load(); // poll immediately on return
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [load]);

  if (!pocId) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
        <p className="font-sora text-sm font-semibold text-content">No POC selected</p>
        <p className="max-w-xs text-xs text-faint">Pick a POC from the top bar to watch its pipeline advance live.</p>
      </div>
    );
  }

  if (!detail) return <BoardSkeleton error={error} />;

  const { poc, stages, coders, runHistory, clarification, deployment, test, cloudResources } = detail;
  const codeRun = detail.runs.find((r) => r.stage === "code");
  // The currently-running stage run, surfaced in the header with its run id (and platform execution id when
  // the DB records one — the current schema does not, so it's usually absent) — item 4.
  const runningRun = detail.runs.find(
    (r) => r.status === "running" || r.status === "queued" || r.status === "waiting_user",
  );
  const active = cloudResources.activeCount;
  const remainingMs = nextPollAtRef.current ? Math.max(0, nextPollAtRef.current - nowMs) : intervalMs;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Header */}
      <div className="flex h-[52px] shrink-0 items-center gap-3 border-b border-line px-6">
        <div className="font-sora text-sm font-semibold">Pipeline</div>
        <CopyId value={poc.poc_id} className="text-xs" />
        {runningRun && (
          <span className="flex items-center gap-1.5 text-xs text-faint" title={`${runningRun.stage} run in progress`}>
            <span className="text-run">● {runningRun.stage}</span>
            <CopyId value={runningRun.run_id} className="text-xs" />
            {runningRun.execution_id && (
              <>
                <span className="text-dim">exec</span>
                <CopyId value={runningRun.execution_id} className="text-xs" />
              </>
            )}
          </span>
        )}
        <span className="flex-1" />
        <PollIndicator
          error={!!error}
          justUpdated={justUpdated}
          remainingMs={remainingMs}
          intervalMs={intervalMs}
          onRefresh={load}
        />
      </div>

      {/* Body */}
      <div className="grid min-h-0 flex-1 auto-rows-min grid-cols-12 content-start gap-4 overflow-y-auto p-6">
        {/* Stepper */}
        <Card className="col-span-12 p-5">
          <Stepper stages={stages} nowMs={nowMs} teardownHint={`${active} active`} flashKeys={flashKeys} />
        </Card>

        {/* Clarification (draft finished with questions) */}
        {clarification && (
          <div className="col-span-12 rounded-xl border border-amber/40 bg-amberBg p-4">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-amber">
              <HelpCircle className="h-4 w-4" />
              Clarification needed{clarification.round ? ` · round ${clarification.round}` : ""}
            </div>
            <ul className="ml-1 space-y-1.5">
              {clarification.questions.map((q, i) => (
                <li key={i} className="flex gap-2 text-sm text-content">
                  <span className="text-amber">{i + 1}.</span>
                  <span>{typeof q === "string" ? q : q.question ?? JSON.stringify(q)}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2.5 text-xs text-muted">Answer in the chat pane — a new draft run will start.</p>
          </div>
        )}

        {/* Code run + right stack */}
        <div className="col-span-12 lg:col-span-7">
          {codeRun && coders.length > 0 ? (
            <CodeRun run={codeRun} coders={coders} />
          ) : (
            <Card className="flex flex-col gap-2 p-5">
              <CardTitle>Code run</CardTitle>
              <p className="text-sm text-faint">No code run yet. Approve the spec and say “build it” to start the coder chain.</p>
            </Card>
          )}
        </div>

        <div className="col-span-12 flex flex-col gap-4 lg:col-span-5">
          {/* Cloud resources */}
          <Card className="flex flex-col gap-2.5 p-[18px]">
            <div className="flex items-center gap-2.5">
              <CardTitle>Cloud resources</CardTitle>
              <span className="flex-1" />
              <span
                className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                  active > 0 ? "bg-failBg text-fail" : "bg-successBg text-success"
                }`}
              >
                {active} active
              </span>
            </div>
            {active === 0 ? (
              <p className="text-xs text-faint">Nothing billable is running for this POC.</p>
            ) : (
              <div className="flex flex-col gap-1.5">
                {cloudResources.items.map((r) => (
                  <div key={`${r.type}:${r.resource_id}`} className="flex items-center justify-between gap-2 text-xs">
                    <span className="text-muted">{r.type}</span>
                    <CopyId value={r.resource_id} className="text-xs" />
                    <span className="tabular-nums text-faint">{fmtCountdown(r.ttl_expires_at, nowMs)}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>

          {/* Deployment */}
          <Card className="flex flex-col gap-2.5 p-[18px]">
            <CardTitle>Deployment</CardTitle>
            <div className="flex gap-2">
              <DeployLink href={deployment?.app} label="App" />
              <DeployLink href={deployment?.api} label="API" />
              <DeployLink href={deployment?.health} label="Health" />
            </div>
            {deployment && (deployment.instance_id || deployment.ttl_expires_at) ? (
              <div className="flex flex-col gap-1 text-xs text-faint">
                {deployment.instance_id && (
                  <div className="flex items-center gap-2">
                    <span>EC2</span>
                    <CopyId value={deployment.instance_id} className="text-xs" />
                  </div>
                )}
                {deployment.ttl_expires_at && (
                  <div className="tabular-nums">TTL {fmtCountdown(deployment.ttl_expires_at, nowMs)}</div>
                )}
              </div>
            ) : (
              <p className="text-xs text-faint">Links appear when the deploy stage publishes.</p>
            )}
          </Card>

          {/* Versions */}
          <Card className="flex flex-col gap-2.5 p-[18px]">
            <CardTitle>Versions</CardTitle>
            <div className="flex flex-wrap gap-2">
              <VersionChip label="spec" value={poc.current_versions.spec} />
              <VersionChip label="code" value={poc.current_versions.code} />
              <VersionChip label="deploy" value={deployment ? "live" : undefined} />
            </div>
          </Card>
        </div>

        {/* Run history */}
        <div className="col-span-12">
          <RunHistory rows={runHistory} />
        </div>

        {/* Test report (when present) */}
        {test && (
          <Card className="col-span-12 flex flex-col gap-2 p-5">
            <CardTitle>Test report</CardTitle>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <StatPill tone="success" label="passed" value={test.passed} />
              <StatPill tone={test.failed > 0 ? "fail" : "grey"} label="failed" value={test.failed} />
              {test.skipped ? <StatPill tone="grey" label="skipped" value={test.skipped} /> : null}
              {test.not_automatable ? <StatPill tone="grey" label="not automatable" value={test.not_automatable} /> : null}
              {test.total ? <span className="text-faint">of {test.total} total</span> : null}
            </div>
            {test.failed > 0 && test.suspected_component && (
              <p className="text-sm text-fail">Suspected component: {test.suspected_component}</p>
            )}
          </Card>
        )}
      </div>
    </div>
  );
}

// The auto-poll indicator: a circular SVG countdown that fills as the next poll approaches, the cadence
// text ("next check in 0:12" / "updated"), and a manual "Refresh now" button.
function PollIndicator({
  error,
  justUpdated,
  remainingMs,
  intervalMs,
  onRefresh,
}: {
  error: boolean;
  justUpdated: boolean;
  remainingMs: number;
  intervalMs: number;
  onRefresh: () => void;
}) {
  const R = 8;
  const C = 2 * Math.PI * R;
  const elapsed = Math.min(1, Math.max(0, (intervalMs - remainingMs) / intervalMs));
  const secs = Math.ceil(remainingMs / 1000);
  const mm = Math.floor(secs / 60);
  const ss = String(secs % 60).padStart(2, "0");
  const label = error ? "reconnecting" : justUpdated ? "updated" : `next check in ${mm}:${ss}`;
  const ring = error ? "text-fail" : justUpdated ? "text-success" : "text-run";

  return (
    <div className="flex items-center gap-2.5 text-xs">
      <span className="relative inline-flex h-5 w-5 items-center justify-center" aria-hidden>
        <svg width="20" height="20" viewBox="0 0 20 20" className="-rotate-90">
          <circle cx="10" cy="10" r={R} fill="none" strokeWidth="2.5" className="stroke-line2" />
          <circle
            cx="10"
            cy="10"
            r={R}
            fill="none"
            strokeWidth="2.5"
            strokeLinecap="round"
            className={`${ring} stroke-current transition-[stroke-dashoffset] duration-1000 ease-linear`}
            strokeDasharray={C}
            strokeDashoffset={error ? C : justUpdated ? 0 : C * (1 - elapsed)}
          />
        </svg>
      </span>
      <span className={`tabular-nums ${justUpdated ? "text-success" : "text-faint"}`}>{label}</span>
      <button
        onClick={onRefresh}
        aria-label="Refresh now"
        title="Refresh now"
        className="flex h-7 items-center gap-1.5 rounded-full border border-line2 px-2.5 text-faint transition-colors hover:border-green hover:text-green"
      >
        <RefreshCw className="h-3 w-3" />
        Refresh now
      </button>
    </div>
  );
}

function DeployLink({ href, label }: { href?: string; label: string }) {
  const base = "flex-1 rounded-lg border py-2.5 text-center text-xs font-semibold";
  if (!href) return <span className={`${base} border-line2 text-dim`}>{label}</span>;
  return (
    <a href={href} target="_blank" rel="noreferrer" className={`${base} border-green/50 text-green hover:bg-green/10`}>
      {label}
    </a>
  );
}

function StatPill({ tone, label, value }: { tone: "success" | "fail" | "grey"; label: string; value: number }) {
  const cls = tone === "success" ? "bg-successBg text-success" : tone === "fail" ? "bg-failBg text-fail" : "bg-elevated text-muted";
  return (
    <span className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 ${cls}`}>
      <span className="font-semibold tabular-nums">{value}</span>
      <span className="text-xs">{label}</span>
    </span>
  );
}

function BoardSkeleton({ error }: { error: string }) {
  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
        <p className="text-sm font-semibold text-fail">Couldn’t load the pipeline</p>
        <p className="max-w-md text-xs text-muted">{error}</p>
        <p className="text-xs text-faint">Retrying on the next poll…</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-4 p-6">
      <Skeleton className="h-24 w-full" />
      <div className="grid grid-cols-12 gap-4">
        <Skeleton className="col-span-7 h-48" />
        <div className="col-span-5 flex flex-col gap-4">
          <Skeleton className="h-20" />
          <Skeleton className="h-24" />
        </div>
      </div>
      <Skeleton className="h-40 w-full" />
    </div>
  );
}
