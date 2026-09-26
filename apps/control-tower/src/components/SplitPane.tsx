"use client";

// The resizable Conversation | Pipeline split (Round 4, item 1). A draggable vertical divider between the
// two panes: pointer drag (mouse + touch, via Pointer Events), keyboard (Tab to the handle, arrow keys move
// it 16 px, Home/End to min/max), and double-click to reset to the design's 5/7 split. The chat pane's width
// is a percentage held in a CSS variable (--ct-chat-w) so it's applied before first paint (see
// SPLIT_INIT_SCRIPT) and clamped by CSS so neither pane drops below 380 px. Below lg the layout stacks and
// the handle is hidden. The handle is a real <button> with aria-label and aria-valuenow.
import { useCallback, useEffect, useRef, useState } from "react";
import {
  DEFAULT_CHAT_PCT,
  clampChatPct,
  loadChatPct,
  nextPctForKey,
  pctFromPointer,
  saveChatPct,
} from "@/lib/split";

export default function SplitPane({
  left,
  right,
  leftClassName = "",
  rightClassName = "",
}: {
  left: React.ReactNode;
  right: React.ReactNode;
  leftClassName?: string;
  rightClassName?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [chatPct, setChatPct] = useState<number>(DEFAULT_CHAT_PCT);
  const pctRef = useRef<number>(DEFAULT_CHAT_PCT);
  const draggingRef = useRef(false);

  const apply = useCallback((pct: number, persist: boolean) => {
    pctRef.current = pct;
    setChatPct(pct);
    if (typeof document !== "undefined") document.documentElement.style.setProperty("--ct-chat-w", `${pct}%`);
    if (persist) saveChatPct(pct);
  }, []);

  const containerWidth = () => containerRef.current?.getBoundingClientRect().width ?? 0;

  // Align React state (drives aria-valuenow) with the persisted value the pre-paint script already applied,
  // and re-clamp on window resize so a shrunk window keeps both panes at least the minimum width.
  useEffect(() => {
    const w = containerWidth();
    apply(clampChatPct(loadChatPct(), w || 100000), false);
    const onResize = () => {
      const cw = containerWidth();
      if (cw > 0) apply(clampChatPct(pctRef.current, cw), false);
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [apply]);

  const onPointerDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    draggingRef.current = true;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    e.preventDefault();
  };
  const onPointerMove = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (!draggingRef.current) return;
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    apply(pctFromPointer(e.clientX, rect.left, rect.width), true);
  };
  const onPointerUp = (e: React.PointerEvent<HTMLButtonElement>) => {
    draggingRef.current = false;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
  };
  const onKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    const next = nextPctForKey(e.key, pctRef.current, containerWidth());
    if (next !== null) {
      e.preventDefault();
      apply(next, true);
    }
  };

  return (
    <div ref={containerRef} className="flex min-h-0 flex-1 flex-col lg:flex-row">
      <div className={`ct-split-chat ${leftClassName}`}>{left}</div>
      <button
        type="button"
        aria-label="Resize the conversation and pipeline panels"
        aria-orientation="vertical"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(chatPct)}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onKeyDown={onKeyDown}
        onDoubleClick={() => apply(DEFAULT_CHAT_PCT, true)}
        title="Drag to resize · double-click to reset · arrow keys to nudge"
        className="ct-split-handle group relative hidden w-1.5 shrink-0 items-center justify-center self-stretch bg-line outline-none transition-colors hover:bg-green/40 focus-visible:bg-green/60 lg:flex"
      >
        <span className="h-8 w-0.5 rounded-full bg-line2 transition-colors group-hover:bg-green group-focus-visible:bg-green" />
      </button>
      <div className={`min-h-0 flex-1 ${rightClassName}`}>{right}</div>
    </div>
  );
}
