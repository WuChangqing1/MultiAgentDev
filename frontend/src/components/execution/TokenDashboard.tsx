import { useStore } from '../../store/useStore'
import { formatCost, formatTokens } from '../../lib/format'
import { EstimatedTag, Metric } from '../ui/Disclosure'

/**
 * Token dashboard.
 *
 * All of this is telemetry: it exists so a human can see what a request costs.
 * It is never injected into a prompt — the backend enforces that structurally.
 */
export function TokenDashboard() {
  const totals = useStore((state) => state.totals)
  const byAgent = useStore((state) => state.byAgent)
  const telemetry = useStore((state) => state.telemetry)

  const current = totals ?? {
    calls: 0,
    prompt_tokens: telemetry?.prompt_tokens ?? null,
    completion_tokens: telemetry?.completion_tokens ?? null,
    reasoning_tokens: telemetry?.reasoning_tokens ?? null,
    total_tokens: telemetry?.total_tokens ?? null,
    cached_tokens: null,
    latency_ms: telemetry?.latency_ms ?? null,
    tokens_per_second: telemetry?.tokens_per_second ?? null,
    cost_usd: null,
    any_estimated: false,
  }

  const rows = Object.entries(byAgent).sort(([, a], [, b]) => (b.total_tokens ?? 0) - (a.total_tokens ?? 0))
  const hasCurrent = current.total_tokens != null || current.calls > 0

  return (
    <div className="space-y-3 px-3 pb-3">
      <div>
        <div className="mb-1.5 flex items-baseline gap-2">
          <span className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">
            Current request
          </span>
          {current.any_estimated && (
            <span className="text-[0.5625rem] uppercase tracking-wide text-caution">
              contains estimates
            </span>
          )}
        </div>

        {hasCurrent ? (
          <div className="grid grid-cols-2 gap-x-3 gap-y-2">
            <Metric label="Total" value={formatTokens(current.total_tokens)} tone="accent" />
            <Metric label="Prompt" value={formatTokens(current.prompt_tokens)} />
            <Metric
              label="Reasoning"
              value={
                <>
                  {formatTokens(current.reasoning_tokens)}
                  <EstimatedTag show={current.any_estimated} />
                </>
              }
            />
            <Metric label="Answer" value={formatTokens(current.completion_tokens)} />
            <Metric label="Model calls" value={current.calls} tone="muted" />
            <Metric
              label="Throughput"
              value={current.tokens_per_second ? `${current.tokens_per_second.toFixed(1)} tok/s` : '—'}
              tone="muted"
            />
            <Metric label="Cost" value={formatCost(current.cost_usd, isLocalOnly(byAgent))} tone="muted" />
          </div>
        ) : (
          <div className="rounded-card border border-dashed border-hairline px-3 py-4 text-center text-2xs text-ink-faint">
            No tokens recorded for this request yet.
          </div>
        )}
      </div>

      <div>
        <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-ink-faint">
          By agent
        </div>
        {rows.length === 0 ? (
          <div className="rounded-card border border-dashed border-hairline px-3 py-3 text-center text-2xs text-ink-faint">
            Per-agent usage appears after the first model call.
          </div>
        ) : (
          <ul className="space-y-1">
            {rows.map(([agent, usage]) => (
              <li key={agent} className="flex items-center gap-2 text-xs">
                <span className="min-w-0 flex-1 truncate text-ink-soft">{agent}</span>
                <span className="shrink-0 font-mono text-[0.6875rem] tabular-nums text-ink-faint">
                  {usage.calls}×
                </span>
                <span className="w-16 shrink-0 text-right font-mono text-[0.6875rem] tabular-nums text-ink">
                  {formatTokens(usage.total_tokens)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

/** True when every recorded call with a cost was local (so cost renders "Local"). */
function isLocalOnly(byAgent: Record<string, { calls: number; cost_usd?: number | null }>): boolean {
  const entries = Object.entries(byAgent)
  if (entries.length === 0) return false
  return entries.every(([agent, usage]) => agent.startsWith('local_') && usage.calls > 0)
}
