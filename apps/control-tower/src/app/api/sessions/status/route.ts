// GET /api/sessions/status?ids=a,b,c -> which of the given UI sessions currently hold reserved platform
// runtime (Round 4, items 2 & 3). Backed by the platform runtime-sessions list; the `session_id` there is
// the UI's X-Session-Id. A session is reported "busy" while it holds ("active") or is releasing ("stopping")
// capacity. This is the lightweight status source for the Sessions-card busy dot; the authoritative
// free/busy oracle for auto-send is the invoke's own 409 (see src/lib/chat.ts).
import { NextResponse } from "next/server";
import { getPlatformClient } from "@/lib/platformClient";
import { busyStatusFor } from "@/lib/sessionStatus";

export const dynamic = "force-dynamic";

export async function GET(req: Request): Promise<Response> {
  const ids = (new URL(req.url).searchParams.get("ids") || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  try {
    const sessions = await getPlatformClient().listRuntimeSessions();
    return NextResponse.json(busyStatusFor(sessions, ids));
  } catch (err) {
    const raw = err instanceof Error ? err.message : String(err);
    console.error("[sessions/status] failed:", raw);
    // Non-fatal: a status probe failure just means we can't show dots — return empty, never an error toast.
    return NextResponse.json({ sessions: {}, busyIds: [] });
  }
}
