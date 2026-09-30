import { useEffect, useState } from 'react'
import { api, type Task } from '../api'

const PRIORITY: Record<Task['priority'], string> = {
  high: 'bg-red-50 text-red-700',
  medium: 'bg-amber-50 text-amber-700',
  low: 'bg-slate-100 text-slate-600',
}

export default function Tasks({ workspaceId, version }: { workspaceId: string; version: number }) {
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.tasks(workspaceId).then(setTasks).catch((e) => setError(e.message))
  }, [workspaceId, version])

  if (error) return <p className="text-sm text-red-700">{error}</p>
  if (tasks === null) return <p className="text-sm text-slate-500">Loading…</p>
  if (tasks.length === 0)
    return (
      <p className="text-sm text-slate-500">
        No tasks yet. Ask the assistant, e.g. “Save a task to review the budget by Friday”.
      </p>
    )
  return (
    <ul className="space-y-2">
      {tasks.map((t) => (
        <li key={t.id} className="rounded-lg border border-slate-200 p-3 text-sm">
          <div className="flex items-start gap-2">
            <p className="flex-1 font-medium">{t.title}</p>
            <span className={`rounded px-1.5 py-0.5 text-xs ${PRIORITY[t.priority]}`}>{t.priority}</span>
          </div>
          {t.description && <p className="mt-1 text-xs text-slate-600">{t.description}</p>}
          <p className="mt-1.5 text-xs text-slate-400">
            {t.due_date ? `Due ${t.due_date} · ` : ''}
            {t.status} · created by the assistant (save_task)
          </p>
        </li>
      ))}
    </ul>
  )
}
