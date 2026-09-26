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
