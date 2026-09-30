import { useEffect, useState } from 'react'
import { api, type Stats } from '../api'

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 p-3">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-0.5 text-lg font-semibold tabular-nums">{value}</p>
      {hint && <p className="text-xs text-slate-400">{hint}</p>}
    </div>
  )
}

export default function StatsPanel({ workspaceId, version }: { workspaceId: string; version: number }) {
  const [stats, setStats] = useState<Stats | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.stats(workspaceId).then(setStats).catch((e) => setError(e.message))
  }, [workspaceId, version])

  if (error) return <p className="text-sm text-red-700">{error}</p>
  if (!stats) return <p className="text-sm text-slate-500">Loading…</p>

  const tools = Object.entries(stats.tool_calls)
  return (
    <div className="space-y-4 text-sm">
      <div className="grid grid-cols-2 gap-2">
        <Tile label="Questions answered" value={`${stats.answered}`} hint={`${stats.failed} failed`} />
        <Tile
          label="Retrieval hit rate"
          value={stats.retrieval_hit_rate == null ? '—' : `${Math.round(stats.retrieval_hit_rate * 100)}%`}
          hint="questions with evidence"
        />
        <Tile
          label="Avg latency"
          value={stats.avg_latency_ms == null ? '—' : `${(stats.avg_latency_ms / 1000).toFixed(1)}s`}
          hint={stats.p95_latency_ms == null ? undefined : `p95 ${(stats.p95_latency_ms / 1000).toFixed(1)}s`}
        />
        <Tile
          label="Tokens"
          value={(stats.prompt_tokens + stats.completion_tokens).toLocaleString()}
          hint={`${stats.prompt_tokens.toLocaleString()} in · ${stats.completion_tokens.toLocaleString()} out`}
        />
      </div>

      <div>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Tool outcomes</h3>
        {tools.length === 0 ? (
          <p className="text-slate-500">No tool calls yet.</p>
        ) : (
          <table className="w-full text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="pb-1 font-normal">Tool</th>
                <th className="pb-1 text-right font-normal">OK</th>
                <th className="pb-1 text-right font-normal">Rejected / failed</th>
              </tr>
            </thead>
            <tbody>
              {tools.map(([name, counts]) => {
                const ok = counts.success ?? 0
                const bad = Object.entries(counts).reduce((s, [k, v]) => (k === 'success' ? s : s + v), 0)
                return (
                  <tr key={name} className="border-t border-slate-100">
                    <td className="py-1.5 font-mono">{name}</td>
                    <td className="py-1.5 text-right tabular-nums text-emerald-700">{ok}</td>
                    <td className="py-1.5 text-right tabular-nums text-red-700">{bad}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {Object.keys(stats.models).length > 0 && (
        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Models used</h3>
          <ul className="space-y-1 text-xs">
            {Object.entries(stats.models).map(([m, n]) => (
              <li key={m} className="flex justify-between">
                <span className="font-mono">{m}</span>
                <span className="tabular-nums text-slate-500">{n}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
