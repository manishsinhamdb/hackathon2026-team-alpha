"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity, Cloud, ExternalLink, FlaskConical, HelpCircle, Link2, Radio, Server, Timer,
} from "lucide-react";
import type { PocDetail } from "@/lib/types";
import { fmtCountdown, fmtDuration, fmtTime } from "@/lib/format";
import { useToast } from "./Toasts";
import { Stepper } from "./Stepper";
import { Chip, CopyId, Skeleton, StatusPill } from "./ui";

export default function PipelineBoard({
  pocId,
  refreshSignal,
  pollMs,
}: {
  pocId: string;
  refreshSignal: number;
  pollMs: number;
}) {
  const [detail, setDetail] = useState<PocDetail | null>(null);
  const [error, setError] = useState("");
  const [lastPoll, setLastPoll] = useState<number>(0);
  const [paused, setPaused] = useState(false);
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const pocRef = useRef(pocId);
  pocRef.current = pocId;
  const toast = useToast();

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
      setDetail(data as PocDetail);
      setError("");
      setLastPoll(Date.now());
    } catch (err) {
      if (pocRef.current !== id) return;
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      toast.error(`Pipeline update failed: ${msg}`);
    }
  }, [toast]);

  // Reset + immediate load when the selected POC changes.
  useEffect(() => {
    setDetail(null);
    setError("");
    load();
  }, [pocId, load]);

  // Immediate refresh whenever the chat pane signals a completed turn.
  useEffect(() => {
    if (refreshSignal > 0) load();
  }, [refreshSignal, load]);

  // 10 s poll, paused while the tab is hidden.
  useEffect(() => {
    const timer = setInterval(() => {
      if (document.hidden) {
        setPaused(true);
        return;
      }
      setPaused(false);
      load();
    }, pollMs);
    const onVis = () => {
      if (!document.hidden) {
        setPaused(false);
        load();
      } else {
        setPaused(true);
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [load, pollMs]);

  // 1 s ticker so running-run elapsed and the TTL countdown advance between polls.
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  if (!pocId) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
        <Activity className="h-8 w-8 text-idle" />
        <p className="text-sm font-medium text-muted">No POC selected</p>
        <p className="max-w-xs text-xs text-faint">Pick a POC from the top bar to watch its pipeline advance live.</p>
      </div>
    );
  }

  if (!detail) {
    return <BoardSkeleton error={error} />;
  }

  const { poc, stages, clarification, deployment, test, cloudResources } = detail;

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      {/* Header */}
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-semibold tracking-tight text-content">{poc.title}</h2>
          <StatusPill status={poc.status} />
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-muted">
          <CopyId value={poc.poc_id} />
          <span className="text-line">·</span>
          <Chip>spec {poc.current_versions.spec ?? "—"}</Chip>
          <Chip>code {poc.current_versions.code ?? "—"}</Chip>
          <span className="text-line">·</span>
          <span>updated {fmtTime(poc.updated_at)}</span>
        </div>
        <div className="mt-1.5 flex items-center gap-1.5 text-[11px] text-faint">
          <Radio className={`h-3 w-3 ${paused ? "text-idle" : "text-ok"}`} />
          {paused ? "Polling paused (tab hidden)" : `Live · polling every ${pollMs / 1000}s`}
          {lastPoll ? <span>· last {fmtTime(new Date(lastPoll).toISOString())}</span> : null}
          {error ? <span className="text-fail">· {error}</span> : null}
        </div>
      </div>

      {/* Stepper */}
      <Card>
        <CardTitle icon={Activity}>Pipeline</CardTitle>
        <Stepper stages={stages} nowMs={nowMs} />
      </Card>

      {/* Clarification */}
      {clarification && (
        <div className="rounded-xl border border-warnline bg-warnbg p-4">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-content">
            <HelpCircle className="h-4 w-4 text-run" />
            Clarification needed{clarification.round ? ` · round ${clarification.round}` : ""}
          </div>
          <ul className="ml-1 space-y-1.5">
            {clarification.questions.map((q, i) => (
              <li key={i} className="flex gap-2 text-sm text-content">
                <span className="text-run">{i + 1}.</span>
                <span>{typeof q === "string" ? q : q.question ?? JSON.stringify(q)}</span>
              </li>
            ))}
          </ul>
          <p className="mt-2.5 rounded-md bg-surface/60 px-2 py-1 text-xs text-muted">
            💬 Answer in the chat pane — a new draft run will start.
          </p>
        </div>
      )}

      {/* Deployment */}
      {deployment && (deployment.app || deployment.api || deployment.health) && (
        <Card>
          <CardTitle icon={Link2}>Deployment</CardTitle>
          <div className="flex flex-wrap gap-2">
            {deployment.app && <LinkButton href={deployment.app} label="App" />}
            {deployment.api && <LinkButton href={deployment.api} label="API" />}
            {deployment.health && <LinkButton href={deployment.health} label="Health" />}
          </div>
          <dl className="mt-3 space-y-1.5 text-sm">
            {deployment.instance_id && (
              <Row icon={Server} label="EC2">
                <CopyId value={deployment.instance_id} />
              </Row>
            )}
            {deployment.ttl_expires_at && (
              <Row icon={Timer} label="TTL">
                <span className="font-medium text-content">{fmtCountdown(deployment.ttl_expires_at, nowMs)}</span>
                <span className="ml-1 text-faint">({fmtTime(deployment.ttl_expires_at)})</span>
              </Row>
            )}
          </dl>
        </Card>
      )}

      {/* Test report */}
      {test && (
        <Card>
          <CardTitle icon={FlaskConical}>Test report</CardTitle>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <StatPill tone="ok" label="passed" value={test.passed} />
            <StatPill tone={test.failed > 0 ? "fail" : "idle"} label="failed" value={test.failed} />
            {test.skipped ? <StatPill tone="idle" label="skipped" value={test.skipped} /> : null}
            {test.not_automatable ? <StatPill tone="idle" label="not automatable" value={test.not_automatable} /> : null}
            {test.total ? <span className="text-muted">of {test.total} total</span> : null}
          </div>
          {test.failed > 0 && test.suspected_component && (
            <p className="mt-2 text-sm text-fail">Suspected component: {test.suspected_component}</p>
          )}
        </Card>
      )}

      {/* Cloud resources */}
      <Card>
        <div className="flex items-center justify-between">
          <CardTitle icon={Cloud} className="mb-0">Cloud resources</CardTitle>
          <span
            className={`inline-flex min-w-[28px] items-center justify-center rounded-full px-2 py-0.5 text-sm font-bold ${
              cloudResources.activeCount > 0 ? "bg-fail text-white" : "bg-ok/15 text-ok"
            }`}
          >
            {cloudResources.activeCount}
          </span>
        </div>
        {cloudResources.items.length === 0 ? (
          <p className="mt-2 text-sm text-muted">No active resources.</p>
        ) : (
          <div className="mt-3 overflow-hidden rounded-lg border border-line">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-xs text-muted">
                <tr>
                  <th className="px-3 py-1.5 text-left font-medium">Type</th>
                  <th className="px-3 py-1.5 text-left font-medium">Resource</th>
                  <th className="px-3 py-1.5 text-left font-medium">TTL</th>
                </tr>
              </thead>
              <tbody>
                {cloudResources.items.map((r) => (
                  <tr key={`${r.type}:${r.resource_id}`} className="border-t border-line">
                    <td className="px-3 py-1.5">{r.type}</td>
                    <td className="px-3 py-1.5"><CopyId value={r.resource_id} /></td>
                    <td className="px-3 py-1.5 tabular-nums">{fmtCountdown(r.ttl_expires_at, nowMs)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Runs & tasks (kept, collapsed) */}
      <details className="rounded-xl border border-line bg-surface">
        <summary className="cursor-pointer select-none px-4 py-3 text-sm font-medium text-muted hover:text-content">
          Runs &amp; tasks ({detail.runs.length})
        </summary>
        <div className="space-y-3 px-4 pb-4">
          {detail.runs.map((r) => (
            <div key={r.run_id} className="rounded-lg border border-line bg-canvas p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-semibold capitalize">{r.stage}</span>
                <CopyId value={r.run_id} />
              </div>
              <div className="mt-1 text-xs text-muted">
                {r.status} ·{" "}
                {r.status === "running" && r.started_at
                  ? `elapsed ${fmtDuration(nowMs - Date.parse(r.started_at))}`
                  : `took ${fmtDuration(r.duration_ms)}`}
                {r.error ? <span className="text-fail"> · {r.error.code}: {r.error.message}</span> : null}
              </div>
              {detail.tasksByRun[r.run_id]?.length ? (
                <table className="mt-2 w-full text-xs">
                  <thead className="text-faint">
                    <tr>
                      <th className="py-1 pr-2 text-left font-medium">#</th>
                      <th className="py-1 pr-2 text-left font-medium">Agent</th>
                      <th className="py-1 pr-2 text-left font-medium">Tool</th>
                      <th className="py-1 pr-2 text-left font-medium">Mode</th>
                      <th className="py-1 text-left font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.tasksByRun[r.run_id].map((t) => (
                      <tr key={t.seq} className="border-t border-line/60">
                        <td className="py-1 pr-2">{t.seq}</td>
                        <td className="py-1 pr-2">{t.agent}</td>
                        <td className="py-1 pr-2 font-mono">{t.tool}</td>
                        <td className="py-1 pr-2">{t.mode ?? "—"}</td>
                        <td className="py-1">{t.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}

/* ---- small presentational helpers ---------------------------------------------------------------- */

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`rounded-xl border border-line bg-surface p-4 shadow-card ${className}`}>{children}</div>;
}

function CardTitle({
  icon: Icon,
  children,
  className = "",
}: {
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`mb-3 flex items-center gap-2 text-sm font-semibold text-content ${className}`}>
      <Icon className="h-4 w-4 text-accent" />
      {children}
    </div>
  );
}

function Row({
  icon: Icon,
  label,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="flex w-16 shrink-0 items-center gap-1.5 text-muted">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </span>
      <span className="min-w-0 flex-1">{children}</span>
    </div>
  );
}

function LinkButton({ href, label }: { href: string; label: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1.5 rounded-lg border border-green-dark/40 bg-green-base/10 px-3 py-1.5 text-sm font-medium text-accent transition-colors hover:bg-green-base/20"
    >
      <ExternalLink className="h-3.5 w-3.5" />
      {label}
    </a>
  );
}

function StatPill({ tone, label, value }: { tone: "ok" | "fail" | "idle"; label: string; value: number }) {
  const cls =
    tone === "ok" ? "bg-ok/12 text-ok" : tone === "fail" ? "bg-fail/12 text-fail" : "bg-surface-2 text-muted";
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
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
        <p className="text-sm font-medium text-fail">Couldn’t load the pipeline</p>
        <p className="max-w-md text-xs text-muted">{error}</p>
        <p className="text-xs text-faint">Retrying on the next poll…</p>
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="space-y-2">
        <Skeleton className="h-6 w-56" />
        <Skeleton className="h-4 w-80" />
      </div>
      <div className="rounded-xl border border-line bg-surface p-4">
        <Skeleton className="mb-4 h-4 w-24" />
        <div className="space-y-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3">
              <Skeleton className="h-7 w-7 rounded-full" />
              <Skeleton className="h-4 w-40" />
            </div>
          ))}
        </div>
      </div>
      <div className="rounded-xl border border-line bg-surface p-4">
        <Skeleton className="mb-3 h-4 w-28" />
        <Skeleton className="h-4 w-24" />
      </div>
    </div>
  );
}
