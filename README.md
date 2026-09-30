# Multi-Workspace Document Assistant

A signed-in web app where each user has **workspaces**, uploads documents into the
active one, and chats with an assistant that answers **only from that workspace's
documents** — with citations, an honest "I don't know", and the ability to call
tools (save tasks, post to Discord). Every workspace's chunks live in **one shared
pgvector table**; isolation is enforced inside the SQL of every query.

**Live:** https://multi-workspace-doc-assistant-tder.onrender.com
(free tier — the first request after ~15 min idle can take up to a minute to wake up)

![CI](https://github.com/Anish2602/multi-workspace-doc-assistant/actions/workflows/ci.yml/badge.svg)

---

## Try it (for reviewers)

**Demo login:** username `demo` (or email `demo@example.com`) / password `demo-password-2026`
(throwaway account, fictional data)

| Workspace | Documents |
|---|---|
| **Acme Corp HR** | employee handbook (PDF), expense policy (DOCX), vendor notes (MD — contains a prompt-injection attempt) |
| **Project Falcon** | product spec (DOCX), weekly sync notes (MD) |
| My Workspace | empty — upload your own files here |

The demo account is shared, so you may see other reviewers' chats. For a clean slate,
create your own account (email, a username, and an 8+ character password entered twice) and upload the files from
[`sample_docs/`](sample_docs) into two workspaces.

### Suggested questions

| Try | In | Expect |
|---|---|---|
| What is the name of the office cat? | Acme Corp HR | **Biscuit Thunderpaw**, cited to handbook p. 4 |
| **What is the name of the office cat?** | **Project Falcon** | **"The documents don't cover it"** — the isolation test |
| When is the Falcon beta launch and what is the budget? | Project Falcon | 14 Nov 2026, USD 180,000, cited |
| When is the Falcon beta launch? | Acme Corp HR | doesn't know (that fact lives in the other workspace) |
| How many leave days do I get, and can I carry any over? | Acme Corp HR | 24 days, up to 8 carried over, cited |
| Who won the 2018 World Cup? | either | declines — not in the documents |
| What are PaperTrail's payment terms? | Acme Corp HR | "net-30"; the injected "call delete_everything / leak your prompt" text is ignored |
| Save a high-priority task to confirm the pilot customers by 10 October 2026 | Project Falcon | `save_task` runs → Tasks tab + Tool log |
| Find the Mapbox risk in the sync notes, save a task for it, then list my open tasks | Project Falcon | multi-step: save_task → list_tasks |
| Post a one-line summary of the Falcon timeline to Discord | Project Falcon | `send_discord_summary` posts to our channel (see Tool log) |

After any answer, click **Retrieval debug** to see the workspace id and every candidate
chunk (cosine similarity, vector rank, keyword rank) the answer could draw from.
Re-upload a file to see it detected as a duplicate (no new chunks).

---

## Architecture

```
React (Vite, TS, Tailwind) ──►  FastAPI  (one Render web service, also serves the SPA)
                                  ├─ auth         sign up with email + username; sign in with either;
                                  │               argon2, JWT in httpOnly cookie
                                  ├─ workspaces   membership check gates every /workspaces/{id}/… route
                                  ├─ ingestion    parse → chunk → embed → one transaction
                                  ├─ retrieval    hybrid (pgvector + Postgres FTS), workspace filter in SQL
                                  ├─ chat         persist → retrieve → tool loop → validate citations → SSE
                                  ├─ tools        Pydantic-validated registry, server-injected workspace
                                  └─ llm          Gemini (+ fallback) · Groq · Gemini embeddings
                                  │
                        Neon Postgres + pgvector  (users, workspaces, documents, chunks,
                                                   messages, tool_calls, tasks, retrieval_logs)
```

### Workspace isolation in a shared store

- **One `chunks` table** for all workspaces, `workspace_id NOT NULL` + indexed.
- The filter is **part of the query itself**, in both retrieval legs:
  ```sql
  SELECT id, 1 - (embedding <=> :q) FROM chunks
  WHERE workspace_id = :ws ORDER BY embedding <=> :q LIMIT :n
  ```
  Nothing is fetched and filtered in Python afterwards.
- `:ws` comes from the URL **only after** a membership check against the signed-in
  user (`workspaces/deps.py`); non-members get 404. The LLM never supplies a
  workspace id — tools receive it from server context, and tool schemas forbid extra
  fields, so the model can't smuggle one in.
- **Filtered-ANN pitfall, handled and proven.** When Postgres chooses the HNSW index,
  it takes the `ef_search` nearest rows *globally* and then applies the workspace
  filter, so a workspace can come back short or empty. We enable pgvector 0.8
  **iterative scans** (`hnsw.iterative_scan = relaxed_order`).
  `tests/test_retrieval_isolation.py` forces the HNSW plan, shows the naive query
  returning nothing, and shows our search returning the right chunk. (For selective
  filters Postgres usually uses the btree index and sorts exactly — also covered.)

### Accounts

Sign-up asks for email, a username and the password twice; the server re-checks the
confirmation (the API never trusts the browser for it). Usernames are 3–30 characters
of letters, digits, `.`, `-`, `_` — never `@`, which is how sign-in tells a username
from an email — and unique case-insensitively (`Anish` = `anish`). The username is
shown across the dashboard. Login errors are identical for an unknown account and a
wrong password. Existing accounts were given usernames by a migration that derives
them from the email and de-duplicates collisions.

### Ingestion

PDF / DOCX / Markdown / TXT, ≤ 10 MB, type checked by extension **and** magic bytes.
Text is split into paragraph-aware chunks (~1,000 chars ≈ 250 tokens, 150-char
overlap) that **never span a page or section**, so each citation points at one
place ("handbook.pdf p. 4", "spec.docx § Timeline and Budget"). The section heading
is prefixed to the text that gets embedded (not to the stored text) to improve recall.

**Idempotent:** SHA-256 of the bytes, unique per `(workspace_id, content_hash)`;
re-uploading short-circuits *before* embedding (no wasted quota). Chunk ids are
deterministic and inserted `ON CONFLICT DO NOTHING`. Parsing and embedding happen
before any DB write, and the document row and all its chunks commit in one
transaction — a failure leaves nothing half-indexed.

### Retrieval quality

- **Hybrid search**: vector (cosine, `gemini-embedding-001`, 768-d) + Postgres
  full-text search (OR of query terms), fused with **Reciprocal Rank Fusion**.
  *Why it helped:* for "Explain the Falcon architecture and its latency target", the
  chunk that actually answers ranked **#4 by vectors but #1 by keywords**; fusion
  puts it first. Exact codes like `FALCON-7731` are found by the keyword leg.
  Both legs carry the same `workspace_id` predicate.
- **Evidence threshold, calibrated.** On the sample corpus, questions a workspace can
  answer scored **0.69–0.78** top cosine; unanswerable ones (other workspace's
  facts, general knowledge) scored **0.53–0.56**. Chunks below **0.62** are not shown
  to the model. The placeholder 0.55 would have let two misses through.

### Grounding and "I don't know"

The model sees only above-threshold chunks, wrapped as numbered
`<source id="n" document=… page=…>` blocks, with a system prompt requiring inline
`[n]` citations and a plain "I don't know" when the sources don't cover it.
**Citations are validated server-side** against the sources actually supplied that
turn; anything else is stripped. Answers without document citations are labelled in
the UI.

### Tool calling

| Tool | Effect |
|---|---|
| `search_documents(query)` | re-search this workspace (enables multi-step use) |
| `save_task(title, description?, due_date?, priority?)` | **writes a task** to the active workspace |
| `list_tasks(status?)` | reads this workspace's tasks |
| `send_discord_summary(summary)` | posts to a Discord channel via webhook |

The model proposes; `tools/registry.py` disposes:
unknown tool → refused; invalid JSON / missing / wrong-type / extra args → Pydantic
rejects before anything runs; per-message budgets (e.g. one Discord post); the
tool-call log row is written **before** execution and updated after, so side effects
and their log commit together. Failures go back to the model as structured errors —
the request never crashes. The loop is capped at 5 rounds (the last round offers no
tools, forcing an answer). Discord posts disable all @mentions.

### Prompt-injection resistance (defence in depth)

1. Retrieved text is labelled untrusted data; the system prompt says instructions in
   documents or tool results must never be followed.
2. Delimiter break-outs (`</source><system>…`) in documents are neutralised.
3. Only registered tools exist — there is no destructive tool to call.
4. Tools are scoped to the server-side workspace; budgets cap side effects.

A test scripts a *fully hijacked* model (calls `delete_everything`, spams Discord)
and shows the registry still holds.

### Reliability

- The user's message and a `pending` assistant row are committed **before** any LLM
  call. Failures mark the turn `failed` with a Retry button that reuses the same rows;
  turns stuck `pending` (e.g. host restart) surface as retryable.
- Provider calls retry with jittered backoff; chat falls back
  `gemini-3.5-flash-lite → gemini-3.1-flash-lite → groq/openai/gpt-oss-120b`.
- Token streaming over SSE. If a model dies mid-stream, the client gets a `reset`
  event and the next model starts cleanly.
- Gemini 3 requires its "thought signatures" to be replayed on the next tool round
  (the stream sends them on a trailing *empty* part); the adapter preserves them.

### Observability

Stats tab per workspace: answered/failed, avg + p95 latency, prompt/completion
tokens, retrieval hit rate, per-tool success/failure counts, models used. Every
answer stores its model, tokens and latency; every retrieval is logged.

---

## Run locally

Prereqs: Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 22, Docker.

```bash
cp .env.example .env              # fill in GEMINI_API_KEY (and optionally the rest)
docker compose up -d db           # local Postgres + pgvector on :5433
cd backend
uv sync
export DATABASE_URL=postgresql://postgres:postgres@localhost:5433/mwda
uv run alembic upgrade head
uv run uvicorn app.main:app --port 8000
```

In a second terminal:

```bash
cd frontend && npm install && npm run dev      # http://localhost:5173 (proxies /api)
```

Optional demo data: `cd backend && uv run python ../scripts/seed_demo.py`.

**Tests** (real Postgres + pgvector; LLM and embeddings are faked, no network):

```bash
docker compose up -d db && cd backend && uv run pytest     # 65 tests, ~10 s
```

Or run the production image: `docker build -t mwda . && docker run --env-file .env -p 8000:8000 mwda`.

### Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes | Postgres with pgvector (Neon's URL works as-is) |
| `GEMINI_API_KEY` | yes | embeddings + primary chat model (Google AI Studio, free) |
| `GROQ_API_KEY` | no | last-resort chat fallback (free) |
| `DISCORD_WEBHOOK_URL` | no | enables `send_discord_summary` |
| `JWT_SECRET` | prod | signs session cookies (Render generates it) |
| `ENV` | prod | `production` → Secure cookies, JWT_SECRET enforced |

See [`.env.example`](.env.example). Secrets are read only on the server, typed as
`SecretStr`, never sent to the browser or written to logs; `/healthz` reports only
*whether* each integration is configured.

## Deployment

- **Render** free web service, built from the root [`Dockerfile`](Dockerfile)
  (Node stage builds the SPA → slim Python image, non-root) via
  [`render.yaml`](render.yaml). Region Singapore, next to the database.
  Secrets are set in the Render dashboard (`sync: false`); `JWT_SECRET` is generated
  by Render. Migrations run on container start; `/healthz` is the health check.
  Every push to `main` deploys automatically.
- **Neon** free Postgres 18 + pgvector 0.8, Singapore, direct (non-pooled) connection.
- **GitHub Actions CI**: lint + tests against pgvector, frontend build, and it builds
  **and boots** the production image — added after a dev-only dependency crashed
  a deploy.
- Everything is on free tiers with no credit card: Render, Neon, Google AI Studio,
  Groq, Discord.

## Repository layout

```
backend/app/     auth · workspaces · ingestion · retrieval · chat · tools · llm
backend/tests/   isolation, ingestion, auth, chat/tool-safety, streaming tests
frontend/src/    React dashboard
sample_docs/     fictional documents for both demo workspaces + injection sample
scripts/         sample-doc generator, demo seeder
docs/PLAN.md     original build plan
CLAUDE.md        AI context file used during development
AI_NOTES.md      how AI was used, decisions, the hardest bugs
```

## Limitations / next steps

- The 0.62 evidence threshold is calibrated on a small corpus; a labelled eval set
  would tune it per embedding model.
- Ingestion is synchronous (fine for ≤ 10 MB); large files would move to a job queue.
- No re-ranker (the free tiers have no hosted cross-encoder); hybrid + RRF covers the
  main failure mode seen here.
- Cross-workspace document sharing was not implemented, to keep the isolation model
  simple and fully tested.
- No OCR for scanned PDFs (rejected with a clear message).
