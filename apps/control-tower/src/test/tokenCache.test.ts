import { describe, expect, it } from "vitest";
import { PlatformClient, TokenCache, extractFinalFromSse, extractReply, type FetchLike } from "@/lib/platform";

const CFG = {
  baseUrl: "https://platform.test",
  projectId: "p1",
  chatWorkspaceId: "ws-1",
  clientId: "cid",
  clientSecret: "secret",
};

function res(status: number, body: unknown): Response {
  const isString = typeof body === "string";
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => (isString ? JSON.parse(body as string) : body),
    text: async () => (isString ? (body as string) : JSON.stringify(body)),
  } as unknown as Response;
}

// A fake fetch that records calls and answers by URL. `tokenValue` lets the caller change what the token
// endpoint returns across calls (to prove refresh mints a new token).
function makeFetch(handlers: {
  onToken?: () => Response;
  onInvoke?: (call: number) => Response;
}): { fetch: FetchLike; tokenCalls: () => number; invokeCalls: () => number } {
  let tokenCalls = 0;
  let invokeCalls = 0;
  const fetchImpl = (async (url: string) => {
    if (url.endsWith("/api/v1/oauth/token")) {
      tokenCalls += 1;
      return handlers.onToken ? handlers.onToken() : res(200, { access_token: `tok-${tokenCalls}`, expires_in: 3600 });
    }
    if (url.includes("/invoke")) {
      invokeCalls += 1;
      return handlers.onInvoke ? handlers.onInvoke(invokeCalls) : res(200, { response: "hi", status: "completed" });
    }
    throw new Error(`unexpected url ${url}`);
  }) as unknown as FetchLike;
  return { fetch: fetchImpl, tokenCalls: () => tokenCalls, invokeCalls: () => invokeCalls };
}

describe("TokenCache", () => {
  it("exchanges once and caches within the TTL", async () => {
    const f = makeFetch({});
    let t = 1_000_000;
    const cache = new TokenCache(CFG, f.fetch, () => t);
    const a = await cache.get();
    const b = await cache.get();
    expect(a).toBe("tok-1");
    expect(b).toBe("tok-1");
    expect(f.tokenCalls()).toBe(1);
  });

  it("refreshes after the token expires (expires_in minus skew)", async () => {
    const f = makeFetch({});
    let t = 0;
    const cache = new TokenCache(CFG, f.fetch, () => t);
    await cache.get(); // tok-1, valid for (3600-60)s
    t += 3_540_000 + 1; // just past expiry
    const again = await cache.get();
    expect(again).toBe("tok-2");
    expect(f.tokenCalls()).toBe(2);
  });

  it("force-refreshes on demand", async () => {
    const f = makeFetch({});
    const cache = new TokenCache(CFG, f.fetch, () => 0);
    await cache.get();
    const forced = await cache.get(true);
    expect(forced).toBe("tok-2");
    expect(f.tokenCalls()).toBe(2);
  });

  it("clamps a short TTL to a 30 s minimum floor", async () => {
    const f = makeFetch({ onToken: () => res(200, { access_token: "short", expires_in: 5 }) });
    let t = 0;
    const cache = new TokenCache(CFG, f.fetch, () => t);
    await cache.get();
    t += 20_000; // 20s later — below the 30s floor, so still cached
    await cache.get();
    expect(f.tokenCalls()).toBe(1);
  });

  it("throws with no credentials leaked when the exchange fails", async () => {
    const f = makeFetch({ onToken: () => res(401, { error: "invalid_client" }) });
    const cache = new TokenCache(CFG, f.fetch, () => 0);
    await expect(cache.get()).rejects.toThrow(/token exchange failed: HTTP 401/);
  });
});

describe("PlatformClient.invokeChat", () => {
  it("returns the agent reply on 200", async () => {
    const f = makeFetch({ onInvoke: () => res(200, { response: "hello there", status: "completed" }) });
    const client = new PlatformClient(CFG, f.fetch, () => 0);
    const r = await client.invokeChat({ sessionId: "s1", message: "hi", userId: "u_ui" });
    expect(r).toEqual({ reply: "hello there", status: "completed", pending: false });
  });

  it("refreshes the token once and retries on a 401 invoke", async () => {
    const f = makeFetch({ onInvoke: (n) => (n === 1 ? res(401, { error: "expired" }) : res(200, { response: "ok" })) });
    const client = new PlatformClient(CFG, f.fetch, () => 0);
    const r = await client.invokeChat({ sessionId: "s1", message: "hi", userId: "u_ui" });
    expect(r.reply).toBe("ok");
    expect(f.tokenCalls()).toBe(2); // initial + forced refresh
    expect(f.invokeCalls()).toBe(2);
  });

  it("surfaces a 504 as a pending 'still replying' result, not an error", async () => {
    const f = makeFetch({ onInvoke: () => res(504, "gateway timeout") });
    const client = new PlatformClient(CFG, f.fetch, () => 0);
    const r = await client.invokeChat({ sessionId: "s1", message: "hi", userId: "u_ui" });
    expect(r.pending).toBe(true);
    expect(r.status).toBe("pending");
    expect(r.reply).toMatch(/still replying/i);
  });

  it("throws on a non-504 server error", async () => {
    const f = makeFetch({ onInvoke: () => res(500, "boom") });
    const client = new PlatformClient(CFG, f.fetch, () => 0);
    await expect(client.invokeChat({ sessionId: "s1", message: "hi", userId: "u_ui" })).rejects.toThrow(/HTTP 500/);
  });
});

describe("session threading (X-Session-Id header)", () => {
  // The platform threads a multi-turn chat session by the X-Session-Id HEADER, not the body session_id.
  // A fake fetch that records the request init lets us assert the header is sent and is stable across turns.
  function makeHeaderFetch(): { fetch: FetchLike; inits: RequestInit[]; urls: string[] } {
    const inits: RequestInit[] = [];
    const urls: string[] = [];
    const fetchImpl = (async (url: string, init?: RequestInit) => {
      if (url.endsWith("/api/v1/oauth/token")) return res(200, { access_token: "tok", expires_in: 3600 });
      urls.push(url);
      inits.push(init ?? {});
      // Streaming path: a minimal SSE done chunk. Sync path: a plain JSON envelope.
      if (url.includes("/invokeStream")) {
        return res(200, 'data: {"chunk_type":"done","content":"ok","metadata":{"status":"completed"}}\n');
      }
      return res(200, { response: "ok", status: "completed" });
    }) as unknown as FetchLike;
    return { fetch: fetchImpl, inits, urls };
  }

  const headerOf = (init: RequestInit, name: string): string | undefined =>
    (init.headers as Record<string, string> | undefined)?.[name];

  it("sends the UI session id as X-Session-Id on the streaming invoke", async () => {
    const f = makeHeaderFetch();
    const client = new PlatformClient(CFG, f.fetch, () => 0);
    await client.invokeChatStream({ sessionId: "ct-abc123", message: "hi", userId: "u_ui" });
    expect(f.urls[0]).toContain("/invokeStream");
    expect(headerOf(f.inits[0], "X-Session-Id")).toBe("ct-abc123");
  });

  it("sends the SAME X-Session-Id on turn 1 and turn 2 of one conversation", async () => {
    const f = makeHeaderFetch();
    const client = new PlatformClient(CFG, f.fetch, () => 0);
    await client.invokeChatStream({ sessionId: "ct-stable", message: "turn 1", userId: "u_ui" });
    await client.invokeChatStream({ sessionId: "ct-stable", message: "turn 2", userId: "u_ui" });
    const t1 = headerOf(f.inits[0], "X-Session-Id");
    const t2 = headerOf(f.inits[1], "X-Session-Id");
    expect(t1).toBe("ct-stable");
    expect(t2).toBe("ct-stable");
    expect(t1).toBe(t2); // one stable session across turns — the fix for the lost-thread bug
  });

  it("also sends X-Session-Id on the synchronous fallback invoke", async () => {
    const f = makeHeaderFetch();
    const client = new PlatformClient(CFG, f.fetch, () => 0);
    await client.invokeChat({ sessionId: "ct-sync", message: "hi", userId: "u_ui" });
    expect(headerOf(f.inits[0], "X-Session-Id")).toBe("ct-sync");
  });
});

describe("extractFinalFromSse", () => {
  const sse = [
    'data: {"chunk_type":"metadata","content":"","metadata":{"status":"started"}}',
    "",
    'data: {"chunk_type":"progress","content":"Waiting for agent…","metadata":{"phase":"starting"}}',
    "",
    'data: {"chunk_type":"text","content":"Hello! I"}',
    "",
    'data: {"chunk_type":"text","content":"\'m POC Builder"}',
    "",
    'data: {"chunk_type":"done","content":"Hello! I\'m POC Builder — full reply.","metadata":{"status":"completed"}}',
    "",
  ].join("\n");

  it("prefers the done chunk's complete content", () => {
    const r = extractFinalFromSse(sse);
    expect(r.reply).toBe("Hello! I'm POC Builder — full reply.");
    expect(r.status).toBe("completed");
    expect(r.pending).toBe(false);
  });

  it("concatenates text chunks when no done chunk arrives", () => {
    const partial = [
      'data: {"chunk_type":"text","content":"part one "}',
      "",
      'data: {"chunk_type":"text","content":"part two"}',
      "",
    ].join("\n");
    expect(extractFinalFromSse(partial).reply).toBe("part one part two");
  });

  it("surfaces an error chunk as an error reply", () => {
    const err = 'data: {"chunk_type":"error","content":"the graph blew up","metadata":{"status":"failed"}}\n';
    const r = extractFinalFromSse(err);
    expect(r.status).toBe("failed");
    expect(r.reply).toMatch(/blew up/);
  });

  it("returns pending when the stream produced no text", () => {
    const empty = 'data: {"chunk_type":"metadata","content":"","metadata":{"status":"started"}}\n';
    expect(extractFinalFromSse(empty).pending).toBe(true);
  });
});

describe("extractReply", () => {
  it("reads a plain string response", () => {
    expect(extractReply({ response: "plain text" })).toBe("plain text");
  });
  it("unwraps a JSON-wrapped response string", () => {
    expect(extractReply({ response: JSON.stringify({ result: "nested" }) })).toBe("nested");
  });
  it("reads result when response is absent", () => {
    expect(extractReply({ result: "from result" })).toBe("from result");
  });
  it("falls back gracefully when nothing is present", () => {
    expect(extractReply({})).toMatch(/no reply/i);
  });
});
