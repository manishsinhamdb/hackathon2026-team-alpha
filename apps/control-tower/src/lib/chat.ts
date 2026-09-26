// Client-side classification of the /api/chat BFF response and the busy-queue retry driver (Round 4,
// item 2). The BFF sanitizes every platform payload: a busy session comes back as HTTP 409 with
// {busy:true, blockingExecutionId, blockingStatus}; any other failure comes back as a friendly {error}
// (the raw platform JSON goes to the server log only). So nothing here ever sees raw platform JSON.
//
// SAFETY: the retry driver treats a 409 as "the send was rejected and NOT enqueued" — the platform's
// SESSION_BUSY guard rejects before accepting, so re-attempting the same message is safe and IS the
// auto-send. Any non-busy outcome (ok/pending accepted, or error) stops the loop, so a message that WAS
// accepted (200 or a 504 "pending") is never sent twice.

export interface ChatOk {
  kind: "ok";
  reply: string;
  status: string;
  pending: boolean; // true when the turn was accepted but is still replying (gateway 504 / client timeout)
  note?: string;
}
export interface ChatBusy {
  kind: "busy";
  blockingExecutionId?: string;
  blockingStatus?: string;
}
export interface ChatErr {
  kind: "error";
  message: string; // friendly; safe to show the user
  detail?: string; // short, non-secret disclosure (e.g. "HTTP 502")
}
export type ChatOutcome = ChatOk | ChatBusy | ChatErr;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function classifyChat(status: number, body: any): ChatOutcome {
  if (status === 409 || body?.busy) {
    return { kind: "busy", blockingExecutionId: body?.blockingExecutionId, blockingStatus: body?.blockingStatus };
  }
  if (status >= 400 || body?.error) {
    const message =
      typeof body?.error === "string" && body.error ? body.error : "The agent service returned an error.";
    return { kind: "error", message, detail: typeof body?.detail === "string" ? body.detail : `HTTP ${status}` };
  }
  return {
    kind: "ok",
    reply: String(body?.reply ?? ""),
    status: String(body?.status ?? "completed"),
    pending: !!body?.pending,
    note: typeof body?.note === "string" ? body.note : undefined,
  };
}

export interface ChatBody {
  session_id: string;
  message: string;
}

// A single POST to the BFF chat route, classified. Never throws for an HTTP error — a network failure
// (fetch reject) surfaces as a friendly error outcome so the caller's loop can stop safely (we do NOT retry
// a network failure, because the send may have been accepted server-side — retrying could double-send).
export async function postChat(fetchImpl: typeof fetch, body: ChatBody): Promise<ChatOutcome> {
  let res: Response;
  try {
    res = await fetchImpl("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    return { kind: "error", message: "Couldn’t reach the server. Check your connection and try again.", detail: err instanceof Error ? err.name : "network" };
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let data: any = {};
  try {
    data = await res.json();
  } catch {
    /* empty/HTML body — classify by status */
  }
  return classifyChat(res.status, data);
}

// Retry a send while the session is busy, then auto-send when it frees. Injected `sleep` (and an optional
// `shouldContinue` gate so the UI can cancel the loop, e.g. on "New session" or "Stop") keep it testable
// with a fake clock. Returns the FINAL outcome: ok/pending/error (accepted or failed), or the last busy
// outcome if the loop was cancelled or hit maxAttempts.
export async function sendChatUntilFree(
  fetchImpl: typeof fetch,
  body: ChatBody,
  opts: {
    sleep: (ms: number) => Promise<void>;
    intervalMs?: number;
    maxAttempts?: number;
    onBusy?: (b: ChatBusy) => void;
    shouldContinue?: () => boolean;
  },
): Promise<ChatOutcome> {
  const interval = opts.intervalMs ?? 5000;
  const max = opts.maxAttempts ?? Infinity;
  let attempt = 0;
  let last: ChatOutcome = { kind: "busy" };
  while (true) {
    if (opts.shouldContinue && !opts.shouldContinue()) return last;
    const outcome = await postChat(fetchImpl, body);
    attempt += 1;
    last = outcome;
    if (outcome.kind !== "busy") return outcome; // accepted (ok/pending) or failed — stop; never double-send
    opts.onBusy?.(outcome);
    if (attempt >= max) return outcome;
    await opts.sleep(interval);
  }
}
