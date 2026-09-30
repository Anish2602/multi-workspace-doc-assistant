import { useEffect, useRef, useState, type DragEvent } from 'react'
import { api, type DocumentInfo } from '../api'

const ACCEPT = '.pdf,.docx,.md,.markdown,.txt'

function size(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(0)} KB` : `${(bytes / 1048576).toFixed(1)} MB`
}

type Notice = { kind: 'ok' | 'warn' | 'error'; text: string }

export default function Documents({
  workspaceId,
  version,
  onChanged,
}: {
  workspaceId: string
  version: number
  onChanged: () => void
}) {
  const [docs, setDocs] = useState<DocumentInfo[] | null>(null)
  const [uploading, setUploading] = useState<string[]>([])
  const [notices, setNotices] = useState<Notice[]>([])
  const [dragging, setDragging] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.documents(workspaceId).then(setDocs).catch((e) => setNotices([{ kind: 'error', text: e.message }]))
  }, [workspaceId, version])

  async function uploadFiles(files: FileList | File[]) {
    const list = Array.from(files)
    if (!list.length) return
    setNotices([])
    setUploading(list.map((f) => f.name))
    const results: Notice[] = []
    // Sequential: keeps us well inside the free embedding API's rate limits.
    for (const file of list) {
      try {
        const r = await api.upload(workspaceId, file)
        results.push(
          r.duplicate
            ? { kind: 'warn', text: `${file.name}: already in this workspace — nothing re-indexed.` }
            : { kind: 'ok', text: `${file.name}: indexed ${r.document.chunk_count} chunks.` },
        )
      } catch (err) {
        results.push({ kind: 'error', text: `${file.name}: ${err instanceof Error ? err.message : 'failed'}` })
      }
      setUploading((u) => u.filter((n) => n !== file.name))
    }
    setNotices(results)
    onChanged()
  }

  async function remove(doc: DocumentInfo) {
    if (!window.confirm(`Remove “${doc.filename}” and its indexed chunks from this workspace?`)) return
    try {
      await api.deleteDocument(workspaceId, doc.id)
      onChanged()
    } catch (err) {
      setNotices([{ kind: 'error', text: err instanceof Error ? err.message : 'Delete failed' }])
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault()
    setDragging(false)
    uploadFiles(e.dataTransfer.files)
  }

  return (
    <div className="space-y-4">
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => input.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
        className={`cursor-pointer rounded-xl border-2 border-dashed p-5 text-center text-sm transition ${
          dragging ? 'border-indigo-400 bg-indigo-50' : 'border-slate-300 hover:border-indigo-300'
        }`}
      >
        <p className="font-medium text-slate-700">Drop files or click to upload</p>
        <p className="mt-1 text-xs text-slate-500">PDF, DOCX, Markdown or text · up to 10 MB each</p>
        <input
          ref={input}
          type="file"
          accept={ACCEPT}
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files) uploadFiles(e.target.files)
            e.target.value = ''
          }}
        />
      </div>

      {uploading.length > 0 && (
        <p className="text-xs text-slate-500">Indexing {uploading.join(', ')}… (parse → chunk → embed)</p>
      )}
      {notices.map((n, i) => (
        <p
          key={i}
          className={`rounded-lg px-3 py-2 text-xs ${
            n.kind === 'ok' ? 'bg-emerald-50 text-emerald-800' : n.kind === 'warn' ? 'bg-amber-50 text-amber-800' : 'bg-red-50 text-red-700'
          }`}
        >
          {n.text}
        </p>
      ))}

      <div>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
          In this workspace {docs && `(${docs.length})`}
        </h3>
        {docs === null ? (
          <p className="text-sm text-slate-500">Loading…</p>
        ) : docs.length === 0 ? (
          <p className="text-sm text-slate-500">No documents yet.</p>
        ) : (
          <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
            {docs.map((d) => (
              <li key={d.id} className="flex items-center gap-3 px-3 py-2.5 text-sm">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium" title={d.filename}>
                    {d.filename}
                  </p>
                  <p className="text-xs text-slate-500">
                    {d.chunk_count} chunks · {size(d.size_bytes)} · {new Date(d.created_at).toLocaleDateString()}
                  </p>
                </div>
                <button
                  onClick={() => remove(d)}
                  className="rounded px-2 py-1 text-xs text-slate-500 hover:bg-red-50 hover:text-red-700"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
