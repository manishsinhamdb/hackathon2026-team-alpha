// POST /api/sessions/:id/stop -> cancel the running turn on a UI chat session by stopping its reserved
// platform runtime (Round 4, item 3). Wire call: POST .../workspaces/{ws}/runtime-sessions/{id}/stop.
//
// The UI only ever passes its OWN client-generated session ids (the X-Session-Id it threads on), and the
// BFF only stops sessions on the single configured chat workspace with its own service account — so this
// can never cancel anything but the current user's own session's execution.
import { NextResponse } from "next/server";
import { getPlatformClient } from "@/lib/platformClient";

export const dynamic = "force-dynamic";

export async function POST(_req: Request, ctx: { params: Promise<{ id: string }> }): Promise<Response> {
  const { id } = await ctx.params;
  if (!id) return NextResponse.json({ error: "session id required" }, { status: 400 });
  try {
    const result = await getPlatformClient().stopRuntimeSession(id);
    // 404 (already free) is a success from the UI's point of view: the turn finished before we cancelled.
    return NextResponse.json({ stopped: result.stopped, alreadyFree: !!result.notFound });
  } catch (err) {
    const raw = err instanceof Error ? err.message : String(err);
    console.error("[sessions/stop] failed:", raw);
    const httpMatch = raw.match(/HTTP (\d{3})/);
    return NextResponse.json(
      { error: "Couldn’t stop the running turn. Please try again.", detail: httpMatch ? `platform ${httpMatch[0]}` : "stop error" },
      { status: 502 },
    );
  }
}
