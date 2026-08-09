# finzorr — Security Review (OWASP LLM Top-10 disposition)

Reviewed against the OWASP Top 10 for LLM Applications. Each risk: what
finzorr does about it, with code references, plus honest open residuals.
Companion docs: PROJECT_PLAN.md §15 (security architecture), §20 (residuals).

| # | OWASP LLM risk | Disposition |
|---|---|---|
| LLM01 | **Prompt injection** | Layered: every untrusted input is delimiter-fenced as data-not-instructions (`app/core/untrusted.py` — web snippets, page bodies, document excerpts, recalled memories); the rag prompt explicitly forbids following in-document instructions (`app/graph/nodes/rag.py`); a deterministic + optional-LLM inbound guard screens jailbreak attempts, observe-only (`app/core/guard.py`); a 76-check injection eval gates CI at 100% (`evals/injection_eval.py`); SSRF redirect re-validation on URL reads (`app/tools_registry/web_tools.py`). |
| LLM02 | **Insecure output handling** | Model output is never executed or interpolated into queries: SQL comes only from the NL2SQL pipeline with allowlist validation + a read-only DB role + LIMIT clamping (`app/nl2sql/executor.py`); the frontend renders markdown, never raw HTML injection paths. |
| LLM03 | **Training-data poisoning** | N/A — no models are trained or fine-tuned; all models are third-party inference. Memory facts (the closest analogue) are length-capped and shape-constrained so they can't become directives (`app/memory/facts.py shape_facts`). |
| LLM04 | **Model denial of service** | Per-turn wall clock (300s), tool timeouts per family, LLM client timeouts, recursion limit 50, rate limits per user (messages + uploads), daily per-provider token budgets that shift traffic down the free chain (`app/ai/completion.py`). |
| LLM05 | **Supply chain** | Locked dependencies (`uv.lock`), pip-audit + npm audit + gitleaks in weekly CI (`.github/workflows/security-scans.yml`), pinned Docker images in the deployment plan. |
| LLM06 | **Sensitive information disclosure** | Tenant isolation for documents (Qdrant payload filter built from the authenticated session only — seam-tested in `tests/test_rag_isolation.py`); memory namespaced per user with right-to-erasure across BOTH backends; sessions/messages ownership-checked (404 for others); trace payloads scrubbed (no row data, no DSNs, image bytes excluded); LangSmith tracing OFF by default in prod (prompts would leave the box). |
| LLM07 | **Insecure plugin/tool design** | Every tool call passes schema validation before the handler runs (`app/tools_registry/dispatcher.py validate_arguments`); tools are timeout-bounded and never-raise; the code sandbox runs non-root, capability-dropped, network-none, and is OFF in prod (`app/tools_registry/code_tools.py`); GitHub MCP tools are a read-only allowlist. |
| LLM08 | **Excessive agency** | Sensitive tools require explicit human approval before executing (HITL interrupt — `app/graph/nodes/tools.py`); declines substitute an honest refusal; approvals park durably and cannot replay stale; watchlist mutations validate every action and drop invalid ones. |
| LLM09 | **Overreliance** | Grounded answers carry citations with exact locators; zero-source research REFUSES instead of fabricating; every finance answer carries a not-investment-advice line; degraded turns are tagged and honest ("I couldn't...") rather than confident guesses. |
| LLM10 | **Model theft** | N/A — no proprietary model weights; API keys live only in the server's env file (0600, never in the repo — gitleaks-gated). |

## Open residuals (tracked in PROJECT_PLAN §20)
- Guard screening is observe-only by design until false-positive rates are
  measured on real traffic; enforcement is a future flag.
- Vector-store isolation is application-enforced (the recommended Qdrant
  multitenancy pattern); DB-enforced isolation (pgvector + Postgres RLS) is
  a deliberate Phase-2 option.
- Qdrant itself runs without auth — acceptable ONLY because no port is
  published (tunnel-only ingress in the deployment plan).
- The lexical parallel-dependency guard and jailbreak pattern floor are
  deterministic, not semantic — their misses are bounded and documented.
