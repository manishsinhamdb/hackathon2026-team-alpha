"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CopyButton } from "./ui";

export interface BubbleProps {
  role: "user" | "assistant" | "system";
  content: string;
  at?: number;
  recovered?: boolean;
}

function clock(at?: number): string {
  if (!at) return "";
  return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// Run ids the agent mentions, surfaced as a mono row with a copy button (a "structured hint").
function runIds(content: string): string[] {
  const found = content.match(/run_[A-Z0-9]{10,}/g) ?? [];
  return Array.from(new Set(found));
}

export default function MessageBubble({ role, content, at, recovered }: BubbleProps) {
  if (role === "system") {
    return <div className="mx-auto max-w-[90%] text-center text-xs italic text-faint">{content}</div>;
  }

  const isUser = role === "user";
  if (isUser) {
    return (
      <div className="flex flex-col items-end gap-1 self-end" style={{ maxWidth: "78%" }}>
        <div className="whitespace-pre-line break-words rounded-[14px_14px_4px_14px] bg-greenDark px-3.5 py-3 text-[13px] text-white">
          {content}
        </div>
        {at ? <div className="text-[11px] text-faint">{clock(at)}</div> : null}
      </div>
    );
  }

  const ids = runIds(content);
  return (
    <div className="flex flex-col items-start gap-1 self-start" style={{ maxWidth: "88%" }}>
      <div
        className={`flex flex-col gap-2.5 rounded-[14px_14px_14px_4px] border border-line bg-surface px-3.5 py-3 text-[13px] ${
          recovered ? "opacity-70" : ""
        }`}
      >
        <div className="md break-words">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
        {ids.map((id) => (
          <div key={id} className="flex items-center gap-2 font-mono text-xs text-muted">
            <span className="truncate">{id}</span>
            <CopyButton value={id} />
          </div>
        ))}
      </div>
      <div className="flex items-center gap-1.5 text-[11px] text-faint">
        {recovered && <span className="rounded bg-elevated px-1 py-px uppercase tracking-wide">recovered</span>}
        {at ? <span>{clock(at)}</span> : null}
      </div>
    </div>
  );
}

// The "agent is replying" pill shown while a turn streams.
export function ReplyingPill() {
  return (
    <div className="flex items-center gap-1.5 self-start rounded-full border border-line bg-surface px-3 py-2 text-xs text-faint">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 rounded-full bg-muted animate-blink"
          style={{ animationDelay: `${i * 0.2}s` }}
        />
      ))}
      agent is replying
    </div>
  );
}
