import { useEffect, useState } from 'react'
import { api, type ToolCallLog } from '../api'

const STATUS: Record<string, string> = {
  success: 'bg-emerald-50 text-emerald-700',
  running: 'bg-slate-100 text-slate-600',
  error: 'bg-red-50 text-red-700',
}

export default function ToolLog({ workspaceId, version }: { workspaceId: string; version: number }) {
  const [calls, setCalls] = useState<ToolCallLog[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)

  useEffect(() => {
    api.toolCalls(workspaceId).then(setCalls).catch((e) => setError(e.message))
  }, [workspaceId, version])

  if (error) return <p className="text-sm text-red-700">{error}</p>
  if (calls === null) return <p className="text-sm text-slate-500">Loading…</p>
  if (calls.length === 0)
    return <p className="text-sm text-slate-500">No tool calls yet in this workspace.</p>

  return (
    <div>
      <p className="mb-3 text-xs text-slate-500">
        Every tool the model asked for — including rejected ones (unknown tool, invalid arguments,
        per-message limits). Arguments are validated before anything runs.
      </p>
      <ul className="space-y-2">
        {calls.map((c) => (
          <li key={c.id} className="rounded-lg border border-slate-200 text-sm">
            <button
              onClick={() => setOpen(open === c.id ? null : c.id)}
              className="flex w-full items-center gap-2 px-3 py-2 text-left"
              aria-expanded={open === c.id}
            >
              <span className="font-mono text-xs font-medium">{c.tool_name}</span>
              <span className={`rounded px-1.5 py-0.5 text-xs ${STATUS[c.status] ?? 'bg-amber-50 text-amber-800'}`}>
                {c.status.replace('_', ' ')}
              </span>
              <span className="ml-auto text-xs text-slate-400">
                {c.latency_ms != null && `${c.latency_ms} ms · `}
                {new Date(c.created_at).toLocaleTimeString()}
              </span>
            </button>
            {open === c.id && (
              <div className="space-y-2 border-t border-slate-100 px-3 py-2 text-xs">
                <div>
                  <p className="text-slate-400">Arguments</p>
                  <pre className="mt-0.5 overflow-x-auto rounded bg-slate-50 p-2">{JSON.stringify(c.arguments, null, 2)}</pre>
                </div>
                {c.error && (
                  <div>
                    <p className="text-slate-400">Error</p>
                    <p className="mt-0.5 text-red-700">{c.error}</p>
                  </div>
                )}
                {c.result && (
                  <div>
                    <p className="text-slate-400">Result</p>
                    <pre className="mt-0.5 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2">
                      {JSON.stringify(c.result, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
