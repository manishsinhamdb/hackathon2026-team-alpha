"use client";

// Client hook that polls the BFF session-status route (backed by the platform runtime-sessions list) and
// returns a { sessionId: busy } map. Used for the "busy" dot in the Sessions cards and the Chat header
// (Round 4, items 2 & 3). Pauses when the tab is hidden. A status-probe failure is non-fatal (empty map).
import { useEffect, useState } from "react";
import type { SessionStatusResponse } from "./sessionStatus";

export function useSessionBusy(ids: string[], intervalMs = 5000): Record<string, boolean> {
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const key = ids.filter(Boolean).join(",");

  useEffect(() => {
    if (!key) {
      setBusy({});
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`/api/sessions/status?ids=${encodeURIComponent(key)}`, { cache: "no-store" });
        if (!res.ok) return;
        const data = (await res.json()) as SessionStatusResponse;
        if (cancelled) return;
        const m: Record<string, boolean> = {};
        for (const [id, s] of Object.entries(data.sessions ?? {})) m[id] = !!s.busy;
        setBusy(m);
      } catch {
        /* non-fatal — leave the last known state */
      }
    };
    poll();
    const t = setInterval(() => {
      if (!document.hidden) poll();
    }, intervalMs);
    const onVis = () => {
      if (!document.hidden) poll();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      cancelled = true;
      clearInterval(t);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [key, intervalMs]);

  return busy;
}
