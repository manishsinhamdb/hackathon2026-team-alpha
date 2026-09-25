"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export interface BubbleProps {
  role: "user" | "assistant" | "system";
  content: string;
  at?: number;
  recovered?: boolean;
}

function fmtClock(at?: number): string {
  if (!at) return "";
  return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function MessageBubble({ role, content, at, recovered }: BubbleProps) {
  if (role === "system") {
    return (
      <div className="mx-auto max-w-[90%] text-center text-xs italic text-muted">{content}</div>
    );
  }

  const isUser = role === "user";
  return (
    <div className={`flex flex-col ${isUser ? "items-end" : "items-start"}`}>
      <div
        className={
          isUser
            ? "max-w-[85%] rounded-2xl rounded-br-sm bg-green-darker px-3.5 py-2 text-[14px] leading-relaxed text-white shadow-sm"
            : `max-w-[88%] rounded-2xl rounded-bl-sm border border-line bg-surface px-3.5 py-2 text-content shadow-sm ${recovered ? "opacity-70" : ""}`
        }
      >
        {isUser ? (
          <div className="whitespace-pre-wrap break-words">{content}</div>
        ) : (
          <div className="md break-words">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
          </div>
        )}
      </div>
      <div className="mt-1 flex items-center gap-1.5 px-1 text-[10px] text-faint">
        {recovered && <span className="rounded bg-surface-2 px-1 py-px uppercase tracking-wide">recovered</span>}
        <span>{isUser ? "You" : "Agent"}</span>
        {at ? <span>· {fmtClock(at)}</span> : null}
      </div>
    </div>
  );
}

// The three-dot "agent is replying" indicator shown while a turn streams.
export function TypingBubble() {
  return (
    <div className="flex items-start">
      <div className="flex items-center gap-1 rounded-2xl rounded-bl-sm border border-line bg-surface px-4 py-3 shadow-sm">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-1.5 w-1.5 rounded-full bg-muted animate-blink"
            style={{ animationDelay: `${i * 0.2}s` }}
          />
        ))}
      </div>
    </div>
  );
}
