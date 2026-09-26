// Platform BFF client: OAuth client-credentials token exchange (cached to expiry, refreshed on 401)
// and the chat invoke call. Deliberately free of any Next / server-only import so it is unit-testable
// with a fake fetch and a fake clock — the route handlers inject the real fetch + Date.now.
//
// Contract (docs/08 §8, docs/06 "Root-stack invoke mechanics"):
//   token:  POST {base}/api/v1/oauth/token  (x-www-form-urlencoded, grant_type=client_credentials)
//   invoke: POST {base}/api/v1/projects/{p}/workspaces/{ws}/invoke  (Bearer, {message, session_id, user_id})
//           -> {success, response, execution_id, status}. A synchronous invoke is capped at ~60 s -> HTTP 504
//           while the agent keeps replying server-side; we surface that as `pending` and tell the user to poll.

export interface PlatformClientConfig {
  baseUrl: string;
  projectId: string;
  chatWorkspaceId: string;
  clientId: string;
  clientSecret: string;
}

export type FetchLike = typeof fetch;
export type NowFn = () => number;

export interface ChatInvokeResult {
  reply: string;
  status: string;
  pending: boolean; // true when the agent is still replying (e.g. gateway 504 / client timeout)
}

// How long a client-credentials token is trusted, minus this many seconds of safety margin.
const TOKEN_SKEW_S = 60;
const TOKEN_MIN_TTL_S = 30;
// Client-side ceiling for a synchronous chat invoke. Chat turns are short now; if we ever exceed this we
// bail to the same "still replying, poll" path as a gateway 504.
const CHAT_TIMEOUT_MS = 90_000;
// The streaming path (/invokeStream) keeps the connection alive through a long turn, so it survives past
// the ~60 s synchronous-gateway cap. A generous ceiling covers a slow spec-summary turn.
const STREAM_TIMEOUT_MS = 300_000;

export class TokenCache {
  private token: string | null = null;
  private expiresAtMs = 0;

  constructor(
    private readonly cfg: PlatformClientConfig,
    private readonly fetchImpl: FetchLike = fetch,
    private readonly now: NowFn = Date.now,
  ) {}

  async get(force = false): Promise<string> {
    if (!force && this.token && this.now() < this.expiresAtMs) return this.token;
    const body = new URLSearchParams({
      grant_type: "client_credentials",
      client_id: this.cfg.clientId,
      client_secret: this.cfg.clientSecret,
    }).toString();
    const res = await this.fetchImpl(`${this.cfg.baseUrl}/api/v1/oauth/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    if (res.status !== 200) {
      // Never echo the credentials; the body is the platform's error (e.g. {"error":"invalid_client"}).
      const text = await safeText(res);
      throw new Error(`token exchange failed: HTTP ${res.status} ${text.slice(0, 200)}`);
    }
    const data = (await res.json()) as { access_token: string; expires_in?: number };
    const ttl = Math.max(Number(data.expires_in ?? 3600) - TOKEN_SKEW_S, TOKEN_MIN_TTL_S);
    this.token = data.access_token;
    this.expiresAtMs = this.now() + ttl * 1000;
    return this.token;
  }

  clear(): void {
    this.token = null;
    this.expiresAtMs = 0;
  }
}

export class PlatformClient {
  private readonly tokens: TokenCache;

  constructor(
    private readonly cfg: PlatformClientConfig,
    private readonly fetchImpl: FetchLike = fetch,
    now: NowFn = Date.now,
  ) {
    this.tokens = new TokenCache(cfg, fetchImpl, now);
  }

  private invokeUrl(stream = false): string {
    const verb = stream ? "invokeStream" : "invoke";
    return `${this.cfg.baseUrl}/api/v1/projects/${this.cfg.projectId}/workspaces/${this.cfg.chatWorkspaceId}/${verb}`;
  }

  // Preferred path: stream the reply over Server-Sent Events. Streaming keeps the HTTP connection alive
  // through a long ReAct turn (e.g. summarising a rich spec, which exceeds the ~60 s synchronous cap), so
  // the browser gets the real reply instead of a 504. We aggregate the stream server-side and return the
  // final text; the browser stays a simple request/response caller.
  async invokeChatStream(args: { sessionId: string; message: string; userId: string }): Promise<ChatInvokeResult> {
    const payload = JSON.stringify({ message: args.message, session_id: args.sessionId, user_id: args.userId });

    const post = async (token: string): Promise<Response> => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), STREAM_TIMEOUT_MS);
      try {
        return await this.fetchImpl(this.invokeUrl(true), {
          method: "POST",
          // The platform threads a multi-turn session by the `X-Session-Id` HEADER, not the body
          // `session_id` (which it ignores for threading, minting a fresh per-execution session each turn).
          // This header is what makes turn 2 share turn 1's thread — see docs/09 § Session handling.
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
            Accept: "text/event-stream",
            "X-Session-Id": args.sessionId,
          },
          body: payload,
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }
    };

    let res: Response;
    try {
      res = await post(await this.tokens.get());
      if (res.status === 401) res = await post(await this.tokens.get(true));
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") return pendingResult();
      throw err;
    }

    if (res.status === 504) return pendingResult();
    if (res.status >= 400) {
      const text = await safeText(res);
      throw new Error(`chat stream invoke failed: HTTP ${res.status} ${text.slice(0, 300)}`);
    }
    // `res.text()` blocks until the whole SSE stream ends (turn complete) — that's what beats the 60 s cap.
    const body = await res.text();
    return extractFinalFromSse(body);
  }

  async invokeChat(args: { sessionId: string; message: string; userId: string }): Promise<ChatInvokeResult> {
    const payload = JSON.stringify({ message: args.message, session_id: args.sessionId, user_id: args.userId });

    const post = async (token: string): Promise<Response> => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), CHAT_TIMEOUT_MS);
      try {
        return await this.fetchImpl(this.invokeUrl(), {
          method: "POST",
          // Same as the streaming path: the session threads on the `X-Session-Id` header, not the body.
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
            "X-Session-Id": args.sessionId,
          },
          body: payload,
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }
    };

    let res: Response;
    try {
      res = await post(await this.tokens.get());
      if (res.status === 401) res = await post(await this.tokens.get(true));
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") {
        return pendingResult(); // took too long client-side; the turn continues server-side
      }
      throw err;
    }

    if (res.status === 504) return pendingResult();
    if (res.status >= 400) {
      const text = await safeText(res);
      throw new Error(`chat invoke failed: HTTP ${res.status} ${text.slice(0, 300)}`);
    }
    const data = (await res.json()) as Record<string, unknown>;
    return { reply: extractReply(data), status: String(data.status ?? "completed"), pending: false };
  }
}

function pendingResult(): ChatInvokeResult {
  return {
    reply: "The agent is still replying. Click “How’s it going?” in a moment — the pipeline board is polling and will update on its own.",
    status: "pending",
    pending: true,
  };
}

// The chat agent replies with plain text under `response` (the platform envelope). Older/other shapes put it
// under `result` or nest it once more; unwrap defensively so a wrapper never leaks into the UI.
export function extractReply(data: Record<string, unknown>): string {
  const candidate = data.response ?? data.result ?? data.message;
  if (typeof candidate === "string") {
    const trimmed = candidate.trim();
    if (trimmed.startsWith("{")) {
      try {
        const inner = JSON.parse(trimmed) as Record<string, unknown>;
        const nested = inner.response ?? inner.result ?? inner.message;
        if (typeof nested === "string") return nested;
      } catch {
        // not JSON — fall through and return the raw string
      }
    }
    return candidate;
  }
  if (candidate && typeof candidate === "object") {
    const obj = candidate as Record<string, unknown>;
    const nested = obj.response ?? obj.result ?? obj.message;
    if (typeof nested === "string") return nested;
    return JSON.stringify(candidate);
  }
  return "(no reply returned by the agent)";
}

// Parse the /invokeStream SSE body into a final reply. Framing (probed live 2026-09-25):
//   data: {"chunk_type":"metadata"|"progress"|"text"|"done"|"error","content":"…","metadata":{...}}
// A `done` chunk carries the complete reply in `content`; otherwise we concatenate the `text` chunks.
export function extractFinalFromSse(body: string): ChatInvokeResult {
  let doneContent: string | null = null;
  let status = "completed";
  let errorMessage: string | null = null;
  const textParts: string[] = [];

  for (const rawLine of body.split(/\r?\n/)) {
    const line = rawLine.trimStart();
    if (!line.startsWith("data:")) continue;
    const jsonPart = line.slice(5).trim();
    if (!jsonPart || jsonPart === "[DONE]") continue;
    let chunk: { chunk_type?: string; content?: string; metadata?: { status?: string } };
    try {
      chunk = JSON.parse(jsonPart);
    } catch {
      continue;
    }
    const md = chunk.metadata;
    if (md?.status) status = md.status;
    switch (chunk.chunk_type) {
      case "text":
        if (typeof chunk.content === "string") textParts.push(chunk.content);
        break;
      case "done":
        if (typeof chunk.content === "string") doneContent = chunk.content;
        break;
      case "error":
        errorMessage = typeof chunk.content === "string" ? chunk.content : "stream error";
        status = "failed";
        break;
      default:
        break; // metadata / progress / tool — no user-visible text
    }
  }

  if (errorMessage && !doneContent && textParts.length === 0) {
    return { reply: `error: ${errorMessage}`, status, pending: false };
  }
  const reply = doneContent ?? textParts.join("");
  if (!reply.trim()) return pendingResult();
  return { reply, status, pending: false };
}

async function safeText(res: Response): Promise<string> {
  try {
    return await res.text();
  } catch {
    return "";
  }
}
