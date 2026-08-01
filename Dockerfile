# syntax=docker/dockerfile:1

# One image, two roles. The API and the Celery worker run the same code and
# differ only in their command, so building them separately would mean keeping
# two dependency sets in step for no benefit — and a worker whose media stack
# drifted from the API's would fail only at the stage that used it.
#
# `slim` rather than `alpine`: the wheels this project installs (psycopg2,
# pydantic-core) ship manylinux binaries. Alpine's musl would force a source
# build of both and roughly triple the image build time.
FROM python:3.13-slim AS base

# FFmpeg is the only system dependency. It is optional in code — a run without it
# still completes, degrading to a storyboard and captions with no mp4 — but a
# container that ships without it would silently produce a worse product than
# the same code on a developer's machine.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies before source, so editing a module does not invalidate the
# install layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY infra/ ./infra/
# `apps/` is copied selectively because apps/web is a Next.js frontend that does
# not belong in a Python image — but its __init__.py has to come along, or the
# `apps` package falls back to a namespace package and `apps.api.main` resolves
# differently in the container than it does in the source tree.
COPY apps/__init__.py ./apps/
COPY apps/api/ ./apps/api/
COPY workers/ ./workers/
COPY packages/ ./packages/

# Runs as a non-root user, which also means the artifacts volume has to be owned
# by that user — hence the chown here rather than relying on the mount default.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data/artifacts \
    && chown -R app:app /app
USER app

EXPOSE 8000

# Overridden by the worker service in compose.
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
