"use client";

import { Check, Pause, X } from "lucide-react";
import type { StageCell } from "@/lib/types";
import { fmtClock, fmtDuration, initials } from "@/lib/format";

// Elapsed for a running run, final duration once ended; null before it starts.
function stageDurationMs(s: StageCell, nowMs: number): number | null {
  const running = s.status === "running" || s.status === "waiting_user";
  if (running && s.started_at) return nowMs - Date.parse(s.started_at);
  return s.duration_ms ?? null;
}

// Static hint shown for a not-started cell (matches the design's quiet secondary line).
const HINTS: Record<string, string> = {
  draft: "not started",
  spec_approved: "gate",
  code: "coders",
  code_approved: "gate",
  deploy: "EC2",
  test: "e2e",
  teardown: "resources",
};

// One cell of the horizontal Draft -> Spec approved -> Code -> Code approved -> Deploy -> Tests -> Torn down
// stepper. Pure given (stage, nowMs) — state-mapping-tested in src/test/stepper.test.tsx.
export function StageStep({
  stage,
  nowMs,
  isLast,
  teardownHint,
  flash,
  onRetry,
}: {
  stage: StageCell;
  nowMs: number;
  isLast: boolean;
  teardownHint?: string;
  flash?: boolean;
  onRetry?: (stage: StageCell) => void; // abandoned cells: Retry / Continue via the chat send path
}) {
  const s = stage;
  const isGate = s.kind === "gate";
  const running = s.status === "running" || s.status === "waiting_user";
  const done = s.status === "succeeded";
  const failed = s.status === "failed";
  const abandoned = s.status === "abandoned"; // stale heartbeat / given up (Round 5) — amber
  const notStarted = s.status === "not_started" || s.status === "cancelled";

  // A node that just reached a terminal state flashes briefly (feature 4).
  const flashCls = flash ? "animate-flash" : "";

  // Circle
  let circle: React.ReactNode;
  if (done) {
    circle = (
      <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-green ${flashCls}`}>
        <Check className="h-3.5 w-3.5 text-greenInk" strokeWidth={3} />
      </span>
    );
  } else if (running) {
    circle = <span className={`h-7 w-7 shrink-0 rounded-full border-[3px] border-runText bg-run box-border ${flashCls}`} />;
  } else if (failed) {
    circle = (
      <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-fail ${flashCls}`}>
        <X className="h-3.5 w-3.5 text-failBg" strokeWidth={3} />
      </span>
    );
  } else if (abandoned) {
    circle = (
      <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border-2 border-amber bg-amberBg box-border ${flashCls}`}>
        <Pause className="h-3 w-3 text-amber" strokeWidth={3} />
      </span>
    );
  } else {
    // not started: dashed ring for a gate, solid ring for a run
    circle = <span className={`h-7 w-7 shrink-0 rounded-full border-2 box-border border-line2 ${isGate ? "border-dashed" : ""}`} />;
  }

  // Label + detail colours
  const labelClass = running ? "text-runText" : abandoned ? "text-amber" : done || failed ? "text-content" : "text-faint";

  let detail: React.ReactNode;
  if (isGate) {
    detail = done ? (
      <span className="text-faint">
        gate · by {initials(s.approvedBy)} {fmtClock(s.ended_at)}
      </span>
    ) : (
      <span className="text-dim">gate</span>
    );
  } else if (done) {
    detail = (
      <span className="text-faint tabular-nums">
        {fmtDuration(s.duration_ms)}
        {s.version ? ` · ${s.version}` : ""}
      </span>
    );
  } else if (running) {
    detail = (
      <span className="text-runText tabular-nums">
        running · {fmtDuration(stageDurationMs(s, nowMs))}
        {s.executions ? <span className="text-faint" title="Resumed / handed over to a new execution"> · exec {s.executions}</span> : null}
      </span>
    );
  } else if (abandoned) {
    const since = s.last_beat_at ? nowMs - Date.parse(s.last_beat_at) : null;
    detail = (
      <span className="flex flex-col items-start gap-1.5">
        <span className="text-amber tabular-nums" title="No heartbeat for over 3 minutes">
          abandoned{since != null && since > 0 ? ` · ${fmtDuration(since)} stale` : ""}
        </span>
        {onRetry && s.run_id && (
          <button
            type="button"
            onClick={() => onRetry(s)}
            className="rounded-full border border-amber/60 px-2.5 py-0.5 text-[11px] font-semibold text-amber hover:bg-amberBg"
          >
            {s.retry_label ?? "Retry"}
          </button>
        )}
      </span>
    );
  } else if (failed) {
    detail = <span className="text-fail">{s.error?.code ? `failed · ${s.error.code}` : "failed"}</span>;
  } else {
    detail = <span className="text-dim">{s.key === "teardown" && teardownHint ? teardownHint : HINTS[s.key] ?? "not started"}</span>;
  }

  return (
    <div
      data-status={s.status}
      data-kind={s.kind}
      className={`flex flex-col items-start gap-2 ${isLast ? "min-w-[96px]" : "flex-1"}`}
    >
      <div className="flex w-full items-center gap-2.5">
        {circle}
        {!isLast && <span className={`h-0.5 flex-1 ${done ? "bg-green" : "bg-line2"}`} />}
      </div>
      <div className={`text-[13px] font-semibold ${labelClass}`}>{s.label}</div>
      <div className="text-xs">{detail}</div>
    </div>
  );
}

export function Stepper({
  stages,
  nowMs,
  teardownHint,
  flashKeys,
  onRetry,
}: {
  stages: StageCell[];
  nowMs: number;
  teardownHint?: string;
  flashKeys?: Set<string>;
  onRetry?: (stage: StageCell) => void;
}) {
  return (
    <div className="flex items-start">
      {stages.map((s, i) => (
        <StageStep
          key={s.key}
          stage={s}
          nowMs={nowMs}
          isLast={i === stages.length - 1}
          teardownHint={teardownHint}
          flash={flashKeys?.has(s.key)}
          onRetry={onRetry}
        />
      ))}
    </div>
  );
}
