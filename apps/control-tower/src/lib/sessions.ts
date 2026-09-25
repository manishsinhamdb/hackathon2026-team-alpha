"use client";

// Client-side chat sessions, persisted in localStorage and shared between the workspace and the library.
// A session is a browser-local chat thread (id generated per tab); conversation *history* still comes from
// the DB on load. This store only tracks the session list (id, name, turn count, last-active time) and the
// active session id — no server involvement.
import { useCallback, useEffect, useState } from "react";

export interface Session {
  id: string;
  name: string;
  turns: number;
  updatedAt: number; // ms epoch of last activity
}

const LIST_KEY = "ct.sessions.v1";
const ACTIVE_KEY = "ct.active.v1";
const EVENT = "ct-sessions-changed";

function genId(): string {
  const rnd =
    typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID().slice(0, 8)
      : Math.random().toString(36).slice(2, 10);
  return `ct-${rnd}`;
}

function read(): { sessions: Session[]; activeId: string } {
  if (typeof window === "undefined") return { sessions: [], activeId: "" };
  try {
    const sessions = JSON.parse(localStorage.getItem(LIST_KEY) || "[]") as Session[];
    const activeId = localStorage.getItem(ACTIVE_KEY) || (sessions[0]?.id ?? "");
    return { sessions: Array.isArray(sessions) ? sessions : [], activeId };
  } catch {
    return { sessions: [], activeId: "" };
  }
}

function write(sessions: Session[], activeId: string) {
  localStorage.setItem(LIST_KEY, JSON.stringify(sessions));
  localStorage.setItem(ACTIVE_KEY, activeId);
  window.dispatchEvent(new Event(EVENT));
}

export function useSessions() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string>("");

  const refresh = useCallback(() => {
    const { sessions, activeId } = read();
    setSessions(sessions);
    setActiveId(activeId);
  }, []);

  // Initialise on mount (creating a first session if none), and stay in sync with other tabs/panes.
  useEffect(() => {
    let { sessions, activeId } = read();
    if (sessions.length === 0) {
      const s: Session = { id: genId(), name: "Session 1", turns: 0, updatedAt: Date.now() };
      sessions = [s];
      activeId = s.id;
      write(sessions, activeId);
    } else if (!sessions.some((s) => s.id === activeId)) {
      activeId = sessions[0].id;
      write(sessions, activeId);
    }
    setSessions(sessions);
    setActiveId(activeId);

    window.addEventListener(EVENT, refresh);
    window.addEventListener("storage", refresh);
    return () => {
      window.removeEventListener(EVENT, refresh);
      window.removeEventListener("storage", refresh);
    };
  }, [refresh]);

  const newSession = useCallback(() => {
    const { sessions } = read();
    const s: Session = { id: genId(), name: `Session ${sessions.length + 1}`, turns: 0, updatedAt: Date.now() };
    const next = [s, ...sessions];
    write(next, s.id);
  }, []);

  const selectSession = useCallback((id: string) => {
    const { sessions } = read();
    if (sessions.some((s) => s.id === id)) write(sessions, id);
  }, []);

  const renameSession = useCallback((id: string, name: string) => {
    const { sessions, activeId } = read();
    write(sessions.map((s) => (s.id === id ? { ...s, name } : s)), activeId);
  }, []);

  const closeSession = useCallback((id: string) => {
    const { sessions, activeId } = read();
    const next = sessions.filter((s) => s.id !== id);
    const nextActive = activeId === id ? next[0]?.id ?? "" : activeId;
    if (next.length === 0) {
      const s: Session = { id: genId(), name: "Session 1", turns: 0, updatedAt: Date.now() };
      write([s], s.id);
    } else {
      write(next, nextActive);
    }
  }, []);

  // Record a chat turn against the active session (bumps the turn count + last-active time).
  const recordTurn = useCallback((id: string) => {
    const { sessions, activeId } = read();
    write(sessions.map((s) => (s.id === id ? { ...s, turns: s.turns + 1, updatedAt: Date.now() } : s)), activeId);
  }, []);

  return { sessions, activeId, newSession, selectSession, renameSession, closeSession, recordTurn };
}
