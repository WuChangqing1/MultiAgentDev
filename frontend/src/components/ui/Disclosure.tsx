import { clsx } from 'clsx'
import { ChevronRight, Copy, Check as CheckIcon } from 'lucide-react'
import { type ReactNode, useState } from 'react'
import { copyText } from '../../lib/format'

/** Collapsible section. Reasoning traces and debug payloads use this so they
 *  stay out of the way until explicitly opened. */
export function Disclosure({
  title,
  children,
  defaultOpen = false,
  badge,
  tone = 'default',
  className,
}: {
  title: ReactNode
  children: ReactNode
  defaultOpen?: boolean
  badge?: ReactNode
  tone?: 'default' | 'muted' | 'accent'
  className?: string
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className={clsx('rounded-card border border-hairline bg-surface/60', className)}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="focus-ring flex w-full items-center gap-1.5 rounded-card px-2.5 py-2 text-left text-xs font-medium text-ink-soft transition-colors hover:bg-sunken/70"
      >
        <ChevronRight
          className={clsx('h-3.5 w-3.5 shrink-0 transition-transform duration-150', open && 'rotate-90')}
          aria-hidden="true"
        />
        <span
          className={clsx(
            'truncate',
            tone === 'accent' && 'text-accent',
            tone === 'muted' && 'text-ink-faint',
          )}
        >
          {title}
        </span>
        {badge && <span className="ml-auto shrink-0">{badge}</span>}
      </button>
      {open && (
        <div className="animate-fade-in border-t border-hairline px-2.5 py-2 text-xs text-ink-soft">
          {children}
        </div>
      )}
    </div>
  )
}

/** Copy-to-clipboard button with transient confirmation. */
export function CopyButton({
  text,
  label = 'Copy',
  className,
}: {
  text: string
  label?: string
  className?: string
}) {
  const [copied, setCopied] = useState(false)

  return (
    <button
      type="button"
      onClick={async () => {
        const ok = await copyText(text)
        if (ok) {
          setCopied(true)
          setTimeout(() => setCopied(false), 1400)
        }
      }}
      title={label}
      aria-label={label}
      className={clsx(
        'focus-ring inline-flex items-center gap-1 rounded p-1 text-ink-faint transition-colors hover:bg-sunken hover:text-ink-soft',
        className,
      )}
    >
      {copied ? (
        <CheckIcon className="h-3.5 w-3.5 text-positive" aria-hidden="true" />
      ) : (
        <Copy className="h-3.5 w-3.5" aria-hidden="true" />
      )}
    </button>
  )
}

/** Monospaced block used for raw JSON, prompts and internal state. */
export function CodeBlock({
  children,
  tone = 'default',
}: {
  children: string
  tone?: 'default' | 'warning' | 'internal'
}) {
  return (
    <pre
      className={clsx(
        'scrollbar-thin max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md border p-2 font-mono text-[0.6875rem] leading-relaxed',
        tone === 'default' && 'border-hairline bg-sunken text-ink-soft',
        tone === 'warning' && 'border-caution/40 bg-caution/5 text-ink',
        tone === 'internal' && 'border-accent/30 bg-accent/5 text-ink',
      )}
    >
      {children}
    </pre>
  )
}

/** Small labelled metric. Used by the token dashboard and detail panels. */
export function Metric({
  label,
  value,
  hint,
  tone = 'default',
}: {
  label: ReactNode
  value: ReactNode
  hint?: ReactNode
  tone?: 'default' | 'muted' | 'accent'
}) {
  return (
    <div className="min-w-0">
      <div className="truncate text-2xs uppercase tracking-wide text-ink-faint">{label}</div>
      <div
        className={clsx(
          'mt-0.5 truncate font-mono text-[0.8125rem] tabular-nums',
          tone === 'default' && 'text-ink',
          tone === 'muted' && 'text-ink-soft',
          tone === 'accent' && 'text-accent',
        )}
      >
        {value}
      </div>
      {hint && <div className="mt-0.5 truncate text-2xs text-ink-faint">{hint}</div>}
    </div>
  )
}

/** Marker for values derived locally rather than reported by the API. */
export function EstimatedTag({ show }: { show: boolean }) {
  if (!show) return null
  return (
    <span
      title="Derived locally — the model API did not report this value"
      className="ml-1 rounded border border-caution/40 px-1 text-[0.5625rem] uppercase tracking-wide text-caution"
    >
      est
    </span>
  )
}
