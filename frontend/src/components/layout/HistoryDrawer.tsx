import { clsx } from 'clsx'
import { MessageSquare, Trash2, X } from 'lucide-react'
import { useStore } from '../../store/useStore'
import { relativeTime } from '../../lib/format'

/** Slide-over listing persisted conversations. */
export function HistoryDrawer() {
  const open = useStore((state) => state.historyOpen)
  const setOpen = useStore((state) => state.setHistoryOpen)
  const conversations = useStore((state) => state.conversations)
  const conversationId = useStore((state) => state.conversationId)
  const openConversation = useStore((state) => state.openConversation)
  const deleteConversation = useStore((state) => state.deleteConversation)

  if (!open) return null

  return (
    <div className="fixed inset-0 z-40 flex" role="dialog" aria-modal="true" aria-label="Conversation history">
      <button
        type="button"
        aria-label="Close history"
        onClick={() => setOpen(false)}
        className="absolute inset-0 bg-black/40 backdrop-blur-[1px]"
      />

      <div className="relative z-10 flex h-full w-80 max-w-[85vw] flex-col border-r border-hairline bg-surface shadow-pop">
        <div className="flex h-11 shrink-0 items-center gap-2 border-b border-hairline px-3">
          <MessageSquare className="h-4 w-4 text-ink-faint" aria-hidden="true" />
          <span className="text-[0.8125rem] font-semibold text-ink">Conversations</span>
          <span className="ml-1 font-mono text-2xs text-ink-faint">{conversations.length}</span>
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Close"
            className="focus-ring ml-auto rounded-md p-1 text-ink-faint hover:bg-sunken hover:text-ink-soft"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="scrollbar-thin flex-1 overflow-y-auto p-2">
          {conversations.length === 0 && (
            <p className="px-2 py-6 text-center text-xs text-ink-faint">
              No saved conversations yet.
            </p>
          )}

          <ul className="space-y-0.5">
            {conversations.map((conversation) => (
              <li key={conversation.id} className="group relative">
                <button
                  type="button"
                  onClick={() => void openConversation(conversation.id)}
                  className={clsx(
                    'focus-ring w-full rounded-card border px-2.5 py-2 pr-8 text-left transition-colors',
                    conversation.id === conversationId
                      ? 'border-accent/50 bg-accent/5'
                      : 'border-transparent hover:border-hairline hover:bg-sunken/70',
                  )}
                >
                  <div className="truncate text-xs font-medium text-ink">{conversation.title}</div>
                  <div className="mt-0.5 flex items-center gap-2 text-2xs text-ink-faint">
                    <span>{conversation.message_count} messages</span>
                    <span>·</span>
                    <span>{relativeTime(conversation.updated_at)}</span>
                  </div>
                </button>

                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation()
                    void deleteConversation(conversation.id)
                  }}
                  title="Delete conversation"
                  aria-label={`Delete ${conversation.title}`}
                  className="focus-ring absolute right-1.5 top-1.5 rounded p-1 text-ink-faint opacity-0 transition-opacity hover:bg-danger/10 hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}
