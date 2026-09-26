"use client";

import { useEffect, useState } from "react";
import { ExternalLink } from "lucide-react";
import type { ArtifactList } from "@/lib/artifacts";
import { Card, CardTitle } from "./ui";

const DISABLED_TIP = "S3 access not configured on the server";

function fmtSize(n?: number): string {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// The Artefacts card (Round 5, item 2): the POC's S3 objects per stage, each with an "open" link that goes
// through the BFF (a <= 5 min presigned GET — the AWS keys never reach the browser). Refetches when
// `refreshKey` changes (the board passes a signature of its runs, so a finished stage shows its outputs).
export default function Artifacts({ pocId, refreshKey }: { pocId: string; refreshKey: string }) {
  const [list, setList] = useState<ArtifactList | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!pocId) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/pocs/${encodeURIComponent(pocId)}/artifacts`, { cache: "no-store" });
        const data = await res.json();
        if (cancelled) return;
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
        setList(data as ArtifactList);
        setError("");
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pocId, refreshKey]);

  // Reset when switching POCs so a stale list never shows under another POC.
  useEffect(() => {
    setList(null);
    setError("");
  }, [pocId]);

  const total = list?.groups.reduce((n, g) => n + g.items.length, 0) ?? 0;

  return (
    <Card className="flex flex-col gap-2.5 p-5">
      <div className="flex items-center gap-2.5">
        <CardTitle>Artefacts</CardTitle>
        <span className="font-mono text-[11px] text-faint">pocs/{pocId}/</span>
        <span className="flex-1" />
        {list && !list.s3 && (
          <span className="rounded-full bg-amberBg px-2 py-0.5 text-[11px] font-semibold text-amber" title={list.note ?? DISABLED_TIP}>
            from DB · open disabled
          </span>
        )}
        {list && <span className="text-xs text-faint">{total} files</span>}
      </div>
      {error && <p className="text-xs text-fail">Couldn’t list artefacts: {error}</p>}
      {!list && !error && <p className="text-xs text-faint">Loading…</p>}
      {list && total === 0 && <p className="text-xs text-faint">No artefacts yet.</p>}
      {list?.groups.map((g) => (
        <div key={g.stage} className="flex flex-col gap-1 border-t border-line pt-2">
          <div className="text-[11px] uppercase tracking-[0.06em] text-faint">{g.label}</div>
          {g.items.map((it) => (
            <div key={it.key} className="flex items-center gap-2 text-xs">
              {it.group && <span className="shrink-0 font-mono text-[11px] text-dim">{it.group.length > 16 ? `${it.group.slice(0, 14)}…` : it.group}</span>}
              <span className="min-w-0 flex-1 truncate font-mono text-muted" title={it.key}>
                {it.name}
              </span>
              {it.size != null && <span className="shrink-0 tabular-nums text-faint">{fmtSize(it.size)}</span>}
              {list.s3 ? (
                <a
                  href={`/api/pocs/${encodeURIComponent(pocId)}/artifacts/open?key=${encodeURIComponent(it.key)}&redirect=1`}
                  target="_blank"
                  rel="noreferrer"
                  className="flex shrink-0 items-center gap-1 rounded-md border border-line2 px-2 py-0.5 text-[11px] text-content hover:border-green hover:text-green"
                >
                  open <ExternalLink className="h-3 w-3" />
                </a>
              ) : (
                <span
                  aria-disabled
                  title={DISABLED_TIP}
                  className="flex shrink-0 cursor-not-allowed items-center gap-1 rounded-md border border-line px-2 py-0.5 text-[11px] text-dim"
                >
                  open <ExternalLink className="h-3 w-3" />
                </span>
              )}
            </div>
          ))}
        </div>
      ))}
    </Card>
  );
}
