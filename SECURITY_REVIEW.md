# finzorr — Security Review (OWASP LLM Top-10 disposition)

Reviewed against the OWASP Top 10 for LLM Applications. Each risk: what
finzorr does about it, with code references, plus honest open residuals.
Companion docs: PROJECT_PLAN.md §15 (security architecture), §20 (residuals),
and the **RAG Security & Quality Hardening Playbook** (§15.1) — a
generalized, risk-by-risk table (retrieval hardening, prompt injection,
access control, data security, hallucination control, context validation,
output validation, citation validation, prompt hardening, vector DB
security, monitoring, evaluation) meant to be reusable when designing any
future retrieval-augmented agent, not just finzorr-specific.

| # | OWASP LLM risk | Disposition |
|---|---|---|
| LLM01 | **Prompt injection** | Layered: every untrusted input is delimiter-fenced as data-not-instructions (`app/core/untrusted.py` — web snippets, page bodies, document excerpts, recalled memories); the rag prompt explicitly forbids following in-document instructions (`app/specialists/rag.py`); a deterministic + optional-LLM inbound guard screens jailbreak attempts, observe-only for one-off messages (`app/domain/guard.py`, `app/core/guard.py`) but write-time REJECTING (400) for stored reusable artifacts — persona `system_prompt`, `custom_instructions` (`app/interface/sharing.py`, `app/interface/auth.py`) — since a saved artifact is replayed every future turn, a different risk profile than a single message; retrieved document excerpts are separately screened at read-time and tagged `guard:doc_injection_suspected` if a fenced block itself contains override phrasing, on top of (never instead of) the fencing; a 76-check injection eval gates CI at 100% (`evals/injection_eval.py`); SSRF redirect re-validation on URL reads (`app/tools_registry/web_tools.py`). |
| LLM02 | **Insecure output handling** | Model output is never executed or interpolated into queries: SQL comes only from the NL2SQL pipeline with allowlist validation + a read-only DB role + LIMIT clamping (`app/nl2sql/executor.py`); the frontend renders markdown, never raw HTML injection paths. Generated answers are also screened observe-only for secret-shaped strings, verbatim system-prompt echo, and degenerate output (`guard.screen_output`, `app/domain/guard.py`) — tagged, never mangled, on the `rag`/`compose` node outputs. |
| LLM03 | **Training-data poisoning** | N/A — no models are trained or fine-tuned; all models are third-party inference. Memory facts (the closest analogue) are length-capped and shape-constrained so they can't become directives (`app/memory/facts.py shape_facts`). |
| LLM04 | **Model denial of service** | Per-turn wall clock (300s), tool timeouts per family, LLM client timeouts, recursion limit 50, rate limits per user (messages + uploads), daily per-provider token budgets that shift traffic down the free chain (`app/infrastructure/llm/completion.py`). |
| LLM05 | **Supply chain** | Locked dependencies (`uv.lock`), pip-audit + npm audit + gitleaks in weekly CI (`.github/workflows/security-scans.yml`), pinned Docker images in the deployment plan. |
| LLM06 | **Sensitive information disclosure** | Tenant isolation for documents (Qdrant payload filter built from the authenticated session only — seam-tested in `tests/test_rag_isolation.py`); memory namespaced per user with right-to-erasure across BOTH backends; sessions/messages ownership-checked (404 for others); PII detection at ingest (types recorded as metadata, never values — `app/domain/pii.py`) with document content and prompts PII-**redacted** in trace payloads (`redact_for_trace`, applied to a copy — never to what's stored or sent to the LLM); row data, DSNs, and image bytes fully **excluded** from traces (not merely redacted — there is no legitimate reason for them to appear at all). Access control, not redaction, is the actual cross-tenant boundary: documents remain fully answerable via RAG because redaction never touches storage or LLM calls. LangSmith tracing OFF by default in prod (prompts would leave the box). |
| LLM07 | **Insecure plugin/tool design** | Every tool call passes schema validation before the handler runs (`app/tools_registry/dispatcher.py validate_arguments`); tools are timeout-bounded and never-raise; the code sandbox runs non-root, capability-dropped, network-none, and is OFF in prod (`app/tools_registry/code_tools.py`); GitHub MCP tools are a read-only allowlist. |
| LLM08 | **Excessive agency** | Sensitive tools require explicit human approval before executing (HITL interrupt — `app/specialists/tools.py`); declines substitute an honest refusal; approvals park durably and cannot replay stale; watchlist mutations validate every action and drop invalid ones. |
| LLM09 | **Overreliance** | Grounded answers carry citations with exact locators; zero-source research REFUSES instead of fabricating; every finance answer carries a not-investment-advice line; degraded turns are tagged and honest ("I couldn't...") rather than confident guesses. Citation markers are range-checked against the actual retrieved set — an out-of-range `[n]` is tagged `citation:invalid` (`app/domain/citations.py`, wired into `rag_node` and `compose_node`); an answer with retrieved context but zero `[n]` markers at all is tagged `hallucination:no_citations` — both deterministic floors, observe-only; `evals/rag_eval.py --judge` (faithfulness axis, local release gate) and `grounded_eval.py`'s `_eval_rag` case are the periodic semantic-verification layer above the floor. |
| LLM10 | **Model theft** | N/A — no proprietary model weights; API keys live only in the server's env file (0600, never in the repo — gitleaks-gated). |

## Open residuals (tracked in PROJECT_PLAN §20)
- Guard screening is observe-only by design until false-positive rates are
  measured on real traffic; enforcement is a future flag.
- Vector-store isolation is application-enforced (the recommended Qdrant
  multitenancy pattern); DB-enforced isolation (pgvector + Postgres RLS) is
  a deliberate Phase-2 option.
- Dev Qdrant now enforces both layers for real (RAG-hardening wave, §15's
  Playbook): `QDRANT__SERVICE__API_KEY` required (`docker-compose.dev.yml`)
  AND the port is loopback-only (`127.0.0.1:6335:6333`, not network-exposed)
  — live-verified 401 unauthenticated / 200 authenticated / no non-loopback
  bind. Production Qdrant auth+network posture is DESIGNED in
  `DEPLOYMENT_PLAN.md` (tunnel-only ingress) but the deploy compose artifact
  is intentionally not yet built — a real gap, not overclaimed as closed.
- The lexical parallel-dependency guard and jailbreak/output-screening
  pattern floors (`app/domain/guard.py`) are deterministic, not semantic —
  their misses are bounded and documented. Citation-marker and
  no-citations-despite-hits checks (`app/domain/citations.py`,
  `app/specialists/rag.py`) are likewise a deterministic floor, not a
  semantic faithfulness judge — `evals/rag_eval.py --judge` is the
  periodic semantic-verification layer, run locally per release.
