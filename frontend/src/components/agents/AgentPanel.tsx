import { Cpu, Server } from 'lucide-react'
import { clsx } from 'clsx'
import { useStore, orderedAgents } from '../../store/useStore'
import { agentMeta } from '../../lib/agents'
import { StatusDot, statusLabel } from '../ui/StatusDot'
import type { AgentStatus, ModelStatus } from '../../types'

/** Left rail: live agent roster plus model availability. */
export function AgentPanel() {
  const agents = useStore((state) => state.agents)
  const telemetry = useStore((state) => state.telemetry)
  const models = useStore((state) => state.models)
  const executionStatus = useStore((state) => state.executionStatus)
  const timeline = useStore((state) => state.timeline)

  const activeAgent = telemetry?.active_agent ?? null
  const running = executionStatus === 'running'

  // Derive each agent's status from the live execution, falling back to idle.
  const statusFor = (key: string): AgentStatus => {
    if (running && activeAgent === key) {
      return key === 'main' ? 'thinking' : 'running'
    }
    const entries = timeline.filter((entry) => entry.agent === key)
    if (entries.length) {
      const last = entries[entries.length - 1]
      if (last.status === 'completed' || last.status === 'failed') return last.status
      if (running) return 'completed'
    }
    return 'idle'
  }

  const listed = orderedAgents(agents)
  const remote = models.filter((model) => !model.is_local)
  const local = models.filter((model) => model.is_local)

  return (
    <div className="flex h-full flex-col">
      <SectionTitle>Agents</SectionTitle>

      <div className="flex-1 space-y-1 overflow-y-auto px-2 pb-2">
        {listed.length === 0 && <SkeletonRows count={5} />}

        {listed.map((agent) => {
          const meta = agentMeta(agent.key)
          const status = statusFor(agent.key)
          const isActive = running && activeAgent === agent.key
          const unavailable = agent.is_local && !agent.available

          return (
            <div
              key={agent.key}
              className={clsx(
                'rounded-card border px-2.5 py-2 transition-colors',
                isActive
                  ? 'border-accent/50 bg-accent/5'
                  : 'border-transparent hover:border-hairline hover:bg-sunken/60',
              )}
            >
              <div className="flex items-center gap-2">
                <StatusDot status={status} showRing />
                <span
                  className={clsx(
                    'truncate text-[0.8125rem] font-medium',
                    isActive ? 'text-accent' : 'text-ink',
                  )}
                >
                  {meta.shortLabel}
                </span>
                <span
                  className={clsx(
                    'ml-auto shrink-0 font-mono text-2xs',
                    status === 'running' || status === 'thinking'
                      ? 'text-accent'
                      : status === 'failed'
                        ? 'text-danger'
                        : status === 'completed'
                          ? 'text-positive'
                          : 'text-ink-faint',
                  )}
                >
                  {statusLabel(status)}
                </span>
              </div>

              <div className="mt-1 flex items-center gap-1.5 pl-4">
                {agent.is_local ? (
                  <Cpu className="h-3 w-3 shrink-0 text-ink-faint" aria-hidden="true" />
                ) : (
                  <Server className="h-3 w-3 shrink-0 text-ink-faint" aria-hidden="true" />
                )}
                <span className="truncate font-mono text-2xs text-ink-faint">{agent.model}</span>
                {unavailable && (
                  <span className="ml-auto shrink-0 rounded border border-caution/40 px-1 text-[0.5625rem] uppercase text-caution">
                    offline
                  </span>
                )}
              </div>

              {isActive && (
                <div className="mt-1.5 truncate pl-4 text-2xs text-ink-soft">{meta.role}</div>
              )}
            </div>
          )
        })}
      </div>

      <SectionTitle>Models</SectionTitle>
      <div className="space-y-1 px-2 pb-3">
        {[...remote, ...local].map((model) => (
          <ModelRow key={`${model.provider}:${model.name}`} model={model} />
        ))}
        {models.length === 0 && <SkeletonRows count={2} />}
      </div>
    </div>
  )
}

function ModelRow({ model }: { model: ModelStatus }) {
  const tone =
    model.status === 'online'
      ? 'text-positive'
      : model.status === 'offline'
        ? 'text-danger'
        : model.status === 'error'
          ? 'text-danger'
          : 'text-caution'

  const dot =
    model.status === 'online'
      ? 'bg-positive'
      : model.status === 'offline' || model.status === 'error'
        ? 'bg-danger'
        : 'bg-caution'

  return (
    <div
      className="rounded-card border border-transparent px-2.5 py-1.5 hover:border-hairline hover:bg-sunken/60"
      title={model.detail ?? undefined}
    >
      <div className="flex items-center gap-2">
        <span className={clsx('h-1.5 w-1.5 shrink-0 rounded-full', dot)} />
        <span className="truncate font-mono text-2xs text-ink-soft">{model.name}</span>
        <span className={clsx('ml-auto shrink-0 text-2xs font-medium', tone)}>{model.status}</span>
      </div>
      {model.detail && (
        <div className="mt-0.5 truncate pl-3.5 text-[0.625rem] text-ink-faint">{model.detail}</div>
      )}
    </div>
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 pb-1.5 pt-3 text-2xs font-semibold uppercase tracking-wider text-ink-faint">
      {children}
    </div>
  )
}

function SkeletonRows({ count }: { count: number }) {
  return (
    <div className="space-y-1.5">
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className="skeleton h-11 w-full" />
      ))}
    </div>
  )
}
