// Pure logic for the searchable POC selector combobox (Round 4, item 5): filter (title + poc id,
// case-insensitive substring), sort (newest first, archived hidden, the currently-followed POC pinned to
// the top), and the compact timestamp format. Kept dependency-free so it unit-tests without React/DOM.
import type { PocSummary } from "./types";

// Fixed 3-letter month names so the format is stable across ICU/CLDR versions (newer ICU renders September
// as "Sept" via toLocaleDateString, but the design calls for "Sep").
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "26 Sep 08:31" — day, short month, 24h HH:MM, no separators. Used both in the closed control and rows.
export function fmtSelectorTime(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${hh}:${mm}`;
}

// "spec vNNN" (or "" when there is no spec version yet).
export function specLabel(p: PocSummary): string {
  return p.versions.spec ? `spec ${p.versions.spec}` : "";
}

function createdMs(p: PocSummary): number {
  const t = Date.parse(p.created_at || "");
  return Number.isNaN(t) ? 0 : t;
}

// Case-insensitive substring match on the title, the UI nickname (Round 5) or the poc id.
export function matchesQuery(p: PocSummary, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    p.title.toLowerCase().includes(q) ||
    p.poc_id.toLowerCase().includes(q) ||
    (p.ui_label ?? "").toLowerCase().includes(q)
  );
}

// The rows to render in the combobox listbox: archived hidden, filtered by the query, sorted newest first,
// with the currently-followed POC pinned to the very top (regardless of its created date or the query — but
// still only when it passes the filter, so typing a non-matching query hides it too).
export function selectorRows(pocs: PocSummary[], query: string, followedId?: string): PocSummary[] {
  const visible = pocs.filter((p) => !p.ui_archived && matchesQuery(p, query));
  const sorted = [...visible].sort((a, b) => createdMs(b) - createdMs(a));
  if (!followedId) return sorted;
  const idx = sorted.findIndex((p) => p.poc_id === followedId);
  if (idx <= 0) return sorted; // not present, or already first
  const [pinned] = sorted.splice(idx, 1);
  return [pinned, ...sorted];
}
