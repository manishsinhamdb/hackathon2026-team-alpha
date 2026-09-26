// BFF chat route. The browser POSTs {session_id, message}; the server exchanges the SA credentials for a
// platform token (never exposed to the browser) and invokes the chat workspace. Returns the reply text.
import { NextResponse } from "next/server";
import { getServerConfig } from "@/lib/env";
import { PlatformClient } from "@/lib/platform";

export const dynamic = "force-dynamic";

let client: PlatformClient | null = null;
function getClient(): PlatformClient {
  if (client) return client;
  const cfg = getServerConfig();
  client = new PlatformClient({
    baseUrl: cfg.platformBaseUrl,
    projectId: cfg.projectId,
    chatWorkspaceId: cfg.chatWorkspaceId,
    clientId: cfg.saClientId,
    clientSecret: cfg.saClientSecret,
  });
  return client;
}

export async function POST(req: Request): Promise<Response> {
  let body: { session_id?: string; message?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  const sessionId = (body.session_id || "").trim();
  const message = (body.message || "").trim();
  if (!sessionId) return NextResponse.json({ error: "session_id is required" }, { status: 400 });
  if (!message) return NextResponse.json({ error: "message is required" }, { status: 400 });

  const cfg = getServerConfig();
  const client = getClient();
  try {
    // Stream by default: it keeps the connection alive through a long turn (a rich spec summary exceeds the
    // ~60 s synchronous cap). Fall back to the plain synchronous invoke only if streaming itself errors.
    let result;
    try {
      result = await client.invokeChatStream({ sessionId, message, userId: cfg.uiUserId });
    } catch (streamErr) {
      // Streaming itself failed — log the raw reason server-side only, then try the synchronous path.
      console.error("[chat] stream path failed, falling back to sync:", streamErr);
      result = await client.invokeChat({ sessionId, message, userId: cfg.uiUserId });
    }
    // The session is still running its previous turn: sanitize to a 409 the UI queues on (no raw JSON).
    if (result.busy) {
      return NextResponse.json(
        { busy: true, code: "SESSION_BUSY", blockingExecutionId: result.blockingExecutionId, blockingStatus: result.blockingStatus },
        { status: 409 },
      );
    }
    return NextResponse.json(result);
  } catch (err) {
    // Any other BFF/platform error: the raw payload (which may echo platform internals) goes to the server
    // log ONLY; the browser gets a friendly message plus a short, non-secret detail for the disclosure.
    const raw = err instanceof Error ? err.message : String(err);
    console.error("[chat] invoke failed:", raw);
    const httpMatch = raw.match(/HTTP (\d{3})/);
    return NextResponse.json(
      { error: "The agent service couldn’t be reached. Please try again in a moment.", detail: httpMatch ? `platform ${httpMatch[0]}` : "invoke error" },
      { status: 502 },
    );
  }
}
