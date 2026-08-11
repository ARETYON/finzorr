# finzorr.ai — Production Deployment Plan (OVH VM + Cloudflare)

**Status: IMPLEMENTED AND LIVE** at https://finzorr.ai (frontend) and
https://api.finzorr.ai (backend). Everything in this document has been
built, deployed, and battle-tested in production — this is now both the
historical record of what was built AND the operational runbook for
running it day-to-day. §9 and §10 (new) capture every real incident hit
along the way and how to recover from each; that section only exists
because these problems actually happened in production, not as
hypothetical risk analysis.

## Reusing this playbook for a new AI agent

The architecture here — Cloudflare Tunnel for a zero-inbound-port backend,
Cloudflare Pages for the static frontend, a GitHub Actions self-hosted
runner for SSH-less CD, Docker Compose for the app stack, and a free-tier
LLM provider chain for $0 steady-state cost — is a general pattern, not
finzorr-specific. To stand up a new agent on the same pattern, this
document stays the script to follow; swap these values throughout:

| Placeholder (finzorr's actual value) | Where it shows up |
|---|---|
| `finzorr` (repo name, GHCR image name, Cloudflare Pages project name, systemd runner label) | Everywhere — `deploy/*.sh`, `.github/workflows/*.yml`, `frontend/wrangler.toml` |
| `finzorr.ai` / `api.finzorr.ai` (domain + subdomain) | Cloudflare Pages custom domain, Tunnel public hostname, `COOKIE_DOMAIN`/`FRONTEND_ORIGIN` in prod.env, Google OAuth authorized origins |
| `ARETYON/finzorr` (GitHub owner/repo) | `vm-bootstrap.sh`'s `REPO_URL`/`gh api` calls, `cd-prod.yml`'s runner registration |
| `ghcr.io/aretyon/finzorr-backend` (GHCR image path) | `docker-compose.prod.yml`, `cd-prod.yml`, `deploy.sh` |
| `/opt/<name>` (server install directory) | Every script in `deploy/` |
| Groq/Gemini as the LLM chain | `app/infrastructure/llm/` — swap providers here if the new agent needs different models; the free-tier-chain-with-graceful-degradation PATTERN is what's reusable, not these specific vendors |
| The six containers (api/postgres/redis/qdrant/ollama/cloudflared) | `docker-compose.prod.yml` — a new agent may need fewer (e.g. no vector DB if it doesn't do RAG) or more; the "no published ports, only cloudflared talks outbound" shape is what to keep |

Everything else below — the Cloudflare Tunnel setup mechanics, the
self-hosted-runner-instead-of-SSH security rationale, the incident list in
§9, the emergency manual-deploy procedure in §10 — transfers directly with
no changes.

---

## 1. Architecture overview — what runs where

There are three places where things run, and it is important to keep them
straight:

**A. Your OVH server** (a dedicated server — bare metal, not a Public
Cloud VPS; same role in this architecture either way) — runs the backend
as six Docker containers, plus a lightweight GitHub Actions self-hosted
runner (a systemd service, not a container) that lets pushes to `main`
deploy themselves — see §4.5:

| Container | What it does | Memory |
|---|---|---|
| api | The finzorr backend (FastAPI + LangGraph) | ~1.5 GB |
| postgres | The database (pgvector/pgvector:pg16 — chat history, users, long-term memory) | ~0.7 GB |
| redis | Cache, rate limits, turn locks | ~0.4 GB |
| qdrant | Vector search for document RAG | ~0.5 GB |
| ollama | TWO SMALL local models only: the embedder (nomic-embed-text, ~275 MB) and an optional 3B emergency chat model (~2 GB) | ~3 GB cap |
| cloudflared | The secure outbound connection to Cloudflare (the "tunnel") | ~0.1 GB |

Total: roughly 3–4 GB steady state → a 6–8 GB VM is comfortable.

**B. External cloud services** (nothing to install — your API just calls
them over the internet with a key, exactly like it already calls Yahoo
Finance):

| Service | Runs | You need |
|---|---|---|
| Groq (console.groq.com) | llama-3.3-70b-versatile — the PRIMARY chat model. A 70B model needs ~40 GB of GPU memory; it physically cannot run on the VM. Groq hosts it on their hardware, free tier. | A free API key |
| Google Gemini (aistudio.google.com) | gemini-2.0-flash — the automatic FALLBACK when Groq errors or hits quota. Also powers image understanding. | A free API key |
| Yahoo Finance / DuckDuckGo | Stock prices / web search — already used today, keyless. | Nothing |

Hugging Face is NOT used anywhere in this plan.

**C. Cloudflare** (already has your domain):

- **Pages** builds and hosts the frontend at `https://finzorr.ai` — it
  connects to your GitHub repo and rebuilds on every push. No server
  involved.
- **Tunnel** connects `https://api.finzorr.ai` to the api container on your
  VM. The tunnel is OUTBOUND-ONLY from the VM: **zero ports are opened to
  the internet on the server.** Nobody can reach Postgres, Redis, Qdrant, or
  even the API directly — Cloudflare is the only door, and it terminates
  HTTPS for you (no certificates to manage).

Request path: `Browser → finzorr.ai (Cloudflare Pages) → api.finzorr.ai
(Cloudflare edge) → tunnel → api container → Groq/Gemini clouds + local
containers`.

### Are Groq and Gemini really free?

Yes — both have genuine free tiers, no credit card required:

| | Groq | Gemini (AI Studio) |
|---|---|---|
| Cost / card | $0, no card | $0, no card |
| Approx. daily limit | ~1,000 requests on the 70B model | ~1,500 requests on Flash |

The app is already built for the limits: Groq exhausted → automatic retry on
Gemini → both exhausted → the tiny 3B model on the VM answers slowly instead
of erroring. The app's own daily token-budget counters shift traffic down
this chain BEFORE providers start rejecting. For a small launch this is
thousands of chat turns per day at $0; paying (Groq's paid tier is cheap)
only becomes a question if the site outgrows that.

---

## 2. Prerequisites — do these before deployment day

### 2.1 API keys and OAuth client

| Item | Status | Steps |
|---|---|---|
| Groq API key | required | console.groq.com → Sign up (easiest: "Continue with Google") → left sidebar **API Keys** → **Create API Key** → name it `finzorr-prod` → Create → copy the key (starts `gsk_...`, shown ONCE — save it now; lost keys are no problem, just create a new one) |
| Gemini API key | required | aistudio.google.com → sign in with your Google account → **Get API key** → **Create API key** → if asked for a project, pick "Create API key in new project" → copy the key (starts `AIza...`) |
| OpenRouter API key | optional (extra free fallback beyond Groq/Gemini) | openrouter.ai → Sign up → **Keys** → **Create Key** → copy — only `:free`-tagged models are needed, no billing setup required |
| Google Client ID (OAuth, for "Sign in with Google") | required — **the SAME client used in local dev works in prod**, this is not a separate credential | Google Client IDs aren't environment-scoped, only their *Authorized JavaScript origins* list is. Go to console.cloud.google.com/apis/credentials → your existing OAuth client (the one set up for local dev — see `PROJECT_PLAN.md` §1 if none exists yet) → add `https://finzorr.ai` (and `https://uat.finzorr.ai` if using UAT) to Authorized JavaScript origins, alongside `http://localhost:5173` → use that same Client ID value in prod env |
| Grok (xAI) | not supported | Not a wired provider in this codebase (only `ollama \| groq \| gemini \| openrouter \| huggingface` are); xAI has no meaningful free tier, so adding it would break the $0-cost-policy default — skip unless you explicitly want a paid exception |

### 2.4 Server specs and SSH
1. Log in to https://manager.ca.ovhcloud.com → **Dedicated Servers**
   (not Public Cloud/VPS) → click your server → the overview shows
   **RAM / vCPU / OS** (need: ≥6 GB RAM, Ubuntu or Debian). Note these
   down.
2. Confirm you can SSH in from your terminal: `ssh <user>@<server-ip>`.
   OVH emailed the credentials when the server was provisioned; add your
   SSH key if you haven't. (This SSH access is for you, one-time setup
   only — the ongoing deploy pipeline in §4.5 does NOT use SSH at all.)

### 2.5 Cloudflare tunnel token — created DURING deployment
Not needed in advance. On deployment day: Cloudflare dashboard → Zero Trust
→ Networks → Tunnels → **Create a tunnel** → name `finzorr-prod` → choose
Docker → copy the long token (starts `eyJ...`). Keep the tab open; you'll
also map the hostname there (step 4.3).

### 2.6 Optional integrations & storage (not required to deploy — add when ready)

| Item | Status | Steps |
|---|---|---|
| Tavily API key | optional (web-search upgrade) | tavily.com → Sign up → Dashboard → copy the API key → set `TAVILY_API_KEY` in prod env. Without it, DuckDuckGo/SearXNG already handle web search — nothing breaks if skipped. |
| GitHub token | optional (only for the GitHub MCP tool) | github.com → Settings → Developer settings → Personal access tokens → generate a token scoped to the repos you want the assistant to read → set `GITHUB_TOKEN` in prod env. Not needed unless you want the assistant to use the GitHub MCP integration. |
| Cloudflare R2 buckets | **not code-wired yet — creating buckets alone won't do anything** | `app/documents/storage.py::get_storage()` currently hard-returns `LocalDiskStorage` with no branch for R2 at all (verified in code — there's no `R2_*` setting to set). `PROJECT_PLAN.md` §13 calls the `DocumentStorage` interface "swappable," meaning the SEAM exists (one new class implementing it would plug in), not that R2 support is built. Until that class is written, uploads/backups stay on the VM's own disk — fine at small scale, just ask if you want the R2 backend actually implemented before launch. |

### 2.7 Monitoring (free — set up any time before or shortly after launch)

| Item | Status | Steps |
|---|---|---|
| UptimeRobot | optional, recommended, no code needed | uptimerobot.com → free account → **Add New Monitor** → HTTP(s) → URL `https://api.finzorr.ai/healthz` → sets up email alerts if the API goes down. Purely external — the `/healthz` endpoint already exists, nothing to change in the app. |
| Sentry | **not code-wired yet** | `sentry-sdk` is not imported or initialized anywhere in the backend or frontend today — this is a `PROJECT_PLAN.md` §15/16 stated intention, not shipped code. Creating a sentry.io project and copying a DSN won't do anything until the SDK is actually added (`sentry_sdk.init(dsn=..., environment=...)` at backend startup + the frontend's React SDK). Ask if you want this implemented before launch — it's a small addition, just not done yet. |

### 2.8 Local dev status (already satisfied — nothing to do here)
These were prerequisites for local development, not for the deployment
described in this document — confirmed already done, listed here only so
this checklist is complete end-to-end:
- ✅ `gh auth login` — logged in as ARETYON.
- ✅ Local dev stack (Ollama + models, Docker, uv, node) — verified ready.
Full detail (if you ever need to redo either on a fresh machine):
`PROJECT_PLAN.md` §1.4.

**Prerequisite summary: two required free keys (Groq, Gemini) + the Google
OAuth client's origins updated for prod + VM specs + working SSH — that's
the required ~10-minute path. Everything in 2.6/2.7 is optional and can be
added before, during, or after initial launch without blocking it.**

---

## 3. What was built in the repo — the deployment-infra file inventory

| File | Purpose | Status |
|---|---|---|
| `backend/Dockerfile` + `.dockerignore` | Packages the backend as a container image (python 3.12-slim, locked deps, non-root `app` user, 2 uvicorn workers, `tesseract-ocr` for scanned-PDF fallback) | ✅ Built, live |
| `deploy/docker-compose.prod.yml` | The six-container stack (§1) — pinned image versions, memory caps, named volumes (`finzorr_pgdata`, `finzorr_qdrant`, `finzorr_ollama`, `finzorr_uploads`), **no published ports** | ✅ Built, live |
| `deploy/vm-bootstrap.sh` | The ONE script run once on a fresh server — installs Docker, `gh` CLI, lays out `/opt/finzorr`, prompts for 4 values, writes `prod.env` (idempotent — reuses existing secrets on a re-run instead of minting new ones that would mismatch already-initialized data), builds a local `:bootstrap` image, migrates + seeds the DB, brings the stack up, health-checks, **and installs + registers the GitHub Actions self-hosted runner as a systemd service** | ✅ Built, ran once at launch — **does NOT install a backup cron** (explicitly out of scope, flagged in the script's own header comment, not silently skipped) |
| `deploy/deploy.sh` | Day-2 deploys: `./deploy/deploy.sh <tag>` (run from `/opt/finzorr`, or by `cd-prod.yml`'s deploy job) — pull image from GHCR, migrate, run checkpointer setup, restart, health-check. Rollback = same command with the previous tag | ✅ Built, live — **requires `IMAGE_TAG` to already exist on GHCR**, will NOT work with a local-only tag (see §10) |
| `deploy/wait-healthy.sh` | Shared health-check poll loop, factored out of `deploy.sh` so scripts with an already-running container (a local-only bootstrap image that was never `docker compose pull`-able) don't need `deploy.sh`'s pull+migrate+up steps just to wait for health | ✅ Built, live |
| `deploy/recover-embed-url.sh` | One-off historical fix for servers bootstrapped before a since-fixed bug where `prod.env` never set `EMBED_OLLAMA_URL`/`OLLAMA_URL`, breaking corpus/fundamentals seeding | ✅ Built, historical — not needed on a fresh `vm-bootstrap.sh` run today, kept for any server still on an old prod.env |
| `deploy/recover-checkpointer-race.sh` | Incident-specific recovery for the checkpointer cross-worker deadlock (§9.3) — terminates stuck DB backends, drops the half-built index, re-runs setup single-process, restarts. **Not a general-purpose deploy script** — see §9.3 before reaching for it | ✅ Built, used |
| `deploy/backup.sh` | Nightly compressed Postgres dump, 14-day rotation | ❌ **NOT BUILT** — `vm-bootstrap.sh`'s own header comment flags this explicitly. The Day-2 ops table (§6) below is honest about this being a real, currently-open gap, not a "just run this" item |
| `.github/workflows/cd-prod.yml` | Every push to `main` touching `backend/**`/`deploy/**` builds+pushes the image to GHCR (GitHub-hosted runner, `docker/setup-buildx-action` required — see §9.6), then a second job — gated by the `production` Environment's required-reviewer approval — runs on the **self-hosted runner on the server** and executes `deploy/deploy.sh <tag>`. No SSH from GitHub to the server at any point | ✅ Built, live — **but only works when the self-hosted runner is actually registered and its systemd service running; see §9.7 and §10 for what to do when it isn't** |
| `.github/workflows/ci-backend.yml` / `ci-frontend.yml` | Lint/typecheck/test/eval gates on every push+PR — not deploy infrastructure per se, but what gates what's allowed to reach `cd-prod.yml` | ✅ Built, live |
| `frontend/wrangler.toml` + Wrangler CLI (§4.2) | Cloudflare Pages project config; the frontend deploys via `wrangler pages deploy` directly, no CI step for it at all | ✅ Built, live |
| `frontend/public/_redirects` | One line so refreshing `/chat` or opening a share link directly doesn't 404 on Cloudflare Pages | ✅ Built, live |
| `frontend/src/pages/Privacy.tsx` + `Terms.tsx` | Required for Google to allow public (non-Testing-mode) OAuth login | ✅ Built, live |
| Code: multi-origin CORS | Backend accepts both `https://finzorr.ai` and `https://www.finzorr.ai` | ✅ Built, live |
| Code: uploads volume | `finzorr_uploads` volume on the `api` service — this was a **real bug that shipped and was live for a period**: uploaded PDFs were lost on every container restart while their Qdrant vectors survived, until fixed (§9.8) | ✅ Fixed, live |

Security defaults baked in: `CODE_INTERPRETER=false` (the Python sandbox
needs docker-inside-docker — a host-escape surface; stays off in prod),
`LANGSMITH_TRACING=false` (user prompts would leave the server; enable
deliberately if ever wanted), dev-login and debug routes automatically
disabled outside dev.

---

## 4. Deployment-day runbook (your steps, in order)

### 4.1 On the VM (~15 minutes, mostly waiting)
```
git clone https://github.com/ARETYON/finzorr.git /tmp/finzorr
sudo bash /tmp/finzorr/deploy/vm-bootstrap.sh
```
The script pauses four times and asks you to paste:
1. `GROQ_API_KEY` (gsk_...)
2. `GEMINI_API_KEY` (AIza...)
3. `GOOGLE_CLIENT_ID`
4. `TUNNEL_TOKEN` (eyJ..., from step 2.5)

Everything else — Docker install, database password, session secret, model
downloads, migrations, seed data — is automatic. It ends by printing a
health check and `docker compose ps`; send me that output. **Note:** this
script deliberately does NOT install a backups cron (§3, §6) — that's a
separate, still-open task, not part of this automated bring-up.

The fundamentals-seed step doubles as the **yfinance test**: if Yahoo
blocks OVH's datacenter IPs (a known risk), this step fails visibly and we
choose an alternative market-data source before launch.

### 4.2 Cloudflare Pages via Wrangler (frontend) — full reference

Using the Wrangler CLI directly (not Git-integration auto-builds) — this
is the ONLY way the frontend ships; there is no CI step for it, every
deploy in this project's history has been this exact manual command,
run from a local machine.

**Install & auth (one-time per machine, not per deploy):**
```bash
# No global install needed — npx fetches/caches it on first use.
# (Not a frontend/package.json devDependency in this project; if you'd
# rather pin a version, `npm install -D wrangler` works too.)
npx wrangler --version        # confirms it resolves; 4.x used throughout
npx wrangler login            # opens a browser, OAuth against your
                               # Cloudflare account — token cached at
                               # ~/Library/Preferences/.wrangler/config/
                               # default.toml (macOS) / equivalent per-OS
npx wrangler whoami            # confirms which account/email you're
                                # authenticated as before deploying
```

**One-time project setup:**
```bash
cd frontend
wrangler pages project create finzorr   # one-time; project name must be
                                         # globally unique per-account,
                                         # not globally across Cloudflare
echo "VITE_API_BASE_URL=https://api.finzorr.ai" >> .env.production
echo "VITE_GOOGLE_CLIENT_ID=<your client id>" >> .env.production
```
- `frontend/wrangler.toml` (checked into the repo) holds the project name
  and build output directory (`name = "finzorr"`,
  `pages_build_output_dir = "dist"`) so every `wrangler pages deploy`
  needs no flags beyond the directory itself.
- `.env.production` is gitignored (same pattern as `.env.local`) — Vite
  bakes `VITE_*` vars into the static build at `npm run build` time,
  BEFORE `wrangler.toml` is even read, so this file must exist and be
  correct before every build, not just once.
- Custom-domain binding stays a one-time **dashboard** step (Wrangler
  doesn't cleanly automate this part today): Workers & Pages → `finzorr`
  project → Custom domains → add `finzorr.ai` and `www.finzorr.ai`
  (Cloudflare wires the DNS itself — no manual DNS record creation).

**Every release — the actual command run throughout this project's life:**
```bash
cd frontend
npm run build
wrangler pages deploy dist --project-name=finzorr --commit-dirty=true
```
- `--commit-dirty=true` is needed because this repo's frontend deploys
  are run straight from a working tree that may have uncommitted-but-
  already-pushed changes relative to Pages' own git-awareness — without
  it, Wrangler warns/prompts about deploying from a "dirty" tree that
  isn't what it expects.
- Each run prints a unique preview URL
  (`https://<hash>.finzorr.pages.dev`) AND promotes to the production
  custom domain (`finzorr.ai`) in the same command — no separate
  "promote" step needed for a deploy off `main`.
- **Verifying a deploy actually landed**: don't trust the command's exit
  code alone — `curl` the live site and grep the built JS asset hash out
  of the HTML, compare it against `dist/assets/index-*.js`'s actual
  filename from the build output. This project's deploy loop always did
  exactly this:
  ```bash
  curl -s https://finzorr.ai/ | grep -o 'index-[a-zA-Z0-9]*\.js'
  curl -s -o /dev/null -w "%{http_code}\n" https://finzorr.ai/
  ```
- No GitHub push required to ship a frontend change — this command is
  the entire deploy. (Nothing stops wiring this same command into a CI
  step later for push-to-deploy, it just isn't done here — every
  frontend deploy in this project so far has been a deliberate manual
  run right after verifying the change locally.)
- **Preview URLs cannot authenticate against the real API** — they're a
  different origin (`*.finzorr.pages.dev` vs `finzorr.ai`), so the
  session cookie (scoped to `.finzorr.ai`) never reaches them and CORS
  blocks the request too. Preview URLs are for eyeballing static UI
  only; always verify real behavior against the production custom domain.

### 4.3 Cloudflare Tunnel hostname (~2 minutes)
In the tunnel you created (Zero Trust → Networks → Tunnels →
`finzorr-prod`) → Public Hostname → Add:
- Subdomain `api`, domain `finzorr.ai`
- Service: type `HTTP`, URL `api:8000`
Then from any browser: `https://api.finzorr.ai/healthz` must return
`{"status":"ok"}`.

### 4.4 Google console (~2 minutes)
console.cloud.google.com → APIs & Services → Credentials → your OAuth
client → **Authorized JavaScript origins** → add `https://finzorr.ai`.
While the consent screen stays in "Testing" mode, only Google accounts you
add as test users can log in — that's your invite-only launch for free.
Publish the consent screen (requires the Privacy page to be live) when you
want the public in.

### 4.5 Enable auto-deploys (optional, after launch)
Two GitHub-side config steps, no secrets:
1. Settings → Environments → create `production` → add yourself as
   required reviewer.
2. After the first `cd-prod.yml` run pushes an image: the repo's Packages
   tab → `finzorr-backend` → Package settings → make it **public**. The
   self-hosted runner pulls images as plain `docker`, with no registry
   login configured (deliberately — one less credential on the server);
   a public GHCR package needs no auth to pull, which is what makes that
   work. (Private is fine too if you'd rather set up a pull token later —
   just isn't done by default here.)

The self-hosted runner was already installed and
registered by `vm-bootstrap.sh` in §4.1, so `cd-prod.yml`'s deploy job can
already reach the server (it runs locally there, polling GitHub outbound —
nothing connects in). From then on: every push to `main` builds+pushes to
GHCR automatically, then waits for your approval click on the deploy job,
which runs `deploy/deploy.sh <sha>` directly on the server. Rollback is
`workflow_dispatch` on the same workflow with a previous tag/sha as input.

**This is a real single point of failure — verify it's actually running
before trusting it.** The runner is a systemd service on the server; if it
stops (crash, server reboot where it didn't re-register, manual `svc.sh
stop`, or anything else), pushes to `main` build+push to GHCR successfully
but the `deploy` job sits queued FOREVER with no error, no timeout, no
notification — it just silently never deploys. This happened in this
project's own history. Check runner status BEFORE assuming a push will
actually reach production:
```bash
gh api repos/<owner>/<repo>/actions/runners --jq '.total_count'
# 0 means nothing will pick up queued deploy jobs, no matter how long you wait
```
If it's 0, either fix the runner (SSH to the server, `sudo systemctl status
actions.runner.*` under `/opt/<name>/actions-runner`, restart or
re-register per vm-bootstrap.sh's §9/9 block) or use the manual emergency
deploy path in §10 instead of waiting on a queued job that will never run.

**Why not SSH:** an SSH deploy key would need to live in GitHub Secrets,
and if it ever leaked, it's an interactive shell on the box — full reach
to Postgres/Redis/Qdrant, not just the API. The self-hosted-runner model
needs no such credential in GitHub at all, and matches the same
outbound-only shape the Cloudflare Tunnel already uses for ingress.

---

## 5. Launch smoke-test checklist

Run through together once everything above is done:

1. `https://api.finzorr.ai/healthz` → `{"status":"ok"}`; `/readyz` → ok
2. Open `https://finzorr.ai` → page loads over HTTPS
3. Log in with Google (as a test user)
4. Send a chat message → streamed answer (served by Groq)
5. Ask a stock question ("TCS price") → chart renders (yfinance works)
6. Upload a small PDF → ask about it → cited answer (RAG + embeddings)
7. Create a share link → open it in a private/logged-out window
8. Refresh mid-conversation → history intact
9. Send messages rapidly → rate limit responds politely
10. `www.finzorr.ai` → works (multi-origin fix)
11. On the VM: `docker compose logs api --tail 50` → clean JSON logs
12. Confirm the self-hosted runner is registered: `gh api
    repos/<owner>/<repo>/actions/runners --jq '.total_count'` → 1
    (§4.5, §9.7 — this silently fails to zero over time if not checked)
13. Take a manual backup now and confirm it's restorable (§6) — there is
    no automated nightly dump yet, so this is on you until that's built

---

## 6. Day-2 operations (keep this section handy)

| Task | How |
|---|---|
| Deploy a new version | Push to `main` → approve the `production` Environment gate in the GitHub Actions run. **Verify the self-hosted runner is actually up first** (§4.5) — a queued deploy job with no runner never errors, it just never runs. If the runner is down, use the manual path in §10 instead of waiting |
| Roll back | GitHub Actions → `cd-prod.yml` → **Run workflow** → enter the previous tag/sha (needs the runner up too), or `cd /opt/finzorr && sudo IMAGE_TAG=<previous-tag> ./deploy/deploy.sh <previous-tag>` directly on the server if that tag was already pulled/built there |
| See logs | `docker compose logs api --tail 100 -f` (needs `IMAGE_TAG=<anything>` set first if not already exported — the compose file requires it just to parse, even for `logs`) |
| Backup now | **Not automated — `deploy/backup.sh` doesn't exist yet (§3).** Manual dump in the meantime: `docker compose exec -T postgres pg_dump -U finzorr finzorr \| gzip > /opt/finzorr/manual-backup-$(date +%Y%m%d).sql.gz`. Building the real nightly-cron version is a real open TODO, not a "nice to have" — there is currently no recovery path from data loss on this server beyond whatever you dump by hand |
| Restore drill | `gunzip -c <backup-file>.sql.gz \| docker compose exec -T postgres psql -U finzorr finzorr` — practice once BEFORE you need it |
| Rotate a key | Edit `/opt/finzorr/prod.env`, then `docker compose up -d api`. NOTE: rotating `SESSION_SECRET` logs every user out |
| Enable LangSmith in prod | Set `LANGSMITH_TRACING=true` + the API key in prod.env, restart api — remember prompts then leave the server |
| Daily drift watch | Add to the VM's cron: `30 7 * * * cd /opt/finzorr && docker compose run --rm api python scripts/drift_watch.py >> /opt/finzorr/backups/drift.log 2>&1` — alerts if any quality eval regresses |
| Live trace-health watch | Add to the VM's cron (only meaningful once `LANGSMITH_TRACING=true` in prod.env): `0 */6 * * * cd /opt/finzorr && docker compose run --rm api python scripts/trace_health_watch.py >> /opt/finzorr/backups/trace-health.log 2>&1` — alerts on live `degraded`/`guard:suspicious` tag rate over the trailing window; skips cleanly (exit 0) if tracing is off |
| Uptime alerts | uptimerobot.com (free) → HTTP monitor on `https://api.finzorr.ai/healthz` → email alert |
| Free-tier pressure | Watch for `ai.budget.exceeded` in logs — the chain absorbs it; recurring daily = time to consider Groq's paid tier |
| Runner health check | `gh api repos/<owner>/<repo>/actions/runners --jq '.total_count'` — 0 means every future push-triggered deploy will queue and never run until fixed (§4.5, §9.7) |
| Docker disk cleanup | Build cache and superseded images accumulate on BOTH the server and any machine that builds images locally (this project hit 46GB reclaimed from local dev machine cache alone — see §9.9). Periodically: `docker system df` to check, then `docker container prune -f && docker image prune -af && docker builder prune -af` when reclaimable space is large. **Caution**: `docker container prune` removes ALL stopped containers including ones you meant to keep — check `docker ps -a` first if anything matters |

---

## 7. Security notes

- **Secrets live in exactly one place**: `/opt/finzorr/prod.env` on the
  server, permissions 600, owned by root. They are never in the GitHub
  repo, never in this document, never in CI, and never in GitHub Secrets
  either — the CD pipeline's deploy step runs locally on the server via a
  self-hosted GitHub Actions runner (not SSH), so there is no deploy
  credential in GitHub at all to leak. Only the built-in, auto-rotated
  `GITHUB_TOKEN` is used, solely to push the built image to GHCR.
- **Nothing listens on the internet.** The tunnel is outbound; there are no
  open ports, so there is nothing to port-scan. The self-hosted runner adds
  no new listener either — it polls GitHub outbound, the same shape as
  `cloudflared`.
- **Deliberately off in prod**: the code-interpreter sandbox
  (docker-in-docker risk) and LangSmith tracing (data leaves the box).
- **Never commit a filled .env** — the repo's .gitignore already blocks it;
  keep it that way.
- If a key ever leaks (pasted somewhere public), rotate it at its console
  and update prod.env — a 2-minute fix for Groq/Gemini.

## 8. Known risks, stated honestly

1. **Yahoo Finance vs datacenter IPs** — tested on day one by the
   fundamentals seed; fallback plan ready if blocked.
2. **Free-tier limits** — absorbed by the budget chain; only matters at
   real scale.
3. **One VM = one point of failure, and there is currently no automated
   backup** — the <5-minute rollback (§6) only helps for a bad *code*
   deploy; it does nothing for data loss (disk failure, accidental
   `DROP`, etc.). `deploy/backup.sh` was planned (§3) but never built.
   This is a real, currently-open gap, not a mitigated risk — treat
   building it as a near-term priority, not a someday item.
4. **Cloudflare Pages preview URLs cannot call the prod API** (different
   site → CORS/cookies) — by design; previews are for eyeballing UI only.
5. **The self-hosted runner is a silent single point of failure for
   automated deploys** — if its systemd service stops for any reason, the
   `cd-prod.yml` deploy job queues forever with no error surfaced anywhere
   (§4.5, §9.7). Verify it's up before assuming a push will reach
   production; §10 is the fallback when it isn't.
6. **Local build-cache/image bloat can silently fill a machine's disk** —
   both the server and any local dev machine that builds this project's
   Docker images will accumulate build cache and superseded image layers
   over time (this project hit 46GB reclaimable on a local dev machine
   alone, which caused an unrelated local Postgres container to crash-loop
   from disk exhaustion mid-session — see §9.9). Not fatal, but worth
   periodic `docker system df` checks (§6).

---

## 9. Incidents actually hit in production, and their fixes

Every item here happened for real, not as a hypothetical risk review. Kept
in full because the pattern (not just the specific fix) generalizes to any
new agent built on this same architecture — several of these are the kind
of mistake that's easy to repeat on a fresh server if you don't know to
look for it.

### 9.1 Disk exhaustion on the OVH server during initial setup
The FIRST real image build attempt on the server failed outright on disk
space — root cause identified precisely, not just worked around:
`backend/Dockerfile`'s builder stage was installing `build-essential`
(gcc/g++ plus ~50 transitive packages, ~336MB) even though it was never
actually needed — every dependency that could plausibly require
compilation (`psycopg[binary]`, `lxml`, `cryptography`, `pymupdf`) ships
pre-built manylinux wheels for linux/amd64, confirmed via a clean local
build showing zero compilation happening. That unnecessary C toolchain was
specifically what pushed the first build over the server's disk limit.
Fixed by dropping `build-essential` from the builder stage entirely — the
image doesn't need `apt` in that stage at all. Separately, Docker's build
cache and layered images continued to accumulate during iteration
afterward (a database that can't write WAL is not a "slow" database, it's
a stopped one) — reclaimed with `docker system df` to see it,
`docker builder prune -af` + `docker image prune -af` to clear it (same
commands as §6's Docker disk cleanup row). **Lesson**: don't just react to
a disk-full error with "clear some space" — check whether something in the
image is actually unnecessary weight first (a build stage installing a
full compiler toolchain "just in case" is a common default that's often
wrong once you actually check what the dependencies need); a fresh VM's
disk is also usually smaller than you'd expect relative to how much Docker
churns through it during active iteration, so check `df -h`/
`docker system df` early and often during initial bring-up, not just after
something breaks.

### 9.2 Missing `EMBED_OLLAMA_URL`/`OLLAMA_URL` — corpus/fundamentals seeding failed
`prod.env` didn't set these two vars in an early version of `vm-bootstrap.sh`.
The `api` container defaulted to `localhost:11434` for the embedder, which
*inside its own network namespace means itself*, not the `ollama`
container — "All connection attempts failed," not an obviously-networking
error message. Fixed in `vm-bootstrap.sh`'s `prod.env` template (both vars
now written unconditionally); `deploy/recover-embed-url.sh` exists for any
server that was bootstrapped before the fix. **Lesson**: in Docker Compose,
service-to-service calls use the *service name* as hostname
(`http://ollama:11434`), never `localhost` — an env var that defaults to
`localhost` for a same-machine-but-different-container dependency is a
guaranteed footgun the first time it's actually exercised in a container,
even though it works fine when running the same code natively on a laptop.

### 9.3 Checkpointer cross-worker deadlock — chat silently hung on "Thinking"
With `--workers 2`, both uvicorn worker *processes* raced to lazily run
`CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoints_thread_id_idx` on
first request. Postgres serialized the two attempts, and any other
concurrently-open transaction on the same table (e.g. an unrelated
`/chat/sessions` request landing on the same worker) blocked the
`CONCURRENTLY` build from ever finishing — every subsequent chat turn hung
forever waiting on the same lock, with no error, no timeout, no log line
pointing at the cause. Two compounding root causes, both fixed:
1. An in-process `asyncio.Lock()` was being used to guard the setup —
   `--workers 2` means two separate OS *processes*, and an `asyncio.Lock`
   provides zero cross-process safety. Each worker had its own,
   independent lock that never contended with the other.
2. `CREATE INDEX CONCURRENTLY` cannot run inside a transaction block at
   all (a hard Postgres restriction) — the connection pool needed
   `autocommit=True` explicitly, which it didn't have.

Fixed by moving index setup into a genuinely one-off, single-process step
(`app/orchestration/setup_checkpointer.py`, run once via `deploy.sh`/
`vm-bootstrap.sh` BEFORE the api workers start, using an
`AsyncConnectionPool` with `autocommit=True`) instead of each worker doing
it lazily on first request. `deploy/recover-checkpointer-race.sh` recovers
a server already stuck in this state: terminates the stuck backends, drops
the half-built (invalid) index, re-runs setup single-process, restarts.
**Lesson**: `--workers N` in uvicorn/gunicorn means N separate processes,
not threads — any "run this exactly once" setup logic needs process-safe
coordination (a dedicated one-off step before workers start, or a
DB-level advisory lock), never an in-process primitive like `asyncio.Lock`
or a plain Python global.

### 9.4 Non-root container couldn't write to `/app` — broke uploads and yfinance's cache
`WORKDIR /app` creates that directory as `root` BEFORE the subsequent
`COPY --chown=app:app /app /app` runs — and `--chown` on `COPY` only
affects the files it copies IN, not the pre-existing parent directory
itself. Net effect: `/app` stayed root-owned (mode 755, no write bit for
`app`) even after the chowned copy landed inside it. The `app` user could
read every existing file but couldn't create NEW ones — which is exactly
what a file upload or yfinance's on-disk cookie cache needs to do. Fixed
with an explicit `RUN chown app:app /app && mkdir -p /app/storage/uploads
&& chown -R app:app /app/storage` step after the `COPY`. **Lesson**: in a
multi-stage Dockerfile with a non-root final user, `--chown` on `COPY`
only ever fixes what that COPY brings in — any directory that existed
before the copy (including ones `WORKDIR` silently creates) needs its own
explicit `chown`, checked by actually testing a write as the runtime user,
not just confirming the container starts.

### 9.5 `deploy.sh` failed against a local-only bootstrap image tag
`vm-bootstrap.sh`'s first-ever image build is local
(`docker build -t <image>:bootstrap backend/`) and deliberately never
pushed to GHCR — there's nothing to push to yet on a fresh server.
`deploy.sh`'s first step is `docker compose pull api`, which fails (or
silently does nothing useful) against a tag that only exists locally.
Fixed by extracting the shared health-wait logic into its own
`deploy/wait-healthy.sh`, so bootstrap-time bring-up can go straight to
`docker compose up -d` + `wait-healthy.sh` without needing `deploy.sh`'s
pull step at all. **Lesson**: "day-0 bring-up" and "day-2 redeploy" are
genuinely different operations even though they look similar (both end
with "the container is running and healthy") — day-0 has no registry
image to pull yet; conflating the two into one script either breaks day-0
or adds an awkward conditional branch. Splitting the shared part
(health-wait) out was cleaner than either.

### 9.6 GitHub Actions build silently broke for BOTH real production fixes in a row
`cd-prod.yml`'s `build-and-push` job used `cache-to: type=gha,mode=max`,
which requires the `docker-container` buildx driver — but the job never
called `docker/setup-buildx-action`, so it ran on the default plain
`docker` driver, which doesn't support that cache backend at all:
`"failed to build: Cache export is not supported for the docker driver."`
This had apparently been broken for a while (a pre-existing, unrelated
bug — not caused by either fix it blocked) and simply never got exercised
until two real deploys needed it back-to-back. Fixed by adding
`docker/setup-buildx-action@v3` before the login/build steps. **Lesson**:
a CD pipeline step that only runs on `push` to `main` doesn't get
exercised nearly as often as CI (which runs on every PR) — a latent break
here can sit undetected for a long time. If a deploy pipeline hasn't
actually shipped anything in a while, don't assume it still works;
verify the build step in isolation before relying on it for something
time-sensitive.

### 9.7 Self-hosted runner silently stopped registering — deploys queued forever
Discovered mid-session: `gh api repos/<owner>/<repo>/actions/runners` returned
`"total_count": 0` — no runner registered at all, despite `vm-bootstrap.sh`
having installed and started it as a systemd service at initial launch.
Root cause not fully diagnosed remotely (no SSH access from the assisting
session by design — see §4.5's rationale) — plausible causes include the
systemd service crashing without restarting, a server reboot where the
service didn't persist, or the runner's registration token/session
expiring. The queued `cd-prod.yml` deploy job showed no error of any
kind — GitHub Actions does not surface "no runner available" as a failure,
it just waits, indefinitely, with the job stuck at `status: "queued"`.
**Not yet fixed at the infrastructure level** (would need direct server
access) — worked around via the manual deploy path (§10) instead.
**Lesson**: a self-hosted runner needs its OWN monitoring — nothing in
GitHub's UI proactively tells you it's down, a queued job just looks like
"still building" if you don't know to check runner count specifically.
Add a periodic health check (even a simple cron hitting
`gh api .../actions/runners` and alerting on `total_count: 0`) rather than
discovering it's down only when a deploy is time-sensitive.

### 9.8 Missing persistent volume for uploads
The `api` service in `docker-compose.prod.yml` had no volume for
`/app/storage/uploads` — every container restart lost all uploaded files
while their Qdrant vectors (and RAG citations pointing at them) survived
untouched, silently producing a permanently broken reference. Fixed by
adding a `finzorr_uploads` named volume, verified locally before shipping
by building the actual production image, writing a file as the non-root
container user into the volume, then reading it back from a **fresh**
container instance on the same volume to confirm real persistence (not
just "the mount didn't error"). **Lesson**: any container path that's
supposed to survive a restart needs an explicit named volume — this is
easy to miss for a path that "just works" during development (where the
process never actually restarts) and only surfaces the first time a real
deploy cycle happens against it.

### 9.9 Local dev machine's own Docker bloat crashed an unrelated local Postgres container
Not a production incident, but real and worth recording: while
investigating a stuck Postgres container on a local development machine
(mid-session, unrelated to any deploy), the actual error was `PANIC: could
not write to file "pg_logical/replorigin_checkpoint.tmp": No space left on
device` — but the HOST disk had 194GB free. The real constraint was
Docker Desktop's internal VM disk, separately capped from the host and
filled by accumulated build cache (13GB+) and superseded images (16GB+)
from repeated local image builds during iterative development. Fixed with
`docker container prune -f && docker image prune -af && docker builder
prune -af`, reclaiming ~46GB, after which the container restarted cleanly
via Postgres's own automatic WAL recovery. **Lesson**: "no space left on
device" inside a container does NOT mean the host is full — Docker
Desktop (and some Docker Engine configurations) impose their own separate
storage ceiling; check `docker system df`, not just `df -h` on the host,
when a containerized service reports disk-full.

### 9.10 Google OAuth `origin_mismatch` — twice, for two different origins
"Sign in with Google" failed with `origin_mismatch` on launch — fixed by
adding `https://finzorr.ai` to the OAuth client's Authorized JavaScript
Origins in Google Cloud Console. It then failed AGAIN the same way for
`https://www.finzorr.ai` specifically, since that's a genuinely different
origin from Google's perspective even though the app treats them as the
same site (§3's multi-origin CORS fix). Both had to be added as separate
entries. **Lesson**: a Google OAuth client's Authorized JavaScript Origins
list needs EVERY exact origin the login button will ever be served from —
apex domain and `www.` subdomain are not interchangeable to Google's
origin check even if your own CORS/cookie handling treats them as
equivalent; add both explicitly, don't assume one covers the other.

### 9.11 The recovery script for one bug shipped with two bugs of its own
`deploy/recover-embed-url.sh` (§9.2's fix for servers bootstrapped before
`EMBED_OLLAMA_URL`/`OLLAMA_URL` were set) itself needed two follow-up fixes
on the same day it was written, both caught by actually running it against
a real server rather than just reading it back:
1. It never exported `IMAGE_TAG` before invoking `docker compose` —
   `docker-compose.prod.yml`'s `image:` line requires that variable to
   parse AT ALL (`${IMAGE_TAG:?set IMAGE_TAG}`), for every invocation, not
   just the final one `deploy.sh` itself sets up. Missed on the first pass
   because it's easy to test the SQL/logic half of a recovery script
   without noticing the compose-parsing half needs its own env var too.
2. It ran `git pull` in whatever directory it happened to be invoked from,
   not `/opt/finzorr` (the actual directory `vm-bootstrap.sh` manages and
   the running stack actually uses) — so running it from a different
   manual clone never touched the checkout that mattered, and a
   just-pushed fix the script itself depended on (`wait-healthy.sh`) was
   missing when it tried to call it. Fixed by having the script `git pull`
   `/opt/finzorr` itself as its very first step, making it safe to invoke
   from any directory.

**Lesson**: this generalizes directly to §10's emergency manual-deploy
procedure — any recovery/emergency script must (a) explicitly set every
environment variable `docker compose` needs to even PARSE the compose
file, not just the ones the script's own logic obviously touches, and (b)
operate on the server's actual managed installation directory explicitly,
never assume the invoker's current working directory happens to match it.
A recovery script that only gets read-reviewed, never actually run against
a real (or realistic) environment before being relied on, will very
plausibly have its own bugs — test the rescue plan, not just the original
fix.

---

## 10. Emergency manual deploy (when the self-hosted runner is unavailable)

Use this when §4.5's runner health check returns `0` and a fix needs to
reach production before the runner is restored. This is exactly the
procedure used in this project's own history when the runner went down
mid-session. Run directly on the server (`ssh` in — this is the one
legitimate use of direct server access outside the runner's own
outbound-poll model, since there is no other path when the runner itself
is what's broken):

```bash
cd /opt/finzorr
git pull
docker build -t ghcr.io/<owner>/<image-name>:bootstrap backend/
export IMAGE_TAG=bootstrap
docker compose -f deploy/docker-compose.prod.yml run --rm api alembic upgrade head
docker compose -f deploy/docker-compose.prod.yml run --rm api python -m app.orchestration.setup_checkpointer
docker compose -f deploy/docker-compose.prod.yml up -d api
./deploy/wait-healthy.sh finzorr-api 300
```

Why each step, and why it differs from the normal `deploy.sh` path:
- `git pull` — pulls the already-merged, already-CI-passed code from
  `main` directly, since there's no GHCR image to pull instead (that's
  what's broken).
- `docker build ... :bootstrap` — builds the image LOCALLY on the server,
  reusing the same `:bootstrap` tag `vm-bootstrap.sh` used at initial
  launch. This is a deliberate, permanent local-only tag for exactly this
  scenario, not a one-off name to remember.
- `export IMAGE_TAG=bootstrap` — every `docker compose` invocation against
  `docker-compose.prod.yml` needs this set, even read-only ones like
  `logs`, because the compose file's `image:` line uses
  `${IMAGE_TAG:?set IMAGE_TAG}` and fails to parse at all without it.
- **Deliberately NOT `deploy.sh`** — `deploy.sh`'s first step is `docker
  compose pull api`, which fails against a tag that only exists locally
  and was never pushed to GHCR (§9.5). Run its remaining steps (migrate,
  checkpointer setup, restart, health-wait) directly instead.
- `alembic upgrade head` — even in an emergency, don't skip migrations;
  running them explicitly here (rather than assuming they're a no-op)
  costs nothing when there's nothing pending and prevents a real gap when
  there is.
- The checkpointer setup step is idempotent (safe to re-run) and cheap —
  always include it rather than trying to determine whether it's "really"
  needed this time.

After this succeeds: verify independently, don't just trust the script's
own exit code — hit `/healthz` and `/readyz` from outside the server
(`curl https://api.finzorr.ai/healthz`), and for any code change that
touches something specific (a new dependency, a new env var, a new
volume), verify THAT specific thing too — e.g. `docker exec finzorr-api
tesseract --version` after a change that added an OCR dependency, or
`docker inspect finzorr-api --format '{{range .Mounts}}{{.Name}} ->
{{.Destination}}{{"\n"}}{{end}}'` after a volume-mount change. A green
health check confirms the process started; it does not confirm the
specific thing you just shipped actually works.

Once the runner is restored, no further action is needed to "reconcile"
anything — the next normal push to `main` picks up from wherever `main`
is, exactly as if the manual deploy had been the runner's own doing.
