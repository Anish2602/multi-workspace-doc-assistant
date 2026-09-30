import { useEffect, useState } from 'react'
import { api, type RetrievalDebug } from '../api'

const pct = (x: number | null) => (x == null ? '—' : x.toFixed(3))

/** Shows exactly which workspace and chunks an answer drew from (isolation proof). */
export default function RetrievalDrawer({
  workspaceId,
  messageId,
  onClose,
}: {
  workspaceId: string
  messageId: string
  onClose: () => void
}) {
  const [data, setData] = useState<RetrievalDebug | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.retrieval(workspaceId, messageId).then(setData).catch((e) => setError(e.message))
  }, [workspaceId, messageId])

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-30 flex justify-end bg-slate-900/30" onClick={onClose}>
      <div
        role="dialog"
        aria-label="Retrieval debug"
        onClick={(e) => e.stopPropagation()}
        className="flex h-full w-full max-w-xl flex-col bg-white shadow-xl"
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <h2 className="font-semibold">Retrieval debug</h2>
          <button onClick={onClose} className="rounded px-2 text-slate-500 hover:bg-slate-100">
            ✕
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4 text-sm">
          {error && <p className="text-red-700">{error}</p>}
          {!data && !error && <p className="text-slate-500">Loading…</p>}
          {data && (
            <>
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 rounded-lg bg-slate-50 p-3 text-xs">
                <dt className="text-slate-500">Workspace</dt>
                <dd className="font-medium">
                  {data.workspace_name}{' '}
                  <span className="font-mono text-slate-400">{data.workspace_id.slice(0, 8)}</span>
                </dd>
                <dt className="text-slate-500">Query</dt>
                <dd>{data.query}</dd>
                <dt className="text-slate-500">Mode</dt>
                <dd>{data.mode} (vector + keyword, RRF fusion)</dd>
                <dt className="text-slate-500">Top similarity</dt>
                <dd>
                  {pct(data.top_similarity)} —{' '}
                  <span className={data.hit ? 'text-emerald-700' : 'text-amber-700'}>
                    {data.hit ? 'evidence found' : 'no chunk above threshold'}
                  </span>
                </dd>
                <dt className="text-slate-500">Latency</dt>
                <dd>{data.latency_ms ?? '—'} ms</dd>
              </dl>
              <p className="mt-3 text-xs text-slate-500">
                Every candidate below came from a SQL query filtered by this workspace's id.
                Green = shown to the model as evidence.
              </p>
              <ol className="mt-3 space-y-2">
                {data.chunks.map((c, i) => (
                  <li
                    key={c.chunk_id}
                    className={`rounded-lg border p-3 ${
                      c.used ? 'border-emerald-200 bg-emerald-50/50' : 'border-slate-200'
                    }`}
                  >
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                      <span className="font-medium">
                        #{i + 1} {c.filename}
                      </span>
                      {c.page && <span className="text-slate-500">p. {c.page}</span>}
                      {c.section && <span className="text-slate-500">{c.section}</span>}
                      <span className="ml-auto font-mono text-slate-500">
                        cos {pct(c.similarity)} · vec #{c.vector_rank ?? '—'} · kw #
                        {c.keyword_rank ?? '—'}
                      </span>
                    </div>
                    <p className="mt-1.5 line-clamp-4 whitespace-pre-wrap text-xs text-slate-600">
                      {c.content}
                    </p>
                  </li>
                ))}
                {data.chunks.length === 0 && (
                  <li className="text-slate-500">No chunks in this workspace matched.</li>
                )}
              </ol>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
