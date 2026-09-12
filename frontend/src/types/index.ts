/**
 * Wire types mirroring the backend Pydantic schemas.
 *
 * Kept hand-written (rather than generated) because the surface is small and
 * the comments double as documentation for which fields are telemetry — those
 * must never be rendered as if they were agent output.
 */

export type AgentStatus =
  | 'idle'
  | 'queued'
  | 'running'
  | 'thinking'
  | 'completed'
  | 'failed'
  | 'skipped'

export type ExecutionStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export type ActionType = 'delegate' | 'answer' | 'continue' | 'review' | 'replan'

export type TokenSource = 'reported' | 'estimated' | 'unavailable'

export interface TokenUsage {
  agent_name?: string | null
  model?: string | null
  provider?: string | null
  is_local: boolean
  prompt_tokens?: number | null
  completion_tokens?: number | null
  total_tokens?: number | null
  reasoning_tokens?: number | null
  cached_tokens?: number | null
  reasoning_tokens_source: TokenSource
  request_start_time?: string | null
  request_end_time?: string | null
  latency_ms?: number | null
  tokens_per_second?: number | null
  cost_usd?: number | null
  retries: number
}

export interface TokenUsageAggregate {
  calls: number
  prompt_tokens?: number | null
  completion_tokens?: number | null
  total_tokens?: number | null
  reasoning_tokens?: number | null
  cached_tokens?: number | null
  latency_ms?: number | null
  tokens_per_second?: number | null
  cost_usd?: number | null
  any_estimated: boolean
}

export interface AgentStep {
  id?: number | null
  execution_id: string
  step_index: number
  agent_name: string
  agent_label?: string | null
  model?: string | null
  is_local: boolean
  task: string
  status: AgentStatus
  stage: string
  input?: string | null
  output?: string | null
  reasoning?: string | null
  parsed_output?: Record<string, unknown> | null
  error?: string | null
  started_at?: string | null
  finished_at?: string | null
  usage?: TokenUsage | null
  debug?: Record<string, unknown> | null
}

export interface AgentFlowNode {
  index: number
  agent_name: string
  agent_label: string
  model?: string | null
  is_local: boolean
  status: AgentStatus
  stage: string
}

export interface Execution {
  id: string
  conversation_id: string
  status: ExecutionStatus
  user_input: string
  final_answer?: string | null
  error?: string | null
  started_at?: string | null
  finished_at?: string | null
  step_count: number
  flow: AgentFlowNode[]
  steps: AgentStep[]
}

export interface Conversation {
  id: string
  title: string
  created_at?: string | null
  updated_at?: string | null
  message_count: number
}

export interface Message {
  id?: number | null
  conversation_id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  reasoning?: string | null
  execution_id?: string | null
  created_at?: string | null
  usage?: TokenUsage | null
}

export interface AgentDescriptor {
  key: string
  label: string
  model: string
  provider: string
  is_local: boolean
  role: string
  reasoning_effort: string
  available: boolean
}

export interface ModelStatus {
  name: string
  provider: string
  is_local: boolean
  status: 'online' | 'offline' | 'configured' | 'unknown' | 'error'
  detail?: string | null
  latency_ms?: number | null
  models: string[]
}

export interface HealthResponse {
  backend: string
  deepseek: string
  minicpm: string
  local_workers_enabled: boolean
  database: string
  version: string
  details: Record<string, unknown>
}

export interface StatsResponse {
  requests: number
  agent_calls: number
  deepseek_calls: number
  local_calls: number
  total_tokens?: number | null
  local_tokens?: number | null
  cloud_tokens?: number | null
  prompt_tokens?: number | null
  completion_tokens?: number | null
  reasoning_tokens?: number | null
  average_latency_ms?: number | null
  cost_usd?: number | null
  by_agent: Record<string, TokenUsageAggregate>
}

export interface ChatResponse {
  execution_id: string
  conversation_id: string
  user_message_id: number
  status: ExecutionStatus
  stream_url: string
}

/** Live, user-visible telemetry for one execution. Never sent to a model. */
export interface RuntimeTelemetry {
  execution_id: string
  active_agent?: string | null
  active_agent_label?: string | null
  active_model?: string | null
  active_is_local: boolean
  status: ExecutionStatus
  stage: string
  prompt_tokens?: number | null
  completion_tokens?: number | null
  reasoning_tokens?: number | null
  total_tokens?: number | null
  latency_ms?: number | null
  tokens_per_second?: number | null
  step_count: number
}

export type EventType =
  | 'execution_started'
  | 'execution_completed'
  | 'execution_failed'
  | 'execution_cancelled'
  | 'agent_queued'
  | 'agent_started'
  | 'agent_reasoning'
  | 'agent_output'
  | 'agent_completed'
  | 'agent_failed'
  | 'decision'
  | 'token_usage'
  | 'telemetry'
  | 'flow_update'
  | 'final_answer'
  | 'final_answer_delta'
  | 'notice'
  | 'debug'
  | 'heartbeat'

export interface ExecutionEvent {
  type: EventType
  execution_id?: string | null
  ts?: string
  agent?: string | null
  agent_label?: string | null
  model?: string | null
  provider?: string | null
  is_local?: boolean | null
  stage?: string | null
  status?: string | null
  step_index?: number | null
  text?: string | null
  delta?: string | null
  message?: string | null
  level?: 'info' | 'warning' | 'error'
  usage?: TokenUsage | null
  data?: Record<string, unknown> | null
}

/** A renderable timeline entry — derived from events, not from a model. */
export interface TimelineEntry {
  id: string
  stepIndex: number
  agent: string
  agentLabel: string
  model?: string | null
  isLocal: boolean
  stage: string
  status: AgentStatus
  task?: string | null
  output?: string | null
  reasoning?: string | null
  error?: string | null
  decision?: {
    action?: ActionType
    agent?: string | null
    task?: string | null
    reason?: string | null
    parseOk?: boolean
    parseStrategy?: string
  } | null
  usage?: TokenUsage | null
  startedAt?: string | null
  finishedAt?: string | null
}

export interface Notice {
  id: string
  level: 'info' | 'warning' | 'error'
  message: string
  ts: string
}
