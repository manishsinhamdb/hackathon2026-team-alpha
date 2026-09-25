"use client";

import { AlertTriangle, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";

interface Toast {
  id: number;
  message: string;
}

interface ToastApi {
  error: (message: string) => void;
}

const ToastContext = createContext<ToastApi>({ error: () => {} });

export function useToast(): ToastApi {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  // Suppress a burst of identical messages (e.g. a failing 10 s poll) to one visible toast.
  const lastRef = useRef<{ message: string; at: number }>({ message: "", at: 0 });

  const dismiss = useCallback((id: number) => {
    setToasts((t) => t.filter((x) => x.id !== id));
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      error: (message: string) => {
        const now = Date.now();
        if (lastRef.current.message === message && now - lastRef.current.at < 8000) return;
        lastRef.current = { message, at: now };
        const id = ++seq.current;
        setToasts((t) => [...t.slice(-3), { id, message }]);
        setTimeout(() => dismiss(id), 6000);
      },
    }),
    [dismiss],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(92vw,380px)] flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            role="alert"
            className="pointer-events-auto flex animate-toast-in items-start gap-2.5 rounded-lg border border-fail/40 bg-surface px-3.5 py-3 shadow-2xl"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-fail" />
            <div className="min-w-0 flex-1 text-sm text-content">{t.message}</div>
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
