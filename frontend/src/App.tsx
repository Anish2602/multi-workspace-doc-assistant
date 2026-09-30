import { useEffect, useState } from 'react'
import { api, type User } from './api'
import Dashboard from './components/Dashboard'
import Login from './components/Login'

export default function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined)

  useEffect(() => {
    api.me().then(setUser).catch(() => setUser(null))
  }, [])

  if (user === undefined) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 text-sm text-slate-500">
        Loading… (the free server may take up to a minute to wake up)
      </div>
    )
  }
  if (user === null) return <Login onAuthenticated={setUser} />
  return (
    <Dashboard
      user={user}
      onLogout={async () => {
        await api.logout().catch(() => undefined)
        setUser(null)
      }}
    />
  )
}
