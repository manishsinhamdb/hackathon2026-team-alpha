"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Paperclip, Send } from "lucide-react";
import type { ConversationMessage } from "@/lib/types";
import { pocIdIn } from "@/lib/live";
import { postChat, type ChatOutcome } from "@/lib/chat";
import { useSessionBusy } from "@/lib/useSessionStatus";
import type { NoticeAction, Transition } from "@/lib/transitions";
import { useToast } from "./Toasts";
import MessageBubble, { ReplyingPill } from "./MessageBubble";
import SystemNotice from "./SystemNotice";
import { EditButton, InlineEdit } from "./LabelEdit";

interface LiveMessage {
  role: "user" | "assistant" | "system" | "notice";
  content: string;
  at: number;
  notice?: Transition; // role "notice": a pipeline transition card posted by the tower (Round 5)
}

// What the workspace can do with the chat pane (Round 5): send through the normal path (Retry / Continue,
// the auto check-in), post a transition card, and ask whether the session is free.
export interface ChatApi {
  // Returns false (and sends nothing) when a turn is already in flight or queued.
  send: (message: string, opts?: { display?: string; auto?: boolean }) => boolean;
  postNotice: (notice: Transition) => void;
  sessionState: () => { inFlight: boolean; queued: boolean };
}

// Canned actions operate on a POC: the chat agent resolves the POC from natural language, so the poc_id is
// appended to the message (the transcript still shows the friendly label).
export function scopeToPoc(message: string, pocId?: string): string {
  return pocId ? `${message}\n\n(This is about POC ${pocId}.)` : message;
}

// A message waiting for the session to free (after a 409 SESSION_BUSY), plus when it was queued (for the
// live timer). The user's message bubble is already shown; only the reply is pending.
interface Queued {
  message: string;
  display?: string;
  since: number;
}

const RETRY_MS = 5_000; // poll the busy session every 5 s, then auto-send when free
const CONFIRM_AGE_MS = 30_000; // confirm before stopping a turn older than this

// Compact action chips. `label` is what the transcript shows; `message` is the canned text actually sent
// to the chat agent — unchanged from the original wiring so behaviour is identical.
const ACTIONS: { label: string; message: string }[] = [
  { label: "Show me the spec", message: "Show me the spec" },
  { label: "Build it", message: "Looks good, go ahead and build it" },
  { label: "Deploy · 4h TTL", message: "Deploy it, I don't need to review the code (4-hour TTL)" },
  { label: "How's it going?", message: "How's it going?" },
  { label: "Tear it down", message: "Tear it down" },
  { label: "Retry", message: "Retry" },
];

export default function Chat({
  sessionId,
  sessionCount,
  pocId,
  onSent,
  onNewSession,
  onPocDetected,
  sessionName,
  onRenameSession,
  apiRef,
}: {
  sessionId: string;
  sessionCount: number;
  pocId: string;
  onSent: () => void;
  onNewSession: () => void;
  onPocDetected?: (pocId: string) => void;
  sessionName?: string;
  onRenameSession?: (name: string) => void;
  apiRef?: React.MutableRefObject<ChatApi | null>;
}) {
  const [messages, setMessages] = useState<LiveMessage[]>([]);
  const [recovered, setRecovered] = useState<ConversationMessage[]>([]);
  const [showRecovered, setShowRecovered] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false); // our own turn is in flight
  const [queued, setQueued] = useState<Queued | null>(null);
  const [stopping, setStopping] = useState(false);
  const [turnStartedAt, setTurnStartedAt] = useState<number | null>(null);
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const [editingName, setEditingName] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const toast = useToast();

  // Guards: bumped to invalidate an in-flight send (on Stop) or the queue retry loop (on New session/Stop).
  const sendGuardRef = useRef(0);
  const queueGuardRef = useRef(0);
  // A message to send once a freshly-created session becomes active ("New session" while queued).
  const carryoverRef = useRef<{ message: string; display?: string } | null>(null);

  // Busy status of this session from the runtime-sessions poll (drives the header dot + a header Stop when a
  // turn is running but not one we started locally, e.g. a previously-accepted "pending" turn).
  const busyMap = useSessionBusy(sessionId ? [sessionId] : []);
  const sessionBusy = !!busyMap[sessionId];

  const pushAssistant = useCallback(
    (outcome: Extract<ChatOutcome, { kind: "ok" }>) => {
      setMessages((m) => [...m, { role: "assistant", content: outcome.reply, at: Date.now() }]);
      const detected = pocIdIn(String(outcome.reply ?? ""));
      if (detected) onPocDetected?.(detected);
    },
    [onPocDetected],
  );

  const pushError = useCallback(
    (outcome: Extract<ChatOutcome, { kind: "error" }>) => {
      setMessages((m) => [...m, { role: "system", content: `⚠ ${outcome.message}`, at: Date.now() }]);
      toast.show({ message: outcome.message, tone: "error", detail: outcome.detail });
    },
    [toast],
  );

  // `auto` marks the tower's own check-in ("How's it going?" after a transition): if the platform says the
  // session is busy it is DROPPED rather than queued, so it can never stack a second message.
  const send = useCallback(
    async (text: string, display?: string, auto?: boolean) => {
      const message = text.trim();
      if (!message || !sessionId || busy || queued) return;
      setMessages((m) => [...m, { role: "user", content: (display ?? text).trim(), at: Date.now() }]);
      setInput("");
      const guard = ++sendGuardRef.current;
      setBusy(true);
      setTurnStartedAt(Date.now());
      const outcome = await postChat(fetch, { session_id: sessionId, message });
      if (sendGuardRef.current !== guard) return; // this turn was stopped/superseded — ignore its result
      setBusy(false);
      setTurnStartedAt(null);
      if (outcome.kind === "busy" && auto) {
        setMessages((m) => [...m, { role: "system", content: "The agent is busy — skipped the automatic check-in.", at: Date.now() }]);
      } else if (outcome.kind === "busy") {
        setQueued({ message, display, since: Date.now() }); // starts the retry loop (effect below)
      } else if (outcome.kind === "error") {
        pushError(outcome);
        onSent();
      } else {
        pushAssistant(outcome);
        onSent();
      }
    },
    [sessionId, busy, queued, onSent, pushAssistant, pushError],
  );

  // Keep a stable ref to the latest `send` so the session-change effect (carryover) can call it without a
  // stale closure and without re-running on every send identity change.
  const sendRef = useRef(send);
  sendRef.current = send;

  // The busy-queue retry loop: poll the session (a fresh invoke of the same message) until it's no longer
  // busy, then land the reply. A 409 means the send was rejected (not enqueued), so re-attempting is safe;
  // any non-busy outcome stops the loop, so an accepted turn is never sent twice.
  useEffect(() => {
    if (!queued) return;
    const guard = ++queueGuardRef.current;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const attempt = async () => {
      if (queueGuardRef.current !== guard) return;
      const outcome = await postChat(fetch, { session_id: sessionId, message: queued.message });
      if (queueGuardRef.current !== guard) return;
      if (outcome.kind === "busy") {
        timer = setTimeout(attempt, RETRY_MS);
        return;
      }
      setQueued(null);
      if (outcome.kind === "ok") pushAssistant(outcome);
      else if (outcome.kind === "error") pushError(outcome);
      onSent();
    };
    attempt(); // immediate first retry (covers re-arm after a Stop); 409s cheaply if still busy
    return () => {
      queueGuardRef.current += 1;
      if (timer) clearTimeout(timer);
    };
  }, [queued, sessionId, onSent, pushAssistant, pushError]);

  // Reset live transcript when the session id changes ("New session"); send any carried-over message.
  useEffect(() => {
    setMessages([]);
    setQueued(null);
    queueGuardRef.current += 1;
    const carry = carryoverRef.current;
    if (carry) {
      carryoverRef.current = null;
      const t = setTimeout(() => sendRef.current(carry.message, carry.display), 0);
      return () => clearTimeout(t);
    }
  }, [sessionId]);

  // Recover the selected POC's stored conversation on load / selection.
  useEffect(() => {
    if (!pocId) {
      setRecovered([]);
      setShowRecovered(false);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/pocs/${pocId}/conversation`, { cache: "no-store" });
        const data = await res.json();
        if (!cancelled && res.ok) setRecovered((data.messages as ConversationMessage[]) ?? []);
      } catch {
        if (!cancelled) setRecovered([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pocId]);

  // 1 s ticker for the queued timer / turn age (only runs while something is pending).
  useEffect(() => {
    if (!queued && !busy) return;
    const t = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(t);
  }, [queued, busy]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, recovered, busy, queued, showRecovered]);

  // Cancel the running turn on this session (item 3). Confirm only for a turn older than 30 s.
  const stopTurn = useCallback(
    async (opts?: { ageMs?: number; rearmQueue?: boolean }) => {
      if (!sessionId) return;
      const age = opts?.ageMs ?? (turnStartedAt ? Date.now() - turnStartedAt : 0);
      if (age > CONFIRM_AGE_MS && !window.confirm("This turn has been running a while. Stop it?")) return;
      setStopping(true);
      sendGuardRef.current += 1; // ignore any in-flight send result
      try {
        const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/stop`, { method: "POST" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          toast.show({ message: data.error || "Couldn’t stop the running turn.", tone: "error", detail: data.detail });
        } else {
          setMessages((m) => [...m, { role: "system", content: "⏹ Turn cancelled.", at: Date.now() }]);
        }
      } catch {
        toast.error("Couldn’t stop the running turn.");
      } finally {
        setStopping(false);
        setBusy(false);
        setTurnStartedAt(null);
        // Re-arm the queue loop so a queued message sends promptly now the session is freeing.
        if (opts?.rearmQueue) setQueued((q) => (q ? { ...q } : q));
        onSent();
      }
    },
    [sessionId, turnStartedAt, toast, onSent],
  );

  // "New session" while queued: move the pending message to a fresh session and send it there.
  const queueToNewSession = useCallback(() => {
    if (queued) carryoverRef.current = { message: queued.message, display: queued.display };
    setQueued(null);
    queueGuardRef.current += 1;
    onNewSession();
  }, [queued, onNewSession]);

  // Canned actions operate on the SELECTED POC. The chat agent resolves the POC from natural language, so
  // we append the poc_id to the message. The transcript still shows the friendly label. Free-typed messages
  // are sent verbatim (they may be a transcript that starts a NEW POC, where scoping would be wrong).
  const sendAction = (label: string, message: string) => {
    send(scopeToPoc(message, pocId), label);
  };

  // A transition card's buttons: canned sends (scoped to the notice's POC), focus the composer, or a link.
  const onNoticeAction = (a: NoticeAction, notice: Transition) => {
    if (a.kind === "focus") {
      taRef.current?.focus();
    } else if (a.kind === "send") {
      send(a.scoped ? scopeToPoc(a.message, notice.poc_id) : a.message, a.label);
    }
  };

  const onPickFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    setInput((cur) => (cur ? `${cur}\n\n${text}` : text));
    if (fileRef.current) fileRef.current.value = "";
    taRef.current?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      send(input);
    }
  };

  const canSend = !!sessionId && !busy && !queued && !stopping;

  // Expose the pane's send path to the workspace (Retry / Continue, transition notices, auto check-in).
  // Re-assigned every render so it always sees the current busy/queued state.
  if (apiRef) {
    apiRef.current = {
      send: (message, opts) => {
        if (!canSend) return false;
        void send(message, opts?.display, opts?.auto);
        return true;
      },
      postNotice: (notice) =>
        setMessages((m) => [...m, { role: "notice", content: notice.title, at: Date.now(), notice }]),
      sessionState: () => ({ inFlight: busy || stopping || !sessionId, queued: !!queued }),
    };
  }
  // A running turn we can stop: our own in-flight turn, or a turn the status poll sees on this session.
  const showHeaderStop = (sessionBusy || busy) && !queued;

  return (
    <section className="flex h-full min-h-0 flex-col border-r border-line bg-ink">
      {/* Header */}
      <div className="flex h-[52px] shrink-0 items-center gap-3 border-b border-line px-5">
        {/* Headline: the session name (inline-editable, stored client-side with the sessions list). */}
        <div className="flex min-w-0 items-center gap-1">
          {editingName && onRenameSession ? (
            <InlineEdit
              value={sessionName ?? ""}
              placeholder="Session name"
              ariaLabel="Session name"
              maxLength={60}
              onSave={(v) => v && onRenameSession(v)}
              onDone={() => setEditingName(false)}
              className="w-44 font-sora text-sm font-semibold"
            />
          ) : (
            <>
              <div className="truncate font-sora text-sm font-semibold" title="Conversation">
                {sessionName || "Conversation"}
              </div>
              {onRenameSession && <EditButton label="Rename session" onClick={() => setEditingName(true)} />}
            </>
          )}
        </div>
        <span className="flex min-w-0 items-center gap-1.5 truncate font-mono text-xs text-faint">
          {(sessionBusy || busy) && (
            <span className="h-2 w-2 rounded-full bg-run" title="This session has a running turn" />
          )}
          session {sessionId || "…"}
        </span>
        {showHeaderStop && (
          <button
            onClick={() => stopTurn()}
            disabled={stopping}
            className="rounded-full border border-fail/50 px-2.5 py-0.5 text-[11px] font-semibold text-fail hover:bg-failBg disabled:opacity-50"
          >
            {stopping ? "Stopping…" : "Stop"}
          </button>
        )}
        <span className="flex-1" />
        <Link href="/library" className="text-xs font-semibold text-content hover:text-green">
          Sessions ({sessionCount})
        </Link>
        <button onClick={onNewSession} className="text-xs font-semibold text-content hover:text-green">
          New session
        </button>
      </div>

      {/* Log */}
      <div ref={logRef} className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-5">
        {recovered.length > 0 && (
          <button
            onClick={() => setShowRecovered((v) => !v)}
            className="self-center rounded-full border border-line px-3 py-1 text-xs text-faint hover:text-content"
          >
            {showRecovered ? "Hide" : "Show"} {recovered.length} earlier {recovered.length === 1 ? "message" : "messages"}
          </button>
        )}
        {showRecovered &&
          recovered.map((m, i) => (
            <MessageBubble key={`r-${i}`} role={m.role === "user" ? "user" : "assistant"} content={m.content} recovered />
          ))}

        {messages.length === 0 && (recovered.length === 0 || !showRecovered) && (
          <div className="m-auto max-w-xs text-center text-sm text-faint">
            Paste a meeting transcript to draft a new POC, or pick one above and use a quick action below.
          </div>
        )}

        {messages.map((m, i) =>
          m.role === "notice" && m.notice ? (
            <SystemNotice key={i} notice={m.notice} at={m.at} disabled={!canSend} onAction={onNoticeAction} />
          ) : m.role !== "notice" ? (
            <MessageBubble key={i} role={m.role} content={m.content} at={m.at} />
          ) : null,
        )}
        {busy && <ReplyingPill onStop={() => stopTurn()} stopping={stopping} />}
      </div>

      {/* Queued (session busy) notice */}
      {queued && (
        <QueuedNotice
          waitedMs={nowMs - queued.since}
          onStop={() => stopTurn({ ageMs: nowMs - queued.since, rearmQueue: true })}
          onNewSession={queueToNewSession}
          stopping={stopping}
        />
      )}

      {/* Composer */}
      <div className="flex shrink-0 flex-col gap-2.5 border-t border-line px-5 pb-[18px] pt-3.5">
        <div className="flex flex-wrap gap-2">
          {ACTIONS.map(({ label, message }) => (
            <button
              key={label}
              disabled={!canSend}
              onClick={() => sendAction(label, message)}
              className="h-[30px] rounded-full border border-line2 bg-surface px-3 text-xs text-content transition-colors hover:border-green hover:text-green disabled:cursor-not-allowed disabled:opacity-50"
            >
              {label}
            </button>
          ))}
        </div>
        <div className="flex items-end gap-2.5 rounded-xl border border-line2 bg-surface py-2.5 pl-3.5 pr-2.5 focus-within:border-green">
          <textarea
            ref={taRef}
            value={input}
            rows={2}
            aria-label="Message the agent"
            placeholder="Message the agent, or paste a meeting transcript to start a new POC  ·  Enter to send, Shift+Enter for a new line"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            className="max-h-40 flex-1 resize-none bg-transparent text-[13px] text-content outline-none placeholder:text-faint"
          />
          <label
            className="flex h-9 w-9 shrink-0 cursor-pointer items-center justify-center rounded-lg border border-line2 text-muted transition-colors hover:text-content"
            title="Attach a .txt transcript"
          >
            <Paperclip className="h-4 w-4" />
            <input ref={fileRef} type="file" accept=".txt,text/plain" className="hidden" onChange={onPickFile} />
          </label>
          <button
            disabled={!canSend || !input.trim()}
            onClick={() => send(input)}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-green text-greenInk transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
            title="Send"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>
    </section>
  );
}

// The inline notice shown while a message waits for the session to free (item 2). Live timer + two actions.
function QueuedNotice({
  waitedMs,
  onStop,
  onNewSession,
  stopping,
}: {
  waitedMs: number;
  onStop: () => void;
  onNewSession: () => void;
  stopping: boolean;
}) {
  const s = Math.max(0, Math.floor(waitedMs / 1000));
  const timer = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  return (
    <div className="mx-5 mb-1 rounded-xl border border-run/40 bg-runBg px-3.5 py-3">
      <div className="flex items-center gap-2 text-sm text-runText">
        <span className="h-2 w-2 animate-blink rounded-full bg-run" />
        <span className="flex-1">The agent is still finishing your previous message…</span>
        <span className="tabular-nums text-xs text-runText/80">waiting {timer}</span>
      </div>
      <p className="mt-1 text-xs text-runText/80">Your message is queued and will send automatically the moment the agent is free.</p>
      <div className="mt-2 flex gap-2">
        <button
          onClick={onStop}
          disabled={stopping}
          className="rounded-lg border border-fail/50 px-3 py-1.5 text-xs font-semibold text-fail hover:bg-failBg disabled:opacity-50"
        >
          {stopping ? "Stopping…" : "Stop the running turn"}
        </button>
        <button
          onClick={onNewSession}
          className="rounded-lg border border-line2 px-3 py-1.5 text-xs font-semibold text-content hover:bg-surface"
        >
          New session
        </button>
      </div>
    </div>
  );
}
