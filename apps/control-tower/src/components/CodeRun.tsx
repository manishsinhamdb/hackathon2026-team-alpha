"use client";

import { Check, Clock } from "lucide-react";
import type { CoderRowView, RunView } from "@/lib/types";
import { fmtClock, fmtDuration, shortId } from "@/lib/format";
import { Card, CardTitle, CopyId, RunStatusPill } from "./ui";

function Bar({ status, percent }: { status: CoderRowView["status"]; percent?: number }) {
  if (status === "done") return <span className="h-1.5 flex-1 rounded-full bg-green" />;
  if (status === "running") {
    return (
      <span className="h-1.5 flex-1 rounded-full bg-line">
        <span className="block h-1.5 rounded-full bg-run" style={{ width: `${percent ?? 55}%` }} />
      </span>
    );
  }
  return <span className="h-1.5 flex-1 rounded-full bg-line" />;
}

function Dot({ status }: { status: CoderRowView["status"] }) {
  if (status === "done")
    return (
      <span className="flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full bg-green">
        <Check className="h-[11px] w-[11px] text-greenInk" strokeWidth={3} />
      </span>
    );
  if (status === "running") return <span className="h-[18px] w-[18px] shrink-0 rounded-full bg-run" />;
  return <span className="h-[18px] w-[18px] shrink-0 rounded-full border-2 border-line2 box-border" />;
}

function durationText(r: CoderRowView): string {
  if (r.status === "queued") return "queued";
  if (r.duration_ms == null) return r.status === "done" ? "done" : "—";
  return fmtDuration(r.duration_ms);
}

export default function CodeRun({ run, coders }: { run: RunView; coders: CoderRowView[] }) {
  const display = run.status === "succeeded" ? "succeeded" : run.status === "failed" ? "failed" : "running";
  return (
    <Card className="flex flex-col gap-3.5 p-5">
      <div className="flex items-center gap-2.5">
        <CardTitle>Code run</CardTitle>
        <CopyId value={run.run_id} display={shortId(run.run_id)} className="text-xs" />
        <span className="flex-1" />
        <RunStatusPill status={display} />
      </div>

      <div className="flex flex-col gap-2.5">
        {coders.map((c) => (
          <div key={c.key} className="flex items-center gap-3">
            <Dot status={c.status} />
            <span className={`w-[130px] shrink-0 font-medium ${c.status === "queued" ? "text-faint" : c.status === "running" ? "text-runText" : ""}`}>
              {c.label}
            </span>
            <Bar status={c.status} percent={c.percent} />
            <span className={`w-14 shrink-0 text-right font-mono text-xs ${c.status === "running" ? "text-runText" : c.status === "queued" ? "text-dim" : "text-faint"}`}>
              {durationText(c)}
            </span>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-2 text-xs text-faint">
        <Clock className="h-3.5 w-3.5" />
        {run.started_at ? `Started ${fmtClock(run.started_at)} · ` : ""}typical duration 6–8 min
      </div>
    </Card>
  );
}
