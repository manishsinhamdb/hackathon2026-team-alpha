"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Paperclip, Send } from "lucide-react";
import type { ConversationMessage } from "@/lib/types";
import { useToast } from "./Toasts";
import MessageBubble, { ReplyingPill } from "./MessageBubble";

interface LiveMessage {
  role: "user" | "assistant" | "system";
  content: string;
  at: number;
}

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
}: {
  sessionId: string;
  sessionCount: number;
  pocId: string;
  onSent: () => void;
  onNewSession: () => void;
}) {
  const [messages, setMessages] = useState<LiveMessage[]>([]);
  const [recovered, setRecovered] = useState<ConversationMessage[]>([]);
  const [showRecovered, setShowRecovered] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const toast = useToast();

  // Reset live transcript when the session id changes ("New session").
  useEffect(() => {
    setMessages([]);
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

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, recovered, busy, showRecovered]);

  const send = useCallback(
    async (text: string, display?: string) => {
      const message = text.trim();
      if (!message || !sessionId || busy) return;
      setMessages((m) => [...m, { role: "user", content: (display ?? text).trim(), at: Date.now() }]);
      setInput("");
      setBusy(true);
      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId, message }),
        });
        const data = await res.json();
        if (!res.ok) {
          const err = data.error || `HTTP ${res.status}`;
          setMessages((m) => [...m, { role: "system", content: `⚠ ${err}`, at: Date.now() }]);
          toast.error(`Chat failed: ${err}`);
        } else {
          setMessages((m) => [...m, { role: "assistant", content: data.reply, at: Date.now() }]);
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setMessages((m) => [...m, { role: "system", content: `⚠ ${msg}`, at: Date.now() }]);
        toast.error(`Chat failed: ${msg}`);
      } finally {
        setBusy(false);
        onSent(); // nudge the board to refresh right away + bump the session turn count
      }
    },
    [sessionId, busy, onSent, toast],
  );

  // Canned actions operate on the SELECTED POC. The chat agent resolves the POC from natural language, so
  // we append the poc_id to the message. The transcript still shows the friendly label. Free-typed messages
  // are sent verbatim (they may be a transcript that starts a NEW POC, where scoping would be wrong).
  const sendAction = (label: string, message: string) => {
    const scoped = pocId ? `${message}\n\n(This is about POC ${pocId}.)` : message;
    send(scoped, label);
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

  const canSend = !!sessionId && !busy;

  return (
    <section className="flex h-full min-h-0 flex-col border-r border-line bg-ink">
      {/* Header */}
      <div className="flex h-[52px] shrink-0 items-center gap-3 border-b border-line px-5">
        <div className="font-sora text-sm font-semibold">Conversation</div>
        <span className="font-mono text-xs text-faint">session {sessionId || "…"}</span>
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

        {messages.map((m, i) => (
          <MessageBubble key={i} role={m.role} content={m.content} at={m.at} />
        ))}
        {busy && <ReplyingPill />}
      </div>

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
