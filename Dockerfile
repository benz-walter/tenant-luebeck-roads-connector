# Base image used for any following layers, pulled on 2026-01-12
ARG IMAGE_PYTHON=python:3.14-slim@sha256:c13298ac1aac4a3afa031198e681eee745de2e3d95a45caa2c457dd4af0b064f

# uv for package handling, formatting and linting, pulled on 2026-01-09
ARG IMAGE_UV=ghcr.io/astral-sh/uv:0.9.22@sha256:1ffe3dc06d4a9a2116279620f21dd1fdb7e3c2b2ca80095010bde524c61dfabd
FROM ${IMAGE_UV} AS uv

# Base image used for any following layers
FROM ${IMAGE_PYTHON} AS base
COPY --from=uv /uv /uvx /bin/

WORKDIR /app
ENV PYTHONPATH=/app \
    # Do no write bytecode for the application while running it as we will never restart the process within the container
    PYTHONDONTWRITEBYTECODE=1 \
    # As we run in a container, uv should use the system python instead of creating a new .venv.
    UV_PYTHON_PREFERENCE=system \
    UV_PROJECT_ENVIRONMENT=/usr/local \
    # Make sure that the lock file is not changed while installing/syncing dependencies
    UV_LOCKED=1 \
    # Compile bytecode to speed up imports and application startup time
    UV_COMPILE_BYTECODE=1

RUN apt update && apt install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Target used in production deployments
FROM base AS production

COPY pyproject.toml uv.lock /
RUN uv sync --no-dev --no-editable

COPY src/ /app/

CMD ["python", "sync.py"]

# Target used for local development
FROM production AS development
RUN uv sync --no-editable
ENV UV_LOCKED=0
