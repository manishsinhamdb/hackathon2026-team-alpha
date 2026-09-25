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
  updated_at: string;
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
export type StageCellStatus = "not_started" | "running" | "succeeded" | "failed" | "waiting_user" | "cancelled";
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
