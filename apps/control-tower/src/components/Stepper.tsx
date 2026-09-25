"use client";

import { Check, Circle, Loader2, ShieldCheck, UserRound, X } from "lucide-react";
import type { StageCell } from "@/lib/types";
import { fmtDuration } from "@/lib/format";
import { CopyId } from "./ui";

// Elapsed for a running run, final duration once ended; null before it starts.
function stageDurationMs(s: StageCell, nowMs: number): number | null {
  const running = s.status === "running" || s.status === "waiting_user";
  if (running && s.started_at) return nowMs - Date.parse(s.started_at);
  return s.duration_ms ?? null;
}

// One cell of the vertical stepper. Pure given (stage, nowMs) — render-tested in src/test/stepper.test.tsx.
export function StageStep({ stage, nowMs, isLast }: { stage: StageCell; nowMs: number; isLast: boolean }) {
  const s = stage;
  const isGate = s.kind === "gate";
  const running = s.status === "running" || s.status === "waiting_user";
  const done = s.status === "succeeded";
  const failed = s.status === "failed";
  const notStarted = s.status === "not_started";

  const ring =
    done ? "border-ok bg-ok/12 text-ok"
      : running ? "border-run bg-run/12 text-run"
      : failed ? "border-fail bg-fail/12 text-fail"
      : "border-line bg-surface-2 text-idle";

  const icon =
    done ? (isGate ? <ShieldCheck className="h-4 w-4" /> : <Check className="h-4 w-4" />)
      : running ? (s.status === "waiting_user" ? <UserRound className="h-4 w-4" /> : <Loader2 className="h-4 w-4 animate-spin" />)
      : failed ? <X className="h-4 w-4" />
      : <Circle className="h-3 w-3" />;

  const elapsed = stageDurationMs(s, nowMs);

  // Secondary line describing the state.
  let detail: React.ReactNode = null;
  if (isGate) {
    detail = done ? (
      <span className="text-muted">
        approved {s.approvedVersion ? <span className="font-medium text-content">{s.approvedVersion}</span> : ""} by{" "}
        <span className="text-content">{s.approvedBy ?? "—"}</span>
      </span>
    ) : (
      <span className="text-faint">awaiting approval</span>
    );
  } else if (notStarted) {
    detail = <span className="text-faint">Not started</span>;
  } else {
    detail = (
      <span className={failed ? "text-fail" : "text-muted"}>
        {running ? "Running · " : failed ? "Failed · " : "Done · "}
        <span className="tabular-nums">{fmtDuration(elapsed)}</span>
        {!running && s.status !== "not_started" && !failed ? "" : ""}
      </span>
    );
  }

  return (
    <li className="relative flex gap-3 pb-1" data-status={s.status} data-kind={s.kind}>
      {/* Rail: dot + connector */}
      <div className="relative flex flex-col items-center">
        <span
          className={`z-10 flex h-7 w-7 items-center justify-center rounded-full border-2 bg-surface ${ring} ${running ? "animate-pulse2" : ""}`}
        >
          {icon}
        </span>
        {!isLast && <span className={`w-px flex-1 ${done ? "bg-ok/40" : "bg-line"}`} style={{ minHeight: 14 }} />}
      </div>

      {/* Body */}
      <div className={`min-w-0 flex-1 pb-4 ${notStarted ? "opacity-60" : ""}`}>
        <div className="flex items-center gap-2">
          <span className={`text-sm font-semibold ${notStarted ? "text-muted" : "text-content"}`}>{s.label}</span>
          <span className="rounded bg-surface-2 px-1.5 py-px text-[10px] font-medium uppercase tracking-wide text-faint">
            {s.kind}
          </span>
        </div>
        <div className="mt-0.5 text-xs">{detail}</div>
        {s.run_id && (
          <div className="mt-1">
            <CopyId value={s.run_id} />
          </div>
        )}
        {failed && s.error && (
          <div className="mt-1 rounded-md border border-fail/25 bg-fail/8 px-2 py-1 text-xs text-fail">
            <span className="font-medium">{s.error.code}</span>
            {s.error.message ? `: ${s.error.message}` : ""}
          </div>
        )}
      </div>
    </li>
  );
}

export function Stepper({ stages, nowMs }: { stages: StageCell[]; nowMs: number }) {
  return (
    <ol className="mt-1">
      {stages.map((s, i) => (
        <StageStep key={s.key} stage={s} nowMs={nowMs} isLast={i === stages.length - 1} />
      ))}
    </ol>
  );
}
