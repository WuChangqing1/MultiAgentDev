import { clsx } from 'clsx'
import { AlertTriangle, Sparkles, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useStore } from '../../store/useStore'
import { DEMO_PROMPTS } from '../../lib/agents'
import { formatLatency, formatTokens } from '../../lib/format'
import { Markdown } from './Markdown'
import { CopyButton } from '../ui/Disclosure'
import { CurrentAgentBanner } from './CurrentAgentBanner'
import type { Message } from '../../types'

export function ChatPanel() {
  const messages = useStore((state) => state.messages)
  const sending = useStore((state) => state.sending)
  const executionStatus = useStore((state) => state.executionStatus)
  const error = useStore((state) => state.error)
  const notices = useStore((state) => state.notices)
  const dismissError = useStore((state) => state.dismissError)
  const dismissNotice = useStore((state) => state.dismissNotice)
  const send = useStore((state) => state.send)
  const cancel = useStore((state) => state.cancel)

  const [draft, setDraft] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const pinnedToBottom = useRef(true)

  // Autoscroll while pinned; never yank the view if the user scrolled up.
  useEffect(() => {
    const node = scrollRef.current
    if (!node || !pinnedToBottom.current) return
    node.scrollTop = node.scrollHeight
  }, [messages])

  const handleScroll = () => {
    const node = scrollRef.current
    if (!node) return
    pinnedToBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 120
  }

  const submit = () => {
    const text = draft.trim()
    if (!text || sending) return
    setDraft('')
    pinnedToBottom.current = true
    void send(text)
    requestAnimationFrame(() => textareaRef.current?.focus())
  }

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  const isEmpty = messages.length === 0

  return (
    <section className="flex h-full min-w-0 flex-col bg-canvas">
      <CurrentAgentBanner />

      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="scrollbar-thin flex-1 overflow-y-auto"
        aria-live="polite"
      >
        {isEmpty ? (
          <EmptyState onPick={(text) => setDraft(text)} />
        ) : (
          <div className="mx-auto w-full max-w-3xl space-y-5 px-4 py-5">
            {messages.map((message) => (
              <MessageBubble
                key={message.id ?? `${message.role}:${message.created_at}`}
                message={message}
                pending={
                  sending && message.role === 'assistant' && !message.content
                }
              />
            ))}
          </div>
        )}
      </div>

      {notices.length > 0 && (
        <div className="space-y-1 px-4 pb-1">
          {notices.slice(0, 2).map((notice) => (
            <div
              key={notice.id}
              className={clsx(
                'flex items-start gap-2 rounded-card border px-2.5 py-1.5 text-xs',
                notice.level === 'error'
                  ? 'border-danger/40 bg-danger/5 text-danger'
                  : notice.level === 'warning'
                    ? 'border-caution/40 bg-caution/5 text-caution'
                    : 'border-hairline bg-sunken text-ink-soft',
              )}
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span className="flex-1">{notice.message}</span>
              <button
                type="button"
                onClick={() => dismissNotice(notice.id)}
                className="focus-ring shrink-0 rounded p-0.5 hover:bg-black/5"
                aria-label="Dismiss notice"
              >
                <X className="h-3 w-3" aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
      )}

      {error && (
        <div className="mx-4 mb-1 flex items-start gap-2 rounded-card border border-danger/40 bg-danger/5 px-3 py-2 text-xs text-danger">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="flex-1">{error}</span>
          <button
            type="button"
            onClick={dismissError}
            className="focus-ring shrink-0 rounded p-0.5 hover:bg-black/5"
            aria-label="Dismiss error"
          >
            <X className="h-3 w-3" aria-hidden="true" />
          </button>
        </div>
      )}

      <div className="border-t border-hairline bg-surface/40 px-4 py-3">
        <div className="mx-auto w-full max-w-3xl">
          <div className="flex items-end gap-2 rounded-card border border-hairline bg-surface px-2.5 py-2 shadow-card transition-colors focus-within:border-accent/50">
            <textarea
              ref={textareaRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKeyDown}
              rows={1}
              placeholder={sending ? 'Agent is working… you can keep typing' : 'Ask anything. Enter to send, Shift+Enter for a new line.'}
              className="scrollbar-thin max-h-44 min-h-[24px] flex-1 resize-none bg-transparent py-1 text-[0.9375rem] leading-relaxed text-ink outline-none placeholder:text-ink-faint"
              style={{ height: 'auto' }}
              onInput={(event) => {
                const target = event.currentTarget
                target.style.height = 'auto'
                target.style.height = `${Math.min(target.scrollHeight, 176)}px`
              }}
            />

            {sending ? (
              <button
                type="button"
                onClick={() => void cancel()}
                className="focus-ring shrink-0 rounded-md border border-hairline px-2.5 py-1.5 text-xs font-medium text-ink-soft transition-colors hover:bg-sunken"
              >
                Stop
              </button>
            ) : (
              <button
                type="button"
                onClick={submit}
                disabled={!draft.trim()}
                className="focus-ring shrink-0 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Send
              </button>
            )}
          </div>

          <div className="mt-1.5 flex items-center justify-between px-0.5 text-2xs text-ink-faint">
            <span>
              {sending
                ? 'Agent is working — the UI stays responsive'
                : 'Enter to send · Shift+Enter for a new line'}
            </span>
            <span>
              {executionStatus && executionStatus !== 'running' ? `Last run: ${executionStatus}` : ''}
            </span>
          </div>
        </div>
      </div>
    </section>
  )
}

function MessageBubble({ message, pending }: { message: Message; pending: boolean }) {
  if (message.role === 'user') {
    return (
      <div className="flex animate-fade-in justify-end">
        <div className="max-w-[85%] rounded-card rounded-br-sm border border-hairline bg-raised px-3.5 py-2.5 shadow-card">
          <div className="whitespace-pre-wrap break-words text-[0.9375rem] leading-relaxed text-ink">
            {message.content}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex animate-fade-in gap-3">
      <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-hairline bg-surface">
        <Sparkles className="h-3.5 w-3.5 text-accent" aria-hidden="true" />
      </div>

      <div className="min-w-0 flex-1">
        {pending ? (
          <ThinkingPlaceholder />
        ) : (
          <>
            <Markdown content={message.content} />
            <div className="mt-1.5 flex items-center gap-3 text-2xs text-ink-faint opacity-0 transition-opacity hover:opacity-100 focus-within:opacity-100">
              <CopyButton text={message.content} label="Copy answer" />
              {message.usage?.total_tokens != null && (
                <span className="font-mono">{formatTokens(message.usage.total_tokens)} tok</span>
              )}
              {message.usage?.latency_ms != null && (
                <span className="font-mono">{formatLatency(message.usage.latency_ms)}</span>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function ThinkingPlaceholder() {
  return (
    <div className="flex items-center gap-2 py-1 text-sm text-ink-faint">
      <span className="flex gap-1">
        <span className="h-1.5 w-1.5 animate-blink rounded-full bg-accent [animation-delay:0ms]" />
        <span className="h-1.5 w-1.5 animate-blink rounded-full bg-accent [animation-delay:200ms]" />
        <span className="h-1.5 w-1.5 animate-blink rounded-full bg-accent [animation-delay:400ms]" />
      </span>
      Agent is working…
    </div>
  )
}

function EmptyState({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col justify-center px-6 py-10">
      <h1 className="text-lg font-semibold tracking-tight text-ink">
        DeepSeek MainAgent × 本地 MiniCPM5-2B
      </h1>
      <p className="mt-1.5 max-w-xl text-sm leading-relaxed text-ink-soft">
        MainAgent 负责规划、判断与最终裁决；能可靠完成的机械性子任务会委派给本地
        Worker。左侧显示当前正在工作的 Agent，右侧显示实时执行时间线与 Token 统计。
      </p>

      <div className="mt-5 space-y-2">
        {DEMO_PROMPTS.map((demo) => (
          <button
            key={demo.title}
            type="button"
            onClick={() => onPick(demo.text)}
            className="focus-ring group flex w-full flex-col items-start gap-1 rounded-card border border-hairline bg-surface/60 px-3 py-2.5 text-left transition-colors hover:border-accent/40 hover:bg-accent/5"
          >
            <span className="flex w-full items-center gap-2">
              <span className="text-[0.8125rem] font-medium text-ink">{demo.title}</span>
              <span className="rounded border border-hairline px-1 text-[0.5625rem] uppercase tracking-wide text-ink-faint">
                {demo.hint}
              </span>
            </span>
            <span className="line-clamp-2 text-xs leading-relaxed text-ink-faint">
              {demo.text.replace(/\n/g, ' ')}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}
