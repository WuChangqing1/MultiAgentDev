/** Small presentation helpers. No business logic, no network. */

/**
 * Format a token count. Returns an em dash for `null`/`undefined` so the UI
 * never implies a number the provider did not report.
 */
export function formatTokens(value?: number | null): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString('en-US')
}

export function formatNumber(value?: number | null, digits = 0): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function formatLatency(ms?: number | null): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

export function formatThroughput(tps?: number | null): string {
  if (tps === null || tps === undefined) return '—'
  return `${tps.toFixed(1)} tok/s`
}

/**
 * Cost rendering. Local inference is free, and unknown pricing is reported as
 * "N/A" rather than a fabricated `$0.000000`.
 */
export function formatCost(cost?: number | null, isLocal = false): string {
  if (isLocal) return 'Local'
  if (cost === null || cost === undefined) return 'N/A'
  if (cost === 0) return '$0'
  if (cost < 0.0001) return `$${cost.toExponential(2)}`
  return `$${cost.toFixed(4)}`
}

export function formatTime(iso?: string | null): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleTimeString('en-GB', { hour12: false })
}

export function formatClock(date = new Date()): string {
  return date.toLocaleTimeString('en-GB', { hour12: false })
}

export function formatDuration(startedAt?: string | null, finishedAt?: string | null): string {
  if (!startedAt) return '—'
  const start = new Date(startedAt).getTime()
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now()
  if (Number.isNaN(start) || Number.isNaN(end)) return '—'
  return formatLatency(Math.max(0, end - start))
}

export function truncate(text: string, max: number): string {
  if (text.length <= max) return text
  return `${text.slice(0, max).trimEnd()}…`
}

export function relativeTime(iso?: string | null): string {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const seconds = Math.round((Date.now() - then) / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

/** Copy text to the clipboard, tolerating non-secure contexts. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
    const area = document.createElement('textarea')
    area.value = text
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(area)
    return ok
  } catch {
    return false
  }
}
