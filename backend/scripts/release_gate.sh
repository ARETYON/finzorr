#!/usr/bin/env bash
# The single pre-release command — chains every gate that CANNOT run in CI
# (rag_eval/grounded_eval need Qdrant+Ollama; CI has neither, and there is
# no fake-embed mode — that would measure nothing) alongside the ones that
# already gate CI, so "is this release ready" is one command, not a manual
# checklist. Exits nonzero on the FIRST failure.
#
# Usage: cd backend && ./scripts/release_gate.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

step() { echo; echo "==> $1"; }

step "mypy --strict"
uv run mypy app/ tests/ scripts/

step "ruff"
uv run ruff check app/ tests/ evals/ alembic/ scripts/

step "full test suite"
uv run pytest tests/ -q

step "routing eval (floor >=90%)"
uv run python -m evals.routing_eval

step "injection-resistance eval (gated 100%)"
uv run python -m evals.injection_eval

step "plan mechanics eval (gated 100%)"
uv run python -m evals.plan_eval

step "RAG retrieval hit-rate (local gate, needs Qdrant+Ollama, >=90%)"
uv run python -m evals.rag_eval --min-hit-rate 0.9 --judge --min-score 7

step "groundedness / citation-validity (live, needs a running provider)"
uv run python -m evals.grounded_eval

echo
echo "=============================================="
echo "  RELEASE GATE: all checks passed."
echo "=============================================="
