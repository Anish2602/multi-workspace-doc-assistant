# AI_NOTES

## Tools and how I split the work

I built this with **Claude Code (Claude Opus 5.5)** as my assistant, using
[`CLAUDE.md`](CLAUDE.md) as the project context file (included exactly as used).

I drove the build from the ground up: the stack, the repo structure, the
architecture and code flow, and the dashboard layout. Claude helped me assemble it
all in one place: it wrote most of the code and tests, ran them at every step, and
explained the trade-offs as we went.

The parts an AI can't do, I did myself. I created and wired every account and key:
**Render** for hosting, **Neon** Postgres + pgvector for the database and vector
store, **Gemini** and **Groq** for the models, and a **Discord webhook** for the
notification tool. I also tested the live app myself: the demo account, a fresh
account, new workspaces, my own documents, and my own questions.

## Key decisions I made

1. **Build from scratch instead of relabelling my old project.** I had already built
   an AI document-intelligence platform, but reusing it would have hidden the parts
   this task is really about: tenant isolation, tool calling and safety.
2. **Real authentication.** Email + password, plus a username you can also sign in
   with, instead of a mock or Google login. Isolation between tenants means nothing
   if anyone can become anyone.
3. **One shared table, with isolation enforced inside the query.** Every
   workspace's chunks live in a single pgvector table, and the `workspace_id` filter
   is part of the SQL vector search itself. Nothing is filtered after the fact. If I
   ask about workspace A's documents while in workspace B, the assistant finds
   nothing and says it doesn't know. The workspace id comes from the server after a
   membership check, never from the model.
4. **Free tools only** (Neon, Render, Gemini, Groq, Discord), as the brief requires.

## The hardest bug: the app worked locally but crashed on Render

After adding the embedding client, everything passed locally: all tests were green
and the app ran. But the **Render deploy crashed on startup**. I opened the Render
deploy logs and shared them with Claude. The traceback ended in
`ModuleNotFoundError: No module named 'httpx'`.

**What went wrong:** Claude had installed `httpx` as a **test-only (dev)
dependency** and later used it in real app code. On my machine and in the tests it
was installed, so nothing failed. The production Docker image installs only runtime
dependencies, so on Render the import crashed.

**Fix:** we moved `httpx` to the runtime dependencies, rebuilt and redeployed. It was
the second "works locally, dies in production" issue, so we added a **CI job that
builds and boots the real production image** on every push. That kind of crash is
now caught before it reaches Render.

**Other issues I hit:**
- **`save_task` failed on every real call.** The task pointed at its tool-call log
  record, but that record was only written *after* the tool ran, so the database
  rejected it. A generic error handler hid the cause. Fix: write the log record
  first, then run the tool, and commit both together.
- **Gemini's newer models broke multi-step tool calls.** They return a hidden
  "thought signature" that must be sent back on the next turn. When streaming, it
  arrives on an empty trailing chunk that is easy to drop. We now keep and replay it.
- **The site "slept".** Render's free tier sleeps after about 15 minutes of
  inactivity, so the first visit showed a loading screen for about a minute. I set up
  an **UptimeRobot** check on `/healthz` every 5 minutes, so it stays awake.

## What I learned about working with AI

- **Test at every checkpoint.** I used Claude to write and pass tests at each
  iteration, so problems didn't pile up into a crash on the final day.
- **Commit after every change.** The history shows the build step by step, and any
  step can be rolled back.
- **Deploy on day 1.** I deployed a "hello world" before building features, so
  deployment problems surfaced early and nothing was rushed at the end.
- **Know the AI's limits.** It can't create accounts or keys on other platforms, so
  I handled those. It can also be confidently wrong: the model names it suggested
  from memory (e.g. `gemini-2.5-flash`) were no longer available to new keys. We
  checked which models the free keys could really use before building on them.
- **Keep secrets out of the conversation.** I once shared a screenshot that showed
  my `.env` file, so I rotated all four keys immediately. Now I only confirm *that* a
  key is set, never its value.

## With more time

- **Opt-in sharing between workspaces**, for documents or saved conversations,
  without weakening the default isolation.
- An **evaluation set** (questions, expected sources, "should refuse" cases) run in
  CI, to tune the relevance threshold and catch regressions.
- **Per-user rate limiting**, to protect the free-tier model quotas.
- A **re-ranker** on top of the hybrid search.
- **Team workspaces with roles** (owner, editor, viewer), background ingestion with
  progress for large files, and OCR for scanned PDFs.
