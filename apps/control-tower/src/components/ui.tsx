"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";
import type { PocStatus } from "@/lib/types";

// Map a POC / stage status to a tone so pills read consistently across the app.
type Tone = "ok" | "run" | "fail" | "idle" | "accent";

const STATUS_TONE: Record<string, Tone> = {
  drafting: "run",
  spec_ready: "accent",
  coding: "run",
  code_ready: "accent",
  deploying: "run",
  deployed: "ok",
  testing: "run",
  tested: "ok",
  failed: "fail",
  torn_down: "idle",
};

const TONE_CLASS: Record<Tone, string> = {
  ok: "bg-ok/12 text-ok ring-1 ring-ok/30",
  run: "bg-run/12 text-run ring-1 ring-run/30",
  fail: "bg-fail/12 text-fail ring-1 ring-fail/30",
  idle: "bg-surface-2 text-muted ring-1 ring-line",
  accent: "bg-green-base/15 text-accent ring-1 ring-green-dark/30",
};

export function StatusPill({ status, className = "" }: { status: PocStatus | string; className?: string }) {
  const tone = STATUS_TONE[status] ?? "idle";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium capitalize ${TONE_CLASS[tone]} ${className}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />
      {String(status).replace(/_/g, " ")}
    </span>
  );
}

export function Chip({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-md bg-surface-2 px-2 py-0.5 text-xs text-muted ring-1 ring-line ${className}`}>
      {children}
    </span>
  );
}

// Health / connection dot: green when connected, red on error, amber while first loading.
export function HealthDot({ state, label }: { state: "ok" | "error" | "loading"; label?: string }) {
  const color = state === "ok" ? "bg-ok" : state === "error" ? "bg-fail" : "bg-run";
  const title = label ?? (state === "ok" ? "Connected" : state === "error" ? "Connection error" : "Connecting…");
  return (
    <span className="inline-flex items-center gap-1.5" title={title}>
      <span className="relative flex h-2.5 w-2.5">
        {state === "ok" && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-ok opacity-40" />}
        <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${color}`} />
      </span>
    </span>
  );
}

// Copyable monospace id (used for run ids, EC2 ids, poc ids).
export function CopyId({ value, className = "" }: { value: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard unavailable — no-op */
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      title="Copy"
      className={`group inline-flex max-w-full items-center gap-1 rounded font-mono text-[11px] text-muted transition-colors hover:text-content ${className}`}
    >
      <span className="truncate">{value}</span>
      {copied ? (
        <Check className="h-3 w-3 shrink-0 text-ok" />
      ) : (
        <Copy className="h-3 w-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-70" />
      )}
    </button>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton rounded-md ${className}`} />;
}
