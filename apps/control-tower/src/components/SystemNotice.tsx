"use client";

import { AlertTriangle, CheckCircle2, ExternalLink, PauseCircle, Radio } from "lucide-react";
import type { NoticeAction, Transition } from "@/lib/transitions";

const TONE: Record<Transition["tone"], { box: string; text: string; btn: string }> = {
  success: { box: "border-success/40 bg-successBg", text: "text-success", btn: "border-success/50 text-success hover:bg-success/10" },
  amber: { box: "border-amber/40 bg-amberBg", text: "text-amber", btn: "border-amber/60 text-amber hover:bg-amber/10" },
  fail: { box: "border-fail/40 bg-failBg", text: "text-fail", btn: "border-fail/50 text-fail hover:bg-fail/10" },
};

function Icon({ t }: { t: Transition }) {
  const cls = `h-4 w-4 shrink-0 ${TONE[t.tone].text}`;
  if (t.kind === "abandoned") return <PauseCircle className={cls} />;
  if (t.tone === "fail" || t.tone === "amber") return <AlertTriangle className={cls} />;
  return <CheckCircle2 className={cls} />;
}

// A pipeline transition posted into the conversation by the tower itself (Round 5, item 4) — a full-width
// tinted card, visibly distinct from the user (green, right) and agent (surface, left) bubbles.
export default function SystemNotice({
  notice,
  at,
  disabled,
  onAction,
}: {
  notice: Transition;
  at?: number;
  disabled: boolean; // a turn is in flight / queued — send actions wait
  onAction: (a: NoticeAction, notice: Transition) => void;
}) {
  const tone = TONE[notice.tone];
  return (
    <div role="status" data-notice={notice.kind} className={`flex w-full flex-col gap-2 rounded-xl border px-3.5 py-3 ${tone.box}`}>
      <div className="flex items-center gap-2">
        <Icon t={notice} />
        <span className={`text-[13px] font-semibold ${tone.text}`}>{notice.title}</span>
        <span className="flex-1" />
        <span className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-faint">
          <Radio className="h-3 w-3" /> control tower
          {at ? ` · ${new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}` : ""}
        </span>
      </div>
      <p className="text-[13px] text-content">{notice.body}</p>
      {notice.actions.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {notice.actions.map((a) =>
            a.kind === "link" ? (
              <a
                key={a.label}
                href={a.href}
                target="_blank"
                rel="noreferrer"
                className={`flex items-center gap-1 rounded-full border px-3 py-1 text-xs font-semibold ${tone.btn}`}
              >
                {a.label} <ExternalLink className="h-3 w-3" />
              </a>
            ) : (
              <button
                key={a.label}
                type="button"
                disabled={a.kind === "send" && disabled}
                onClick={() => onAction(a, notice)}
                className={`rounded-full border px-3 py-1 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-50 ${tone.btn}`}
              >
                {a.label}
              </button>
            ),
          )}
        </div>
      )}
    </div>
  );
}
