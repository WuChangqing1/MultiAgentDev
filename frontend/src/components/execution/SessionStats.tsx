import { useEffect } from 'react'
import { useStore } from '../../store/useStore'
import { formatCost, formatLatency, formatTokens } from '../../lib/format'
import { Metric } from '../ui/Disclosure'

/** Session-wide telemetry roll-up, persisted in SQLite across page reloads. */
export function SessionStats() {
  const stats = useStore((state) => state.stats)
  const refreshStats = useStore((state) => state.refreshStats)

  useEffect(() => {
    void refreshStats()
  }, [refreshStats])

  if (!stats || stats.requests === 0) {
    return (
      <div className="px-3 py-6 text-center text-2xs text-ink-faint">
        No completed requests in this database yet. Session statistics accumulate here.
      </div>
    )
  }

  return (
    <div className="space-y-3 px-3 pb-3">
      <div className="grid grid-cols-2 gap-x-3 gap-y-2">
        <Metric label="Requests" value={stats.requests} tone="accent" />
        <Metric label="Agent calls" value={stats.agent_calls} />
        <Metric label="DeepSeek calls" value={stats.deepseek_calls} tone="muted" />
        <Metric label="Local calls" value={stats.local_calls} tone="muted" />
      </div>

      <div>
        <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-ink-faint">
          Tokens
        </div>
        <div className="grid grid-cols-2 gap-x-3 gap-y-2">
          <Metric label="Total" value={formatTokens(stats.total_tokens)} tone="accent" />
          <Metric label="Cloud" value={formatTokens(stats.cloud_tokens)} />
          <Metric label="Local" value={formatTokens(stats.local_tokens)} />
          <Metric label="Reasoning" value={formatTokens(stats.reasoning_tokens)} />
        </div>
      </div>

      <div>
        <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-ink-faint">
          Performance
        </div>
        <div className="grid grid-cols-2 gap-x-3 gap-y-2">
          <Metric label="Avg latency" value={formatLatency(stats.average_latency_ms)} tone="muted" />
          <Metric
            label="Cost"
            value={formatCost(stats.cost_usd, (stats.cloud_tokens ?? 0) === 0)}
            tone="muted"
          />
        </div>
      </div>

      {Object.keys(stats.by_agent).length > 0 && (
        <div>
          <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-ink-faint">
            By agent
          </div>
          <ul className="space-y-1">
            {Object.entries(stats.by_agent).map(([agent, usage]) => (
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
        </div>
      )}

      {stats.cost_usd == null && (stats.cloud_tokens ?? 0) > 0 && (
        <p className="text-2xs leading-relaxed text-ink-faint">
          Cost shows N/A because no DeepSeek pricing is configured. Set
          <span className="mx-1 font-mono">DEEPSEEK_PRICE_INPUT</span>/
          <span className="mx-1 font-mono">DEEPSEEK_PRICE_OUTPUT</span>
          in <span className="font-mono">.env</span> to estimate it.
        </p>
      )}
    </div>
  )
}
