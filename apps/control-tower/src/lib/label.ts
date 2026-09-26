// The UI's SECOND (and last) DB write: the POC nickname `pocs.ui_label` (+ `ui_label_at`). Agents ignore
// both fields; the UI shows the nickname wherever the title shows (agent title beneath, muted) and search
// matches it. Like archive.ts this is a pure function over a minimal collection interface so it is
// unit-testable with a fake DB (src/test/label.test.ts) — the route wires in the real Mongo collection.
import type { ArchiveCollection, ArchiveUpdate } from "./archive";
import type { PocSummary } from "./types";

export const LABEL_MAX = 80;

// Trim, collapse internal whitespace (a nickname is one line), cap at 80 characters. Non-strings -> "".
export function normalizeLabel(raw: unknown): string {
  if (typeof raw !== "string") return "";
  return raw.replace(/\s+/g, " ").trim().slice(0, LABEL_MAX).trim();
}

// Set (or, for an empty label, $unset) the nickname. Only ever touches ui_label / ui_label_at.
export async function setLabel(
  coll: ArchiveCollection,
  pocId: string,
  rawLabel: unknown,
  nowIso: string = new Date().toISOString(),
): Promise<{ ok: boolean; matched: number; label: string }> {
  const label = normalizeLabel(rawLabel);
  const update: ArchiveUpdate = label
    ? { $set: { ui_label: label, ui_label_at: nowIso } }
    : { $unset: { ui_label: "", ui_label_at: "" } };
  const res = await coll.updateOne({ poc_id: pocId }, update);
  return { ok: res.matchedCount > 0, matched: res.matchedCount, label };
}

// The headline to show for a POC: the nickname when set, else the agent's title.
export function displayTitle(p: { title: string; ui_label?: string }): string {
  return p.ui_label || p.title;
}

// The agent title to show beneath a nickname (small, muted) — only when a nickname hides it.
export function subTitle(p: { title: string; ui_label?: string }): string | undefined {
  return p.ui_label && p.ui_label !== p.title ? p.title : undefined;
}

// Client helper: POST the nickname to the BFF. Returns the stored (normalized) label.
export async function saveLabel(fetchImpl: typeof fetch, pocId: string, label: string): Promise<string> {
  const res = await fetchImpl(`/api/pocs/${encodeURIComponent(pocId)}/label`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label }),
  });
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const data: any = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
  return String(data?.ui_label ?? "");
}

// Apply a nickname to a POC list locally (optimistic update before the server confirms).
export function withLabel(pocs: PocSummary[], pocId: string, label: string): PocSummary[] {
  const clean = normalizeLabel(label);
  return pocs.map((p) => {
    if (p.poc_id !== pocId) return p;
    const next = { ...p };
    if (clean) next.ui_label = clean;
    else delete next.ui_label;
    return next;
  });
}
