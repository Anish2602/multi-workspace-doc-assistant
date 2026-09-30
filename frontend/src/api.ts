// Thin client for the FastAPI backend. Same origin in prod and dev (Vite proxy),
// so the httpOnly session cookie is sent automatically; no tokens touch JS.

export type User = { id: string; email: string }
export type Workspace = { id: string; name: string; created_at: string }
export type DocumentInfo = {
  id: string
  filename: string
  size_bytes: number
  status: string
  chunk_count: number
  created_at: string
}
export type Citation = {
  n: number
  chunk_id: string
  document_id: string
  filename: string
  page: number | null
  section: string | null
  snippet: string
}
export type Message = {
  id: string
  role: 'user' | 'assistant'
  content: string
  status: 'pending' | 'done' | 'failed'
  citations: Citation[] | null
  model: string | null
  latency_ms: number | null
  prompt_tokens: number | null
  completion_tokens: number | null
  reply_to_id: string | null
  created_at: string
}
export type ToolCallLog = {
  id: string
  message_id: string | null
  tool_name: string
  arguments: Record<string, unknown> | null
  status: string
  result: Record<string, unknown> | null
  error: string | null
  latency_ms: number | null
  created_at: string
}
export type Task = {
  id: string
  title: string
  description: string | null
  due_date: string | null
  priority: 'low' | 'medium' | 'high'
  status: string
  created_by_tool_call_id: string | null
  created_at: string
}
export type RetrievedChunk = {
  chunk_id: string
  document_id: string
  filename: string
  page: number | null
  section: string | null
  content: string
  similarity: number | null
  vector_rank: number | null
  keyword_rank: number | null
  score: number
  used?: boolean
}
export type RetrievalDebug = {
  workspace_id: string
  workspace_name: string
  query: string
  mode: string
  hit: boolean
  top_similarity: number | null
  latency_ms: number | null
  chunks: RetrievedChunk[]
}
export type Stats = {
  messages: number
  answered: number
  failed: number
  retrieval_hit_rate: number | null
  avg_latency_ms: number | null
  p95_latency_ms: number | null
  prompt_tokens: number
  completion_tokens: number
  tool_calls: Record<string, Record<string, number>>
  models: Record<string, number>
}

export type TurnEvent =
  | { type: 'start'; user_message_id: string; message_id: string }
  | { type: 'retrieval'; hit: boolean; sources: number; top_similarity: number | null }
  | {
      type: 'tool_call'
      id: string
      name: string
      arguments: Record<string, unknown> | null
      status: string
      result: Record<string, unknown>
    }
  | { type: 'token'; text: string }
  | { type: 'reset' }
  | { type: 'answer'; message_id: string; content: string; citations: Citation[] }
  | { type: 'error'; message: string; message_id: string }

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function detailOf(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown })?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg)
  return fallback
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: 'same-origin', headers: {} }
  if (body instanceof FormData) init.body = body
  else if (body !== undefined) {
    init.body = JSON.stringify(body)
    init.headers = { 'content-type': 'application/json' }
  }
  let res: Response
  try {
    res = await fetch(`/api${path}`, init)
  } catch {
    throw new ApiError(0, 'Network error — is the server reachable?')
  }
  if (res.status === 204) return undefined as T
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(res.status, detailOf(data, `Request failed (${res.status})`))
  return data as T
}

export const api = {
  me: () => request<User>('GET', '/auth/me'),
  login: (email: string, password: string) =>
    request<User>('POST', '/auth/login', { email, password }),
  signup: (email: string, password: string) =>
    request<User>('POST', '/auth/signup', { email, password }),
  logout: () => request<void>('POST', '/auth/logout'),

  workspaces: () => request<Workspace[]>('GET', '/workspaces'),
  createWorkspace: (name: string) => request<Workspace>('POST', '/workspaces', { name }),

  documents: (ws: string) => request<DocumentInfo[]>('GET', `/workspaces/${ws}/documents`),
  upload: (ws: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ document: DocumentInfo; duplicate: boolean }>(
      'POST',
      `/workspaces/${ws}/documents`,
      form,
    )
  },
  deleteDocument: (ws: string, id: string) =>
    request<void>('DELETE', `/workspaces/${ws}/documents/${id}`),

  messages: (ws: string) => request<Message[]>('GET', `/workspaces/${ws}/messages`),
  retry: (ws: string, messageId: string) =>
    request<{ message: Message }>('POST', `/workspaces/${ws}/messages/${messageId}/retry`),
  retrieval: (ws: string, messageId: string) =>
    request<RetrievalDebug>('GET', `/workspaces/${ws}/messages/${messageId}/retrieval`),
  tasks: (ws: string) => request<Task[]>('GET', `/workspaces/${ws}/tasks`),
  toolCalls: (ws: string) => request<ToolCallLog[]>('GET', `/workspaces/${ws}/tool-calls`),
  stats: (ws: string) => request<Stats>('GET', `/workspaces/${ws}/stats`),
}

/** POST a chat message and consume the Server-Sent Events stream. */
export async function streamChat(
  ws: string,
  message: string,
  onEvent: (e: TurnEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/workspaces/${ws}/chat/stream`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ message }),
    signal,
  })
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => null)
    throw new ApiError(res.status, detailOf(data, `Chat failed (${res.status})`))
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      const line = frame.split('\n').find((l) => l.startsWith('data: '))
      if (line) onEvent(JSON.parse(line.slice(6)) as TurnEvent)
    }
  }
}
