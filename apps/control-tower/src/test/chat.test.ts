import { describe, expect, it, vi } from "vitest";
import { classifyChat, postChat, sendChatUntilFree } from "@/lib/chat";

function res(status: number, body: unknown): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
  } as unknown as Response;
}

describe("classifyChat", () => {
  it("maps a 200 reply to ok, carrying pending + note", () => {
    const o = classifyChat(200, { reply: "hi", status: "completed", pending: false, note: "x" });
    expect(o).toEqual({ kind: "ok", reply: "hi", status: "completed", pending: false, note: "x" });
  });
  it("maps a 200 pending reply", () => {
    const o = classifyChat(200, { reply: "still replying", status: "pending", pending: true });
    expect(o.kind).toBe("ok");
    if (o.kind === "ok") expect(o.pending).toBe(true);
  });
  it("maps a 409 (or body.busy) to busy with the blocking fields", () => {
    const o = classifyChat(409, { busy: true, blockingExecutionId: "exec_1", blockingStatus: "pending" });
    expect(o).toEqual({ kind: "busy", blockingExecutionId: "exec_1", blockingStatus: "pending" });
    expect(classifyChat(200, { busy: true }).kind).toBe("busy");
  });
  it("maps an error to a friendly outcome with a short detail (never raw JSON)", () => {
    const o = classifyChat(502, { error: "The agent service couldn’t be reached.", detail: "platform HTTP 500" });
    expect(o.kind).toBe("error");
    if (o.kind === "error") {
      expect(o.message).toMatch(/couldn/i);
      expect(o.detail).toBe("platform HTTP 500");
    }
  });
  it("synthesises a friendly message when the error body has none", () => {
    const o = classifyChat(500, {});
    expect(o.kind).toBe("error");
    if (o.kind === "error") expect(o.message).toMatch(/error/i);
  });
});

describe("postChat", () => {
  it("classifies a 200 response", async () => {
    const fetchImpl = (async () => res(200, { reply: "ok", status: "completed", pending: false })) as unknown as typeof fetch;
    expect((await postChat(fetchImpl, { session_id: "s", message: "m" })).kind).toBe("ok");
  });
  it("turns a network failure into a friendly error (never retried by the driver)", async () => {
    const fetchImpl = (async () => {
      throw new Error("boom");
    }) as unknown as typeof fetch;
    const o = await postChat(fetchImpl, { session_id: "s", message: "m" });
    expect(o.kind).toBe("error");
  });
});

describe("sendChatUntilFree — 409 → queued → auto-send after free", () => {
  it("retries while busy, then sends when the session frees", async () => {
    const responses = [
      res(409, { busy: true, blockingExecutionId: "exec_1", blockingStatus: "pending" }),
      res(409, { busy: true }),
      res(200, { reply: "done", status: "completed", pending: false }),
    ];
    let call = 0;
    const fetchImpl = (async () => responses[call++]) as unknown as typeof fetch;
    const onBusy = vi.fn();
    const sleep = vi.fn(async () => {});

    const outcome = await sendChatUntilFree(fetchImpl, { session_id: "s", message: "m" }, { sleep, onBusy, intervalMs: 5000 });

    expect(outcome.kind).toBe("ok");
    if (outcome.kind === "ok") expect(outcome.reply).toBe("done");
    expect(call).toBe(3); // two 409s + one accepted
    expect(onBusy).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenCalledWith(5000);
  });

  it("stops immediately on an accepted (pending) turn — never double-sends", async () => {
    let call = 0;
    const fetchImpl = (async () => {
      call++;
      return res(200, { reply: "", status: "pending", pending: true });
    }) as unknown as typeof fetch;
    const sleep = vi.fn(async () => {});
    const outcome = await sendChatUntilFree(fetchImpl, { session_id: "s", message: "m" }, { sleep });
    expect(outcome.kind).toBe("ok");
    expect(call).toBe(1);
    expect(sleep).not.toHaveBeenCalled();
  });

  it("stops on an error outcome without retrying", async () => {
    let call = 0;
    const fetchImpl = (async () => {
      call++;
      return res(502, { error: "nope" });
    }) as unknown as typeof fetch;
    const sleep = vi.fn(async () => {});
    const outcome = await sendChatUntilFree(fetchImpl, { session_id: "s", message: "m" }, { sleep });
    expect(outcome.kind).toBe("error");
    expect(call).toBe(1);
  });

  it("honours a shouldContinue gate (cancelled queue)", async () => {
    const fetchImpl = (async () => res(409, { busy: true })) as unknown as typeof fetch;
    const sleep = vi.fn(async () => {});
    const outcome = await sendChatUntilFree(fetchImpl, { session_id: "s", message: "m" }, {
      sleep,
      shouldContinue: () => false,
    });
    expect(outcome.kind).toBe("busy"); // never even attempted
  });
});
