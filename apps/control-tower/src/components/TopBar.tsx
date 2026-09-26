"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Droplet, Library, Plus, Search, Star, User } from "lucide-react";
import type { PocSummary } from "@/lib/types";
import { fmtSelectorTime, selectorRows, specLabel } from "@/lib/selector";
import { HealthChip, StatusPill } from "./ui";
import ThemeToggle from "./ThemeToggle";

// The product mark: a green rounded square with a droplet glyph (the design's leaf/drop mark).
function ProductMark() {
  return (
    <div className="flex min-w-[200px] items-center gap-3">
      <div className="flex h-[34px] w-[34px] items-center justify-center rounded-[10px] bg-green">
        <Droplet className="h-[18px] w-[18px] text-greenInk" fill="currentColor" strokeWidth={0} />
      </div>
      <div className="flex flex-col gap-px leading-none">
        <div className="font-sora text-[15px] font-semibold tracking-tight">POC Builder</div>
        <div className="text-[11px] uppercase tracking-[0.08em] text-faint">Control Tower</div>
      </div>
    </div>
  );
}

export default function TopBar({
  pocs,
  selected,
  onSelect,
  onNewPoc,
  connection,
  project,
  followedId,
}: {
  pocs: PocSummary[];
  selected: string;
  onSelect: (id: string) => void;
  onNewPoc: () => void;
  connection: "ok" | "error" | "loading";
  project: string;
  followedId?: string;
}) {
  const current = pocs.find((p) => p.poc_id === selected);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = "poc-selector-listbox";

  const rows = useMemo(() => selectorRows(pocs, query, followedId), [pocs, query, followedId]);
  // The navigable items are the filtered rows plus a trailing "New POC" affordance (index === rows.length).
  const newPocIndex = rows.length;

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const openList = () => {
    setOpen(true);
    setQuery("");
    setActiveIndex(0);
  };
  const close = () => {
    setOpen(false);
    setQuery("");
    inputRef.current?.blur();
  };
  const choose = (id: string) => {
    onSelect(id);
    close();
  };
  const activate = (i: number) => {
    if (i === newPocIndex) {
      onNewPoc();
      close();
    } else if (rows[i]) {
      choose(rows[i].poc_id);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) openList();
      else setActiveIndex((i) => Math.min(newPocIndex, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (open) setActiveIndex((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      if (open) {
        e.preventDefault();
        activate(activeIndex);
      }
    } else if (e.key === "Escape") {
      if (open) {
        e.preventDefault();
        close();
      }
    }
  };

  return (
    <header className="flex h-16 shrink-0 items-center gap-5 border-b border-line bg-ink px-6">
      <ProductMark />

      {/* Searchable POC selector (combobox) */}
      <div ref={ref} className="relative min-w-0 flex-1" style={{ maxWidth: 640 }}>
        <div
          className={`flex h-[42px] w-full items-center gap-2.5 rounded-[10px] border bg-surface px-3.5 text-content ${
            open ? "border-green" : "border-line"
          }`}
          onClick={() => {
            if (!open) openList();
            inputRef.current?.focus();
          }}
        >
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${connection === "error" ? "bg-fail" : connection === "loading" ? "bg-faint" : "bg-run"}`}
          />
          {open ? <Search className="h-4 w-4 shrink-0 text-faint" /> : null}
          <input
            ref={inputRef}
            role="combobox"
            aria-expanded={open}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={open ? `poc-opt-${activeIndex}` : undefined}
            aria-label="Search and select a POC"
            value={open ? query : current ? current.title : ""}
            placeholder={open ? "Search title or POC id…" : "Select a POC…"}
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
              setActiveIndex(0);
            }}
            onFocus={() => !open && openList()}
            onKeyDown={onKeyDown}
            className="min-w-0 flex-1 truncate bg-transparent font-semibold text-content outline-none placeholder:font-normal placeholder:text-faint"
          />
          {/* Closed control: show the selected POC's spec / status / created timestamp. */}
          {!open && current && (
            <>
              {current.versions.spec && (
                <span className="shrink-0 font-mono text-xs text-faint">spec {current.versions.spec}</span>
              )}
              <StatusPill status={current.status} />
              <span className="shrink-0 text-xs text-faint">{fmtSelectorTime(current.created_at)}</span>
            </>
          )}
          <ChevronDown className="h-4 w-4 shrink-0 text-faint" />
        </div>

        {open && (
          <ul
            id={listId}
            role="listbox"
            className="absolute left-0 right-0 top-[48px] z-30 max-h-[60vh] overflow-y-auto rounded-xl border border-line bg-surface p-1.5 shadow-2xl"
          >
            {rows.length === 0 && (
              <li className="px-3 py-3 text-sm text-faint" aria-disabled>
                No POC matches.
              </li>
            )}
            {rows.map((p, i) => {
              const isFollowed = !!followedId && p.poc_id === followedId;
              return (
                <li
                  key={p.poc_id}
                  id={`poc-opt-${i}`}
                  role="option"
                  aria-selected={p.poc_id === selected}
                  onMouseEnter={() => setActiveIndex(i)}
                  onClick={() => choose(p.poc_id)}
                  className={`flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-left ${
                    i === activeIndex ? "bg-elevated" : ""
                  }`}
                >
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5">
                      {isFollowed && <Star className="h-3 w-3 shrink-0 text-green" fill="currentColor" strokeWidth={0} />}
                      <span className="truncate text-sm font-medium">{p.title}</span>
                      {isFollowed && <span className="shrink-0 text-[10px] uppercase tracking-wide text-green">following</span>}
                    </span>
                    <span className="block truncate font-mono text-[11px] text-faint">{p.poc_id}</span>
                  </span>
                  {specLabel(p) && <span className="shrink-0 font-mono text-[11px] text-faint">{specLabel(p)}</span>}
                  <StatusPill status={p.status} />
                  <span className="shrink-0 text-[11px] text-faint">{fmtSelectorTime(p.created_at)}</span>
                </li>
              );
            })}
            {/* New POC affordance (navigable as the last item). */}
            <li
              id={`poc-opt-${newPocIndex}`}
              role="option"
              aria-selected={false}
              onMouseEnter={() => setActiveIndex(newPocIndex)}
              onClick={() => activate(newPocIndex)}
              className={`mt-1 flex cursor-pointer items-center gap-2 rounded-lg border-t border-line px-3 py-2.5 text-sm font-semibold text-green ${
                activeIndex === newPocIndex ? "bg-elevated" : ""
              }`}
            >
              <Plus className="h-4 w-4" strokeWidth={2.2} /> New POC
            </li>
          </ul>
        )}
      </div>

      {/* Right actions */}
      <div className="ml-auto flex items-center gap-2.5">
        <button
          onClick={onNewPoc}
          className="flex h-10 items-center gap-2 rounded-[10px] bg-green px-4 font-semibold text-greenInk transition-opacity hover:opacity-90"
        >
          <Plus className="h-4 w-4" strokeWidth={2.2} /> New POC
        </button>
        <Link
          href="/library"
          className="flex h-10 items-center gap-2 rounded-[10px] border border-line2 px-3.5 font-medium text-content transition-colors hover:bg-surface"
        >
          <Library className="h-4 w-4" /> POC library
        </Link>
        <HealthChip project={project} state={connection} />
        <ThemeToggle />
        <div
          className="flex h-9 w-9 items-center justify-center rounded-full bg-line2 text-content"
          title="Signed-in user"
        >
          <User className="h-4 w-4" />
        </div>
      </div>
    </header>
  );
}
