import { clsx } from 'clsx'
import { useStore } from '../../store/useStore'
import { agentMeta } from '../../lib/agents'
import { formatTime, formatTokens, formatLatency } from '../../lib/format'
import { StatusDot, statusLabel } from '../ui/StatusDot'
import type { TimelineEntry } from '../../types'

/** Clickable execution timeline. Selecting a row opens the detail panel. */
export function ExecutionTimeline() {
  const timeline = useStore((state) => state.timeline)
  const selectedStepId = useStore((state) => state.selectedStepId)
  const selectStep = useStore((state) => state.selectStep)

  if (timeline.length === 0) {
    return (
      <div className="px-3 py-6 text-center text-2xs text-ink-faint">
        Timeline appears here as the request runs.
      </div>
    )
  }

  return (
    <ol className="space-y-0.5">
      {timeline.map((entry) => (
        <TimelineRow
          key={entry.id}
          entry={entry}
          selected={selectedStepId === entry.id}
          onSelect={() => selectStep(selectedStepId === entry.id ? null : entry.id)}
        />
      ))}
    </ol>
  )
}

function TimelineRow({
  entry,
  selected,
  onSelect,
}: {
  entry: TimelineEntry
  selected: boolean
  onSelect: () => void
}) {
  const meta = agentMeta(entry.agent)
  const open = entry.status === 'running' || entry.status === 'thinking'
  const action = entry.decision?.action

  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-expanded={selected}
        className={clsx(
          'focus-ring w-full rounded-card border px-2 py-1.5 text-left transition-colors',
          selected
            ? 'border-accent/50 bg-accent/5'
            : 'border-transparent hover:border-hairline hover:bg-sunken/60',
        )}
      >
        <div className="flex items-center gap-2">
          <span className="shrink-0 font-mono text-[0.625rem] tabular-nums text-ink-faint">
            {formatTime(entry.startedAt)}
          </span>
          <StatusDot status={entry.status} showRing={open} />
          <span
            className={clsx(
              'truncate text-xs font-medium',
              entry.status === 'failed' ? 'text-danger' : 'text-ink',
            )}
          >
            {meta.shortLabel}
          </span>
          {action && (
            <span className="shrink-0 rounded border border-hairline px-1 font-mono text-[0.5625rem] uppercase text-ink-faint">
              {action}
            </span>
          )}
          <span
            className={clsx(
              'ml-auto shrink-0 text-[0.625rem] font-medium',
              entry.status === 'completed'
                ? 'text-positive'
                : entry.status === 'failed'
                  ? 'text-danger'
                  : open
                    ? 'text-accent'
                    : 'text-ink-faint',
            )}
          >
            {statusLabel(entry.status)}
          </span>
        </div>

        <div className="mt-1 flex items-center gap-2 pl-0.5">
          {entry.task && (
            <span className="truncate text-2xs text-ink-faint">
              {entry.decision?.task ?? entry.task}
            </span>
          )}
          <span className="ml-auto flex shrink-0 items-center gap-2 font-mono text-[0.625rem] text-ink-faint">
            {entry.usage?.total_tokens != null && <span>{formatTokens(entry.usage.total_tokens)} tok</span>}
            {entry.usage?.latency_ms != null && <span>{formatLatency(entry.usage.latency_ms)}</span>}
          </span>
        </div>
      </button>
    </li>
  )
}
