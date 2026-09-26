"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PocSummary } from "@/lib/types";
import { useSessions } from "@/lib/sessions";
import { chooseAutoFollow, newPocIds } from "@/lib/live";
import Chat from "./Chat";
import PipelineBoard from "./PipelineBoard";
import SplitPane from "./SplitPane";
import TopBar from "./TopBar";
import { ToastProvider, useToast } from "./Toasts";

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
  const toast = useToast();
  const pocsRef = useRef<PocSummary[]>([]);
  const pocIdsRef = useRef<string[]>([]); // ids from the last load, for new-POC-during-turn detection (item 4)
  const selectedRef = useRef<string>("");
  const followedRef = useRef<string>(""); // last poc we auto-followed, to avoid re-toasting
  const manualRef = useRef<boolean>(false); // the user picked a POC this session — never auto-override it

  const loadPocs = useCallback(async (): Promise<PocSummary[]> => {
    try {
      const res = await fetch("/api/pocs", { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      const list = data.pocs as PocSummary[];
      pocsRef.current = list;
      pocIdsRef.current = list.map((p) => p.poc_id);
      setPocs(list);
      setListError("");
      return list;
    } catch (err) {
      setListError(err instanceof Error ? err.message : String(err));
      return pocsRef.current;
    } finally {
      setLoaded(true);
    }
  }, []);

  // Apply an auto-follow candidate under the precedence rule (manual selection always wins).
  const tryAutoFollow = useCallback(
    (candidate?: string) => {
      const pick = chooseAutoFollow({ candidate, selected: selectedRef.current, manualChosen: manualRef.current });
      if (!pick || followedRef.current === pick) return;
      followedRef.current = pick;
      setSelected(pick);
      const title = pocsRef.current.find((p) => p.poc_id === pick)?.title;
      toast.show({ message: `Following ${pick}${title ? ` — ${title}` : ""}`, tone: "success" });
    },
    [toast],
  );

  // Honour ?poc=<id> (e.g. opened from the library) once on mount — treat it as a manual choice.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("poc");
    if (id) {
      manualRef.current = true;
      setSelected(id);
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

  // Item 4 fallback: on a fresh session with nothing selected, ask the BFF which POC this session is about
  // (best-effort: newest UI-owned POC) so the board follows something sensible on load.
  useEffect(() => {
    if (!activeId || selectedRef.current || manualRef.current) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/sessions/${encodeURIComponent(activeId)}/poc`, { cache: "no-store" });
        const data = await res.json();
        if (!cancelled && data?.poc?.poc_id) tryAutoFollow(data.poc.poc_id);
      } catch {
        /* non-fatal */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeId, tryAutoFollow]);

  // The workspace selector hides archived POCs (the library's Archived filter shows them).
  const selectablePocs = useMemo(() => pocs.filter((p) => !p.ui_archived), [pocs]);
  selectedRef.current = selected;

  const onSelect = useCallback((id: string) => {
    manualRef.current = true; // explicit pick — auto-follow must never override it
    setSelected(id);
  }, []);

  const onNewPoc = () => {
    setSelected("");
    manualRef.current = false;
    followedRef.current = ""; // a fresh POC may be created next; allow auto-follow again
    newSession();
  };

  const onSent = useCallback(async () => {
    setRefreshSignal((n) => n + 1);
    if (activeId) recordTurn(activeId);
    // A turn may have created a POC. Compare the id set before/after this refresh: a single new id is a POC
    // this turn created — auto-follow it even if the reply text didn't repeat the poc_id (item 4).
    const before = pocIdsRef.current.slice();
    const after = await loadPocs();
    const created = newPocIds(before, after.map((p) => p.poc_id));
    if (created.length === 1) tryAutoFollow(created[0]);
  }, [activeId, recordTurn, loadPocs, tryAutoFollow]);

  // Reply-text detection (a poc_… in the agent's reply) — kept as a fallback path, routed through the same
  // precedence rule.
  const onPocDetected = useCallback((pocId: string) => tryAutoFollow(pocId), [tryAutoFollow]);

  const connection: "ok" | "error" | "loading" = listError ? "error" : loaded ? "ok" : "loading";

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <TopBar
        pocs={selectablePocs}
        selected={selected}
        onSelect={onSelect}
        onNewPoc={onNewPoc}
        connection={connection}
        project={PROJECT}
        followedId={followedRef.current}
      />

      {/* Chat | Pipeline with a draggable split (5/7 default); stacked below lg (handle hidden). */}
      <SplitPane
        leftClassName="flex h-[50vh] min-h-0 shrink-0 flex-col lg:h-auto"
        rightClassName="bg-panel"
        left={
          <Chat
            sessionId={activeId}
            sessionCount={sessions.length}
            pocId={selected}
            onSent={onSent}
            onNewSession={newSession}
            onPocDetected={onPocDetected}
          />
        }
        right={<PipelineBoard pocId={selected} refreshSignal={refreshSignal} />}
      />
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
