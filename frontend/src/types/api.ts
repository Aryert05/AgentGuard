export type ExecutionStatus = 'pending' | 'running' | 'completed' | 'partially_completed' | 'failed';
export type ExecutionMode = 'deterministic' | 'llm';
export type ToolCallStatus = 'started' | 'completed' | 'failed';

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  environment: string;
  checks: Record<string, string>;
}

export interface ToolCallSummary {
  tool_name: string;
  status: ToolCallStatus;
  duration_ms?: number | null;
}

export interface ExecutionRequest {
  task: string;
  mode?: ExecutionMode;
  max_tool_calls?: number;
}

export interface ExecutionResponse {
  execution_id: string;
  task_id: string;
  status: ExecutionStatus;
  mode: ExecutionMode;
  started_at: string;
  completed_at?: string | null;
  duration_ms?: number | null;
  successful_tool_call_count: number;
  failed_tool_call_count: number;
  tool_calls: ToolCallSummary[];
  final_output?: string | null;
  persisted: boolean;
}

export interface ExecutionSummaryResponse {
  execution_id: string;
  task_id: string;
  task_description?: string | null;
  status: ExecutionStatus;
  mode: ExecutionMode;
  started_at: string;
  completed_at?: string | null;
  duration_ms?: number | null;
  successful_tool_call_count: number;
  failed_tool_call_count: number;
  metadata: Record<string, any>;
}

export interface ExecutionListResponse {
  total: number;
  limit: number;
  offset: number;
  items: ExecutionSummaryResponse[];
}

export interface ExecutionEventResponse {
  event_id: string;
  execution_id: string;
  task_id: string;
  event_index: number;
  step_number: number;
  timestamp: string;
  event_type: string;
  component: string;
  tool_name?: string | null;
  status: string;
  duration_ms?: number | null;
  error_type?: string | null;
  error_message?: string | null;
  metadata: Record<string, any>;
}

export interface ExecutionEventsResponse {
  execution_id: string;
  events: ExecutionEventResponse[];
  next_after_event_index?: number | null;
}

export interface EvidenceResponse {
  description: string;
  source: string;
  event_ids: string[];
  observed_value?: number | null;
  threshold?: number | null;
}

export interface FailurePatternResponse {
  pattern_type: string;
  severity: string;
  description: string;
  event_ids: string[];
  tool_name?: string | null;
  count: number;
  threshold?: number | null;
  metadata: Record<string, any>;
}

export interface MLRiskResponse {
  probability: number;
  threshold: number;
  model_name?: string | null;
  source: string;
  predicted_failure: boolean;
}

export interface FailureDiagnosisResponse {
  is_failure: boolean;
  primary_category: string;
  secondary_categories: string[];
  confidence: number;
  summary: string;
  execution_id?: string | null;
  task_id?: string | null;
  tool_name?: string | null;
  terminal_observed: boolean;
  execution_status?: string | null;
  partial_results_available: boolean;
  patterns: FailurePatternResponse[];
  evidence: EvidenceResponse[];
  ml_risk?: MLRiskResponse | null;
}

export interface RecoveryRecommendationResponse {
  action: string;
  diagnosis_category: string;
  target_tool?: string | null;
  risk: string;
  requires_human_approval: boolean;
  max_attempts: number;
  rationale: string;
  expected_benefit: string;
  evidence_event_ids: string[];
}

export interface DiagnosisResponse {
  diagnosis: FailureDiagnosisResponse;
  recommendations: RecoveryRecommendationResponse[];
  ml_error?: string | null;
}

export interface ActivityBucket {
  date: string;
  total: number;
  successful: number;
  failed: number;
}

export interface OverviewMetricsResponse {
  total_executions: number;
  successful_executions: number;
  failed_executions: number;
  partially_completed_executions: number;
  running_executions: number;
  pending_executions: number;
  success_rate: number;
  failure_rate: number;
  avg_duration_ms?: number | null;
  total_tool_calls: number;
  successful_tool_call_count: number;
  failed_tool_call_count: number;
  status_distribution: Record<string, number>;
  failure_category_distribution: Record<string, number>;
  recent_activity: ActivityBucket[];
}

export interface ProblemDetails {
  type?: string;
  title?: string;
  status: number;
  detail: string;
  code?: string;
  request_id?: string;
  instance?: string;
}
