import { useState } from 'react'
import { clsx } from 'clsx'
import { Activity, Coins, GitBranch, ListTree, PieChart } from 'lucide-react'
import { useStore } from '../../store/useStore'
import { AgentFlow } from './AgentFlow'
import { ExecutionTimeline } from './ExecutionTimeline'
import { StepDetail } from './StepDetail'
import { TokenDashboard } from './TokenDashboard'
import { SessionStats } from './SessionStats'

type Tab = 'timeline' | 'flow' | 'tokens' | 'session'

const TABS: { id: Tab; label: string; icon: typeof Activity }[] = [
  { id: 'timeline', label: 'Timeline', icon: ListTree },
  { id: 'flow', label: 'Flow', icon: GitBranch },
  { id: 'tokens', label: 'Tokens', icon: PieChart },
  { id: 'session', label: 'Session', icon: Coins },
]

/** Right rail: what happened, in what order, at what cost. */
export function ExecutionPanel() {
  const [tab, setTab] = useState<Tab>('timeline')
  const flow = useStore((state) => state.flow)
  const timeline = useStore((state) => state.timeline)
  const executionStatus = useStore((state) => state.executionStatus)
  const telemetry = useStore((state) => state.telemetry)

  return (
    <aside className="flex h-full w-full min-w-0 flex-col border-l border-hairline bg-surface/30">
      <div className="flex items-center gap-1 border-b border-hairline px-2 py-1.5">
        <Activity className="ml-1 h-3.5 w-3.5 text-ink-faint" aria-hidden="true" />
        <span className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">
          Execution
        </span>

        {executionStatus && (
          <span
            className={clsx(
              'ml-1 rounded border px-1 text-[0.5625rem] uppercase tracking-wide',
              executionStatus === 'running'
                ? 'border-accent/40 text-accent'
                : executionStatus === 'completed'
                  ? 'border-positive/40 text-positive'
                  : executionStatus === 'failed'
                    ? 'border-danger/40 text-danger'
                    : 'border-hairline text-ink-faint',
            )}
          >
            {executionStatus}
          </span>
        )}

        <span className="ml-auto font-mono text-[0.625rem] text-ink-faint">
          {timeline.length} step{timeline.length === 1 ? '' : 's'}
          {telemetry?.step_count ? '' : ''}
        </span>
      </div>

      <nav className="flex shrink-0 gap-0.5 border-b border-hairline px-2 py-1.5" role="tablist">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={clsx(
              'focus-ring flex flex-1 items-center justify-center gap-1 rounded-md px-2 py-1 text-2xs font-medium transition-colors',
              tab === id ? 'bg-raised text-ink shadow-card' : 'text-ink-faint hover:bg-sunken hover:text-ink-soft',
            )}
          >
            <Icon className="h-3 w-3" aria-hidden="true" />
            {label}
          </button>
        ))}
      </nav>

      <div className="scrollbar-thin flex-1 overflow-y-auto py-2">
        {tab === 'timeline' && <ExecutionTimeline />}
        {tab === 'flow' && (
          <div className="px-3">
            <AgentFlow nodes={flow} />
          </div>
        )}
        {tab === 'tokens' && <TokenDashboard />}
        {tab === 'session' && <SessionStats />}
      </div>

      <StepDetail />
    </aside>
  )
}
