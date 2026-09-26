// Run liveness (Round 5, item 1): the "abandoned" rule and the Retry / Continue chat message. Pure and
// dependency-free so it unit-tests without React/DOM (src/test/runHealth.test.ts).
//
// Every durable stage (draft, code, deploy, teardown) touches `runs.heartbeat_at` at least every 30 s while
// it runs. A run is ABANDONED when it is still queued/running but its newest sign of life — the max of
// heartbeat_at, updated_at, started_at (older runs have no heartbeat_at, so updated_at is the fallback) — is
// older than 180 s. A run the chat agent already gave up on is status "failed" with error.code "ABANDONED";
// that shows as abandoned too.

export const ABANDON_MS = 180_000;

export type RetryStage = "draft" | "code" | "deploy" | "teardown" | "test";

// The loose run shape these helpers read (a RawRun or anything with the same fields).
export interface LivenessRun {
  status: string;
  started_at?: string;
  updated_at?: string;
  heartbeat_at?: string;
  error?: { code?: string } | null;
}

function ms(iso?: string): number {
  if (!iso) return NaN;
  return Date.parse(iso);
}

// The newest of (heartbeat_at, updated_at, started_at) as an ISO string, or undefined when none parse.
export function lastBeatAt(run: LivenessRun): string | undefined {
  let best: { t: number; iso: string } | undefined;
  for (const iso of [run.heartbeat_at, run.updated_at, run.started_at]) {
    const t = ms(iso);
    if (!Number.isNaN(t) && (!best || t > best.t)) best = { t, iso: iso! };
  }
  return best?.iso;
}

export function isAbandoned(run: LivenessRun, nowMs: number): boolean {
  if (run.status === "failed") return run.error?.code === "ABANDONED";
  if (run.status !== "queued" && run.status !== "running") return false;
  const beat = lastBeatAt(run);
  if (!beat) return false; // no timestamps at all — can't judge, don't cry wolf
  return nowMs - Date.parse(beat) > ABANDON_MS;
}

// The exact message the chat agent understands as "retry / resume this stage". It marks a stale run
// failed/abandoned and resumes it (or starts fresh).
export function retryMessage(stage: RetryStage | string, pocId: string, runId: string): string {
  return `Retry the ${stage} stage for ${pocId} (continue run ${runId} if it can be resumed).`;
}

const DONE = new Set(["succeeded", "done", "completed"]);

// "Continue" when the run already made progress (any done task or step), else "Retry".
export function retryLabel(steps: Array<{ status: string }>, tasks: Array<{ status: string }>): "Retry" | "Continue" {
  return steps.some((s) => DONE.has(s.status)) || tasks.some((t) => DONE.has(t.status)) ? "Continue" : "Retry";
}
