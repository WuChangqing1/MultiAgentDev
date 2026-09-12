/**
 * Application state.
 *
 * Responsibilities, deliberately kept in one place so the components stay
 * presentational:
 *
 *  * conversation + message bookkeeping (optimistic, then reconciled)
 *  * live execution state built by reducing the SSE event stream
 *  * model/agent catalogue and health
 *  * UI preferences (theme, panels, debug)
 *
 * Note the strict split the backend enforces: `telemetry` is user-visible
 * monitoring data. It is rendered, never fed back into a prompt.
 */

import { create } from 'zustand'
import { api, ApiError, streamExecution } from '../api/client'
import { AGENT_ORDER } from '../lib/agents'
import type {
  AgentDescriptor,
  ExecutionEvent,
  ExecutionStatus,
  HealthResponse,
  Message,
  ModelStatus,
  Notice,
  RuntimeTelemetry,
  StatsResponse,
  TimelineEntry,
  TokenUsage,
  TokenUsageAggregate,
  Conversation,
  AgentFlowNode,
} from '../types'

export type Theme = 'light' | 'dark'

const THEME_KEY = 'multiagent.theme'
const TIMELINE_LIMIT = 300
const NOTICE_LIMIT = 6

interface State {
  // -- bootstrap ---------------------------------------------------------
  booted: boolean
  bootError: string | null

  // -- catalogue ---------------------------------------------------------
  health: HealthResponse | null
  models: ModelStatus[]
  agents: AgentDescriptor[]
  stats: StatsResponse | null

  // -- conversations -----------------------------------------------------
  conversations: Conversation[]
  conversationId: string | null
  messages: Message[]

  // -- live execution ----------------------------------------------------
  executionId: string | null
  executionStatus: ExecutionStatus | null
  telemetry: RuntimeTelemetry | null
  timeline: TimelineEntry[]
  flow: AgentFlowNode[]
  notices: Notice[]
  totals: TokenUsageAggregate | null
  byAgent: Record<string, TokenUsageAggregate>

  // -- ui ----------------------------------------------------------------
  theme: Theme
  debugMode: boolean
  showReasoning: boolean
  showSettings: boolean
  historyOpen: boolean
  selectedStepId: string | null
  sending: boolean
  error: string | null

  // -- actions -----------------------------------------------------------
  boot: () => Promise<void>
  refreshHealth: (probeDeepseek?: boolean) => Promise<void>
  refreshStats: () => Promise<void>
  refreshConversations: () => Promise<void>

  send: (text: string) => Promise<void>
  cancel: () => Promise<void>
  /** Reconcile the optimistic assistant message with the persisted execution. */
  finalizeTurn: (
    executionId: string,
    placeholderId: number,
    streamed: string,
    failure: string | null,
  ) => Promise<void>

  openConversation: (id: string) => Promise<void>
  newConversation: () => void
  deleteConversation: (id: string) => Promise<void>

  loadExecution: (id: string) => Promise<void>
  selectStep: (id: string | null) => void

  setTheme: (theme: Theme) => void
  toggleTheme: () => void
  setDebugMode: (on: boolean) => void
  setShowReasoning: (on: boolean) => void
  setShowSettings: (on: boolean) => void
  setHistoryOpen: (on: boolean) => void
  dismissError: () => void
  dismissNotice: (id: string) => void
}

// --------------------------------------------------------------------------
// Module-level (non-reactive) handles
// --------------------------------------------------------------------------

let disposeStream: (() => void) | null = null
let healthTimer: ReturnType<typeof setInterval> | null = null

function readTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    /* storage unavailable */
  }
  return 'dark'
}

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  try {
    localStorage.setItem(THEME_KEY, theme)
  } catch {
    /* storage unavailable */
  }
}

function messageOf(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return String(error)
}

const nowIso = () => new Date().toISOString()

// --------------------------------------------------------------------------
// Event reduction
// --------------------------------------------------------------------------

interface ReduceInput {
  timeline: TimelineEntry[]
  flow: AgentFlowNode[]
  notices: Notice[]
  telemetry: RuntimeTelemetry | null
  totals: TokenUsageAggregate | null
  byAgent: Record<string, TokenUsageAggregate>
  executionStatus: ExecutionStatus | null
  finalAnswer: string | null
}

/**
 * Fold one SSE event into the derived execution state.
 *
 * Pure and exported so it can be unit-reasoned-about independently of the store.
 */
export function reduceEvent(state: ReduceInput, event: ExecutionEvent): ReduceInput {
  const timeline = state.timeline
  const findEntry = (agent?: string | null, stepIndex?: number | null) => {
    for (let i = timeline.length - 1; i >= 0; i -= 1) {
      const entry = timeline[i]
      if (stepIndex != null && entry.stepIndex === stepIndex && entry.agent === agent) return entry
      if (stepIndex == null && agent && entry.agent === agent && isOpen(entry.status)) return entry
    }
    return undefined
  }

  switch (event.type) {
    case 'execution_started':
      return {
        ...state,
        executionStatus: 'running',
        timeline: [],
        flow: [],
        notices: [],
        totals: null,
        byAgent: {},
        telemetry: event.execution_id
          ? {
              execution_id: event.execution_id,
              active_agent: null,
              active_agent_label: null,
              active_model: null,
              active_is_local: false,
              status: 'running',
              stage: 'planning',
              step_count: 0,
            }
          : state.telemetry,
      }

    case 'agent_started': {
      const stepIndex = event.step_index ?? timeline.length + 1
      // Re-starting the same (agent, step) replaces the entry rather than
      // duplicating it, which keeps retries readable in the timeline.
      const filtered = timeline.filter(
        (entry) => !(entry.stepIndex === stepIndex && entry.agent === event.agent),
      )
      const entry: TimelineEntry = {
        id: `${stepIndex}:${event.agent}:${event.ts ?? nowIso()}`,
        stepIndex,
        agent: event.agent ?? 'unknown',
        agentLabel: event.agent_label ?? event.agent ?? 'Unknown',
        model: event.model,
        isLocal: Boolean(event.is_local),
        stage: event.stage ?? '',
        status: event.is_local ? 'running' : 'thinking',
        startedAt: event.ts ?? nowIso(),
      }
      return { ...state, timeline: cap([...filtered, entry]) }
    }

    case 'agent_reasoning': {
      const entry = findEntry(event.agent, event.step_index)
      if (!entry) return state
      return {
        ...state,
        timeline: replace(timeline, entry.id, {
          reasoning: (entry.reasoning ?? '') + (event.text ?? ''),
        }),
      }
    }

    case 'agent_output': {
      const entry = findEntry(event.agent, event.step_index)
      if (!entry) return state
      return {
        ...state,
        timeline: replace(timeline, entry.id, {
          output: (entry.output ?? '') + (event.delta ?? event.text ?? ''),
        }),
      }
    }

    case 'decision': {
      const entry = findEntry('main', event.step_index)
      if (!entry) return state
      const data = event.data ?? {}
      const decision: NonNullable<TimelineEntry['decision']> = {
        action: data.action as TimelineEntry['decision'] extends null
          ? never
          : 'delegate' | 'answer' | 'continue' | 'review' | 'replan' | undefined,
        agent: (data.agent as string | null) ?? null,
        task: (data.task as string | null) ?? null,
        reason: (data.reason as string | null) ?? null,
        parseOk: data.parse_ok as boolean | undefined,
        parseStrategy: data.parse_strategy as string | undefined,
      }
      return { ...state, timeline: replace(timeline, entry.id, { decision }) }
    }

    case 'agent_completed': {
      const entry = findEntry(event.agent, event.step_index)
      if (!entry) return state
      return {
        ...state,
        timeline: replace(timeline, entry.id, {
          status: 'completed',
          finishedAt: event.ts ?? nowIso(),
          output: event.text ?? entry.output ?? null,
        }),
      }
    }

    case 'agent_failed': {
      const entry = findEntry(event.agent, event.step_index)
      if (!entry) return state
      return {
        ...state,
        timeline: replace(timeline, entry.id, {
          status: 'failed',
          finishedAt: event.ts ?? nowIso(),
          error: event.message ?? 'Agent failed',
        }),
      }
    }

    case 'token_usage': {
      const usage = event.usage ?? null
      const entry = findEntry(event.agent, event.step_index)
      const totals = (event.data?.execution_totals as TokenUsageAggregate | undefined) ?? state.totals
      const byAgent = { ...state.byAgent }
      if (usage?.agent_name) {
        byAgent[usage.agent_name] = mergeUsage(byAgent[usage.agent_name], usage)
      }
      return {
        ...state,
        totals: totals ?? state.totals,
        byAgent,
        timeline: entry && usage ? replace(timeline, entry.id, { usage }) : timeline,
      }
    }

    case 'telemetry': {
      const telemetry = (event.data?.telemetry as RuntimeTelemetry | undefined) ?? state.telemetry
      const flow = (event.data?.flow as AgentFlowNode[] | undefined) ?? state.flow
      return { ...state, telemetry, flow }
    }

    case 'flow_update': {
      const flow = (event.data?.flow as AgentFlowNode[] | undefined) ?? state.flow
      return { ...state, flow }
    }

    case 'final_answer_delta':
      return { ...state, finalAnswer: (state.finalAnswer ?? '') + (event.delta ?? '') }

    case 'final_answer':
      return { ...state, finalAnswer: event.text ?? state.finalAnswer }

    case 'notice': {
      const notice: Notice = {
        id: `${event.ts ?? nowIso()}:${state.notices.length}`,
        level: event.level ?? 'info',
        message: event.message ?? '',
        ts: event.ts ?? nowIso(),
      }
      if (!notice.message) return state
      return { ...state, notices: [notice, ...state.notices].slice(0, NOTICE_LIMIT) }
    }

    case 'execution_completed':
      return { ...state, executionStatus: 'completed' }
    case 'execution_failed':
      return { ...state, executionStatus: 'failed' }
    case 'execution_cancelled':
      return { ...state, executionStatus: 'cancelled' }

    default:
      return state
  }
}

function isOpen(status: TimelineEntry['status']): boolean {
  return status === 'running' || status === 'thinking' || status === 'queued'
}

function cap(entries: TimelineEntry[]): TimelineEntry[] {
  return entries.length > TIMELINE_LIMIT ? entries.slice(-TIMELINE_LIMIT) : entries
}

function replace(
  entries: TimelineEntry[],
  id: string,
  patch: Partial<TimelineEntry>,
): TimelineEntry[] {
  return entries.map((entry) => (entry.id === id ? { ...entry, ...patch } : entry))
}

/** Fold one usage record into an agent's running aggregate. */
function mergeUsage(
  previous: TokenUsageAggregate | undefined,
  usage: TokenUsage,
): TokenUsageAggregate {
  const add = (a?: number | null, b?: number | null) =>
    a == null && b == null ? null : (a ?? 0) + (b ?? 0)
  return {
    calls: (previous?.calls ?? 0) + 1,
    prompt_tokens: add(previous?.prompt_tokens, usage.prompt_tokens),
    completion_tokens: add(previous?.completion_tokens, usage.completion_tokens),
    total_tokens: add(previous?.total_tokens, usage.total_tokens),
    reasoning_tokens: add(previous?.reasoning_tokens, usage.reasoning_tokens),
    cached_tokens: add(previous?.cached_tokens, usage.cached_tokens),
    latency_ms: add(previous?.latency_ms, usage.latency_ms),
    tokens_per_second: previous?.tokens_per_second ?? usage.tokens_per_second ?? null,
    cost_usd: add(previous?.cost_usd, usage.cost_usd),
    any_estimated:
      (previous?.any_estimated ?? false) || usage.reasoning_tokens_source === 'estimated',
  }
}

// --------------------------------------------------------------------------
// Store
// --------------------------------------------------------------------------

export const useStore = create<State>()((set, get) => ({
  booted: false,
  bootError: null,

  health: null,
  models: [],
  agents: [],
  stats: null,

  conversations: [],
  conversationId: null,
  messages: [],

  executionId: null,
  executionStatus: null,
  telemetry: null,
  timeline: [],
  flow: [],
  notices: [],
  totals: null,
  byAgent: {},

  theme: readTheme(),
  debugMode: false,
  showReasoning: true,
  showSettings: false,
  historyOpen: false,
  selectedStepId: null,
  sending: false,
  error: null,

  // -- bootstrap ---------------------------------------------------------
  boot: async () => {
    applyTheme(get().theme)
    try {
      const [health, models, agents, stats, conversations] = await Promise.all([
        api.health(false),
        api.modelStatus(false),
        api.agents(),
        api.stats(),
        api.conversations(),
      ])
      set({
        booted: true,
        bootError: null,
        health,
        models,
        agents,
        stats,
        conversations,
      })
    } catch (error) {
      set({ booted: true, bootError: messageOf(error) })
    }

    if (healthTimer) clearInterval(healthTimer)
    healthTimer = setInterval(() => void get().refreshHealth(false), 30_000)
  },

  refreshHealth: async (probeDeepseek = false) => {
    try {
      const [health, models] = await Promise.all([
        api.health(probeDeepseek),
        api.modelStatus(false),
      ])
      set({ health, models, bootError: null })
    } catch (error) {
      set({ bootError: messageOf(error) })
    }
  },

  refreshStats: async () => {
    try {
      set({ stats: await api.stats() })
    } catch {
      /* stats are advisory; never surface an error for them */
    }
  },

  refreshConversations: async () => {
    try {
      set({ conversations: await api.conversations() })
    } catch (error) {
      set({ error: messageOf(error) })
    }
  },

  // -- chat --------------------------------------------------------------
  send: async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || get().sending) return

    const conversationId = get().conversationId
    const optimistic: Message = {
      conversation_id: conversationId ?? 'pending',
      role: 'user',
      content: trimmed,
      created_at: nowIso(),
    }
    const placeholderId = -Date.now()
    const placeholder: Message = {
      id: placeholderId,
      conversation_id: conversationId ?? 'pending',
      role: 'assistant',
      content: '',
      created_at: nowIso(),
    }

    disposeStream?.()
    disposeStream = null

    set({
      sending: true,
      error: null,
      messages: [...get().messages, optimistic, placeholder],
      timeline: [],
      flow: [],
      notices: [],
      totals: null,
      byAgent: {},
      telemetry: null,
      executionStatus: 'running',
      selectedStepId: null,
      historyOpen: false,
    })

    let executionId: string
    try {
      const response = await api.chat(trimmed, conversationId)
      executionId = response.execution_id
      set({
        executionId,
        conversationId: response.conversation_id,
        messages: get().messages.map((message) =>
          message.id === placeholderId
            ? { ...message, execution_id: executionId, conversation_id: response.conversation_id }
            : message,
        ),
      })
    } catch (error) {
      set({
        sending: false,
        executionStatus: 'failed',
        error: messageOf(error),
        messages: get().messages.filter((message) => message.id !== placeholderId),
      })
      return
    }

    let answer = ''
    disposeStream = streamExecution(executionId, {
      onEvent: (event) => {
        const current = get()
        const reduced = reduceEvent(
          {
            timeline: current.timeline,
            flow: current.flow,
            notices: current.notices,
            telemetry: current.telemetry,
            totals: current.totals,
            byAgent: current.byAgent,
            executionStatus: current.executionStatus,
            finalAnswer: answer,
          },
          event,
        )
        answer = reduced.finalAnswer ?? answer

        const terminal =
          event.type === 'execution_completed' ||
          event.type === 'execution_failed' ||
          event.type === 'execution_cancelled'

        set({
          timeline: reduced.timeline,
          flow: reduced.flow,
          notices: reduced.notices,
          telemetry: reduced.telemetry,
          totals: reduced.totals,
          byAgent: reduced.byAgent,
          executionStatus: reduced.executionStatus,
          // Stream the answer into the placeholder as it arrives.
          messages: answer
            ? get().messages.map((message) =>
                message.id === placeholderId ? { ...message, content: answer } : message,
              )
            : get().messages,
          ...(terminal
            ? {
                sending: false,
                ...(event.type === 'execution_failed'
                  ? { error: event.message ?? 'The execution failed.' }
                  : {}),
              }
            : {}),
        })

        if (terminal) {
          void get().finalizeTurn(executionId, placeholderId, answer, event.message ?? null)
        }
      },
      onError: (error) => {
        set({
          sending: false,
          executionStatus: 'failed',
          error: `Live stream interrupted: ${error.message}`,
        })
        void get().finalizeTurn(executionId, placeholderId, answer, error.message)
      },
      onClose: () => {
        if (get().sending) {
          set({ sending: false })
          void get().finalizeTurn(executionId, placeholderId, answer, null)
        }
      },
    })
  },

  finalizeTurn: async (executionId, placeholderId, answer, failure) => {
    try {
      const execution = await api.execution(executionId)
      set((state) => ({
        messages: state.messages.map((message) =>
          message.id === placeholderId
            ? {
                ...message,
                content: execution.final_answer || answer || failure || '',
                execution_id: executionId,
                usage: execution.steps.at(-1)?.usage ?? message.usage,
              }
            : message,
        ),
        executionStatus: execution.status,
        flow: execution.flow.length ? execution.flow : state.flow,
        error: execution.error ?? state.error,
      }))
    } catch {
      /* keep the streamed text we already have */
    } finally {
      set({ sending: false })
      void get().refreshStats()
      void get().refreshConversations()
    }
  },

  cancel: async () => {
    const executionId = get().executionId
    if (!executionId) return
    try {
      await api.cancel(executionId)
      set({ sending: false, executionStatus: 'cancelled' })
    } catch (error) {
      set({ error: messageOf(error) })
    }
  },

  // -- conversations -----------------------------------------------------
  openConversation: async (id: string) => {
    disposeStream?.()
    disposeStream = null
    try {
      const [conversation, messages, executions] = await Promise.all([
        api.conversation(id),
        api.messages(id),
        api.executions(id),
      ])
      const latest = executions[0]
      let timeline: TimelineEntry[] = []
      let flow: AgentFlowNode[] = []
      if (latest) {
        const detail = await api.execution(latest.id)
        flow = detail.flow
        timeline = detail.steps.map((step) => ({
          id: `db:${step.id ?? `${step.step_index}:${step.agent_name}`}`,
          stepIndex: step.step_index,
          agent: step.agent_name,
          agentLabel: step.agent_label ?? step.agent_name,
          model: step.model,
          isLocal: step.is_local,
          stage: step.stage,
          status: step.status,
          task: step.task,
          output: step.output,
          reasoning: step.reasoning,
          error: step.error,
          usage: step.usage,
          startedAt: step.started_at,
          finishedAt: step.finished_at,
        }))
      }
      set({
        conversationId: conversation.id,
        messages,
        timeline,
        flow,
        notices: [],
        executionId: latest?.id ?? null,
        executionStatus: latest?.status ?? null,
        historyOpen: false,
        selectedStepId: null,
        error: null,
        sending: false,
      })
    } catch (error) {
      set({ error: messageOf(error) })
    }
  },

  newConversation: () => {
    disposeStream?.()
    disposeStream = null
    set({
      conversationId: null,
      messages: [],
      timeline: [],
      flow: [],
      notices: [],
      totals: null,
      byAgent: {},
      telemetry: null,
      executionId: null,
      executionStatus: null,
      selectedStepId: null,
      historyOpen: false,
      error: null,
    })
  },

  deleteConversation: async (id: string) => {
    try {
      await api.deleteConversation(id)
      if (get().conversationId === id) get().newConversation()
      await get().refreshConversations()
    } catch (error) {
      set({ error: messageOf(error) })
    }
  },

  loadExecution: async (id: string) => {
    try {
      const execution = await api.execution(id)
      set({
        executionId: execution.id,
        executionStatus: execution.status,
        flow: execution.flow,
        timeline: execution.steps.map((step) => ({
          id: `db:${step.id ?? `${step.step_index}:${step.agent_name}`}`,
          stepIndex: step.step_index,
          agent: step.agent_name,
          agentLabel: step.agent_label ?? step.agent_name,
          model: step.model,
          isLocal: step.is_local,
          stage: step.stage,
          status: step.status,
          task: step.task,
          output: step.output,
          reasoning: step.reasoning,
          error: step.error,
          usage: step.usage,
          startedAt: step.started_at,
          finishedAt: step.finished_at,
        })),
        selectedStepId: null,
      })
    } catch (error) {
      set({ error: messageOf(error) })
    }
  },

  selectStep: (id) => set({ selectedStepId: id }),

  // -- ui ----------------------------------------------------------------
  setTheme: (theme) => {
    applyTheme(theme)
    set({ theme })
  },
  toggleTheme: () => get().setTheme(get().theme === 'dark' ? 'light' : 'dark'),
  setDebugMode: (on) => set({ debugMode: on }),
  setShowReasoning: (on) => set({ showReasoning: on }),
  setShowSettings: (on) => set({ showSettings: on }),
  setHistoryOpen: (on) => set({ historyOpen: on }),
  dismissError: () => set({ error: null }),
  dismissNotice: (id) => set({ notices: get().notices.filter((notice) => notice.id !== id) }),
}))

/** Agents in canonical display order, falling back to the static catalogue. */
export function orderedAgents(agents: AgentDescriptor[]): AgentDescriptor[] {
  const byKey = new Map(agents.map((agent) => [agent.key, agent]))
  return AGENT_ORDER.map((key) => byKey.get(key)).filter(
    (agent): agent is AgentDescriptor => Boolean(agent),
  )
}
