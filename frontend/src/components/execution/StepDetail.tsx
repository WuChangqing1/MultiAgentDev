import { clsx } from 'clsx'
import { useStore } from '../../store/useStore'
import { agentMeta } from '../../lib/agents'
import {
  formatDuration,
  formatLatency,
  formatThroughput,
  formatTime,
  formatTokens,
} from '../../lib/format'
import { CodeBlock, Disclosure, EstimatedTag, Metric } from '../ui/Disclosure'
import { StatusBadge } from '../ui/StatusDot'
import { Markdown } from '../chat/Markdown'

/**
 * Detail view for one timeline entry.
 *
 * Reasoning is collapsed by default everywhere: it is useful for debugging and
 * should never be mistaken for the agent's answer.
 */
export function StepDetail() {
  const timeline = useStore((state) => state.timeline)
  const selectedStepId = useStore((state) => state.selectedStepId)
  const showReasoning = useStore((state) => state.showReasoning)
  const debugMode = useStore((state) => state.debugMode)
  const selectStep = useStore((state) => state.selectStep)

  const entry = timeline.find((item) => item.id === selectedStepId)
  if (!entry) return null

  const meta = agentMeta(entry.agent)
  const usage = entry.usage

  return (
    <div className="animate-fade-in border-t border-hairline bg-surface/40">
      <div className="flex items-center gap-2 px-3 py-2">
        <span className="text-xs font-semibold text-ink">{entry.agentLabel}</span>
        <StatusBadge status={entry.status} />
        <button
          type="button"
          onClick={() => selectStep(null)}
          className="focus-ring ml-auto rounded px-1.5 py-0.5 text-2xs text-ink-faint hover:bg-sunken hover:text-ink-soft"
        >
          Close
        </button>
      </div>

      <div className="scrollbar-thin max-h-[26rem] space-y-2.5 overflow-y-auto px-3 pb-3">
        <div className="grid grid-cols-3 gap-x-3 gap-y-2">
          <Metric label="Model" value={entry.model ?? '—'} tone="muted" />
          <Metric label="Stage" value={entry.stage || '—'} tone="muted" />
          <Metric label="Step" value={`#${entry.stepIndex}`} tone="muted" />
          <Metric label="Started" value={formatTime(entry.startedAt)} tone="muted" />
          <Metric label="Ended" value={formatTime(entry.finishedAt)} tone="muted" />
          <Metric
            label="Duration"
            value={formatDuration(entry.startedAt, entry.finishedAt)}
            tone="muted"
          />
        </div>

        <div className="rounded-card border border-hairline bg-surface/60 px-2.5 py-1.5">
          <div className="mb-1 text-2xs uppercase tracking-wide text-ink-faint">Role</div>
          <div className="text-xs text-ink-soft">{meta.role || '—'}</div>
        </div>

        {entry.decision && (
          <div className="rounded-card border border-hairline bg-surface/60 px-2.5 py-1.5">
            <div className="mb-1 text-2xs uppercase tracking-wide text-ink-faint">
              Decision (structured)
            </div>
            <div className="space-y-0.5 text-xs text-ink-soft">
              <div>
                <span className="text-ink-faint">action: </span>
                <span className="font-mono">{entry.decision.action ?? '—'}</span>
              </div>
              {entry.decision.agent && (
                <div>
                  <span className="text-ink-faint">agent: </span>
                  <span className="font-mono">{entry.decision.agent}</span>
                </div>
              )}
              {entry.decision.task && <div className="break-words">{entry.decision.task}</div>}
              {entry.decision.reason && (
                <div className="text-ink-faint">reason: {entry.decision.reason}</div>
              )}
              {debugMode && entry.decision.parseStrategy && (
                <div className="text-ink-faint">
                  parsed via <span className="font-mono">{entry.decision.parseStrategy}</span>
                  {entry.decision.parseOk === false && (
                    <span className="ml-1 text-caution">(fallback used)</span>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {entry.task && (
          <Section title="Task">
            <CodeBlock>{entry.task}</CodeBlock>
          </Section>
        )}

        {entry.error && (
          <Section title="Error">
            <CodeBlock tone="warning">{entry.error}</CodeBlock>
          </Section>
        )}

        {entry.output && (
          <Section title="Output">
            <div className="max-h-72 overflow-y-auto rounded-md border border-hairline bg-surface px-2.5 py-2">
              <Markdown content={entry.output} compact />
            </div>
          </Section>
        )}

        {entry.reasoning && showReasoning && (
          <Disclosure
            title="Reasoning"
            badge={
              <span className="rounded border border-hairline px-1 text-[0.5625rem] uppercase text-ink-faint">
                not the answer
              </span>
            }
          >
            <div className="scrollbar-thin max-h-64 overflow-y-auto whitespace-pre-wrap break-words font-mono text-[0.6875rem] leading-relaxed">
              {entry.reasoning}
            </div>
          </Disclosure>
        )}

        {usage && (
          <Section title="Token usage">
            <div className="grid grid-cols-3 gap-x-3 gap-y-2">
              <Metric label="Prompt" value={formatTokens(usage.prompt_tokens)} />
              <Metric label="Completion" value={formatTokens(usage.completion_tokens)} />
              <Metric
                label="Reasoning"
                value={
                  <>
                    {formatTokens(usage.reasoning_tokens)}
                    <EstimatedTag show={usage.reasoning_tokens_source === 'estimated'} />
                  </>
                }
              />
              <Metric label="Total" value={formatTokens(usage.total_tokens)} tone="accent" />
              <Metric label="Latency" value={formatLatency(usage.latency_ms)} tone="muted" />
              <Metric label="Throughput" value={formatThroughput(usage.tokens_per_second)} tone="muted" />
            </div>
            <div className="mt-2 text-2xs text-ink-faint">
              {usage.reasoning_tokens_source === 'estimated'
                ? 'Reasoning tokens are estimated locally — the model API does not report them.'
                : 'All values shown were reported by the model API.'}
            </div>
          </Section>
        )}

        {debugMode && (
          <Disclosure title="Debug · raw payload" tone="accent">
            <DebugPayload entry={entry} />
          </Disclosure>
        )}
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-2xs uppercase tracking-wide text-ink-faint">{title}</div>
      {children}
    </div>
  )
}

function DebugPayload({ entry }: { entry: NonNullable<ReturnType<typeof useStore.getState>['timeline'][number]> }) {
  const payload = {
    agent: entry.agent,
    model: entry.model,
    is_local: entry.isLocal,
    status: entry.status,
    started_at: entry.startedAt,
    finished_at: entry.finishedAt,
    id: entry.id,
  }
  return (
    <div className="space-y-2">
      <CodeBlock>{JSON.stringify(payload, null, 2)}</CodeBlock>
      <div className={clsx('text-2xs text-ink-faint')}>
        Prompt text is stored server-side per step; open the Prompt Viewer in Settings to inspect the
        exact system / task / context / internal-state layout.
      </div>
    </div>
  )
}
