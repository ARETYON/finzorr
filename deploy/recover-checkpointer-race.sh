#!/usr/bin/env bash
# One-time recovery for the checkpointer-setup deadlock: with `--workers 2`,
# both uvicorn workers raced to lazily call `checkpointer.setup()` on their
# first request, each running `CREATE INDEX CONCURRENTLY IF NOT EXISTS
# checkpoints_thread_id_idx` — Postgres serialized the two, and any
# concurrently-open transaction (e.g. an unrelated /chat/sessions request
# on the same worker) blocked the CONCURRENTLY build from ever finishing.
# Every chat turn hung silently waiting on the same table lock.
#
# Fix: terminate the stuck backends, drop the half-built (invalid) index,
# run the new one-off setup_checkpointer step ONCE before workers start
# (see app/orchestration/setup_checkpointer.py), then restart. Safe to re-run.
#
# Self-updates /opt/finzorr first — safe to invoke from any directory.
# Usage: sudo bash deploy/recover-checkpointer-race.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo bash $0" >&2
  exit 1
fi

INSTALL_DIR="/opt/finzorr"
COMPOSE_FILE="${INSTALL_DIR}/deploy/docker-compose.prod.yml"

echo "==> 0/4 Updating ${INSTALL_DIR}"
git -C "${INSTALL_DIR}" pull --ff-only
cd "${INSTALL_DIR}"
export IMAGE_TAG=bootstrap  # matches the tag vm-bootstrap.sh built on first run

echo "==> 1/4 Terminating stuck backends and dropping the invalid index"
docker compose -f "${COMPOSE_FILE}" exec -T postgres psql -U finzorr -d finzorr -c "
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
WHERE datname = 'finzorr' AND pid != pg_backend_pid() AND state != 'idle';
"
docker compose -f "${COMPOSE_FILE}" exec -T postgres psql -U finzorr -d finzorr -c "
DROP INDEX CONCURRENTLY IF EXISTS checkpoints_thread_id_idx;
"

echo "==> 2/4 Stopping api (so no worker can race the setup step below)"
docker compose -f "${COMPOSE_FILE}" stop api

echo "==> 3/4 Running checkpointer setup once, single-process"
docker compose -f "${COMPOSE_FILE}" run --rm api python -m app.orchestration.setup_checkpointer

echo "==> 4/4 Restarting api"
docker compose -f "${COMPOSE_FILE}" up -d api
./deploy/wait-healthy.sh finzorr-api 300

echo
echo "==> Recovery complete. Send a chat message to confirm it actually replies now."
