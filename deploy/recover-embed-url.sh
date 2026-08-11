#!/usr/bin/env bash
# One-time recovery for servers that ran vm-bootstrap.sh BEFORE commit
# 8ded832 (which fixed prod.env never setting EMBED_OLLAMA_URL/OLLAMA_URL,
# causing glossary/fundamentals seeding to fail with "All connection
# attempts failed" — the api container tried localhost:11434, which
# inside its own network namespace means itself, not the ollama
# container). Safe to run more than once — the env-var append is
# idempotent, and it does NOT touch Postgres/session/Qdrant secrets.
#
# Self-updates /opt/finzorr (the checkout the running stack actually uses)
# before doing anything else, so it's safe to invoke from any directory —
# e.g. your own manual clone — not just from inside /opt/finzorr itself.
# Usage: sudo bash deploy/recover-embed-url.sh   (from any git checkout of this repo)
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo bash $0" >&2
  exit 1
fi

INSTALL_DIR="/opt/finzorr"
ENV_FILE="${INSTALL_DIR}/prod.env"
COMPOSE_FILE="${INSTALL_DIR}/deploy/docker-compose.prod.yml"

if [ ! -f "${ENV_FILE}" ]; then
  echo "No ${ENV_FILE} found — this script is only for servers that already ran vm-bootstrap.sh once." >&2
  exit 1
fi

echo "==> 0/3 Updating ${INSTALL_DIR} itself (this is the git checkout the"
echo "    running stack actually uses — NOT wherever you ran this from)"
git -C "${INSTALL_DIR}" pull --ff-only

echo "==> 1/3 Patching ${ENV_FILE} (idempotent — skips lines already present)"
grep -q '^EMBED_OLLAMA_URL=' "${ENV_FILE}" || echo "EMBED_OLLAMA_URL=http://ollama:11434" >> "${ENV_FILE}"
grep -q '^OLLAMA_URL=' "${ENV_FILE}" || echo "OLLAMA_URL=http://ollama:11434" >> "${ENV_FILE}"

echo "==> 2/3 Re-running glossary + fundamentals seeding"
cd "${INSTALL_DIR}"
export IMAGE_TAG=bootstrap  # matches the tag vm-bootstrap.sh built on first run
docker compose -f "${COMPOSE_FILE}" run --rm api python -m app.rag.ingest_corpus
docker compose -f "${COMPOSE_FILE}" run --rm api python -m app.nl2sql.jobs.refresh_fundamentals

echo "==> 3/3 Bringing up the full stack"
docker compose -f "${COMPOSE_FILE}" up -d
# NOT deploy.sh — the bootstrap tag is local-only (never pushed to GHCR),
# so deploy.sh's `docker compose pull` would correctly get denied.
./deploy/wait-healthy.sh finzorr-api 300

echo
echo "==> Recovery complete. Continue with vm-bootstrap.sh's runner-registration"
echo "    step (9/9) if you haven't already registered the self-hosted runner."
