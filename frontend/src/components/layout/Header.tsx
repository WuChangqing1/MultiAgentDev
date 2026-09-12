import { clsx } from 'clsx'
import { Bug, History, Moon, PanelRightClose, PanelRightOpen, Plus, Settings, Sun } from 'lucide-react'
import { useStore } from '../../store/useStore'

export function Header({
  rightPanelOpen,
  onToggleRightPanel,
}: {
  rightPanelOpen: boolean
  onToggleRightPanel: () => void
}) {
  const theme = useStore((state) => state.theme)
  const toggleTheme = useStore((state) => state.toggleTheme)
  const debugMode = useStore((state) => state.debugMode)
  const setDebugMode = useStore((state) => state.setDebugMode)
  const setShowSettings = useStore((state) => state.setShowSettings)
  const setHistoryOpen = useStore((state) => state.setHistoryOpen)
  const newConversation = useStore((state) => state.newConversation)
  const health = useStore((state) => state.health)
  const models = useStore((state) => state.models)

  const local = models.find((model) => model.is_local)
  const localOk = local?.status === 'online'

  return (
    <header className="flex h-11 shrink-0 items-center gap-2 border-b border-hairline bg-surface/50 px-3">
      <button
        type="button"
        onClick={() => setHistoryOpen(true)}
        title="History"
        aria-label="Open conversation history"
        className="focus-ring rounded-md p-1.5 text-ink-faint transition-colors hover:bg-sunken hover:text-ink-soft"
      >
        <History className="h-4 w-4" aria-hidden="true" />
      </button>

      <div className="flex items-baseline gap-2">
        <span className="text-[0.8125rem] font-semibold tracking-tight text-ink">MultiAgent</span>
        <span className="hidden font-mono text-2xs text-ink-faint sm:inline">
          DeepSeek × MiniCPM5-2B
        </span>
      </div>

      <span
        className={clsx(
          'ml-1 hidden items-center gap-1.5 rounded-md border px-1.5 py-0.5 text-2xs md:inline-flex',
          localOk ? 'border-positive/40 text-positive' : 'border-caution/40 text-caution',
        )}
        title={local?.detail ?? undefined}
      >
        <span className={clsx('h-1.5 w-1.5 rounded-full', localOk ? 'bg-positive' : 'bg-caution')} />
        {localOk ? 'Local model online' : 'Local model offline — DeepSeek only'}
      </span>

      {health?.database === 'error' && (
        <span className="hidden items-center rounded-md border border-danger/40 px-1.5 py-0.5 text-2xs text-danger md:inline-flex">
          Database error
        </span>
      )}

      <div className="ml-auto flex items-center gap-0.5">
        <button
          type="button"
          onClick={newConversation}
          title="New conversation"
          aria-label="New conversation"
          className="focus-ring rounded-md p-1.5 text-ink-faint transition-colors hover:bg-sunken hover:text-ink-soft"
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
        </button>

        <button
          type="button"
          onClick={() => setDebugMode(!debugMode)}
          title="Debug mode"
          aria-pressed={debugMode}
          className={clsx(
            'focus-ring rounded-md p-1.5 transition-colors',
            debugMode ? 'bg-accent/10 text-accent' : 'text-ink-faint hover:bg-sunken hover:text-ink-soft',
          )}
        >
          <Bug className="h-4 w-4" aria-hidden="true" />
        </button>

        <button
          type="button"
          onClick={onToggleRightPanel}
          title={rightPanelOpen ? 'Hide execution panel' : 'Show execution panel'}
          aria-label="Toggle execution panel"
          className="focus-ring rounded-md p-1.5 text-ink-faint transition-colors hover:bg-sunken hover:text-ink-soft"
        >
          {rightPanelOpen ? (
            <PanelRightClose className="h-4 w-4" aria-hidden="true" />
          ) : (
            <PanelRightOpen className="h-4 w-4" aria-hidden="true" />
          )}
        </button>

        <button
          type="button"
          onClick={toggleTheme}
          title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          aria-label="Toggle colour theme"
          className="focus-ring rounded-md p-1.5 text-ink-faint transition-colors hover:bg-sunken hover:text-ink-soft"
        >
          {theme === 'dark' ? (
            <Sun className="h-4 w-4" aria-hidden="true" />
          ) : (
            <Moon className="h-4 w-4" aria-hidden="true" />
          )}
        </button>

        <button
          type="button"
          onClick={() => setShowSettings(true)}
          title="Settings"
          aria-label="Open settings"
          className="focus-ring rounded-md p-1.5 text-ink-faint transition-colors hover:bg-sunken hover:text-ink-soft"
        >
          <Settings className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
    </header>
  )
}
