# AI_NOTES

## Tools and how the work was split

- **Claude Code** (Claude Opus 5.5, desktop app), in one long session, with
  [`CLAUDE.md`](CLAUDE.md) as the project context file (included exactly as used).
- **Split:** I set the scope and made the product and architecture calls: build from
  scratch rather than reuse my earlier document-intelligence project, real email and
  password auth instead of mock or Google login, Discord for the side-effect tool,
  DOCX support, and all-free services. I also made every account and secret, and
  reviewed each phase. Claude wrote most of the code and tests, ran them, and deployed.
- **How we worked:** plan first ([`docs/PLAN.md`](docs/PLAN.md)), deploy a
  hello-world on day 1, then build phase by phase with a commit (or several) per phase.
- **Rule for every claim about an external system:** verify it with a probe or a
  test before building on it. Claude kept a running log of every wrong turn as it
  happened (outside the repo); the ones below come from it.

## Key decisions

1. **Isolation lives in the SQL, and the workspace id never comes from the model.**
   Every retrieval query has `WHERE workspace_id = :ws` in the same statement as the
   vector `ORDER BY`. `:ws` is resolved from the URL only after a membership check.
   Tools get it from server context, and their argument schemas forbid extra fields.
   One shared table plus a tested query was preferred over row-level security, to
   keep the whole boundary in one readable place.
2. **Evidence threshold measured, not guessed.** Answerable questions scored
   0.69–0.78 top cosine; unanswerable ones scored 0.53–0.56. The cut-off is 0.62. The
   first placeholder (0.55) would have fed the model irrelevant chunks for two of the
   "should refuse" questions.
3. **Small, page- and section-bounded chunks plus hybrid retrieval.** Chunks never
   cross a page or heading, so every citation points to one place. Keyword search
   fused with RRF rescued the chunk that answered "Falcon architecture": it was #4 by
   vectors and #1 by keywords.
4. **Synchronous, all-or-nothing ingestion on a free host.** No background worker
   whose state is lost on restart. Parse and embed first, then commit the document and
   all its chunks in one transaction; a content hash makes re-uploads no-ops.

## The hardest wrong turn: the "HNSW filter pitfall" that didn't reproduce

**What the AI got wrong.** Early on, Claude explained confidently that with an HNSW
index plus a `WHERE workspace_id` filter, *a small workspace can come back with fewer
than k results or none*. On that basis it added pgvector's iterative scan and planned
it as a headline point for this document.

**How we noticed.** Rather than just asserting the fix, Claude wrote a test to prove
it. The test put 300 chunks in workspace BIG and 1 in SMALL, then ran the naive filtered
query from SMALL, expecting nothing back. It **found the chunk**, which contradicted
the claim. `EXPLAIN` showed why: for a selective filter, Postgres used the ordinary
btree index on `workspace_id` and sorted the rows exactly, so HNSW was never touched.
The pitfall is real only when the planner **chooses** HNSW, which happens for large or
unselective workspaces.

**How we fixed it.** The test now forces the HNSW plan: it drops the btree index
inside a transaction that is rolled back and disables sequential and bitmap scans. It
asserts that `EXPLAIN` uses HNSW, shows the naive query returning `[]`, and shows our
search still returning the right chunk. The docstring and README now describe the
real behaviour.

That test then turned out to be **flaky**: 3 passes, then a failure. The single SMALL
vector was orthogonal to everything else and sometimes ended up unreachable in the
HNSW graph. The fix was to give it one shared term and verify 25/25 runs. The same
episode exposed a process bug: Claude ran `pytest | tail -1 && git commit`, and the
pipe hid pytest's failure exit code, so a red test was committed. Every chained
command now uses `set -o pipefail`.

**Lesson:** a plausible explanation of database behaviour is a hypothesis; `EXPLAIN`
and a test that can fail are the evidence.

### Other AI mistakes caught (short)

- **Works locally, dies in production, twice.** A FastAPI return annotation that only
  broke once the built SPA existed (only inside Docker), and `httpx` added as a
  dev-only dependency but imported by app code. The second one crashed the Render
  deploy. After that we added a CI job that builds **and boots** the production image.
- **`save_task` failed on every real call.** The task referenced its tool-call log
  row by foreign key, but that row was only inserted after the tool ran. A catch-all
  `except` hid the cause. Fix: write the log row first, commit the side effect and the
  log together, and log unexpected errors on the server.
- **Model names from memory.** The plan assumed `gemini-2.5-flash`, which returns 404
  for new users; the newer full Flash models returned 503 on the free tier. We probed
  every candidate with a real tool call before choosing and pinning models.
- **Gemini 3 "thought signatures".** Probing showed multi-turn tool calls fail with a
  400 unless the model's raw parts are replayed. When streaming, the signature arrives
  on a trailing *empty* text part that a naive implementation would drop.
- **Demo email.** The `.test` address Claude proposed is rejected by our own email
  validation, so we switched to `example.com`.

## With more time

- A labelled eval set (questions, expected sources, should-refuse cases) run in CI,
  to tune the threshold and catch regressions in grounding.
- A cross-encoder re-ranker, if a free hosted option appears.
- Opt-in cross-workspace sharing through a separate grants table, so default
  isolation stays untouched.
- Background ingestion with progress for large files; OCR for scanned PDFs.
- Rate limiting per user on chat and uploads to protect the free-tier quotas.
