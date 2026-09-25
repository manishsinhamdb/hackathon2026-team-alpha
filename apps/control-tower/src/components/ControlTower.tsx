"use client";

import { useCallback, useEffect, useState } from "react";
import type { PocSummary } from "@/lib/types";
import Chat from "./Chat";
import PipelineBoard from "./PipelineBoard";
import TopBar from "./TopBar";
import { ToastProvider } from "./Toasts";

const POLL_MS = 10_000;

function newSessionId(): string {
  const rnd =
    typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID().slice(0, 8)
      : Math.random().toString(36).slice(2, 10);
  return `ct-${rnd}`;
}

function ControlTowerInner() {
  const [pocs, setPocs] = useState<PocSummary[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [sessionId, setSessionId] = useState<string>("");
  const [listError, setListError] = useState<string>("");
  const [loaded, setLoaded] = useState(false);
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
    } finally {
      setLoaded(true);
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
  // "New POC": clear the selection and start a fresh session so a pasted transcript starts a brand-new POC
  // (canned actions scope to a selected POC; free-typed input with none selected is sent verbatim).
  const onNewPoc = () => {
    setSelected("");
    setSessionId(newSessionId());
  };
  const onSent = useCallback(() => setRefreshSignal((n) => n + 1), []);

  const connection: "ok" | "error" | "loading" = listError ? "error" : loaded ? "ok" : "loading";

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <TopBar
        pocs={pocs}
        selected={selected}
        onSelect={setSelected}
        onNewPoc={onNewPoc}
        onNewSession={onNewSession}
        sessionId={sessionId}
        connection={connection}
      />

      {/* Two columns ≥1024px; stacked below. */}
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <div className="flex h-[55vh] min-h-0 shrink-0 flex-col border-b border-line lg:h-auto lg:w-[42%] lg:min-w-[380px] lg:max-w-[560px] lg:border-b-0 lg:border-r">
          <Chat sessionId={sessionId} pocId={selected} onSent={onSent} />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto bg-canvas p-4 lg:p-6">
          <PipelineBoard pocId={selected} refreshSignal={refreshSignal} pollMs={POLL_MS} />
        </div>
      </div>
    </div>
  );
}

export default function ControlTower() {
  return (
    <ToastProvider>
      <ControlTowerInner />
    </ToastProvider>
  );
}
