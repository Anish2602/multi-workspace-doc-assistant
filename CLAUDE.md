# CLAUDE.md

Guidance for Claude Code working in this repo. Full design: `docs/PLAN.md`.

## What this is

Multi-workspace document assistant: users upload documents into workspaces,
chat with an LLM that answers only from the active workspace's documents (with
citations), and the LLM can call tools. All workspaces share ONE pgvector table.

## Non-negotiable rules

1. **Workspace isolation is a security boundary.** Every query on workspace-owned
   data filters by `workspace_id` *inside the SQL* (for vectors: in the same query
   as `ORDER BY embedding <=> ...`). Never filter retrieved rows in Python after
   the fact. Never create per-workspace tables/indexes.
2. **The workspace id comes from the server**, after checking the current user is a
   member. Never accept it from the LLM; never add it to a tool's argument schema.
3. **Retrieved document text is untrusted data.** Wrap it in source delimiters, tell
   the model it is data not instructions, and never let it change which tools exist.
4. **Tool calls are validated before execution** (Pydantic). Unknown tool or bad
   args → return a structured error to the model and log it; never raise to the user.
   No destructive tools.
5. **Persist before calling the LLM.** The user's message is saved first; LLM
   failures mark the message `failed` so it can be retried.
6. **Ingestion is idempotent**: content hash per workspace + deterministic chunk ids.
7. **No secrets in code, logs, or the frontend.** Config comes from env vars via
   `app/config.py`; `.env` is git-ignored; `.env.example` holds placeholders only.
8. **Free services only** (Gemini / Groq / Neon / Render / Discord). No paid APIs.

## Layout

- `backend/app/` — FastAPI app. One package per concern: `auth`, `workspaces`,
  `ingestion`, `retrieval`, `chat`, `tools`, `llm`, `observability`.
- `frontend/` — React + Vite + TS; built output is served by FastAPI.
- `backend/tests/` — pytest; LLM and embedding calls are mocked.

## Commands

```bash
cd backend && uv sync                      # install
cd backend && uv run uvicorn app.main:app --reload
cd backend && uv run pytest
cd backend && uv run alembic upgrade head
cd frontend && npm install && npm run dev
docker build -t mwda . && docker run --env-file .env -p 8000:8000 mwda
```

## Conventions

- Python 3.12, type hints everywhere, async SQLAlchemy 2.0 style.
- Keep modules small; business logic out of route handlers.
- Every new behaviour on the quality bar (isolation, idempotency, tool validation,
  injection, refusal) gets a test.
- Small, focused commits with descriptive messages.
