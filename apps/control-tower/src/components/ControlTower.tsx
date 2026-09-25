"use client";

import { useCallback, useEffect, useState } from "react";
import type { PocSummary } from "@/lib/types";
import Chat from "./Chat";
import PipelineBoard from "./PipelineBoard";

const POLL_MS = 10_000;

function newSessionId(): string {
  const rnd = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID().slice(0, 8) : Math.random().toString(36).slice(2, 10);
  return `ct-${rnd}`;
}

export default function ControlTower() {
  const [pocs, setPocs] = useState<PocSummary[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [sessionId, setSessionId] = useState<string>("");
  const [listError, setListError] = useState<string>("");
  // Bumped after a chat turn completes so the board fetches immediately instead of waiting for the next tick.
  const [refreshSignal, setRefreshSignal] = useState(0);

  // Session id is generated per browser tab, after mount (avoids SSR/hydration mismatch).
  useEffect(() => {
    setSessionId(newSessionId());
  }, []);

  const loadPocs = useCallback(async () => {
    try {
      const res = await fetch("/api/pocs", { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setPocs(data.pocs as PocSummary[]);
      setListError("");
    } catch (err) {
      setListError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  // Poll the POC list on the same 10 s cadence; pause when the tab is hidden.
  useEffect(() => {
    loadPocs();
    let timer: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (timer) return;
      timer = setInterval(() => {
        if (!document.hidden) loadPocs();
      }, POLL_MS);
    };
    const stop = () => {
      if (timer) clearInterval(timer);
      timer = null;
    };
    start();
    const onVis = () => {
      if (document.hidden) return;
      loadPocs();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [loadPocs]);

  const onNewSession = () => setSessionId(newSessionId());
  const onSent = useCallback(() => setRefreshSignal((n) => n + 1), []);

  return (
    <div className="app">
      <div className="topbar">
        <h1>🛫 Control Tower</h1>
        <span className="poc-list">
          <label htmlFor="poc-select" style={{ color: "var(--muted)" }}>POC</label>
          <select
            id="poc-select"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
          >
            <option value="">— select a POC —</option>
            {pocs.map((p) => (
              <option key={p.poc_id} value={p.poc_id}>
                {p.title} · {p.status} {p.versions.spec ? `· spec ${p.versions.spec}` : ""}
              </option>
            ))}
          </select>
        </span>
        <div className="spacer" />
        {listError ? <span className="err mono">list error: {listError}</span> : null}
        <span className="session-id">session {sessionId || "…"}</span>
        <button onClick={onNewSession}>New session</button>
      </div>

      <div className="panes">
        <div className="pane-left">
          <Chat sessionId={sessionId} pocId={selected} onSent={onSent} />
        </div>
        <div className="pane-right">
          <PipelineBoard pocId={selected} refreshSignal={refreshSignal} pollMs={POLL_MS} />
        </div>
      </div>
    </div>
  );
}
