import { describe, expect, it } from "vitest";
import { PlatformClient, type FetchLike } from "@/lib/platform";

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

// Fake fetch that records the runtime-session calls and answers by URL + method.
function makeFetch(handlers: {
  onList?: () => Response;
  onStop?: (call: number) => Response;
  onInvoke?: (call: number) => Response;
}): { fetch: FetchLike; stopUrls: string[]; methods: string[]; tokenCalls: () => number } {
  let tokenCalls = 0;
  let stopCalls = 0;
  let invokeCalls = 0;
  const stopUrls: string[] = [];
  const methods: string[] = [];
  const fetchImpl = (async (url: string, init?: RequestInit) => {
    if (url.endsWith("/api/v1/oauth/token")) {
      tokenCalls += 1;
      return res(200, { access_token: `tok-${tokenCalls}`, expires_in: 3600 });
    }
    if (url.endsWith("/runtime-sessions")) {
      methods.push(String(init?.method ?? "GET"));
      return handlers.onList ? handlers.onList() : res(200, { sessions: [] });
    }
    if (url.includes("/runtime-sessions/") && url.endsWith("/stop")) {
      stopCalls += 1;
      stopUrls.push(url);
      methods.push(String(init?.method ?? "GET"));
      return handlers.onStop ? handlers.onStop(stopCalls) : res(200, { success: true });
    }
    if (url.includes("/invoke")) {
      invokeCalls += 1;
      return handlers.onInvoke ? handlers.onInvoke(invokeCalls) : res(200, { response: "hi" });
    }
    throw new Error(`unexpected url ${url}`);
  }) as unknown as FetchLike;
  return { fetch: fetchImpl, stopUrls, methods, tokenCalls: () => tokenCalls };
}

describe("listRuntimeSessions", () => {
  it("returns the sessions array", async () => {
    const f = makeFetch({
      onList: () => res(200, { sessions: [{ session_id: "ct-a", status: "active" }] }),
    });
    const client = new PlatformClient(CFG, f.fetch, () => 0);
    const list = await client.listRuntimeSessions();
    expect(list).toEqual([{ session_id: "ct-a", status: "active" }]);
  });
});

describe("stopRuntimeSession", () => {
  it("POSTs to the runtime-session stop endpoint and reports stopped", async () => {
    const f = makeFetch({ onStop: () => res(200, { success: true }) });
    const client = new PlatformClient(CFG, f.fetch, () => 0);
    const r = await client.stopRuntimeSession("ct-a");
    expect(r).toEqual({ stopped: true });
    expect(f.stopUrls[0]).toBe("https://platform.test/api/v1/projects/p1/workspaces/ws-1/runtime-sessions/ct-a/stop");
    expect(f.methods).toContain("POST");
  });

  it("treats a 404 as already-free, not an error", async () => {
    const f = makeFetch({ onStop: () => res(404, { code: "NOT_FOUND" }) });
    const client = new PlatformClient(CFG, f.fetch, () => 0);
    expect(await client.stopRuntimeSession("gone")).toEqual({ stopped: false, notFound: true });
  });

  it("refreshes the token once and retries on a 401", async () => {
    const f = makeFetch({ onStop: (n) => (n === 1 ? res(401, { error: "expired" }) : res(200, { success: true })) });
    const client = new PlatformClient(CFG, f.fetch, () => 0);
    const r = await client.stopRuntimeSession("ct-a");
    expect(r.stopped).toBe(true);
    expect(f.tokenCalls()).toBe(2);
  });

  it("throws on a non-404 error status", async () => {
    const f = makeFetch({ onStop: () => res(500, "boom") });
    const client = new PlatformClient(CFG, f.fetch, () => 0);
    await expect(client.stopRuntimeSession("ct-a")).rejects.toThrow(/HTTP 500/);
  });
});

describe("invoke returns a busy result on 409 SESSION_BUSY (no raw JSON thrown)", () => {
  const busyBody = JSON.stringify({ code: "SESSION_BUSY", blocking_execution_id: "exec_9", blocking_status: "pending" });

  it("sync invoke: surfaces busy with the blocking fields", async () => {
    const f = makeFetch({ onInvoke: () => res(409, busyBody) });
    const client = new PlatformClient(CFG, f.fetch, () => 0);
    const r = await client.invokeChat({ sessionId: "s", message: "m", userId: "u" });
    expect(r.busy).toBe(true);
    expect(r.blockingExecutionId).toBe("exec_9");
    expect(r.blockingStatus).toBe("pending");
  });

  it("streaming invoke: surfaces busy too", async () => {
    const f = makeFetch({ onInvoke: () => res(409, busyBody) });
    const client = new PlatformClient(CFG, f.fetch, () => 0);
    const r = await client.invokeChatStream({ sessionId: "s", message: "m", userId: "u" });
    expect(r.busy).toBe(true);
    expect(r.blockingExecutionId).toBe("exec_9");
  });
});
