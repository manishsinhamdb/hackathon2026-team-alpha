"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";
import type { PocStatus, RunDisplayStatus } from "@/lib/types";

// The five pill tones from the design.
type Tone = "success" | "run" | "amber" | "fail" | "grey";

const TONE_CLASS: Record<Tone, string> = {
  success: "bg-successBg text-success",
  run: "bg-runBg text-runText",
  amber: "bg-amberBg text-amber",
  fail: "bg-failBg text-fail",
  grey: "bg-line2 text-content",
};

const STATUS_TONE: Record<string, Tone> = {
  drafting: "run",
  spec_ready: "success",
  coding: "run",
  code_ready: "success",
  deploying: "run",
  deployed: "success",
  testing: "run",
  tested: "success",
  failed: "fail",
  code_failed: "fail",
  torn_down: "grey",
};

const RUN_TONE: Record<RunDisplayStatus, Tone> = {
  succeeded: "success",
  running: "run",
  failed: "fail",
  questions: "amber",
  cancelled: "grey",
  queued: "grey",
  waiting_user: "amber",
};

function pillText(s: string): string {
  return String(s).replace(/_/g, " ").toUpperCase();
}

// A rounded status pill. `size="sm"` is the table/stepper size; default is the top-bar size.
export function StatusPill({ status, className = "" }: { status: PocStatus | string; className?: string }) {
  const tone = STATUS_TONE[status] ?? "grey";
  return (
    <span className={`inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] font-semibold tracking-wide ${TONE_CLASS[tone]} ${className}`}>
      {pillText(status)}
    </span>
  );
}

export function RunStatusPill({ status, className = "" }: { status: RunDisplayStatus; className?: string }) {
  const tone = RUN_TONE[status] ?? "grey";
  return (
    <span className={`inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ${TONE_CLASS[tone]} ${className}`}>
      {pillText(status)}
    </span>
  );
}

// A version chip (spec v003 / code — / deploy —). Dimmed when there is no version.
export function VersionChip({ label, value }: { label: string; value?: string }) {
  const has = !!value;
  return (
    <span className={`rounded-full border border-line bg-ink px-2.5 py-1 font-mono text-xs ${has ? "text-content" : "text-faint"}`}>
      {label} {value ?? "—"}
    </span>
  );
}

// A green health chip: "<project> · healthy" (green dot) or a red error state.
export function HealthChip({ project, state }: { project: string; state: "ok" | "error" | "loading" }) {
  const dot = state === "ok" ? "bg-green" : state === "error" ? "bg-fail" : "bg-faint";
  const label = state === "error" ? "unreachable" : state === "loading" ? "connecting" : "healthy";
  return (
    <div className="flex h-10 items-center gap-2 rounded-[10px] border border-line bg-surface px-3 text-xs text-faint">
      <span className={`h-2 w-2 rounded-full ${dot}`} />
      {project} · {label}
    </div>
  );
}

// Inline monospace id that copies on click (run ids, poc ids, EC2 ids).
export function CopyId({ value, display, className = "" }: { value: string; display?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard unavailable */
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      title="Copy"
      className={`group inline-flex max-w-full items-center gap-1 rounded font-mono text-xs text-muted transition-colors hover:text-content ${className}`}
    >
      <span className="truncate">{display ?? value}</span>
      {copied ? (
        <Check className="h-3 w-3 shrink-0 text-success" />
      ) : (
        <Copy className="h-3 w-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-70" />
      )}
    </button>
  );
}

// A bordered "copy" button shown next to an id (matches the chat structured hint in the design).
export function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard unavailable */
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      className="rounded-md border border-line2 px-2 py-0.5 text-[11px] text-faint transition-colors hover:text-content"
    >
      {copied ? "copied" : "copy"}
    </button>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton rounded-md ${className}`} />;
}

// A dark card surface used across the pipeline board and library.
export function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`rounded-xl border border-line bg-surface ${className}`}>{children}</div>;
}

export function CardTitle({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`font-sora text-[13px] font-semibold text-content ${className}`}>{children}</div>;
}
