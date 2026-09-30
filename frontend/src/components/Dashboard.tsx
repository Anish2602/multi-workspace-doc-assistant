import { useCallback, useEffect, useState } from 'react'
import { api, type User, type Workspace } from '../api'
import Chat from './Chat'
import Documents from './Documents'
import StatsPanel from './StatsPanel'
import Tasks from './Tasks'
import ToolLog from './ToolLog'
import WorkspaceSwitcher from './WorkspaceSwitcher'

const TABS = ['Documents', 'Tasks', 'Tool log', 'Stats'] as const
type Tab = (typeof TABS)[number]
const ACTIVE_KEY = 'mwda.activeWorkspace'

function readActive(): string | null {
  try {
    return localStorage.getItem(ACTIVE_KEY)
  } catch {
    return null
  }
}

export default function Dashboard({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [activeId, setActiveId] = useState<string | null>(readActive())
  const [tab, setTab] = useState<Tab>('Documents')
  // Bumped after each chat turn / upload so side panels refetch.
  const [version, setVersion] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const refresh = useCallback(() => setVersion((v) => v + 1), [])

  useEffect(() => {
    api
      .workspaces()
      .then((list) => {
        setWorkspaces(list)
        setActiveId((current) =>
          current && list.some((w) => w.id === current) ? current : (list[0]?.id ?? null),
        )
      })
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    try {
      if (activeId) localStorage.setItem(ACTIVE_KEY, activeId)
    } catch {
      /* storage unavailable: switching still works for this session */
    }
  }, [activeId])

  const active = workspaces.find((w) => w.id === activeId) ?? null

  return (
    <div className="flex h-screen flex-col bg-slate-50 text-slate-900">
      <header className="flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-4 py-2.5">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-600 text-sm font-bold text-white">
            D
          </div>
          <span className="hidden font-semibold sm:inline">Document Assistant</span>
        </div>
        <WorkspaceSwitcher
          workspaces={workspaces}
          active={active}
          onSelect={(id) => setActiveId(id)}
          onCreate={async (name) => {
            const ws = await api.createWorkspace(name)
            setWorkspaces((list) => [...list, ws])
            setActiveId(ws.id)
          }}
        />
        <div className="ml-auto flex items-center gap-3 text-sm">
          <span className="hidden text-slate-500 md:inline">{user.email}</span>
          <button
            onClick={onLogout}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-slate-700 hover:bg-slate-50"
          >
            Sign out
          </button>
        </div>
      </header>

      {error && <p className="bg-red-50 px-4 py-2 text-sm text-red-700">{error}</p>}

      {active ? (
        // key={active.id}: switching workspace remounts everything, so no state
        // (messages, streams, panels) can carry over from the previous workspace.
        <div key={active.id} className="flex min-h-0 flex-1 flex-col lg:flex-row">
          <section className="flex min-h-0 flex-1 flex-col">
            <Chat workspace={active} onTurnFinished={refresh} />
          </section>
          <aside className="flex max-h-[45vh] min-h-0 flex-col border-t border-slate-200 bg-white lg:max-h-none lg:w-[400px] lg:border-l lg:border-t-0">
            <nav className="flex border-b border-slate-200 text-sm">
              {TABS.map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`flex-1 px-2 py-2.5 font-medium ${
                    tab === t
                      ? 'border-b-2 border-indigo-600 text-indigo-700'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  {t}
                </button>
              ))}
            </nav>
            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              {tab === 'Documents' && (
                <Documents workspaceId={active.id} version={version} onChanged={refresh} />
              )}
              {tab === 'Tasks' && <Tasks workspaceId={active.id} version={version} />}
              {tab === 'Tool log' && <ToolLog workspaceId={active.id} version={version} />}
              {tab === 'Stats' && <StatsPanel workspaceId={active.id} version={version} />}
            </div>
          </aside>
        </div>
      ) : (
        <p className="p-6 text-sm text-slate-500">Loading workspaces…</p>
      )}
    </div>
  )
}
