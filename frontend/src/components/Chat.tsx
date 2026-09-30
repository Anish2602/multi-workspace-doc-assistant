import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { api, streamChat, type Message, type TurnEvent, type Workspace } from '../api'
import AnswerBody from './AnswerBody'
import RetrievalDrawer from './RetrievalDrawer'

type LiveStep = { label: string; state: 'ok' | 'warn' | 'error' }

const SUGGESTIONS = [
  'Summarise the documents in this workspace.',
  'What are the key dates mentioned?',
  'Save a task to review these documents by Friday.',
  'List my open tasks.',
]

function toolLabel(name: string, status: string): LiveStep {
  const pretty: Record<string, string> = {
    search_documents: 'Searched documents',
    save_task: 'Saved a task',
    list_tasks: 'Listed tasks',
    send_discord_summary: 'Posted to Discord',
  }
  if (status === 'success') return { label: pretty[name] ?? `Called ${name}`, state: 'ok' }
  const why = status.replace('_', ' ')
  return { label: `Tool ${name} rejected (${why})`, state: status === 'error' ? 'error' : 'warn' }
}

export default function Chat({
  workspace,
  onTurnFinished,
}: {
  workspace: Workspace
  onTurnFinished: () => void
}) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [live, setLive] = useState<LiveStep[]>([])
  const [streamText, setStreamText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [debugId, setDebugId] = useState<string | null>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const abort = useRef<AbortController | null>(null)

  const reload = () =>
    api
      .messages(workspace.id)
      .then(setMessages)
      .catch((e) => setError(e.message))

  useEffect(() => {
    reload()
    return () => abort.current?.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id])

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, live, streamText])

  async function send(text: string) {
    const question = text.trim()
    if (!question || busy) return
    setBusy(true)
    setError(null)
    setInput('')
    setLive([{ label: 'Searching this workspace…', state: 'ok' }])
    setStreamText('')
    const optimistic: Message = {
      id: `tmp-${Date.now()}`,
      role: 'user',
      content: question,
      status: 'done',
      citations: null,
      model: null,
      latency_ms: null,
      prompt_tokens: null,
      completion_tokens: null,
      reply_to_id: null,
      created_at: new Date().toISOString(),
    }
    setMessages((m) => [...m, optimistic])
    abort.current = new AbortController()
    try {
      await streamChat(
        workspace.id,
        question,
        (e: TurnEvent) => {
          if (e.type === 'retrieval') {
            setLive([
              {
                label: e.hit
                  ? `Found ${e.sources} relevant passage${e.sources === 1 ? '' : 's'}`
                  : 'No relevant passages in this workspace',
                state: e.hit ? 'ok' : 'warn',
              },
            ])
          } else if (e.type === 'tool_call') {
            setLive((steps) => [...steps, toolLabel(e.name, e.status)])
          } else if (e.type === 'token') {
            setStreamText((t) => t + e.text)
          } else if (e.type === 'reset') {
            // A model failed mid-answer; the next one starts over.
            setStreamText('')
            setLive((steps) => [...steps, { label: 'Switched to a backup model', state: 'warn' }])
          }
        },
        abort.current.signal,
      )
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        setError(err instanceof Error ? err.message : 'Chat failed')
      }
    } finally {
      // The server persisted everything (question first); the list is the source of truth.
      await reload()
      setLive([])
      setStreamText('')
      setBusy(false)
      onTurnFinished()
    }
  }

  async function retry(id: string) {
    setBusy(true)
    setLive([{ label: 'Retrying…', state: 'ok' }])
    try {
      await api.retry(workspace.id, id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Retry failed')
    } finally {
      await reload()
      setLive([])
      setBusy(false)
      onTurnFinished()
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send(input)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6">
        <div className="mx-auto max-w-3xl space-y-5">
          {messages.length === 0 && !busy && (
            <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-6 text-center">
              <h2 className="font-semibold">Ask about “{workspace.name}”</h2>
              <p className="mt-1 text-sm text-slate-500">
                Answers come only from this workspace's documents, with citations. Upload
                documents in the panel, then try:
              </p>
              <div className="mt-4 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="rounded-full border border-slate-200 px-3 py-1.5 text-xs text-slate-700 hover:border-indigo-300 hover:bg-indigo-50"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m) =>
            m.role === 'user' ? (
              <div key={m.id} className="flex justify-end">
                <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-indigo-600 px-4 py-2.5 text-sm text-white">
                  {m.content}
                </div>
              </div>
            ) : (
              <div key={m.id} className="flex">
                <div
                  className={`max-w-[92%] rounded-2xl rounded-bl-md border px-4 py-3 text-sm ${
                    m.status === 'failed'
                      ? 'border-red-200 bg-red-50 text-red-800'
                      : 'border-slate-200 bg-white'
                  }`}
                >
                  {m.status === 'pending' ? (
                    <span className="text-slate-500">Working on it…</span>
                  ) : (
                    <AnswerBody content={m.content} citations={m.citations ?? []} />
                  )}
                  <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-400">
                    {m.status === 'failed' && (
                      <button
                        onClick={() => retry(m.id)}
                        disabled={busy}
                        className="rounded-md bg-red-600 px-2.5 py-1 font-medium text-white hover:bg-red-700 disabled:opacity-60"
                      >
                        Retry
                      </button>
                    )}
                    {m.status === 'done' && (
                      <>
                        {m.citations && m.citations.length === 0 && (
                          <span
                            title="The answer doesn't cite this workspace's documents (e.g. a refusal or a tool-only action)."
                            className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500"
                          >
                            no document sources
                          </span>
                        )}
                        <button
                          onClick={() => setDebugId(m.id)}
                          className="underline decoration-dotted hover:text-indigo-600"
                        >
                          Retrieval debug
                        </button>
                        {m.latency_ms != null && <span>{(m.latency_ms / 1000).toFixed(1)}s</span>}
                        {m.model && <span>{m.model}</span>}
                      </>
                    )}
                  </div>
                </div>
              </div>
            ),
          )}

          {busy && (
            <div className="rounded-2xl rounded-bl-md border border-slate-200 bg-white px-4 py-3 text-sm">
              <ul className="space-y-1 text-xs">
                {live.map((s, i) => (
                  <li
                    key={i}
                    className={
                      s.state === 'ok'
                        ? 'text-slate-500'
                        : s.state === 'warn'
                          ? 'text-amber-700'
                          : 'text-red-700'
                    }
                  >
                    {s.state === 'ok' ? '✓' : '!'} {s.label}
                  </li>
                ))}
              </ul>
              {streamText ? (
                <div className="mt-2">
                  <AnswerBody content={streamText} citations={[]} />
                </div>
              ) : (
                <div className="mt-2 flex gap-1" aria-label="Assistant is thinking">
                  <span className="h-2 w-2 animate-bounce rounded-full bg-slate-300" />
                  <span className="h-2 w-2 animate-bounce rounded-full bg-slate-300 [animation-delay:120ms]" />
                  <span className="h-2 w-2 animate-bounce rounded-full bg-slate-300 [animation-delay:240ms]" />
                </div>
              )}
            </div>
          )}
          <div ref={bottom} />
        </div>
      </div>

      {error && (
        <p className="mx-auto w-full max-w-3xl px-4 pb-2 text-sm text-red-700">{error}</p>
      )}
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault()
          send(input)
        }}
        className="border-t border-slate-200 bg-white px-4 py-3"
      >
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            maxLength={4000}
            placeholder={`Ask about ${workspace.name}…`}
            className="max-h-40 min-h-[42px] flex-1 resize-none rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
          />
          <button
            disabled={busy || !input.trim()}
            className="h-[42px] rounded-xl bg-indigo-600 px-4 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </form>

      {debugId && (
        <RetrievalDrawer
          workspaceId={workspace.id}
          messageId={debugId}
          onClose={() => setDebugId(null)}
        />
      )}
    </div>
  )
}
