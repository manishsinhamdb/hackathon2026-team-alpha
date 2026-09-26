// Pure helpers for the "live" behaviours: auto-following a POC a turn creates (feature 2) and the adaptive
// poll cadence (feature 4). Kept dependency-free so they unit-test without React/DOM.
import type { PocDetail } from "./types";

// Adaptive poll cadence: fast while a run for this POC is live, slow when the POC is idle. Never zero.
export const ACTIVE_POLL_MS = 15_000; // any stage running (draft/code/deploy/tests/teardown)
export const IDLE_POLL_MS = 120_000; // nothing running

// A POC id the agent may surface in a reply (poc_<ULID>). Used to auto-follow a freshly-created POC.
export function pocIdIn(text: string): string | undefined {
  return text.match(/poc_[A-Z0-9]{10,}/)?.[0];
}

export function isRunActive(status: string): boolean {
  return status === "running" || status === "queued" || status === "waiting_user";
}

// True when any run for the POC is live — the board must keep polling fast and never stop.
export function anyRunActive(detail: PocDetail): boolean {
  return detail.runs.some((r) => isRunActive(r.status));
}

export function pollIntervalMs(detail: PocDetail): number {
  return anyRunActive(detail) ? ACTIVE_POLL_MS : IDLE_POLL_MS;
}

// --- item 4: auto-follow the POC a turn creates -----------------------------------------------------------
// The reply-text detection (pocIdIn) misses the case where a turn creates a POC but the reply summarises the
// spec without repeating the poc_id. So the client also snapshots the poc-id set BEFORE a turn and compares
// it AFTER: a poc_id present after but not before was created by this turn.

// POC ids that appeared in `after` but were not in `before`.
export function newPocIds(before: Iterable<string>, after: Iterable<string>): string[] {
  const seen = new Set(before);
  const out: string[] = [];
  for (const id of after) if (!seen.has(id)) out.push(id);
  return out;
}

// Selection precedence for auto-follow. Returns the poc to follow, or null to leave the selection alone.
// MANUAL selection always wins — if the user picked a POC this session we never override it. Otherwise we
// follow a detected candidate when nothing is selected, or when the current (auto-followed) selection
// differs from what the turn/DB indicates.
export function chooseAutoFollow(args: {
  candidate?: string;
  selected?: string;
  manualChosen: boolean;
}): string | null {
  const { candidate, selected, manualChosen } = args;
  if (!candidate) return null;
  if (manualChosen) return null;
  if (!selected) return candidate;
  if (selected === candidate) return null;
  return candidate;
}
