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
    try {
      const result = await client.invokeChatStream({ sessionId, message, userId: cfg.uiUserId });
      return NextResponse.json(result);
    } catch (streamErr) {
      const result = await client.invokeChat({ sessionId, message, userId: cfg.uiUserId });
      return NextResponse.json({ ...result, note: `stream fell back to sync: ${streamErr instanceof Error ? streamErr.message : String(streamErr)}` });
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: msg }, { status: 502 });
  }
}
