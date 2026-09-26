// Shapes shared between the BFF routes and the client. These mirror the platform DB documents
// (packages/shared_tools/poc_shared_tools/metadata.py + poc_contracts schemas), narrowed to what
// the UI renders. The UI is READ-ONLY on the DB, so these are all read models.

// The documented enum, but kept open (`string & {}`) because the live DB can carry values the schema
// doesn't list (e.g. "code_failed"). The UI only ever displays the status, so we never want a mismatch
// to break rendering.
export type PocStatus =
  | "drafting" | "spec_ready" | "coding" | "code_ready" | "deploying"
  | "deployed" | "testing" | "tested" | "failed" | "torn_down"
  // eslint-disable-next-line @typescript-eslint/ban-types
  | (string & {});

export type RunStage = "draft" | "code" | "deploy" | "test" | "teardown";
export type RunStatus = "queued" | "running" | "waiting_user" | "succeeded" | "failed" | "cancelled";

export interface PocSummary {
  poc_id: string;
  title: string;
  status: PocStatus;
  versions: { spec?: string; code?: string };
  created_at: string;
  updated_at: string;
  ui_archived?: boolean;
  ui_label?: string; // the UI-only nickname (Round 5); agents ignore it
}

export interface StepView {
  name: string;
  status: string;
}

export interface RunView {
  run_id: string;
  stage: RunStage;
  status: RunStatus;
  started_at?: string;
  ended_at?: string;
  duration_ms: number | null; // elapsed for a still-running run, final duration once ended
  current_step?: string;
  error?: { code: string; message: string; component?: string } | null;
  steps: StepView[];
  // Platform execution id, shown next to a running run in the pipeline header WHEN present. The current DB
  // schema does not store it on run documents (verified), so this is usually absent; kept optional and
  // passed through defensively so it appears automatically if the platform starts recording it.
  execution_id?: string;
  // Round 5 — liveness. `abandoned` = queued/running with a heartbeat older than 180 s, or failed with
  // error.code ABANDONED (the chat agent gave up on it). `last_beat_at` is the newest of heartbeat_at /
  // updated_at / started_at. `executions` / `handovers` come from runs.executions / runs.continuations.
  abandoned: boolean;
  last_beat_at?: string;
  executions?: number;
  handovers: number;
  has_progress: boolean; // any done task/step — Retry becomes "Continue"
  needs_clarification?: boolean; // draft run finished with questions
  test_passed?: boolean; // deploy run outputs.test_passed (the deploy ran the e2e tests)
  app_url?: string; // deploy run outputs.urls.app
}

export interface TaskView {
  seq: number;
  agent: string;
  tool: string;
  mode?: string;
  status: string;
  duration_ms?: number | null;
}

// One cell of the fixed Draft -> Spec approved -> Code -> Code approved -> Deploy -> Tests -> Torn down stepper.
export type StageCellStatus =
  | "not_started" | "running" | "succeeded" | "failed" | "waiting_user" | "cancelled" | "abandoned";
export interface StageCell {
  key: string;
  label: string;
  kind: "run" | "gate";
  status: StageCellStatus;
  run_id?: string;
  started_at?: string;
  ended_at?: string;
  duration_ms?: number | null;
  error?: { code: string; message: string } | null;
  approvedBy?: string;
  approvedVersion?: string;
  version?: string; // spec/code version stamped on a succeeded run cell
  last_beat_at?: string; // abandoned cells: when the run last showed signs of life
  executions?: number; // > 1 once the run was handed over / resumed
  retry_label?: "Retry" | "Continue"; // abandoned cells: Continue when the run has any done task/step
}

// One coder row of the "Code run" card (contract -> seed -> backend -> frontend -> assemble), derived from
// the latest code run's tasks.
export type CoderStatus = "done" | "running" | "queued";
export interface CoderRowView {
  key: string;
  label: string;
  status: CoderStatus;
  duration_ms?: number | null;
  started_at?: string;
  percent?: number; // 0-100 when the task reports progress; otherwise undefined
}

// A "Run history" table row. `display` folds a draft-with-questions into its own status for the pill.
export type RunDisplayStatus =
  | "succeeded" | "running" | "failed" | "questions" | "cancelled" | "queued" | "waiting_user" | "abandoned";
export interface RunHistoryRow {
  stage: RunStage;
  stageLabel: string;
  run_id: string;
  display: RunDisplayStatus;
  started_at?: string;
  duration_ms: number | null;
  executions?: number; // shown subtly ("exec 2") once a run was resumed
}

// The library "Today" card: four counts derived from the DB.
export interface TodaySummary {
  drafted: number; // POCs created today
  deployedTested: number; // POCs that reached a tested run today
  cloudLive: number; // active cloud resources across all POCs
  transcriptToTestedMs: number | null; // most recent created -> tested duration today
}

export interface ClarificationView {
  round?: number;
  questions: Array<{ id?: string; question?: string; field?: string } | string>;
}

export interface DeploymentView {
  run_id?: string;
  app?: string;
  api?: string;
  health?: string;
  instance_id?: string;
  ttl_expires_at?: string;
}

export interface TestReportView {
  run_id?: string;
  total?: number;
  passed: number;
  failed: number;
  skipped?: number;
  not_automatable?: number;
  suspected_component?: string | null;
}

export interface CloudResourceView {
  type: string;
  resource_id: string;
  ttl_expires_at?: string;
}

export interface PocDetail {
  poc: {
    poc_id: string;
    title: string;
    ui_label?: string;
    status: PocStatus;
    owner_user_id: string;
    current_versions: { spec?: string; code?: string };
    tags: string[];
    created_at: string;
    updated_at: string;
  };
  approvals: Array<{ stage: string; version: string; approved_by: string; at: string; implicit: boolean }>;
  runs: RunView[];
  tasksByRun: Record<string, TaskView[]>;
  stages: StageCell[];
  coders: CoderRowView[];
  runHistory: RunHistoryRow[];
  clarification: ClarificationView | null;
  deployment: DeploymentView | null;
  test: TestReportView | null;
  cloudResources: { activeCount: number; items: CloudResourceView[] };
}

export interface ConversationMessage {
  seq?: number;
  role: string;
  content: string;
  run_id?: string;
}
