"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ChevronDown, Droplet, Library, Plus, User } from "lucide-react";
import type { PocSummary } from "@/lib/types";
import { fmtDateShort } from "@/lib/format";
import { HealthChip, StatusPill } from "./ui";
import ThemeToggle from "./ThemeToggle";

// The product mark: a green rounded square with a droplet glyph (the design's leaf/drop mark).
function ProductMark() {
  return (
    <div className="flex min-w-[200px] items-center gap-3">
      <div className="flex h-[34px] w-[34px] items-center justify-center rounded-[10px] bg-green">
        <Droplet className="h-[18px] w-[18px] text-greenInk" fill="currentColor" strokeWidth={0} />
      </div>
      <div className="flex flex-col gap-px leading-none">
        <div className="font-sora text-[15px] font-semibold tracking-tight">POC Builder</div>
        <div className="text-[11px] uppercase tracking-[0.08em] text-faint">Control Tower</div>
      </div>
    </div>
  );
}

export default function TopBar({
  pocs,
  selected,
  onSelect,
  onNewPoc,
  connection,
  project,
}: {
  pocs: PocSummary[];
  selected: string;
  onSelect: (id: string) => void;
  onNewPoc: () => void;
  connection: "ok" | "error" | "loading";
  project: string;
}) {
  const current = pocs.find((p) => p.poc_id === selected);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  return (
    <header className="flex h-16 shrink-0 items-center gap-5 border-b border-line bg-ink px-6">
      <ProductMark />

      {/* POC selector */}
      <div ref={ref} className="relative min-w-0 flex-1" style={{ maxWidth: 640 }}>
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex h-[42px] w-full items-center gap-3 rounded-[10px] border border-line bg-surface px-3.5 text-left text-content"
        >
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${connection === "error" ? "bg-fail" : connection === "loading" ? "bg-faint" : "bg-run"}`}
          />
          {current ? (
            <>
              <span className="min-w-0 truncate font-semibold">{current.title}</span>
              {current.versions.spec && (
                <span className="shrink-0 font-mono text-xs text-faint">spec {current.versions.spec}</span>
              )}
              <StatusPill status={current.status} />
              <span className="shrink-0 text-xs text-faint">created {fmtDateShort(current.created_at)}</span>
            </>
          ) : (
            <span className="truncate text-faint">Select a POC…</span>
          )}
          <span className="flex-1" />
          <ChevronDown className="h-4 w-4 shrink-0 text-faint" />
        </button>

        {open && (
          <div className="absolute left-0 right-0 top-[48px] z-30 max-h-[60vh] overflow-y-auto rounded-xl border border-line bg-surface p-1.5 shadow-2xl">
            {pocs.length === 0 && <div className="px-3 py-3 text-sm text-faint">No POCs.</div>}
            {pocs.map((p) => (
              <button
                key={p.poc_id}
                onClick={() => {
                  onSelect(p.poc_id);
                  setOpen(false);
                }}
                className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left transition-colors hover:bg-elevated ${
                  p.poc_id === selected ? "bg-elevated" : ""
                }`}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{p.title}</span>
                  <span className="block truncate font-mono text-[11px] text-faint">{p.poc_id}</span>
                </span>
                {p.versions.spec && <span className="shrink-0 font-mono text-[11px] text-faint">spec {p.versions.spec}</span>}
                <StatusPill status={p.status} />
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Right actions */}
      <div className="ml-auto flex items-center gap-2.5">
        <button
          onClick={onNewPoc}
          className="flex h-10 items-center gap-2 rounded-[10px] bg-green px-4 font-semibold text-greenInk transition-opacity hover:opacity-90"
        >
          <Plus className="h-4 w-4" strokeWidth={2.2} /> New POC
        </button>
        <Link
          href="/library"
          className="flex h-10 items-center gap-2 rounded-[10px] border border-line2 px-3.5 font-medium text-content transition-colors hover:bg-surface"
        >
          <Library className="h-4 w-4" /> POC library
        </Link>
        <HealthChip project={project} state={connection} />
        <ThemeToggle />
        <div
          className="flex h-9 w-9 items-center justify-center rounded-full bg-line2 text-content"
          title="Signed-in user"
        >
          <User className="h-4 w-4" />
        </div>
      </div>
    </header>
  );
}
