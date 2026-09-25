"use client";

import { ChevronDown, PlusCircle, RefreshCw, TowerControl } from "lucide-react";
import type { PocSummary } from "@/lib/types";
import { HealthDot, StatusPill } from "./ui";

export default function TopBar({
  pocs,
  selected,
  onSelect,
  onNewPoc,
  onNewSession,
  sessionId,
  connection,
}: {
  pocs: PocSummary[];
  selected: string;
  onSelect: (id: string) => void;
  onNewPoc: () => void;
  onNewSession: () => void;
  sessionId: string;
  connection: "ok" | "error" | "loading";
}) {
  const current = pocs.find((p) => p.poc_id === selected);

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-ink px-4 py-2.5 text-white">
      <div className="flex items-center gap-2.5">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-green-base/15 text-green-base">
          <TowerControl className="h-5 w-5" />
        </span>
        <div className="leading-tight">
          <div className="text-sm font-semibold tracking-tight">
            POC Builder <span className="text-white/40">—</span> Control Tower
          </div>
          <div className="text-[11px] text-white/45">Chat + live pipeline</div>
        </div>
      </div>

      {/* POC selector */}
      <div className="flex items-center gap-2">
        <div className="relative">
          <select
            aria-label="Select POC"
            value={selected}
            onChange={(e) => onSelect(e.target.value)}
            className="max-w-[320px] appearance-none truncate rounded-md border border-white/15 bg-white/5 py-1.5 pl-3 pr-8 text-sm text-white outline-none transition-colors hover:border-white/30 focus:border-green-base"
          >
            <option value="" className="bg-ink">— select a POC —</option>
            {pocs.map((p) => (
              <option key={p.poc_id} value={p.poc_id} className="bg-ink">
                {p.title}
                {p.versions.spec ? ` · spec ${p.versions.spec}` : ""}
              </option>
            ))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-white/50" />
        </div>
        {current && <StatusPill status={current.status} />}
      </div>

      <div className="flex-1" />

      <div className="flex items-center gap-2">
        <button
          onClick={onNewPoc}
          className="inline-flex items-center gap-1.5 rounded-md bg-green-base px-3 py-1.5 text-sm font-semibold text-ink transition-colors hover:bg-green-dark hover:text-white"
        >
          <PlusCircle className="h-4 w-4" /> New POC
        </button>
        <button
          onClick={onNewSession}
          className="inline-flex items-center gap-1.5 rounded-md border border-white/15 px-3 py-1.5 text-sm text-white/90 transition-colors hover:border-white/30 hover:bg-white/5"
        >
          <RefreshCw className="h-3.5 w-3.5" /> New session
        </button>
        <div className="ml-1 flex items-center gap-2 border-l border-white/10 pl-3">
          <HealthDot state={connection} />
          <span className="hidden font-mono text-[11px] text-white/45 sm:inline" title="Chat session id">
            {sessionId || "…"}
          </span>
        </div>
      </div>
    </header>
  );
}
