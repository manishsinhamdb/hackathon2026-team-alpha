"use client";

import { Check, X } from "lucide-react";
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
}: {
  stage: StageCell;
  nowMs: number;
  isLast: boolean;
  teardownHint?: string;
}) {
  const s = stage;
  const isGate = s.kind === "gate";
  const running = s.status === "running" || s.status === "waiting_user";
  const done = s.status === "succeeded";
  const failed = s.status === "failed";
  const notStarted = s.status === "not_started" || s.status === "cancelled";

  // Circle
  let circle: React.ReactNode;
  if (done) {
    circle = (
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-green">
        <Check className="h-3.5 w-3.5 text-greenInk" strokeWidth={3} />
      </span>
    );
  } else if (running) {
    circle = <span className="h-7 w-7 shrink-0 rounded-full border-[3px] border-runText bg-run box-border" />;
  } else if (failed) {
    circle = (
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-fail">
        <X className="h-3.5 w-3.5 text-failBg" strokeWidth={3} />
      </span>
    );
  } else {
    // not started: dashed ring for a gate, solid ring for a run
    circle = <span className={`h-7 w-7 shrink-0 rounded-full border-2 box-border border-line2 ${isGate ? "border-dashed" : ""}`} />;
  }

  // Label + detail colours
  const labelClass = running ? "text-runText" : done || failed ? "text-content" : "text-faint";

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
    detail = <span className="text-runText tabular-nums">running · {fmtDuration(stageDurationMs(s, nowMs))}</span>;
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

export function Stepper({ stages, nowMs, teardownHint }: { stages: StageCell[]; nowMs: number; teardownHint?: string }) {
  return (
    <div className="flex items-start">
      {stages.map((s, i) => (
        <StageStep key={s.key} stage={s} nowMs={nowMs} isLast={i === stages.length - 1} teardownHint={teardownHint} />
      ))}
    </div>
  );
}
