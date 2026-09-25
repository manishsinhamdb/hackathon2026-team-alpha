"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  FileText, Hammer, HelpCircle, MessageSquare, Paperclip, RotateCcw, Rocket, Send, Trash2,
} from "lucide-react";
import type { ConversationMessage } from "@/lib/types";
import { useToast } from "./Toasts";
import MessageBubble, { TypingBubble } from "./MessageBubble";

interface LiveMessage {
  role: "user" | "assistant" | "system";
  content: string;
  at: number;
}

// Compact action chips. `label` is what the transcript shows; `message` is the canned text actually sent
// to the chat agent — unchanged from the original wiring so behaviour is identical.
const ACTIONS: { label: string; message: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { label: "Show me the spec", message: "Show me the spec", icon: FileText },
  { label: "Build it", message: "Looks good, go ahead and build it", icon: Hammer },
  { label: "Deploy (4h TTL)", message: "Deploy it, I don't need to review the code (4-hour TTL)", icon: Rocket },
  { label: "How's it going?", message: "How's it going?", icon: HelpCircle },
  { label: "Tear it down", message: "Tear it down", icon: Trash2 },
  { label: "Retry", message: "Retry", icon: RotateCcw },
];

export default function Chat({
  sessionId,
  pocId,
  onSent,
}: {
  sessionId: string;
  pocId: string;
  onSent: () => void;
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
  }, [messages, recovered, busy]);

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
        onSent(); // nudge the board to refresh right away
      }
    },
    [sessionId, busy, onSent, toast],
  );

  // Canned actions operate on the SELECTED POC. The chat agent resolves the POC from natural language, so
  // we append the poc_id to the message (a fresh browser session otherwise has no POC in context). The
  // transcript still shows the friendly button label. Free-typed messages are sent verbatim (they may be a
  // transcript that starts a NEW POC, where scoping would be wrong).
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
    // Enter sends; Shift+Enter (or ⌘/Ctrl+Enter) inserts a newline.
    if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      send(input);
    }
  };

  const canSend = !!sessionId && !busy;

  return (
    <section className="flex h-full min-h-0 flex-col bg-canvas">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-line bg-surface px-4 py-2.5">
        <MessageSquare className="h-4 w-4 text-accent" />
        <span className="text-sm font-semibold">Chat</span>
        {recovered.length > 0 && (
          <button
            onClick={() => setShowRecovered((v) => !v)}
            className="ml-auto rounded-md px-2 py-0.5 text-xs text-muted transition-colors hover:bg-surface-2 hover:text-content"
          >
            {showRecovered ? "Hide" : "Show"} {recovered.length} recovered
          </button>
        )}
      </div>

      {/* Log */}
      <div ref={logRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {showRecovered &&
          recovered.map((m, i) => (
            <MessageBubble
              key={`r-${i}`}
              role={m.role === "user" ? "user" : "assistant"}
              content={m.content}
              recovered
            />
          ))}

        {messages.length === 0 && (recovered.length === 0 || !showRecovered) ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <MessageSquare className="h-8 w-8 text-idle" />
            <p className="text-sm font-medium text-muted">Start a conversation</p>
            <p className="max-w-xs text-xs text-faint">
              Paste a meeting transcript to draft a new POC, or pick one above and use a quick action below.
            </p>
          </div>
        ) : null}

        {messages.map((m, i) => (
          <MessageBubble key={i} role={m.role} content={m.content} at={m.at} />
        ))}
        {busy && <TypingBubble />}
      </div>

      {/* Composer (sticky) */}
      <div className="border-t border-line bg-surface px-3 pb-3 pt-2.5">
        <div className="mb-2 flex flex-wrap gap-1.5">
          {ACTIONS.map(({ label, message, icon: Icon }) => (
            <button
              key={label}
              disabled={!canSend}
              onClick={() => sendAction(label, message)}
              className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-2 px-2.5 py-1 text-xs font-medium text-content transition-colors hover:border-green-dark hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </div>

        <div className="flex items-end gap-2 rounded-xl border border-line bg-canvas p-2 focus-within:border-green-dark">
          <label
            className="flex h-9 w-9 shrink-0 cursor-pointer items-center justify-center rounded-lg text-muted transition-colors hover:bg-surface-2 hover:text-content"
            title="Attach a .txt transcript"
          >
            <Paperclip className="h-4 w-4" />
            <input ref={fileRef} type="file" accept=".txt,text/plain" className="hidden" onChange={onPickFile} />
          </label>
          <textarea
            ref={taRef}
            value={input}
            rows={1}
            placeholder="Message the agent…  (Enter to send · Shift+Enter for a new line)"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            className="max-h-40 min-h-[36px] flex-1 resize-none bg-transparent py-1.5 text-sm text-content outline-none placeholder:text-faint"
          />
          <button
            disabled={!canSend || !input.trim()}
            onClick={() => send(input)}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-green-base text-ink transition-colors hover:bg-green-dark hover:text-white disabled:cursor-not-allowed disabled:bg-idle disabled:text-white/70"
            title="Send"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>
    </section>
  );
}
