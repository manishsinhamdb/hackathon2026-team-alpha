"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { PocSummary } from "@/lib/types";
import { useSessions } from "@/lib/sessions";
import Chat from "./Chat";
import PipelineBoard from "./PipelineBoard";
import TopBar from "./TopBar";
import { ToastProvider } from "./Toasts";

const POLL_MS = 10_000;
const PROJECT = "hackathon2026"; // the deploy-target platform project shown in the health chip

function ControlTowerInner() {
  const [pocs, setPocs] = useState<PocSummary[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [listError, setListError] = useState<string>("");
  const [loaded, setLoaded] = useState(false);
  // Bumped after a chat turn completes so the board fetches immediately instead of waiting for the next tick.
  const [refreshSignal, setRefreshSignal] = useState(0);
  const { sessions, activeId, newSession, recordTurn } = useSessions();

  // Honour ?poc=<id> (e.g. opened from the library) once on mount.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("poc");
    if (id) setSelected(id);
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
    const timer = setInterval(() => {
      if (!document.hidden) loadPocs();
    }, POLL_MS);
    const onVis = () => {
      if (!document.hidden) loadPocs();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [loadPocs]);

  // The workspace selector hides archived POCs (the library's Archived filter shows them).
  const selectablePocs = useMemo(() => pocs.filter((p) => !p.ui_archived), [pocs]);

  const onNewPoc = () => {
    setSelected("");
    newSession();
  };
  const onSent = useCallback(() => {
    setRefreshSignal((n) => n + 1);
    if (activeId) recordTurn(activeId);
  }, [activeId, recordTurn]);

  const connection: "ok" | "error" | "loading" = listError ? "error" : loaded ? "ok" : "loading";

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <TopBar
        pocs={selectablePocs}
        selected={selected}
        onSelect={setSelected}
        onNewPoc={onNewPoc}
        connection={connection}
        project={PROJECT}
      />

      {/* Chat (5/12) · Pipeline (7/12); stacked below lg. */}
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <div className="flex h-[50vh] min-h-0 shrink-0 flex-col lg:h-auto lg:w-[41.6%] lg:min-w-[420px]">
          <Chat
            sessionId={activeId}
            sessionCount={sessions.length}
            pocId={selected}
            onSent={onSent}
            onNewSession={newSession}
          />
        </div>
        <div className="min-h-0 flex-1 bg-panel">
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
