import { clsx } from 'clsx'
import { Check, Loader2, MinusCircle, X } from 'lucide-react'
import type { AgentStatus } from '../../types'

const TONE: Record<
  AgentStatus,
  { dot: string; text: string; label: string; pulse: boolean; icon: 'spinner' | 'check' | 'x' | 'dot' }
> = {
  idle: { dot: 'bg-ink-faint/50', text: 'text-ink-faint', label: 'Idle', pulse: false, icon: 'dot' },
  queued: { dot: 'bg-info', text: 'text-info', label: 'Queued', pulse: false, icon: 'dot' },
  running: { dot: 'bg-accent', text: 'text-accent', label: 'Running', pulse: true, icon: 'spinner' },
  thinking: { dot: 'bg-accent', text: 'text-accent', label: 'Thinking', pulse: true, icon: 'spinner' },
  completed: { dot: 'bg-positive', text: 'text-positive', label: 'Completed', pulse: false, icon: 'check' },
  failed: { dot: 'bg-danger', text: 'text-danger', label: 'Failed', pulse: false, icon: 'x' },
  skipped: { dot: 'bg-ink-faint/50', text: 'text-ink-faint', label: 'Skipped', pulse: false, icon: 'dot' },
}

export function StatusDot({
  status,
  className,
  showRing = false,
}: {
  status: AgentStatus
  className?: string
  showRing?: boolean
}) {
  const tone = TONE[status] ?? TONE.idle
  return (
    <span className={clsx('relative inline-flex h-2 w-2 shrink-0', className)}>
      {tone.pulse && showRing && (
        <span className={clsx('absolute inset-0 rounded-full animate-pulse-ring', tone.dot)} />
      )}
      <span className={clsx('relative h-2 w-2 rounded-full', tone.dot)} />
    </span>
  )
}

export function StatusBadge({ status, compact = false }: { status: AgentStatus; compact?: boolean }) {
  const tone = TONE[status] ?? TONE.idle
  const Icon =
    tone.icon === 'spinner'
      ? Loader2
      : tone.icon === 'check'
        ? Check
        : tone.icon === 'x'
          ? X
          : MinusCircle

  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-md border border-hairline bg-sunken px-1.5 py-0.5 text-2xs font-medium',
        tone.text,
        compact && 'px-1 py-0',
      )}
    >
      <Icon
        className={clsx('h-3 w-3', tone.icon === 'spinner' && 'animate-spin')}
        aria-hidden="true"
      />
      {!compact && tone.label}
    </span>
  )
}

export function statusLabel(status: AgentStatus): string {
  return (TONE[status] ?? TONE.idle).label
}

/** Tailwind text colour for a status, for places that only need the tone. */
export function statusTextClass(status: AgentStatus): string {
  return (TONE[status] ?? TONE.idle).text
}

/** Tailwind background colour for a status, used by the flow diagram. */
export function statusBgClass(status: AgentStatus): string {
  return (TONE[status] ?? TONE.idle).dot
}
