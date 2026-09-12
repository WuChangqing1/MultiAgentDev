import { clsx } from 'clsx'
import { ArrowDown, User } from 'lucide-react'
import { useStore } from '../../store/useStore'
import { agentMeta } from '../../lib/agents'
import { statusBgClass, statusLabel } from '../ui/StatusDot'
import type { AgentFlowNode } from '../../types'

/**
 * Compact vertical flow: User -> each agent turn -> current state.
 *
 * Deliberately not a graph editor. The point is to answer "what is happening
 * now, and what already happened" at a glance.
 */
export function AgentFlow({ nodes }: { nodes: AgentFlowNode[] }) {
  const sending = useStore((state) => state.sending)
  const timeline = useStore((state) => state.timeline)
  const telemetry = useStore((state) => state.telemetry)

  // Prefer the live timeline (it includes the in-flight turn); fall back to the
  // server-side flow snapshot.
  const items: AgentFlowNode[] = timeline.length
    ? timeline.map((entry) => ({
        index: entry.stepIndex,
        agent_name: entry.agent,
        agent_label: entry.agentLabel,
        model: entry.model,
        is_local: entry.isLocal,
        status: entry.status,
        stage: entry.stage,
      }))
    : nodes

  const activeIndex = telemetry?.active_agent
    ? items.reduce((last, node, index) => (node.agent_name === telemetry.active_agent ? index : last), -1)
    : -1

  return (
    <div className="space-y-0.5">
      <FlowRow label="User" tone="user" />
      {items.map((node, index) => (
        <div key={`${node.index}:${node.agent_name}:${index}`}>
          <Connector />
          <FlowRow
            label={agentMeta(node.agent_name).shortLabel || node.agent_label}
            tone="agent"
            status={node.status}
            model={node.model ?? undefined}
            isLocal={node.is_local}
            active={sending && telemetry?.active_agent === node.agent_name && index === activeIndex}
            index={node.index}
          />
        </div>
      ))}
      {items.length === 0 && <div className="px-2 py-3 text-center text-2xs text-ink-faint">No agent activity yet</div>}
    </div>
  )
}

function Connector() {
  return (
    <div className="flex h-4 justify-center">
      <ArrowDown className="h-3 w-3 text-ink-faint/60" aria-hidden="true" />
    </div>
  )
}

function FlowRow({
  label,
  tone,
  status,
  model,
  isLocal,
  active = false,
  index,
}: {
  label: string
  tone: 'user' | 'agent'
  status?: AgentFlowNode['status']
  model?: string
  isLocal?: boolean
  active?: boolean
  index?: number
}) {
  const dot = status ? statusBgClass(status) : 'bg-ink-faint/50'

  return (
    <div
      className={clsx(
        'flex items-center gap-2 rounded-card border px-2 py-1.5 transition-colors',
        active
          ? 'border-accent/50 bg-accent/5'
          : status === 'failed'
            ? 'border-danger/40 bg-danger/5'
            : 'border-hairline bg-surface/50',
      )}
    >
      {tone === 'user' ? (
        <User className="h-3 w-3 shrink-0 text-ink-faint" aria-hidden="true" />
      ) : (
        <span className={clsx('h-1.5 w-1.5 shrink-0 rounded-full', dot)} />
      )}

      <span
        className={clsx(
          'truncate text-xs font-medium',
          active ? 'text-accent' : tone === 'user' ? 'text-ink-soft' : 'text-ink',
        )}
      >
        {label}
      </span>

      {index != null && <span className="shrink-0 font-mono text-[0.625rem] text-ink-faint">#{index}</span>}

      <span className="ml-auto flex shrink-0 items-center gap-1.5">
        {model && <span className="hidden truncate font-mono text-[0.625rem] text-ink-faint sm:inline">{model}</span>}
        {isLocal && (
          <span className="rounded border border-hairline px-1 text-[0.5625rem] uppercase text-ink-faint">
            local
          </span>
        )}
        {status && (
          <span
            className={clsx(
              'text-[0.625rem] font-medium',
              status === 'completed'
                ? 'text-positive'
                : status === 'failed'
                  ? 'text-danger'
                  : status === 'running' || status === 'thinking'
                    ? 'text-accent'
                    : 'text-ink-faint',
            )}
          >
            {statusLabel(status)}
          </span>
        )}
      </span>
    </div>
  )
}
