#!/usr/bin/env bash
# ONE-TIME setup on a fresh OVH dedicated server (or VM). Run as root:
#   git clone https://github.com/ARETYON/finzorr.git /tmp/finzorr
#   sudo bash /tmp/finzorr/deploy/vm-bootstrap.sh
#
# Installs Docker, lays out /opt/finzorr, prompts for 4 values, builds a
# bootstrap image, migrates + seeds the DB, brings the stack up, and
# installs + registers a GitHub Actions SELF-HOSTED RUNNER so that future
# deploys (cd-prod.yml) happen with zero SSH access from GitHub — the
# runner polls GitHub outbound, same shape as the cloudflared tunnel this
# stack already uses. See DEPLOYMENT_PLAN.md §3/§4/§4.5 for the full plan
# this script implements.
#
# NOTE (deliberately out of scope for this pass, flagged not silently
# skipped): nightly backups (deploy/backup.sh) are not yet implemented —
# this script does NOT install a backup cron. Add that before real launch.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo bash $0" >&2
  exit 1
fi

REPO_URL="https://github.com/ARETYON/finzorr.git"
INSTALL_DIR="/opt/finzorr"
GHCR_IMAGE="ghcr.io/aretyon/finzorr-backend"

echo "==> 1/9 Installing Docker + Compose plugin (if missing)"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

echo "==> 2/9 Installing gh CLI (if missing)"
if ! command -v gh >/dev/null 2>&1; then
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg
  chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list
  apt-get update -qq && apt-get install -y gh
fi

echo "==> 3/9 Laying out ${INSTALL_DIR}"
if [ -d "${INSTALL_DIR}/.git" ]; then
  git -C "${INSTALL_DIR}" pull --ff-only
else
  git clone "${REPO_URL}" "${INSTALL_DIR}"
fi
cd "${INSTALL_DIR}"

echo "==> 4/9 Collecting required values"
read -rsp "GROQ_API_KEY (gsk_...): " GROQ_API_KEY; echo
read -rsp "GEMINI_API_KEY (AIza...): " GEMINI_API_KEY; echo
read -rp  "GOOGLE_CLIENT_ID (....apps.googleusercontent.com): " GOOGLE_CLIENT_ID
read -rsp "TUNNEL_TOKEN (eyJ..., from the Cloudflare dashboard): " TUNNEL_TOKEN; echo

echo "==> 5/9 Writing /opt/finzorr/prod.env (permissions 600, root-owned)"
# Idempotency: if a prod.env already exists (a re-run after a partial
# failure, e.g. Postgres already initialized its volume with a password),
# REUSE its generated secrets rather than minting new ones that would no
# longer match the already-initialized data. Only the 4 user-provided
# values get refreshed on a re-run.
EXISTING_ENV="${INSTALL_DIR}/prod.env"
if [ -f "${EXISTING_ENV}" ]; then
  POSTGRES_PASSWORD="$(grep -oP '^POSTGRES_PASSWORD=\K.*' "${EXISTING_ENV}")"
  SESSION_SECRET="$(grep -oP '^SESSION_SECRET=\K.*' "${EXISTING_ENV}")"
  QDRANT_API_KEY="$(grep -oP '^QDRANT_API_KEY=\K.*' "${EXISTING_ENV}")"
else
  POSTGRES_PASSWORD="$(openssl rand -hex 24)"
  SESSION_SECRET="$(openssl rand -hex 32)"
  QDRANT_API_KEY="$(openssl rand -hex 16)"
fi
cat > "${INSTALL_DIR}/prod.env" <<EOF
APP_ENV=prod
LOG_LEVEL=INFO
DATABASE_URL=postgresql+asyncpg://finzorr:${POSTGRES_PASSWORD}@postgres:5432/finzorr
NL2SQL_RO_DATABASE_URL=postgresql+asyncpg://finzorr:${POSTGRES_PASSWORD}@postgres:5432/finzorr
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
REDIS_URL=redis://redis:6379/0
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=${QDRANT_API_KEY}
QDRANT__SERVICE__API_KEY=${QDRANT_API_KEY}
EMBED_OLLAMA_URL=http://ollama:11434
OLLAMA_URL=http://ollama:11434
LLM_PROVIDER=groq
LLM_FALLBACK_PROVIDER=gemini
GROQ_API_KEY=${GROQ_API_KEY}
GEMINI_API_KEY=${GEMINI_API_KEY}
GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
SESSION_SECRET=${SESSION_SECRET}
COOKIE_DOMAIN=.finzorr.ai
FRONTEND_ORIGIN=https://finzorr.ai
TUNNEL_TOKEN=${TUNNEL_TOKEN}
CODE_INTERPRETER=false
LANGSMITH_TRACING=false
EOF
chmod 600 "${INSTALL_DIR}/prod.env"

echo "==> 6/9 Building bootstrap image (first bring-up only — CI takes over after)"
docker build -t "${GHCR_IMAGE}:bootstrap" backend/

echo "==> 7/9 Bringing up data services, migrating, seeding"
export IMAGE_TAG=bootstrap
docker compose -f deploy/docker-compose.prod.yml up -d postgres redis qdrant ollama
echo "    waiting for postgres..."
until docker compose -f deploy/docker-compose.prod.yml exec -T postgres pg_isready -U finzorr >/dev/null 2>&1; do sleep 2; done
docker compose -f deploy/docker-compose.prod.yml run --rm api alembic upgrade head
docker compose -f deploy/docker-compose.prod.yml run --rm api python -m app.orchestration.setup_checkpointer
docker compose -f deploy/docker-compose.prod.yml exec -T ollama ollama pull nomic-embed-text:v1.5
docker compose -f deploy/docker-compose.prod.yml exec -T ollama ollama pull llama3.2:3b-instruct-fp16
docker compose -f deploy/docker-compose.prod.yml run --rm api python -m app.rag.ingest_corpus
docker compose -f deploy/docker-compose.prod.yml run --rm api python -m app.nl2sql.jobs.refresh_fundamentals

echo "==> 8/9 Starting the full stack"
docker compose -f deploy/docker-compose.prod.yml up -d
# NOT deploy.sh here — the bootstrap tag is a LOCAL-only image (never
# pushed to GHCR), so deploy.sh's `docker compose pull` would correctly
# get denied. Just wait for the already-started container to go healthy.
./deploy/wait-healthy.sh finzorr-api 300

echo "==> 9/9 Registering GitHub Actions self-hosted runner"
if ! gh auth status >/dev/null 2>&1; then
  echo "    gh is not authenticated on this server yet — completing device login now."
  echo "    (This is a one-time browser confirmation, not a new secret to manage.)"
  gh auth login --web
fi
RUNNER_DIR="${INSTALL_DIR}/actions-runner"
mkdir -p "${RUNNER_DIR}" && cd "${RUNNER_DIR}"
RUNNER_VERSION="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest | grep -oP '"tag_name": "v\K[^"]+')"
curl -fsSL -o runner.tar.gz \
  "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
tar xzf runner.tar.gz && rm runner.tar.gz
REG_TOKEN="$(gh api -X POST repos/ARETYON/finzorr/actions/runners/registration-token --jq .token)"
./config.sh --url https://github.com/ARETYON/finzorr --token "${REG_TOKEN}" --unattended --name "finzorr-prod-server" --labels "self-hosted,finzorr-prod"
./svc.sh install
./svc.sh start
cd "${INSTALL_DIR}"

echo
echo "=============================================="
echo "  Bootstrap complete."
echo "  Remaining ONE-TIME manual steps (GitHub UI):"
echo "    1. Settings > Environments > create 'production', add yourself as required reviewer"
echo "    2. Package settings for ${GHCR_IMAGE} > make it public (or the runner needs a pull token)"
echo "  Then: push to main triggers cd-prod.yml, which deploys here after your approval click."
echo "=============================================="
