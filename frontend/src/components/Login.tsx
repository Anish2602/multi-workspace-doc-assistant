import { useState, type FormEvent, type ReactNode } from 'react'
import { api, type User } from '../api'

// Mirrors the server rule (the server re-validates everything).
const USERNAME_RE = /^[A-Za-z0-9_.-]{3,30}$/

const inputClass =
  'mt-1 w-full rounded-lg border px-3 py-2 outline-none focus:ring-2 focus:ring-indigo-100'

function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string
  hint?: string
  error?: string | null
  children: ReactNode
}) {
  return (
    <label className="block text-sm">
      <span className="text-slate-700">{label}</span>
      {children}
      {error ? (
        <span className="mt-1 block text-xs text-red-600">{error}</span>
      ) : (
        hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>
      )}
    </label>
  )
}

export default function Login({ onAuthenticated }: { onAuthenticated: (u: User) => void }) {
  const [mode, setMode] = useState<'login' | 'signup'>('login')
  const [identifier, setIdentifier] = useState('')
  const [email, setEmail] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [touched, setTouched] = useState<Record<string, boolean>>({})
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const usernameError =
    username && !USERNAME_RE.test(username)
      ? '3–30 characters: letters, numbers, dot, dash or underscore (no spaces or @).'
      : null
  const confirmError = confirm && confirm !== password ? 'Passwords do not match.' : null
  const signupInvalid = !!usernameError || !!confirmError || password.length < 8

  function switchMode(m: 'login' | 'signup') {
    setMode(m)
    setError(null)
    setTouched({})
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (mode === 'signup' && signupInvalid) {
      setTouched({ username: true, confirm: true })
      return
    }
    setBusy(true)
    setError(null)
    try {
      const user =
        mode === 'login'
          ? await api.login(identifier.trim(), password)
          : await api.signup({
              email: email.trim(),
              username: username.trim(),
              password,
              confirm_password: confirm,
            })
      onAuthenticated(user)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
    } finally {
      setBusy(false)
    }
  }

  const border = (bad: string | null, key: string) =>
    bad && touched[key] ? 'border-red-400 focus:border-red-500' : 'border-slate-300 focus:border-indigo-500'

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-8">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-600 text-lg font-bold text-white">
            D
          </div>
          <h1 className="text-xl font-semibold text-slate-900">Document Assistant</h1>
          <p className="mt-1 text-sm text-slate-500">
            Ask questions about your workspace's documents.
          </p>
        </div>
        <form
          onSubmit={submit}
          noValidate={mode === 'signup'}
          className="space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"
        >
          <div className="grid grid-cols-2 rounded-lg bg-slate-100 p-1 text-sm">
            {(['login', 'signup'] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => switchMode(m)}
                className={`rounded-md py-1.5 font-medium ${
                  mode === m ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500'
                }`}
              >
                {m === 'login' ? 'Sign in' : 'Create account'}
              </button>
            ))}
          </div>

          {mode === 'login' ? (
            <>
              <Field label="Email or username">
                <input
                  required
                  autoComplete="username"
                  value={identifier}
                  onChange={(e) => setIdentifier(e.target.value)}
                  className={`${inputClass} border-slate-300 focus:border-indigo-500`}
                />
              </Field>
              <Field label="Password">
                <input
                  type="password"
                  required
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className={`${inputClass} border-slate-300 focus:border-indigo-500`}
                />
              </Field>
            </>
          ) : (
            <>
              <Field label="Email">
                <input
                  type="email"
                  required
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className={`${inputClass} border-slate-300 focus:border-indigo-500`}
                />
              </Field>
              <Field
                label="Username"
                hint="Shown across your dashboard. You can also sign in with it."
                error={touched.username ? usernameError : null}
              >
                <input
                  required
                  autoComplete="username"
                  maxLength={30}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  onBlur={() => setTouched((t) => ({ ...t, username: true }))}
                  aria-invalid={!!(touched.username && usernameError)}
                  className={`${inputClass} ${border(usernameError, 'username')}`}
                />
              </Field>
              <Field label="Password" hint="At least 8 characters.">
                <input
                  type="password"
                  required
                  minLength={8}
                  autoComplete="new-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className={`${inputClass} border-slate-300 focus:border-indigo-500`}
                />
              </Field>
              <Field label="Confirm password" error={touched.confirm ? confirmError : null}>
                <input
                  type="password"
                  required
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  onBlur={() => setTouched((t) => ({ ...t, confirm: true }))}
                  aria-invalid={!!(touched.confirm && confirmError)}
                  className={`${inputClass} ${border(confirmError, 'confirm')}`}
                />
              </Field>
            </>
          )}

          {error && (
            <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </p>
          )}
          <button
            disabled={busy || (mode === 'signup' && !!(email && username && password && confirm) && signupInvalid)}
            className="w-full rounded-lg bg-indigo-600 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-60"
          >
            {busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}
          </button>
        </form>
      </div>
    </main>
  )
}
