# Build Plan — Multi-Workspace Document Assistant

A signed-in web app where each user has workspaces; documents uploaded into a
workspace are chunked, embedded and stored in **one shared pgvector table**; an
assistant answers questions grounded only in the **active workspace's** chunks
(with citations, or an honest "I don't know") and can call validated tools.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic |
| Frontend | React + Vite + TypeScript + Tailwind, built to static files served by FastAPI |
| DB / vector store | Neon Postgres + pgvector (single `chunks` table, `workspace_id` column) |
| Embeddings | Gemini embedding model, 768 dims |
| Chat LLM | Gemini Flash (primary), Groq Llama (fallback) — both free, both tool-calling |
| Notifications | Discord channel webhook |
| Hosting | Render free web service (Docker), UptimeRobot keep-alive; HF Spaces as backup |
| Auth | Email + password (argon2/bcrypt hash), JWT in httpOnly cookie |

## Data model

| Table | Key columns |
|---|---|
| users | id, email (unique), password_hash |
| workspaces | id, name, owner_id |
| workspace_members | workspace_id, user_id |
| documents | id, workspace_id, filename, content_hash, status, chunk_count — unique (workspace_id, content_hash) |
| chunks | id (deterministic), workspace_id, document_id, content, embedding vector(768), page, section, chunk_index, tsv |
| messages | id, workspace_id, role, content, citations, status (pending/done/failed) |
| tool_calls | id, workspace_id, message_id, tool_name, args, status, result, error, latency_ms |
| tasks | id, workspace_id, title, description, due_date, priority, status |
| retrieval_logs | message_id, workspace_id, chunk_ids, scores, hit |

Every workspace-owned table: `workspace_id NOT NULL` + index.

## API

```
POST /api/auth/signup | /login | /logout     GET /api/auth/me
GET/POST /api/workspaces
POST/GET /api/workspaces/{id}/documents      DELETE /api/documents/{id}
POST /api/workspaces/{id}/chat   (SSE)       GET /api/workspaces/{id}/messages
POST /api/messages/{id}/retry
GET  /api/workspaces/{id}/tasks | /tool-calls | /stats
GET  /api/messages/{id}/retrieval
GET  /healthz
```

Every `{id}` route verifies membership of the current user first (404 otherwise).

## Pipelines

**Ingestion:** PDF/TXT/MD/DOCX, ≤10 MB → SHA-256 → skip if (workspace, hash) exists →
extract text (keep page numbers) → heading/paragraph-aware chunks (~800 tokens,
~100 overlap) → batch embed with retry → insert chunks in one transaction with
deterministic ids (`ON CONFLICT DO NOTHING`) → status ready/failed.

**Retrieval:** `WHERE workspace_id = :ws ORDER BY embedding <=> :q LIMIT k` — the
filter lives inside the vector query; `:ws` comes from the server-verified session,
never from the model. Stretch: hybrid with `tsv` full-text + RRF, same filter in both
legs. Below a similarity threshold → refuse without calling the LLM.

**Chat:** persist user message first → retrieve → prompt (sources wrapped as
untrusted data, cite `[n]`, refuse if unsupported) → tool loop (max 5 rounds) →
stream answer → validate citations against supplied sources → persist answer +
retrieval log. LLM failure: retry/backoff → Groq fallback → mark `failed`, allow retry.

**Tools:** `search_documents(query)`, `save_task(title, description?, due_date?, priority?)`,
`list_tasks(status?)`, `send_discord_summary(summary)`. Pydantic-validated;
unknown tool / bad args → structured error back to the model, logged, never crashes.
`workspace_id` is injected server-side, never a model argument. No destructive tools.

## Frontend

Login/Signup → Dashboard: workspace switcher, documents (upload/list/delete), chat
(streaming, citations, retry), tasks, tool-call log, retrieval-debug drawer, stats.

## Tests (LLM mocked)

Isolation (A's fact never retrieved in B), idempotent re-upload, tool validation
(unknown / missing / wrong-type args), prompt-injection doc, "I don't know" path,
cross-user access denied, LLM failure keeps the question.

## Demo data

Demo login `demo@docassistant.test` (password in seed/README). Workspace A "Acme
Corp HR" (contains a distinctive fact), workspace B "Project Falcon", plus a
prompt-injection sample document.

## Timeline (72h)

| Slot | Work |
|---|---|
| Day 1 AM | Scaffold, CLAUDE.md, schema, hello-world live on Render + Neon |
| Day 1 PM | Auth, workspaces, ingestion, retrieval + isolation test |
| Day 1 Eve | RAG chat with citations + refusal |
| Day 2 AM | Tool registry, loop, logging, Discord |
| Day 2 PM | React dashboard end-to-end on the live URL |
| Day 2 Eve | Hardening + tests (all quality-bar checks) |
| Day 3 AM | Stretch: debug view, streaming, hybrid, stats |
| Day 3 PM | Seed, README, AI_NOTES, final live check, submit (+buffer) |
