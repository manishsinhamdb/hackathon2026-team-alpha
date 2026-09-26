// Pure derivation of per-session busy status from the platform runtime-sessions list. Shared by the BFF
// status route and unit-tested directly. A session is "busy" while it holds ("active") or is releasing
// ("stopping") reserved runtime capacity. Dependency-free (no server-only / DOM imports).
import type { RuntimeSession } from "./platform";

export interface SessionStatus {
  busy: boolean;
  status: string;
  lastActivityAt?: string;
}

export interface SessionStatusResponse {
  sessions: Record<string, SessionStatus>;
  busyIds: string[];
}

export function isBusyStatus(status: string | undefined): boolean {
  return status === "active" || status === "stopping";
}

// Map the runtime-session list onto the requested ids. Ids not present in the list are free (not busy).
export function busyStatusFor(sessions: RuntimeSession[], ids: string[]): SessionStatusResponse {
  const byId = new Map(sessions.map((s) => [s.session_id, s]));
  const out: Record<string, SessionStatus> = {};
  const busyIds: string[] = [];
  for (const id of ids) {
    const s = byId.get(id);
    const busy = isBusyStatus(s?.status);
    out[id] = { busy, status: s?.status ?? "free", lastActivityAt: s?.last_activity_at };
    if (busy) busyIds.push(id);
  }
  return { sessions: out, busyIds };
}
