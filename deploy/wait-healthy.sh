#!/usr/bin/env bash
# Shared health-check wait loop, factored out of deploy.sh so callers that
# already have a running container (bootstrap's local-only image, which
# can't be `docker compose pull`-ed since it was never pushed to a
# registry) don't have to go through deploy.sh's pull+migrate+up steps
# just to wait for health.
# Usage: wait-healthy.sh <container-name> [max-wait-seconds, default 300]
set -euo pipefail

CONTAINER="${1:?Usage: $0 <container-name> [max-wait-seconds]}"
MAX_WAIT="${2:-300}"

elapsed=0
while [ "${elapsed}" -lt "${MAX_WAIT}" ]; do
  status="$(docker inspect --format='{{.State.Health.Status}}' "${CONTAINER}" 2>/dev/null || echo starting)"
  if [ "${status}" = "healthy" ]; then
    echo "==> ${CONTAINER} healthy after ${elapsed}s"
    exit 0
  fi
  sleep 10
  elapsed=$((elapsed + 10))
done

echo "==> FAILED: ${CONTAINER} did not become healthy within ${MAX_WAIT}s" >&2
docker logs "${CONTAINER}" --tail 100 >&2
exit 1
