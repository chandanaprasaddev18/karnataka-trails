# The API image. Also runs the worker in-process when
# TRIPPLAN_WORKER__IN_PROCESS=true, which is how a one-process free tier gets its
# itineraries built (see WorkerSettings.in_process).
#
# Two stages so the runtime image carries no build tooling. `uv` because that is
# what the project locks with, and `--frozen` so a deploy installs exactly what
# `uv.lock` says — a deploy that silently resolves different versions than the ones
# the tests ran against is not a deploy of this commit.

FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, in their own layer: they change far less often than the
# source, so an ordinary code change rebuilds in seconds.
#
# `--extra api` is not optional here despite the name: FastAPI and uvicorn live in
# that extra (the API and the worker share one image, two entrypoints), so a plain
# `uv sync` builds an image whose CMD fails with "uvicorn: not found".
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev --extra api

# The project itself.
COPY src ./src
COPY migrations ./migrations
COPY seeds ./seeds
RUN uv sync --frozen --no-dev --extra api


FROM python:3.12-slim-bookworm AS runtime

# Non-root. Nothing here needs to write to the image.
RUN useradd --create-home --uid 10001 tripplan

WORKDIR /app
COPY --from=builder --chown=tripplan:tripplan /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    # Structured logs, because a hosted platform's log viewer is all you get.
    TRIPPLAN_LOG_JSON=true \
    # No shell on a managed host to run `make migrate` from.
    TRIPPLAN_MIGRATE_ON_START=true

USER tripplan

# $PORT is injected by most platforms (Render, Koyeb, Fly); 8000 is the local default.
ENV PORT=8000
EXPOSE 8000

# One worker process. Not a performance choice: the in-process job worker claims
# jobs with FOR UPDATE SKIP LOCKED, so several web workers would be several job
# workers, and on a 512 MB free instance that is a memory problem before it is a
# throughput win.
CMD ["sh", "-c", "uvicorn tripplan.api.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
