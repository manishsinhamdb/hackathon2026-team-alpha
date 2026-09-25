// Small formatting helpers shared by the client components. Pure, no imports.

export function fmtDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return `${m}m ${String(rs).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

// "20:34" — 24h clock, matching the design.
export function fmtClock(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// "25 Sep · 20:34" — short date used in the top bar.
export function fmtDateShort(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const day = d.toLocaleDateString([], { day: "numeric", month: "short" });
  return `${day} · ${fmtClock(iso)}`;
}

// "25 Sep 2026 · 20:34" — full date used in the library table.
export function fmtDateFull(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const day = d.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
  return `${day} · ${fmtClock(iso)}`;
}

// A live countdown to a TTL timestamp (positive = time left, negative shown as "expired").
export function fmtCountdown(ttlIso?: string, nowMs: number = Date.now()): string {
  if (!ttlIso) return "—";
  const t = Date.parse(ttlIso);
  if (Number.isNaN(t)) return ttlIso;
  const diff = t - nowMs;
  if (diff <= 0) return "expired";
  return fmtDuration(diff) + " left";
}

// Up to two uppercase initials for an avatar / "by <who>" label. Names split on spaces; opaque ids fall
// back to their first two alphanumeric characters.
export function initials(who?: string): string {
  if (!who) return "··";
  const words = who.trim().split(/[\s._-]+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  const clean = who.replace(/[^a-zA-Z0-9]/g, "");
  return (clean.slice(0, 2) || "··").toUpperCase();
}

// Collapse a long id to "run_01M3CBCF…RZPH6" for compact display.
export function shortId(id?: string, head = 12, tail = 4): string {
  if (!id) return "—";
  if (id.length <= head + tail + 1) return id;
  return `${id.slice(0, head)}…${id.slice(-tail)}`;
}
