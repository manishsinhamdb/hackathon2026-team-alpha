// Best-effort resolution of the POC a UI chat session is about (Round 4, item 4).
//
// The platform DB has NO session_id ↔ poc_id link to join on (verified against the live schema:
// `conversations` is keyed by poc_id, not session_id; `runs` carries no execution/session id). The chat
// agent keeps the active-POC context only in its (internal) LangGraph checkpointer, which the read-only BFF
// can't see, and we may not change agent code to add the linkage. So the primary, session-correct signal is
// CLIENT-side "a new POC appeared during this turn" (see src/lib/live.ts newPocIds / chooseAutoFollow).
//
// This route provides a documented FALLBACK: the newest non-archived POC owned by the UI user. It's used
// only on first load when nothing is selected and the client hasn't detected a new POC — good enough to pick
// up the most recent POC, and never overrides a manual selection (the client enforces that).
import type { PocSummary } from "./types";

export interface ResolvedPoc {
  poc_id: string;
  title: string;
}

function createdMs(iso?: string): number {
  const t = Date.parse(iso || "");
  return Number.isNaN(t) ? 0 : t;
}

// The newest non-archived POC in the list, or null when there is none. `pocs` is expected to already be
// scoped to the UI user (the route filters by owner_user_id); this stays a pure function of the input.
export function resolveSessionPoc(pocs: PocSummary[]): ResolvedPoc | null {
  const candidates = pocs.filter((p) => !p.ui_archived);
  if (candidates.length === 0) return null;
  let best = candidates[0];
  for (const p of candidates) if (createdMs(p.created_at) > createdMs(best.created_at)) best = p;
  return { poc_id: best.poc_id, title: best.title };
}
