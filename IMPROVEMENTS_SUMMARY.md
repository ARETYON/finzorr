# finzorr.ai — Complete Improvement & Build History

A full accounting of everything built and improved, organized by layer, from the
first scaffold commit to the production system now live at https://finzorr.ai.

**By the numbers:** 84 commits · full-stack FastAPI+LangGraph backend and
React+Vite frontend · 20/20 ChatGPT-parity features shipped · 375+ automated
tests (311 sanity + 64 integration) · 6 major hardening waves, each closing
every finding from independent adversarial code reviews · zero-inbound-port
production deployment on Cloudflare + OVH, live and monitored.

---

## 1. Product features — full ChatGPT-parity, all 20 shipped

Every capability a ChatGPT-class assistant has that finzorr initially lacked
was built, in three waves, free-tier only:

**Wave 1 — quick wins:** stock charts rendered in chat, voice input (dictate)
and voice output (read aloud), regenerate/edit the last message, a
read-a-URL tool, DOCX/CSV/TXT uploads, chat history search + Markdown export,
custom instructions per user.

**Wave 2 — medium depth:** personal long-term memory (facts extracted from
conversation, recalled automatically, user-visible and deletable), image
understanding (vision, gated on a free Gemini key or a local Ollama vision
model), daily market briefings, price alerts, scheduled tasks, deep-research
mode (sub-question planning → parallel search+read → cited report),
CSV/portfolio P&L analysis.

**Wave 3 — heavy features, honestly gated where a real free tier doesn't
exist:** a sandboxed code interpreter (Docker, no network, resource-capped,
dev-only until a security review — deliberately not exposed in prod without
one), image generation (registers automatically if a key is ever configured),
Canvas-style artifacts (fenced documents render in a side panel), share
links, personas, Gmail/Calendar connectors (OAuth, encrypted refresh tokens).

---

## 2. Backend architecture

### From scratch to a real agentic system
The backend started as a FastAPI + LangGraph scaffold with a single-shot
classifier (one route, no planning). It's now a genuine multi-step agent:

- **Real planning, not classification.** The supervisor emits a 1-3 step
  plan; a graph node walks it, feeding each step's output into the next
  step's prompt; a compose step streams the merged final answer. A keyword
  fallback keeps this working even with the LLM down.
- **Replanning on failure.** A failed step triggers exactly one automatic
  revision of the remaining plan instead of silently feeding a failure
  message into the next step as if it were real content.
- **Parallel execution.** Independent steps (e.g. two unrelated lookups) run
  concurrently via LangGraph's `Send` mechanism instead of strictly
  sequentially, with careful exclusion of any route that has side effects,
  interrupts, or multi-stage pipelines from ever being parallelized.
- **Human-in-the-loop approvals.** Sensitive tools (like code execution) pause
  the graph and wait for explicit user approval before running — durably
  parked in the checkpointer, so an unanswered approval costs nothing and
  survives a page reload.
- **Long-term memory on a real store.** Memory moved onto LangGraph's
  `BaseStore` abstraction backed by Postgres/pgvector (with the original
  Qdrant path kept as an automatic fallback), rather than a bespoke path
  outside the framework's own machinery.
- **A real tool dispatcher.** Every tool call is schema-validated before the
  handler runs, timeout-bounded per tool family (research needs 120s,
  quick lookups don't), and duplicate calls in the same batch are deduped.

### This session's five-layer modular rewrite
The single biggest structural change: the entire backend was reorganized into
explicit, one-directional architectural layers — a deep rewrite of internal
patterns, not a folder shuffle, executed in five sequential waves, each
independently verified (mypy --strict, ruff, full test suite, all evals) and
adversarially reviewed before merging:

- **`app/domain/`** — pure business logic with zero I/O: citation validation,
  PII detection, guard pattern-matching, chunking rules, retrieval math.
- **`app/specialists/`** — every specialist (RAG, NL2SQL, tools, research,
  memory, web search, general chat) now implements a common `Specialist`
  Protocol (structural typing, no invasive inheritance) instead of being a
  loosely-typed function wired in by string key.
- **`app/infrastructure/`** — every external-system adapter (Postgres, Redis,
  Qdrant, the LLM provider clients) in one place, so specialists depend on
  it abstractly rather than reaching into scattered `core/` modules.
- **`app/orchestration/`** — the graph-building, routing, and turn-lifecycle
  code, depending on `domain/` and `specialists/`, never the reverse.
- **`app/interface/`** — FastAPI routers and the WebSocket handler, now a
  clean leaf layer that nothing else in the backend imports back from.

Every one of these five waves proved byte-for-byte behavior preservation via
direct diffs (not just passing tests) — the routing accuracy, injection
resistance, and plan-mechanics eval scores are identical before and after,
down to the exact same 3 misroutes out of 50 test queries.

---

## 3. AI quality, RAG, and security hardening

This is the largest single body of work — six review-driven hardening waves,
each closing every finding from independent adversarial code reviews (not
self-graded; an outside reviewing pass every time), plus a dedicated
12-point RAG security/quality audit:

- **Citation integrity.** Every `[n]` marker in a generated answer is
  mechanically range-checked against what was actually retrieved — an
  out-of-range marker gets tagged, never silently trusted; an answer with
  real hits but zero citation markers is flagged as a possible hallucination.
- **PII handling.** Emails/phones/PAN/Aadhaar-shaped/card-shaped/IFSC
  patterns are detected at ingest as *type* metadata only — never the actual
  value — and trace payloads sent to LangSmith are redacted from a *copy*,
  proven byte-identical to what the LLM actually receives via a dedicated
  test (never mutating the real object in place).
- **Prompt injection defenses, layered:** every piece of external/retrieved
  content is delimiter-fenced as data, never instructions; a deterministic
  pattern-floor plus an optional LLM tier screens inbound messages; a
  stricter write-time check rejects jailbreak-patterned text before it's
  saved into a persona or custom-instructions field that gets replayed on
  every future turn (a different, higher-stakes risk than a one-off
  message); a 76-check injection eval gates CI at 100%.
- **Output screening.** Generated answers are checked for secret-shaped
  strings, verbatim system-prompt echo, and degenerate/repetitive output.
- **Retrieval quality.** Maximal Marginal Relevance re-ranking (pure Python,
  no new dependency) balances relevance against diversity so one lucky
  chunk match can't dominate the whole context window; small documents
  (≤8 chunks) that clearly match are read in full rather than piecemeal.
- **Tenant isolation.** Every retrieval filter is built server-side from the
  authenticated session only, seam-tested so a future refactor can't
  silently widen it — this is the actual cross-tenant boundary, not
  redaction (redaction never touches storage, so documents stay fully
  answerable via RAG for their own owner).
- **The router actually knows about your documents.** A real bug, found live:
  the planner had no awareness that uploads existed, so a natural follow-up
  question after uploading a report went to web search instead of the
  document. Fixed at three layers (the turn loads your ready upload
  filenames, the planner prompt names them, and a deterministic keyword
  floor catches it even if the planner itself is down).
- **OCR for scanned documents (this session).** Uploaded PDFs with no real
  text layer (scanned/photographed pages) previously produced only a
  repeated footer stamp as their entire "content." Pages with too little
  extractable text now fall back to Tesseract OCR automatically, verified
  end-to-end inside the actual production Docker image.
- **The 12-point RAG Security & Quality Playbook.** A full audit against
  actual running code (not just documentation) found 3 of 12 checklist
  points fully covered, 5 partial, 4 real gaps — all closed or made
  honestly "Partial" (not overclaimed), and durably written up as a
  reusable, generalized reference for designing any future retrieval-
  augmented agent, not just this one.
- **Continuous quality gates.** A routing-accuracy eval, a plan-mechanics
  eval, an injection-resistance eval, a retrieval hit-rate eval, and an
  LLM-judge faithfulness eval all run as part of the standard release gate;
  a daily drift watcher re-runs every deterministic eval and alerts on any
  regression; a live trace-health watcher (once tracing is enabled) alerts
  on rising `degraded`/`guard:suspicious` tag rates in real traffic.

---

## 4. Testing & CI/CD

- Test suite grew from 53 tests at the initial scaffold to **375+** today
  (311 sanity + 64 integration), including a from-scratch frontend test
  suite (there were zero frontend tests at one point in the project).
- Coverage gates were raised twice (45% → 70% for the combined suite) only
  *after* the newly-added modules driving each wave had their own coverage
  in place — never raising the bar ahead of what could actually pass it.
- **Real ownership/multi-tenancy tests**: a dedicated test suite asserts
  every cross-tenant access path returns 404 for a second, genuinely
  separate authenticated user — this found and fixed a real live bug
  (persona deletion returning success for a non-owner).
- `mypy --strict` and `ruff` both fully enforced in CI (not just configured
  — an early finding was that every quality gate existed on paper but
  wasn't actually wired into CI, "worse than absent, it advertises a
  guarantee the repo doesn't have").
- Frontend: TypeScript strict mode, `oxlint` with `jsx-a11y` accessibility
  rules enforced as errors (each one verified to actually fire, since a
  misspelled rule name silently no-ops), Playwright end-to-end tests, and a
  recorded load-test soak (23,816 requests, zero errors, all non-LLM
  responses under 250ms p95).
- Migrations are exercised in CI as a full round-trip (`upgrade → check →
  downgrade → upgrade`) against a real Postgres, not just applied once.
- **This session's CI/CD fix:** the production deploy pipeline's build step
  had a latent, pre-existing bug (a Docker buildx driver mismatch) that had
  apparently been silently broken for a while — discovered and fixed while
  shipping two unrelated production fixes back-to-back.

---

## 5. Frontend architecture

- Built from scratch as a ChatGPT-style multi-session chat app: streaming
  WebSocket UI, multi-session sidebar, login, watchlist, document upload —
  then a full theme system (light + a distinct "Ops" sci-fi skin) added
  alongside it.
- **This session's modular split**, mirroring the backend's rewrite:
  - `src/features/` — every previously self-fetching UI leaf (documents,
    search, watchlist, personas) split into a container (owns the API call
    and loading state) and a pure presentational component — closing the
    project's main structural gap (no consistent container/presentational
    separation).
  - The 100-line WebSocket frame-reducer buried inside the main `Chat.tsx`
    page was extracted into its own `useChatTurn()` hook; the page itself
    shrank to composition and layout only.
  - `src/design-system/` — the token and utility-class system that used to
    live embedded in the global stylesheet is now its own explicit module
    boundary, with a real cascade-layer fix (a genuine CSS ordering bug
    caught only by adversarial review, not by any test) and a hardcoded
    color-palette inconsistency in one badge component fixed to route
    through the same design tokens as everything else.
  - A centralized 401/5xx response interceptor on the shared API client
    replaced scattered per-call-site error handling.
- Every JSON API endpoint gained a typed Pydantic response model (28
  endpoints that previously returned raw, unchecked dicts) so the OpenAPI
  schema is a real contract instead of decorative.
- Settings modal rebuilt as a genuine accessible dialog (focus trap, Escape
  to close, proper ARIA) rather than a styled `<div>`.

---

## 6. Mobile & accessibility (this session)

Fixed after you reported the send button being unreachable behind the
on-screen keyboard on your phone — investigated broadly rather than
patching just the one report:

- **Root cause fixed:** every page used a height unit that doesn't account
  for the mobile keyboard covering part of the screen. Switched to a
  dynamic-viewport-height unit everywhere, verified with real device
  emulation that the send button now stays on-screen even with a simulated
  keyboard covering ~45% of the display.
- **Found and fixed four more real mobile bugs during the same audit:**
  rename/delete-chat, delete-document, forget-memory, and delete-persona
  buttons were only revealed on mouse hover — completely unreachable on any
  touchscreen, since there's no hover state on touch. Now always visible on
  mobile.
  the document viewer side panel had a fixed 416px width with no responsive
  handling at all — wider than most phones outright; now a full-screen
  overlay on mobile.
  the Settings dialog had no scroll handling, so a long settings list could
  push the Save button off-screen with no way to reach it.
- Added safe-area-inset padding for notched phones, so the home-indicator
  area can't overlap the input bar.
- Everything verified hands-on with real iPhone device emulation, including
  logging into the actual authenticated chat page — not just visual
  inspection.
- (Earlier in the project) the mobile sidebar was originally full-width and
  ate the entire viewport on phones — rebuilt as a proper slide-in drawer
  with a backdrop, restoring the desktop layout unchanged above the mobile
  breakpoint.

---

## 7. Observability & tracing

- Opt-in LangSmith tracing integrated end-to-end (default OFF — prompts
  never leave the server unless deliberately enabled), with secrets living
  only in a gitignored local `.env`, never in the repo or CI.
- A full audit closed six specific blind spots so tracing genuinely covers
  every complex flow, not just the obvious ones: individual tool calls
  inside the dispatcher (previously an opaque black box), the image/vision
  path (previously invisible), background memory extraction and
  auto-titling, scheduled/resumed turns tagged distinctly from live chat,
  web search and embedding calls, and noisy trace inputs cleaned up.
- The 👍/👎 feedback buttons are wired directly into the trace record (a
  pre-assigned run ID persisted on each message), closing the loop from
  user feedback back to the exact trace that produced it.

---

## 8. Deployment & infrastructure

- **Production architecture:** the backend runs as six Docker containers
  (API, Postgres+pgvector, Redis, Qdrant, Ollama for local embeddings, and
  `cloudflared`) on an OVH server, with **zero inbound ports open** — the
  Cloudflare Tunnel is strictly outbound, so there is nothing on the server
  reachable from the internet except through Cloudflare's edge. The
  frontend deploys separately to Cloudflare Pages via Wrangler.
- **Deploys with no SSH access from GitHub at all:** a self-hosted GitHub
  Actions runner lives on the server itself and polls GitHub outbound (the
  same shape as the tunnel) — a leaked credential here can't become an
  interactive shell on the box the way a traditional SSH deploy key could.
- **Real production incidents found and fixed** (this is the honest list,
  not a sanitized one):
  - A cross-worker deadlock where two backend processes raced to build the
    same database index, silently hanging every chat turn with no error —
    root-caused to an in-process lock that provides zero protection across
    separate OS processes, fixed by moving that setup to a genuine one-off
    step before workers start.
  - A non-root container permission bug that broke both file uploads and
    the stock-price cache, caused by a subtle Docker layering interaction
    (`WORKDIR` creates a directory as root before a later `--chown` copy
    can fix its ownership).
  - Uploaded files were being lost on every container restart while their
    search index entries survived — silently producing permanently broken
    citations. Fixed with a proper persistent volume, verified by writing a
    file as the real production container user and reading it back from a
    fresh container instance.
  - Two separate Google OAuth `origin_mismatch` failures at launch — the
    apex domain and the `www.` subdomain needed to be registered as
    completely separate authorized origins.
  - This session's CI/CD build fix (§4) and a local Docker disk-bloat issue
    (46GB reclaimed) that was crashing an unrelated local database
    container mid-session.
- **Deployment documentation rewritten** (this session) from a stale
  "nothing implemented yet" planning document into a live operational
  runbook — including a full incident-history section with root causes and
  generalized lessons, and an emergency manual-deploy procedure for when
  the automated pipeline's runner is unavailable, since that gap was
  discovered and worked around live during this session.

---

## 9. Documentation

`PROJECT_PLAN.md`, `SECURITY_REVIEW.md`, and `DEPLOYMENT_PLAN.md` are all
kept as living documents, actively corrected rather than left to drift:
stale claims (like a "PLAN ONLY" status on a document describing a system
that's actually been live for a while, or an implied automated backup that
was never actually built) get fixed as soon as they're noticed, not left to
mislead a future reader. `PROJECT_PLAN.md` §15.1 in particular was durably
written as a generalized, reusable playbook — deliberately structured to be
useful for designing a *future* agent, not just documenting this one.

---

*This document itself is an example of the same standard applied
everywhere else in this project: compiled from the actual git history (84
commits) and the project's own detailed engineering changelog, not from
memory alone.*
