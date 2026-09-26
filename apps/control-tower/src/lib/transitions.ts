// Transition notices (Round 5, item 4). When a board poll shows a stage transition for the selected POC,
// the tower posts a system card into the conversation with the outcome and next-action buttons, and — if the
// session is free — auto-sends ONE "How's it going?" so the agent narrates and asks for approval.
//
// Pure: `detectTransitions(prev, next)` compares two consecutive poll results (null prev = baseline, so the
// first poll after page load / POC switch never fires), and `claimUnseen` dedupes per
// (poc_id, run_id, transition) against a persisted seen-set so reloads / re-polls never repeat a notice.
// Unit-tested in src/test/transitions.test.ts.
import { displayTitle } from "./label";
import { retryMessage } from "./runHealth";
import type { PocDetail, RunView } from "./types";

export type TransitionKind =
  | "spec_ready" | "questions" | "code_ready" | "deployed" | "tested" | "failed" | "abandoned" | "torn_down";

export type NoticeAction =
  | { kind: "send"; label: string; message: string; scoped: boolean } // scoped: append the POC id like the chips
  | { kind: "focus"; label: string } // focus the composer
  | { kind: "link"; label: string; href: string };

export interface Transition {
  id: string; // dedupe key: `${poc_id}:${run_id}:${kind}` (abandoned also carries the execution number)
  poc_id: string;
  run_id: string;
  stage: RunView["stage"];
  kind: TransitionKind;
  tone: "success" | "amber" | "fail";
  title: string;
  body: string;
  actions: NoticeAction[];
}

// The canned messages — same wording as the Conversation chips (src/components/Chat.tsx ACTIONS).
export const CANNED = {
  spec: "Show me the spec",
  build: "Looks good, go ahead and build it",
  deploy: "Deploy it, I don't need to review the code (4-hour TTL)",
  teardown: "Tear it down",
  summary: "Give me a summary of this POC",
  status: "How's it going?",
} as const;

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
const ACTIVE = new Set(["queued", "running", "waiting_user"]);

function stageName(stage: RunView["stage"]): string {
  return { draft: "Draft", code: "Code", deploy: "Deploy", test: "Tests", teardown: "Teardown" }[stage] ?? stage;
}

function testLine(next: PocDetail): string {
  const t = next.test;
  if (!t) return "";
  return ` Tests: ${t.passed} passed, ${t.failed} failed${t.total ? ` of ${t.total}` : ""}.`;
}

function build(kind: TransitionKind, run: RunView, next: PocDetail): Transition {
  const poc = next.poc.poc_id;
  const name = displayTitle(next.poc);
  const base = { poc_id: poc, run_id: run.run_id, stage: run.stage, kind };
  const id = `${poc}:${run.run_id}:${kind}${kind === "abandoned" ? `:${run.executions ?? 1}` : ""}`;
  const app = next.deployment?.app ?? run.app_url;
  const retry: NoticeAction = { kind: "send", label: "Retry", message: retryMessage(run.stage, poc, run.run_id), scoped: false };
  switch (kind) {
    case "spec_ready":
      return { ...base, id, tone: "success", title: "Spec ready", body: `The draft for ${name} finished${next.poc.current_versions.spec ? ` — spec ${next.poc.current_versions.spec}` : ""}.`,
        actions: [{ kind: "send", label: "Show me the spec", message: CANNED.spec, scoped: true }, { kind: "send", label: "Build it", message: CANNED.build, scoped: true }] };
    case "questions":
      return { ...base, id, tone: "amber", title: "Clarification needed", body: `The draft for ${name} has questions for you${next.clarification?.questions.length ? ` (${next.clarification.questions.length})` : ""}.`,
        actions: [{ kind: "focus", label: "Answer in chat" }] };
    case "code_ready":
      return { ...base, id, tone: "success", title: "Code ready", body: `The code run for ${name} succeeded${next.poc.current_versions.code ? ` — code ${next.poc.current_versions.code}` : ""}.`,
        actions: [{ kind: "send", label: "Deploy · 4h TTL", message: CANNED.deploy, scoped: true }] };
    case "deployed":
    case "tested": {
      const actions: NoticeAction[] = [];
      if (app) actions.push({ kind: "link", label: "Open App", href: app });
      actions.push({ kind: "send", label: "Tear it down", message: CANNED.teardown, scoped: true });
      const failed = (next.test?.failed ?? 0) > 0 || run.test_passed === false;
      return { ...base, id, tone: kind === "tested" && failed ? "amber" : "success",
        title: kind === "tested" ? (failed ? "Deployed — tests failing" : "Deployed & tested") : "Deployed",
        body: `${name} is live.${kind === "tested" ? testLine(next) : ""}`, actions };
    }
    case "failed":
      return { ...base, id, tone: "fail", title: `${stageName(run.stage)} failed`, body: `${run.error?.code ? `${run.error.code}: ` : ""}${run.error?.message ?? "The run failed."}`.slice(0, 280),
        actions: [retry] };
    case "abandoned":
      return { ...base, id, tone: "amber", title: `${stageName(run.stage)} run abandoned`, body: `No heartbeat from ${run.run_id} for over 3 minutes — the stage stopped making progress.`,
        actions: [{ ...retry, label: run.has_progress ? "Continue" : "Retry" }] };
    case "torn_down":
      return { ...base, id, tone: "success", title: "Torn down", body: `All cloud resources for ${name} were removed.`,
        actions: [{ kind: "send", label: "Summary", message: CANNED.summary, scoped: true }] };
  }
}

// Compare two consecutive polls of the SAME POC. prev === null (first poll after load / switch) -> [] so a
// baseline is established before anything fires. A run absent from prev counts as "was not terminal" (it
// started and finished between polls).
export function detectTransitions(prev: PocDetail | null, next: PocDetail): Transition[] {
  if (!prev || prev.poc.poc_id !== next.poc.poc_id) return [];
  const before = new Map(prev.runs.map((r) => [r.run_id, r]));
  const deployActive = next.runs.some((r) => r.stage === "deploy" && ACTIVE.has(r.status) && !r.abandoned);
  const out: Transition[] = [];
  for (const run of next.runs) {
    const was = before.get(run.run_id);
    // Abandoned (stale heartbeat, or failed with ABANDONED) — fires once per execution.
    if (run.abandoned) {
      if (!was?.abandoned || (was.executions ?? 1) !== (run.executions ?? 1)) out.push(build("abandoned", run, next));
      continue;
    }
    const justFinished = TERMINAL.has(run.status) && (!was || was.status !== run.status);
    if (!justFinished) continue;
    if (run.status === "failed") {
      // A test run failing inside a still-running deploy is reported by the deploy's own outcome.
      if (!(run.stage === "test" && deployActive)) out.push(build("failed", run, next));
      continue;
    }
    if (run.status !== "succeeded") continue; // cancelled: no notice
    switch (run.stage) {
      case "draft":
        out.push(build(run.needs_clarification ? "questions" : "spec_ready", run, next));
        break;
      case "code":
        out.push(build("code_ready", run, next));
        break;
      case "deploy": {
        // The deploy agent runs the e2e tests inside the deploy run; fold them into one "tested" notice.
        const started = Date.parse(run.started_at ?? "") || 0;
        const tested = run.test_passed !== undefined ||
          next.runs.some((r) => r.stage === "test" && TERMINAL.has(r.status) && (Date.parse(r.started_at ?? "") || 0) >= started);
        out.push(build(tested ? "tested" : "deployed", run, next));
        break;
      }
      case "test":
        if (!deployActive) out.push(build("tested", run, next)); // a standalone re-test
        break;
      case "teardown":
        out.push(build("torn_down", run, next));
        break;
    }
  }
  return out;
}

/* ---- Dedupe (persisted seen-set) ------------------------------------------------------------------------ */

export const SEEN_KEY = "ct.notices.seen.v1";
const SEEN_CAP = 300;

type KV = Pick<Storage, "getItem" | "setItem">;

function readSeen(store: KV): string[] {
  try {
    const v = JSON.parse(store.getItem(SEEN_KEY) || "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

// Return only the transitions never seen before, and record them as seen (bounded, newest kept).
export function claimUnseen(transitions: Transition[], store: KV | null): Transition[] {
  if (!transitions.length) return [];
  const seen = store ? readSeen(store) : [];
  const set = new Set(seen);
  const fresh: Transition[] = [];
  for (const t of transitions) {
    if (set.has(t.id)) continue;
    set.add(t.id);
    seen.push(t.id);
    fresh.push(t);
  }
  if (store && fresh.length) {
    try {
      store.setItem(SEEN_KEY, JSON.stringify(seen.slice(-SEEN_CAP)));
    } catch {
      /* storage full / disabled — dedupe still holds for this page */
    }
  }
  return fresh;
}

// The auto check-in rule: exactly one "How's it going?" per poll batch that produced fresh notices, and only
// when the session is free (no turn in flight, nothing queued). Busy -> skip (never queue a second one).
export function shouldAutoCheckIn(fresh: Transition[], session: { inFlight: boolean; queued: boolean }): boolean {
  return fresh.length > 0 && !session.inFlight && !session.queued;
}
