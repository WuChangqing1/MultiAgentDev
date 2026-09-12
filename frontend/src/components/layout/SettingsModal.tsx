import { useEffect, useState } from 'react'
import { clsx } from 'clsx'
import { Check, RefreshCw, RotateCcw, X } from 'lucide-react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'

interface SettingsPayload {
  deepseek?: {
    model: string
    base_url: string
    configured: boolean
    api_key_fingerprint: string
    max_tokens: number
    temperature: number
    reasoning_effort: string
    price_input: number | null
    price_output: number | null
  }
  local?: {
    model: string
    base_url: string
    max_tokens: number
    temperature: number
    top_p: number
    context_window: number
    reasoning: Record<string, string>
    /** agent key -> the Settings field that controls its reasoning policy. */
    reasoning_fields?: Record<string, string>
  }
  orchestration?: { max_agent_steps: number; enable_local_workers: boolean }
  ui?: { show_reasoning: boolean; debug_mode: boolean }
  overrides?: Record<string, unknown>
  prompts?: Record<string, number>
}

/** Runtime settings. Secrets are never displayed — only a redacted fingerprint. */
export function SettingsModal() {
  const open = useStore((state) => state.showSettings)
  const setOpen = useStore((state) => state.setShowSettings)
  const refreshHealth = useStore((state) => state.refreshHealth)
  const setDebugMode = useStore((state) => state.setDebugMode)
  const setShowReasoning = useStore((state) => state.setShowReasoning)

  const [data, setData] = useState<SettingsPayload | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    void (async () => {
      try {
        setData((await api.settings()) as SettingsPayload)
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause))
      }
    })()
  }, [open])

  if (!open) return null

  const patch = async (body: Record<string, unknown>, note: string) => {
    setBusy(true)
    setError(null)
    try {
      const result = (await api.updateSettings(body)) as { effective: SettingsPayload }
      setData(result.effective)
      setSaved(note)
      setTimeout(() => setSaved(null), 1800)
      await refreshHealth(true)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 sm:p-8">
      <button
        type="button"
        aria-label="Close settings"
        onClick={() => setOpen(false)}
        className="fixed inset-0 bg-black/45 backdrop-blur-[1px]"
      />

      <div
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
        className="relative z-10 my-auto w-full max-w-2xl animate-fade-in rounded-card border border-hairline bg-surface shadow-pop"
      >
        <div className="flex items-center gap-2 border-b border-hairline px-4 py-3">
          <h2 className="text-sm font-semibold text-ink">Settings</h2>
          {saved && (
            <span className="flex items-center gap-1 text-2xs text-positive">
              <Check className="h-3 w-3" aria-hidden="true" />
              {saved}
            </span>
          )}
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Close"
            className="focus-ring ml-auto rounded-md p-1 text-ink-faint hover:bg-sunken hover:text-ink-soft"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="scrollbar-thin max-h-[70vh] space-y-5 overflow-y-auto px-4 py-4">
          {error && (
            <p className="rounded-card border border-danger/40 bg-danger/5 px-3 py-2 text-xs text-danger">
              {error}
            </p>
          )}

          {!data ? (
            <div className="space-y-2">
              <div className="skeleton h-16 w-full" />
              <div className="skeleton h-16 w-full" />
              <div className="skeleton h-16 w-full" />
            </div>
          ) : (
            <>
              <Group title="DeepSeek — Main Agent">
                <Row label="API key">
                  <span className="font-mono text-xs text-ink-soft">
                    {data.deepseek?.configured
                      ? data.deepseek.api_key_fingerprint
                      : 'not configured'}
                  </span>
                  <span className="ml-2 text-2xs text-ink-faint">
                    managed in <span className="font-mono">.env</span> only
                  </span>
                </Row>
                <Row label="Model">
                  <TextInput
                    value={data.deepseek?.model ?? ''}
                    onCommit={(value) => patch({ deepseek_model: value }, 'DeepSeek model updated')}
                  />
                </Row>
                <Row label="Base URL">
                  <TextInput
                    value={data.deepseek?.base_url ?? ''}
                    onCommit={(value) => patch({ deepseek_base_url: value }, 'Base URL updated')}
                  />
                </Row>
                <Row label="Max tokens">
                  <NumberInput
                    value={data.deepseek?.max_tokens ?? 4096}
                    min={64}
                    step={256}
                    onCommit={(value) =>
                      patch({ main_agent_max_tokens: value }, 'MainAgent max tokens updated')
                    }
                  />
                </Row>
                <Row label="Temperature">
                  <NumberInput
                    value={data.deepseek?.temperature ?? 0.3}
                    min={0}
                    max={2}
                    step={0.1}
                    onCommit={(value) =>
                      patch({ main_agent_temperature: value }, 'MainAgent temperature updated')
                    }
                  />
                </Row>
                <Row label="Cost">
                  <span className="text-xs text-ink-soft">
                    {data.deepseek?.price_input == null && data.deepseek?.price_output == null
                      ? 'N/A — set prices in .env to estimate'
                      : `in $${data.deepseek?.price_input ?? 0} / out $${data.deepseek?.price_output ?? 0} per 1M`}
                  </span>
                </Row>
              </Group>

              <Group title="Local MiniCPM5-2B — Workers">
                <Row label="Model">
                  <TextInput
                    value={data.local?.model ?? ''}
                    onCommit={(value) => patch({ local_model_name: value }, 'Local model updated')}
                  />
                </Row>
                <Row label="Endpoint">
                  <TextInput
                    value={data.local?.base_url ?? ''}
                    onCommit={(value) =>
                      patch({ local_model_base_url: value }, 'Local endpoint updated')
                    }
                  />
                </Row>
                <Row label="Worker max tokens">
                  <NumberInput
                    value={data.local?.max_tokens ?? 2048}
                    min={64}
                    step={128}
                    onCommit={(value) => patch({ worker_max_tokens: value }, 'Worker max tokens updated')}
                  />
                </Row>
                <Row label="Temperature / top_p">
                  <NumberInput
                    value={data.local?.temperature ?? 1}
                    min={0}
                    max={2}
                    step={0.05}
                    onCommit={(value) => patch({ worker_temperature: value }, 'Worker temperature updated')}
                  />
                  <span className="ml-2 font-mono text-xs text-ink-faint">
                    top_p {data.local?.top_p}
                  </span>
                </Row>
                <Row label="Context window">
                  <span className="font-mono text-xs text-ink-soft">
                    {data.local?.context_window?.toLocaleString()} tokens
                  </span>
                </Row>
                <div className="mt-1.5 border-t border-hairline pt-2">
                  <div className="mb-1.5 text-2xs uppercase tracking-wide text-ink-faint">
                    Reasoning policy
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    {Object.entries(data.local?.reasoning ?? {}).map(([agent, effort]) => {
                      // The backend tells us which settings field controls this
                      // agent; deriving it from the key would break for any
                      // agent whose name does not follow the convention.
                      const field = data.local?.reasoning_fields?.[agent]
                      return (
                        <label key={agent} className="flex items-center gap-2 text-xs">
                          <span className="min-w-0 flex-1 truncate text-ink-soft">{agent}</span>
                          <select
                            value={effort}
                            disabled={!field}
                            title={field ? `PATCH { ${field}: ... }` : 'no settings field declared'}
                            onChange={(event) =>
                              field &&
                              patch({ [field]: event.target.value }, `${agent} reasoning updated`)
                            }
                            className="focus-ring rounded border border-hairline bg-surface px-1 py-0.5 font-mono text-2xs text-ink disabled:opacity-40"
                          >
                            {['none', 'low', 'medium', 'high'].map((option) => (
                              <option key={option} value={option}>
                                {option}
                              </option>
                            ))}
                          </select>
                        </label>
                      )
                    })}
                  </div>
                </div>
              </Group>

              <Group title="Orchestration">
                <Row label="Max agent steps">
                  <NumberInput
                    value={data.orchestration?.max_agent_steps ?? 12}
                    min={1}
                    max={64}
                    onCommit={(value) => patch({ max_agent_steps: value }, 'Step budget updated')}
                  />
                  <span className="ml-2 text-2xs text-ink-faint">hard ceiling per request</span>
                </Row>
                <Row label="Local workers">
                  <Toggle
                    checked={data.orchestration?.enable_local_workers ?? true}
                    onChange={(value) =>
                      patch({ enable_local_workers: value }, value ? 'Local workers enabled' : 'Local workers disabled')
                    }
                  />
                </Row>
              </Group>

              <Group title="Interface">
                <Row label="Show reasoning">
                  <Toggle
                    checked={data.ui?.show_reasoning ?? true}
                    onChange={(value) => {
                      setShowReasoning(value)
                      void patch({ show_reasoning: value }, 'Reasoning visibility updated')
                    }}
                  />
                  <span className="ml-2 text-2xs text-ink-faint">
                    reasoning stays collapsed by default
                  </span>
                </Row>
                <Row label="Debug mode">
                  <Toggle
                    checked={data.ui?.debug_mode ?? false}
                    onChange={(value) => {
                      setDebugMode(value)
                      void patch({ debug_mode: value }, 'Debug mode updated')
                    }}
                  />
                  <span className="ml-2 text-2xs text-ink-faint">
                    raw decisions, parse errors, retries
                  </span>
                </Row>
              </Group>

              {data.prompts && (
                <Group title="Prompt files">
                  <p className="mb-1.5 text-2xs leading-relaxed text-ink-faint">
                    Character counts per prompt file. Edits take effect on the next request without a
                    restart; the static prefix is cached by the provider between edits.
                  </p>
                  <ul className="space-y-0.5">
                    {Object.entries(data.prompts).map(([name, chars]) => (
                      <li key={name} className="flex items-center gap-2 text-xs">
                        <span className="font-mono text-ink-soft">{name}.md</span>
                        <span className="ml-auto font-mono text-2xs text-ink-faint">
                          {chars.toLocaleString()} chars
                        </span>
                      </li>
                    ))}
                  </ul>
                </Group>
              )}
            </>
          )}
        </div>

        <div className="flex items-center gap-2 border-t border-hairline px-4 py-3">
          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              try {
                const result = (await api.validate()) as unknown
                setSaved(`Validated: ${Array.isArray(result) ? result.map((s: { status: string }) => s.status).join(' / ') : 'ok'}`)
                await refreshHealth(true)
              } catch (cause) {
                setError(cause instanceof Error ? cause.message : String(cause))
              } finally {
                setBusy(false)
              }
            }}
            className="focus-ring inline-flex items-center gap-1.5 rounded-md border border-hairline px-2.5 py-1.5 text-xs font-medium text-ink-soft transition-colors hover:bg-sunken disabled:opacity-50"
          >
            <RefreshCw className={clsx('h-3.5 w-3.5', busy && 'animate-spin')} aria-hidden="true" />
            Test connections
          </button>

          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              try {
                const result = (await api.resetSettings()) as { effective: SettingsPayload }
                setData(result.effective)
                setSaved('Reset to .env values')
                await refreshHealth(true)
              } finally {
                setBusy(false)
              }
            }}
            className="focus-ring inline-flex items-center gap-1.5 rounded-md border border-hairline px-2.5 py-1.5 text-xs font-medium text-ink-soft transition-colors hover:bg-sunken disabled:opacity-50"
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            Reset overrides
          </button>

          <span className="ml-auto text-2xs text-ink-faint">
            Overrides live in memory and vanish on restart.
          </span>
        </div>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------
// Small form primitives
// --------------------------------------------------------------------------

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-2xs font-semibold uppercase tracking-wider text-ink-faint">{title}</h3>
      <div className="space-y-1.5 rounded-card border border-hairline bg-canvas/40 p-3">{children}</div>
    </section>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="w-40 shrink-0 text-ink-soft">{label}</span>
      {children}
    </div>
  )
}

function TextInput({ value, onCommit }: { value: string; onCommit: (value: string) => void }) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])

  return (
    <input
      type="text"
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => draft !== value && onCommit(draft)}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur()
      }}
      className="focus-ring min-w-0 flex-1 rounded-md border border-hairline bg-surface px-2 py-1 font-mono text-xs text-ink"
    />
  )
}

function NumberInput({
  value,
  min,
  max,
  step = 1,
  onCommit,
}: {
  value: number
  min?: number
  max?: number
  step?: number
  onCommit: (value: number) => void
}) {
  const [draft, setDraft] = useState(String(value))
  useEffect(() => setDraft(String(value)), [value])

  const commit = () => {
    const parsed = Number(draft)
    if (!Number.isFinite(parsed) || parsed === value) return
    onCommit(parsed)
  }

  return (
    <input
      type="number"
      value={draft}
      min={min}
      max={max}
      step={step}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur()
      }}
      className="focus-ring w-24 rounded-md border border-hairline bg-surface px-2 py-1 font-mono text-xs text-ink"
    />
  )
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={clsx(
        'focus-ring relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors',
        checked ? 'border-accent bg-accent/80' : 'border-hairline bg-sunken',
      )}
    >
      <span
        className={clsx(
          'h-3.5 w-3.5 rounded-full bg-white shadow transition-transform',
          checked ? 'translate-x-[1.15rem]' : 'translate-x-[0.15rem]',
        )}
      />
    </button>
  )
}
