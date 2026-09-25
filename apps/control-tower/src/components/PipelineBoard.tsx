"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { HelpCircle } from "lucide-react";
import type { PocDetail } from "@/lib/types";
import { fmtCountdown, fmtDuration } from "@/lib/format";
import { useToast } from "./Toasts";
import { Stepper } from "./Stepper";
import CodeRun from "./CodeRun";
import RunHistory from "./RunHistory";
import { Card, CardTitle, CopyId, Skeleton, VersionChip } from "./ui";

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

  useEffect(() => {
    setDetail(null);
    setError("");
    load();
  }, [pocId, load]);

  useEffect(() => {
    if (refreshSignal > 0) load();
  }, [refreshSignal, load]);

  // 10 s poll, paused while the tab is hidden.
  useEffect(() => {
    const timer = setInterval(() => {
      if (!document.hidden) load();
    }, pollMs);
    const onVis = () => {
      if (!document.hidden) load();
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
      <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
        <p className="font-sora text-sm font-semibold text-content">No POC selected</p>
        <p className="max-w-xs text-xs text-faint">Pick a POC from the top bar to watch its pipeline advance live.</p>
      </div>
    );
  }

  if (!detail) return <BoardSkeleton error={error} />;

  const { poc, stages, coders, runHistory, clarification, deployment, test, cloudResources } = detail;
  const codeRun = detail.runs.find((r) => r.stage === "code");
  const refreshedAgo = lastPoll ? Math.max(0, Math.round((nowMs - lastPoll) / 1000)) : null;
  const active = cloudResources.activeCount;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Header */}
      <div className="flex h-[52px] shrink-0 items-center gap-3 border-b border-line px-6">
        <div className="font-sora text-sm font-semibold">Pipeline</div>
        <CopyId value={poc.poc_id} className="text-xs" />
        <span className="flex-1" />
        <span className="flex items-center gap-1.5 text-xs text-faint">
          <span className={`h-2 w-2 rounded-full ${error ? "bg-fail" : "bg-green"}`} />
          {error ? "reconnecting" : refreshedAgo === null ? "live" : `live · refreshed ${refreshedAgo}s ago`}
        </span>
      </div>

      {/* Body */}
      <div className="grid min-h-0 flex-1 auto-rows-min grid-cols-12 content-start gap-4 overflow-y-auto p-6">
        {/* Stepper */}
        <Card className="col-span-12 p-5">
          <Stepper stages={stages} nowMs={nowMs} teardownHint={`${active} active`} />
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
