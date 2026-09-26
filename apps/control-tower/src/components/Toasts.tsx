"use client";

import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";

type Tone = "error" | "info" | "success";

interface Toast {
  id: number;
  message: string;
  tone: Tone;
  href?: string;
  linkLabel?: string;
  detail?: string;
}

export interface ToastInput {
  message: string;
  tone?: Tone;
  href?: string; // optional external link (e.g. the deployed App URL)
  linkLabel?: string;
  ttlMs?: number;
  detail?: string; // short, non-secret disclosure shown behind a "details" toggle (never raw platform JSON)
}

interface ToastApi {
  error: (message: string) => void;
  show: (input: ToastInput) => void;
}

const ToastContext = createContext<ToastApi>({ error: () => {}, show: () => {} });

export function useToast(): ToastApi {
  return useContext(ToastContext);
}

const TONE_BORDER: Record<Tone, string> = {
  error: "border-fail/40",
  info: "border-line2",
  success: "border-success/50",
};

function ToneIcon({ tone }: { tone: Tone }) {
  if (tone === "error") return <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-fail" />;
  if (tone === "success") return <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />;
  return <Info className="mt-0.5 h-4 w-4 shrink-0 text-run" />;
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  // Suppress a burst of identical messages (e.g. a failing 10 s poll) to one visible toast.
  const lastRef = useRef<{ message: string; at: number }>({ message: "", at: 0 });

  const dismiss = useCallback((id: number) => {
    setToasts((t) => t.filter((x) => x.id !== id));
  }, []);

  const push = useCallback(
    (input: ToastInput) => {
      const now = Date.now();
      if (lastRef.current.message === input.message && now - lastRef.current.at < 8000) return;
      lastRef.current = { message: input.message, at: now };
      const id = ++seq.current;
      const toast: Toast = {
        id,
        message: input.message,
        tone: input.tone ?? "info",
        href: input.href,
        linkLabel: input.linkLabel,
        detail: input.detail,
      };
      setToasts((t) => [...t.slice(-3), toast]);
      // Errors with a details disclosure linger a little longer so the user can expand them.
      setTimeout(() => dismiss(id), input.ttlMs ?? (input.detail ? 9000 : 6000));
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      error: (message: string) => push({ message, tone: "error" }),
      show: (input: ToastInput) => push(input),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(92vw,380px)] flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            role="alert"
            className={`pointer-events-auto flex animate-toast-in items-start gap-2.5 rounded-lg border bg-surface px-3.5 py-3 shadow-2xl ${TONE_BORDER[t.tone]}`}
          >
            <ToneIcon tone={t.tone} />
            <div className="min-w-0 flex-1 text-sm text-content">
              <div className="break-words">{t.message}</div>
              {t.detail && (
                <details className="mt-1 text-xs text-faint">
                  <summary className="cursor-pointer select-none hover:text-content">details</summary>
                  <div className="mt-1 break-words font-mono">{t.detail}</div>
                </details>
              )}
              {t.href && (
                <a
                  href={t.href}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-1 inline-block font-semibold text-green underline underline-offset-2"
                >
                  {t.linkLabel ?? "Open"}
                </a>
              )}
            </div>
            <button
              onClick={() => dismiss(t.id)}
              className="shrink-0 rounded p-0.5 text-faint transition-colors hover:text-content"
              aria-label="Dismiss"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
