// Small formatting helpers shared by the client components. Pure, no imports.

export function fmtDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return `${m}m ${rs}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function fmtTime(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
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
