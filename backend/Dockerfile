# Multi-stage build: uv-managed deps compiled in a full image, shipped in a
# slim runtime image. Two uvicorn workers per DEPLOYMENT_PLAN.md's sizing.

FROM python:3.12-slim AS builder

# No build-essential: every dependency (psycopg[binary], lxml, cryptography,
# pymupdf) ships manylinux wheels for linux/amd64 — confirmed via a clean
# local build with zero compilation. Keeping this stage free of a C
# toolchain saves ~336MB and avoids needing apt at all in the builder.

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Dependencies first — separate layer, cached across code-only changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime

# --create-home: several libraries (yfinance's cache, anything reading
# $HOME) need a real, writable home directory — a bare --no-create-home
# system user broke yfinance's CookieCache with a silent permission error.
RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app
# WORKDIR creates /app as root BEFORE this COPY runs, and --chown on COPY
# only affects the files it copies in, not the pre-existing directory
# itself — so /app stayed root-owned (mode 755, no write for `app`) even
# after the chowned copy landed inside it. The app user could read every
# file but couldn't create NEW ones (storage/uploads, on first upload) at
# the top level. Fix ownership of the directory itself explicitly.
RUN chown app:app /app && mkdir -p /app/storage/uploads && chown -R app:app /app/storage

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    APP_ENV=prod

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
