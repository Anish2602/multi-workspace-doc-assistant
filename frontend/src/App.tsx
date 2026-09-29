import { useEffect, useState } from 'react'

type Health = { status: string; db: string }

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)

  useEffect(() => {
    fetch('/healthz')
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setHealth({ status: 'unreachable', db: 'unknown' }))
  }, [])

  return (
    <main className="min-h-screen bg-slate-50 text-slate-900 flex items-center justify-center p-4">
      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h1 className="text-xl font-semibold">Multi-Workspace Document Assistant</h1>
        <p className="mt-2 text-sm text-slate-600">
          API: {health ? health.status : 'checking…'} · Database: {health ? health.db : '…'}
        </p>
      </div>
    </main>
  )
}
