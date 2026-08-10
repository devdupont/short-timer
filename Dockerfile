# syntax=docker/dockerfile:1

# Runtime image for the API. The frontend is a static bundle served by GitHub
# Pages and is deliberately not part of this image.
FROM python:3.12-slim

# uv installs from the committed lockfile, so the image matches local dev.
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies resolve in their own layer so editing source doesn't reinstall
# the world on every build.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY shortimer ./shortimer
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Drop privileges: nothing here needs root at runtime.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8000

# --proxy-headers makes request.client the real caller rather than the ingress
# proxy. Trusting all forwarders is safe only because the container is not
# routable except through Container Apps' ingress.
CMD ["sh", "-c", "exec uvicorn shortimer.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
