"use client";

import { useEffect, useRef, useState } from "react";
import { Pencil } from "lucide-react";
import { LABEL_MAX } from "@/lib/label";

// A small inline text editor (Round 5, item 3): used for the POC nickname (top bar, selector rows, library
// table) and the session name (Conversation headline). Enter / blur saves, Escape cancels. Clicks and keys are
// stopped from bubbling so it can sit inside the combobox / a clickable row.
export function InlineEdit({
  value,
  placeholder,
  onSave,
  onDone,
  maxLength = LABEL_MAX,
  className = "",
  ariaLabel,
}: {
  value: string;
  placeholder?: string;
  onSave: (next: string) => void;
  onDone: () => void;
  maxLength?: number;
  className?: string;
  ariaLabel: string;
}) {
  const [draft, setDraft] = useState(value);
  const ref = useRef<HTMLInputElement>(null);
  const doneRef = useRef(false);

  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);

  const finish = (save: boolean) => {
    if (doneRef.current) return;
    doneRef.current = true;
    if (save && draft.trim() !== value.trim()) onSave(draft.trim());
    onDone();
  };

  return (
    <input
      ref={ref}
      value={draft}
      maxLength={maxLength}
      aria-label={ariaLabel}
      placeholder={placeholder}
      onChange={(e) => setDraft(e.target.value)}
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Enter") {
          e.preventDefault();
          finish(true);
        } else if (e.key === "Escape") {
          e.preventDefault();
          finish(false);
        }
      }}
      onBlur={() => finish(true)}
      className={`min-w-0 rounded-md border border-green bg-ink px-2 py-0.5 text-content outline-none ${className}`}
    />
  );
}

// The pencil affordance that switches a title into edit mode.
export function EditButton({ onClick, label, className = "" }: { onClick: () => void; label: string; className?: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-faint transition-colors hover:bg-elevated hover:text-content ${className}`}
    >
      <Pencil className="h-3 w-3" />
    </button>
  );
}
