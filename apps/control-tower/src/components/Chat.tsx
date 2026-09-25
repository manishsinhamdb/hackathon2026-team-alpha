"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ConversationMessage } from "@/lib/types";

interface LiveMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

const ACTIONS: string[] = [
  "Show me the spec",
  "Looks good, go ahead and build it",
  "Deploy it, I don't need to review the code (4-hour TTL)",
  "How's it going?",
  "Tear it down",
  "Retry",
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
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Reset live transcript when the session id changes ("New session").
  useEffect(() => {
    setMessages([]);
  }, [sessionId]);

  // Recover the selected POC's stored conversation on load / selection.
  useEffect(() => {
    if (!pocId) {
      setRecovered([]);
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
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [messages, recovered]);

  const send = useCallback(
    async (text: string, display?: string) => {
      const message = text.trim();
      if (!message || !sessionId || busy) return;
      setMessages((m) => [...m, { role: "user", content: (display ?? text).trim() }]);
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
          setMessages((m) => [...m, { role: "system", content: `error: ${data.error || res.status}` }]);
        } else {
          setMessages((m) => [...m, { role: "assistant", content: data.reply }]);
        }
      } catch (err) {
        setMessages((m) => [...m, { role: "system", content: `error: ${err instanceof Error ? err.message : String(err)}` }]);
      } finally {
        setBusy(false);
        onSent(); // nudge the board to refresh right away
      }
    },
    [sessionId, busy, onSent],
  );

  // Canned actions operate on the SELECTED POC. The chat agent resolves the POC from natural language, so
  // we append the poc_id to the message (a fresh browser session otherwise has no POC in context). The
  // transcript still shows the friendly button label. Free-typed messages are sent verbatim (they may be a
  // transcript that starts a NEW POC, where scoping would be wrong).
  const sendAction = (label: string) => {
    const scoped = pocId ? `${label}\n\n(This is about POC ${pocId}.)` : label;
    send(scoped, label);
  };

  const onPickFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    // Paste the file content into the message box (no upload in this version).
    setInput((cur) => (cur ? `${cur}\n\n${text}` : text));
    if (fileRef.current) fileRef.current.value = "";
  };

  return (
    <>
      <div className="chat-head">
        <strong>Chat</strong>
        <span className="session-id">· {sessionId || "generating…"}</span>
        {busy ? <span style={{ color: "var(--run)" }}>sending…</span> : null}
      </div>

      <div className="chat-log" ref={logRef}>
        {recovered.length > 0 && (
          <details>
            <summary>{recovered.length} recovered message(s) from this POC&rsquo;s conversation</summary>
            {recovered.map((m, i) => (
              <div key={`r-${i}`} className={`msg ${m.role === "user" ? "user" : "assistant"}`} style={{ opacity: 0.75 }}>
                <span className="role">{m.role} (recovered)</span>
                {m.content}
              </div>
            ))}
          </details>
        )}
        {messages.length === 0 && recovered.length === 0 ? (
          <div className="empty">Say hello, or use a quick action below.</div>
        ) : null}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.role !== "system" && <span className="role">{m.role}</span>}
            {m.content}
          </div>
        ))}
      </div>

      <div className="actions">
        <span className="label">Quick actions</span>
        {ACTIONS.map((a) => (
          <button key={a} className="action-btn" disabled={busy || !sessionId} onClick={() => sendAction(a)}>
            {a}
          </button>
        ))}
      </div>

      <div className="chat-input">
        <textarea
          value={input}
          placeholder="Type a message, or paste a transcript with the .txt picker…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              send(input);
            }
          }}
        />
        <div className="row">
          <button disabled={busy || !sessionId || !input.trim()} onClick={() => send(input)}>
            Send <span style={{ opacity: 0.6 }}>(⌘/Ctrl+Enter)</span>
          </button>
          <label className="action-btn" style={{ border: "1px solid var(--border)", borderRadius: 6, padding: "6px 10px" }}>
            📎 Attach .txt
            <input
              ref={fileRef}
              type="file"
              accept=".txt,text/plain"
              style={{ display: "none" }}
              onChange={onPickFile}
            />
          </label>
        </div>
      </div>
    </>
  );
}
