import { useEffect, useRef, useState, type FormEvent } from 'react'
import type { Workspace } from '../api'

type Props = {
  workspaces: Workspace[]
  active: Workspace | null
  onSelect: (id: string) => void
  onCreate: (name: string) => Promise<void>
}

export default function WorkspaceSwitcher({ workspaces, active, onSelect, onCreate }: Props) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  async function create(e: FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    try {
      await onCreate(name.trim())
      setName('')
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create workspace')
    }
  }

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex max-w-[60vw] items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50"
      >
        <span className="text-xs uppercase tracking-wide text-slate-400">Workspace</span>
        <span className="truncate">{active?.name ?? '—'}</span>
        <span className="text-slate-400">▾</span>
      </button>
      {open && (
        <div className="absolute left-0 z-20 mt-1 w-72 rounded-xl border border-slate-200 bg-white p-2 shadow-lg">
          <ul role="listbox" className="max-h-64 overflow-y-auto">
            {workspaces.map((w) => (
              <li key={w.id}>
                <button
                  role="option"
                  aria-selected={w.id === active?.id}
                  onClick={() => {
                    onSelect(w.id)
                    setOpen(false)
                  }}
                  className={`w-full truncate rounded-lg px-3 py-2 text-left text-sm ${
                    w.id === active?.id
                      ? 'bg-indigo-50 font-medium text-indigo-700'
                      : 'hover:bg-slate-50'
                  }`}
                >
                  {w.name}
                </button>
              </li>
            ))}
          </ul>
          <form onSubmit={create} className="mt-2 flex gap-2 border-t border-slate-100 pt-2">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={100}
              placeholder="New workspace name"
              className="min-w-0 flex-1 rounded-lg border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-indigo-500"
            />
            <button className="rounded-lg bg-indigo-600 px-3 text-sm font-medium text-white hover:bg-indigo-700">
              Add
            </button>
          </form>
          {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
        </div>
      )}
    </div>
  )
}
