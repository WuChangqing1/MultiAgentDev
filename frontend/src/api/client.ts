/**
 * Typed API client.
 *
 * The browser talks only to the FastAPI backend. It never contacts DeepSeek or
 * llama.cpp directly — those credentials and endpoints stay server-side.
 */

import type {
  AgentDescriptor,
  ChatResponse,
  Conversation,
  Execution,
  ExecutionEvent,
  HealthResponse,
  Message,
  ModelStatus,
  StatsResponse,
} from '../types'

const BASE = '/api'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch (cause) {
    throw new ApiError(
      'Cannot reach the backend. Is it running on 127.0.0.1:8000?',
      0,
      cause,
    )
  }

  if (!response.ok) {
    let detail: unknown
    let message = `Request failed (${response.status})`
    try {
      const body = await response.json()
      detail = body
      if (typeof body?.detail === 'string') message = body.detail
    } catch {
      /* body was not JSON; keep the generic message */
    }
    throw new ApiError(message, response.status, detail)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  health: (probeDeepseek = false) =>
    request<HealthResponse>(`/health?deepseek_probe=${probeDeepseek}`),

  modelStatus: (force = false) => request<ModelStatus[]>(`/models/status?force=${force}`),

  agents: () => request<AgentDescriptor[]>('/agents'),

  stats: () => request<StatsResponse>('/stats'),

  conversations: () => request<Conversation[]>('/conversations'),

  conversation: (id: string) => request<Conversation>(`/conversations/${id}`),

  messages: (id: string) => request<Message[]>(`/conversations/${id}/messages`),

  deleteConversation: (id: string) =>
    request<{ deleted: boolean }>(`/conversations/${id}`, { method: 'DELETE' }),

  execution: (id: string) => request<Execution>(`/executions/${id}`),

  executions: (conversationId: string) =>
    request<Execution[]>(`/conversations/${conversationId}/executions`),

  modelCalls: (id: string) =>
    request<{ calls: Record<string, unknown>[]; totals: Record<string, unknown> }>(
      `/executions/${id}/calls`,
    ),

  settings: () => request<Record<string, unknown>>('/settings'),

  updateSettings: (patch: Record<string, unknown>) =>
    request<Record<string, unknown>>('/settings', {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  resetSettings: () => request<Record<string, unknown>>('/settings/reset', { method: 'POST' }),

  reloadSettings: () => request<Record<string, unknown>>('/settings/reload', { method: 'POST' }),

  validate: () => request<ModelStatus[]>('/settings/validate', { method: 'POST' }),

  chat: (message: string, conversationId?: string | null, maxSteps?: number) =>
    request<ChatResponse>('/chat', {
      method: 'POST',
      body: JSON.stringify({
        message,
        conversation_id: conversationId ?? null,
        max_steps: maxSteps ?? null,
      }),
    }),

  cancel: (executionId: string) =>
    request<{ cancelled: boolean }>(`/executions/${executionId}/cancel`, { method: 'POST' }),
}

/**
 * Subscribe to an execution's SSE stream.
 *
 * Implemented with `fetch` + `ReadableStream` rather than `EventSource` because
 * we want a clean abort handle and identical error semantics to the rest of the
 * client. Returns a disposer.
 */
export function streamExecution(
  executionId: string,
  handlers: {
    onEvent: (event: ExecutionEvent) => void
    onError?: (error: Error) => void
    onClose?: () => void
  },
): () => void {
  const controller = new AbortController()
  let closed = false

  const finish = () => {
    if (closed) return
    closed = true
    handlers.onClose?.()
  }

  void (async () => {
    try {
      const response = await fetch(`${BASE}/events/${executionId}`, {
        headers: { Accept: 'text/event-stream' },
        signal: controller.signal,
      })
      if (!response.ok || !response.body) {
        throw new Error(`Event stream unavailable (${response.status})`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // SSE frames are separated by a blank line.
        let boundary = buffer.indexOf('\n\n')
        while (boundary !== -1) {
          const frame = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)
          const parsed = parseFrame(frame)
          if (parsed) handlers.onEvent(parsed)
          boundary = buffer.indexOf('\n\n')
        }
      }
      finish()
    } catch (error) {
      if ((error as Error)?.name === 'AbortError') {
        finish()
        return
      }
      closed = true
      handlers.onError?.(error as Error)
      handlers.onClose?.()
    }
  })()

  return () => {
    closed = true
    controller.abort()
  }
}

function parseFrame(frame: string): ExecutionEvent | null {
  const dataLines = frame
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())

  if (dataLines.length === 0) return null // heartbeat comment

  try {
    return JSON.parse(dataLines.join('\n')) as ExecutionEvent
  } catch {
    return null
  }
}
