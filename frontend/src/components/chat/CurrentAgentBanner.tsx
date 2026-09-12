import { clsx } from 'clsx'
import { Cpu, Server } from 'lucide-react'
import { useStore } from '../../store/useStore'
import { agentMeta, stageLabel } from '../../lib/agents'
import { StatusDot } from '../ui/StatusDot'

/**
 * Always-answers-the-question banner: who is working right now, on what model,
 * doing what. Updates from the SSE stream, so it changes mid-request rather
 * than only when the whole run finishes.
 */
export function CurrentAgentBanner() {
  const telemetry = useStore((state) => state.telemetry)
  const executionStatus = useStore((state) => state.executionStatus)
  const sending = useStore((state) => state.sending)

  const running = executionStatus === 'running' && sending
  const agentKey = telemetry?.active_agent ?? null
  const meta = agentMeta(agentKey)

  const terminated =
    executionStatus === 'completed' ||
    executionStatus === 'failed' ||
    executionStatus === 'cancelled'

  const label = running ? meta.label : terminated ? 'Idle' : 'Ready'
  const detail = running
    ? stageLabel(telemetry?.stage)
    : terminated
      ? `Last run ${executionStatus}`
      : 'Waiting for your request'

  const isLocal = telemetry?.active_is_local ?? meta.isLocal
  const model = telemetry?.active_model ?? (running ? meta.model : '—')

  return (
    <div
      className={clsx(
        'flex items-center gap-3 border-b px-4 py-2 transition-colors',
        running ? 'border-accent/30 bg-accent/5' : 'border-hairline bg-surface/40',
      )}
    >
      <StatusDot status={running ? (meta.isLocal ? 'running' : 'thinking') : 'idle'} showRing />

      <div className="flex min-w-0 flex-1 items-baseline gap-2">
        <span className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">
          Current agent
        </span>
        <span
          className={clsx(
            'truncate text-[0.8125rem] font-medium',
            running ? 'text-accent' : 'text-ink-soft',
          )}
        >
          {label}
        </span>
        <span className="truncate text-xs text-ink-faint">{detail}</span>
      </div>

      <div className="flex shrink-0 items-center gap-1.5">
        {isLocal ? (
          <Cpu className="h-3.5 w-3.5 text-ink-faint" aria-hidden="true" />
        ) : (
          <Server className="h-3.5 w-3.5 text-ink-faint" aria-hidden="true" />
        )}
        <span className="font-mono text-2xs text-ink-soft">{model}</span>
        {running && isLocal && (
          <span className="rounded border border-accent/30 px-1 text-[0.5625rem] uppercase tracking-wide text-accent">
            local
          </span>
        )}
      </div>
    </div>
  )
}
