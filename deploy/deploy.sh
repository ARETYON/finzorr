#!/usr/bin/env bash
# Day-2 deploy: pull the given image tag, migrate, restart, health-check.
# Usage: ./deploy.sh <tag>          (tag = a git sha, or "latest")
# Rollback: same command with the PREVIOUS tag — this script has no
# rollback-specific branch, it just deploys whatever tag you give it.
#
# Run either locally on the server (cd /opt/finzorr && sudo ./deploy/deploy.sh <tag>)
# or by the self-hosted GitHub Actions runner (cd-prod.yml's deploy job) —
# path resolution below makes both work regardless of caller's CWD.
set -euo pipefail

if [ -z "${1:-}" ]; then
  echo "Usage: $0 <tag>" >&2
  exit 1
fi

export IMAGE_TAG="$1"
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.prod.yml"

echo "==> Deploying finzorr-backend:${IMAGE_TAG}"

echo "==> Pulling image"
docker compose -f "$COMPOSE_FILE" pull api

echo "==> Running migrations"
docker compose -f "$COMPOSE_FILE" run --rm api alembic upgrade head

echo "==> Restarting api"
docker compose -f "$COMPOSE_FILE" up -d api

echo "==> Waiting for health check"
"$SCRIPT_DIR/wait-healthy.sh" finzorr-api 300
