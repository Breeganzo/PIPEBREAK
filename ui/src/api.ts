export interface Axis {
  key: string;
  name: string;
  question: string;
  /** Share of the composite on a fixable incident. */
  weight_repair: number;
  /** Share on an unfixable one. Null where the axis does not apply. */
  weight_escalate: number | null;
}
export interface Degenerate { strategy: string; score: number; }

export interface Overview {
  tasks: number;
  repairable: number;
  escalation: number;
  marts: string[];
  seed: number;
  axes: Axis[];
  degenerate: Degenerate[];
  models: string[];
}

export interface TaskInfo {
  task_id: string;
  family: string;
  expected_action: "REPAIR" | "ESCALATE";
  primary_mart: string;
  symptom: string;
  failure_mode: string;
  table: string;
  column: string | null;
  note: string;
  impact: Record<string, number>;
}

export interface Score {
  task_id: string;
  model: string;
  family: string;
  expected_action: string;
  action: string;
  judgment: number;
  detection: number;
  detection_table: number;
  detection_column: number;
  detection_mode: number;
  repair: number | null;
  repair_exact: boolean;
  blast: number;
  blast_worsened: string[];
  restraint: number | null;
  composite: number;
  wrote_data: boolean;
  error: string;
}

export interface Summary {
  model: string;
  tasks: number;
  detection: number;
  repair_rate: number;
  repair_fidelity: number;
  abstention_rate: number;
  false_repair_rate: number;
  blast_incidents: number;
  no_decision: number;
  composite: number;
}

export interface ModelResult {
  model: string;
  summary: Summary | null;
  scores: Score[];
}

export interface Progress {
  state: string;
  model: string;
  task: string;
  done: number;
  total: number;
  at?: number;
  workers?: Progress[];
}

export interface ToolCall { tool: string; args: Record<string, unknown>; result: string; }
export interface Step { step: number; content: string | null; note?: string; calls?: ToolCall[]; }

export interface Transcript {
  task_id: string;
  model: string;
  expected_action: string;
  action: string;
  table: string;
  column: string;
  failure_mode: string;
  rationale: string;
  steps: number;
  writes: string[];
  rebuilds: number;
  seconds: number;
  error: string;
  transcript: Step[];
  truth?: {
    table: string;
    column: string | null;
    failure_mode: string;
    expected_action: string;
    note: string;
  };
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export const api = {
  overview: () => get<Overview>("/api/overview"),
  tasks: () => get<TaskInfo[]>("/api/tasks"),
  results: () => get<{ models: ModelResult[] }>("/api/results"),
  progress: () => get<Progress>("/api/progress"),
  transcript: (slug: string, taskId: string) =>
    get<Transcript>(`/api/transcript/${slug}/${taskId}`),
};

export const slug = (model: string) => model.replace(/\//g, "__").replace(/:/g, "_");
export const pct = (v: number) => `${Math.round(v * 100)}%`;
