"use client";

import type { RunHistoryRow } from "@/lib/types";
import { fmtDateShort, fmtDuration, shortId } from "@/lib/format";
import { Card, CardTitle, CopyId, RunStatusPill } from "./ui";

const COLS = "grid grid-cols-5 gap-2 items-center";

export default function RunHistory({ rows }: { rows: RunHistoryRow[] }) {
  return (
    <Card className="flex flex-col gap-2.5 p-5">
      <div className="flex items-center gap-2.5">
        <CardTitle>Run history</CardTitle>
        <span className="flex-1" />
        <span className="text-xs text-faint">{rows.length} total</span>
      </div>
      <div className={`${COLS} px-1 text-[11px] uppercase tracking-[0.06em] text-faint`}>
        <span>Stage</span>
        <span>Run</span>
        <span>Status</span>
        <span>Started</span>
        <span>Duration</span>
      </div>
      {rows.length === 0 && <div className="border-t border-line px-1 py-3 text-sm text-faint">No runs yet.</div>}
      {rows.map((r) => (
        <div key={r.run_id} className={`${COLS} border-t border-line px-1 py-2 text-[13px]`}>
          <span>{r.stageLabel}</span>
          <CopyId value={r.run_id} display={shortId(r.run_id)} className="text-muted" />
          <span className="flex items-center gap-1.5">
            <RunStatusPill status={r.display} />
            {r.executions ? (
              <span className="text-[11px] text-faint" title="Resumed / handed over to a new execution">
                exec {r.executions}
              </span>
            ) : null}
          </span>
          <span className="text-muted">{fmtDateShort(r.started_at)}</span>
          <span className="text-muted tabular-nums">{fmtDuration(r.duration_ms)}</span>
        </div>
      ))}
    </Card>
  );
}
