import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useStore } from './store/useStore'
import { Header } from './components/layout/Header'
import { HistoryDrawer } from './components/layout/HistoryDrawer'
import { SettingsModal } from './components/layout/SettingsModal'
import { AgentPanel } from './components/agents/AgentPanel'
import { ChatPanel } from './components/chat/ChatPanel'
import { ExecutionPanel } from './components/execution/ExecutionPanel'

const RIGHT_PANEL_KEY = 'multiagent.rightPanel'

export default function App() {
  const boot = useStore((state) => state.boot)
  const refreshHealth = useStore((state) => state.refreshHealth)
  const bootError = useStore((state) => state.bootError)

  const [rightPanelOpen, setRightPanelOpen] = useState(() => {
    try {
      return localStorage.getItem(RIGHT_PANEL_KEY) !== 'closed'
    } catch {
      return true
    }
  })

  useEffect(() => {
    void boot()
  }, [boot])

  useEffect(() => {
    try {
      localStorage.setItem(RIGHT_PANEL_KEY, rightPanelOpen ? 'open' : 'closed')
    } catch {
      /* storage unavailable */
    }
  }, [rightPanelOpen])

  return (
    <div className="flex h-full flex-col overflow-hidden bg-canvas">
      <Header
        rightPanelOpen={rightPanelOpen}
        onToggleRightPanel={() => setRightPanelOpen((value) => !value)}
      />

      {bootError && (
        <div className="flex items-center gap-2 border-b border-danger/40 bg-danger/5 px-4 py-2 text-xs text-danger">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="flex-1">{bootError}</span>
          <button
            type="button"
            onClick={() => void refreshHealth(true)}
            className="focus-ring shrink-0 rounded border border-danger/40 px-2 py-0.5 font-medium hover:bg-danger/10"
          >
            Retry
          </button>
        </div>
      )}

      <main className="flex min-h-0 flex-1">
        {/* Left rail: agent roster + model availability */}
        <div className="hidden w-56 shrink-0 flex-col border-r border-hairline bg-surface/30 lg:flex xl:w-60">
          <AgentPanel />
        </div>

        {/* Centre: conversation */}
        <div className="min-w-0 flex-1">
          <ChatPanel />
        </div>

        {/* Right rail: execution observability */}
        {rightPanelOpen && (
          <div className="hidden w-[19rem] shrink-0 xl:block 2xl:w-[21rem]">
            <ExecutionPanel />
          </div>
        )}
      </main>

      <HistoryDrawer />
      <SettingsModal />
    </div>
  )
}
