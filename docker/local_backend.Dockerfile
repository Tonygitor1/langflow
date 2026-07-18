# langflow/docker/local_backend.Dockerfile
#
# Lightweight dev image for local k3s development.
# - Source code is mounted at /app via a hostPath PersistentVolume at runtime
# - .venv is created inside the mounted source (langflow/.venv on the host)
#   by make backend_local on first run — it persists across restarts
# - Frontend runs on the host (localhost:3000), not in this container
#
# Build:
#   docker build \
#     -f docker/local_backend.Dockerfile \
#     -t agents-market-local-langflow:latest \
#     --build-arg HOST_UID=$(id -u) \
#     .

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ARG HOST_UID=1000

# Install system dependencies
# - lsof: used by make backend_local to kill stale process on port 7860
# - libpq5: PostgreSQL client library (runtime dep for psycopg2)
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install --no-install-recommends -y \
        build-essential \
        curl \
        gcc \
        git \
        libpq5 \
        lsof \
        xz-utils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create non-root user matching host UID for filesystem permission alignment
# (the mounted source and .venv are owned by the host user)
RUN useradd --uid ${HOST_UID} --gid 0 --no-create-home --home-dir /app/data user

USER user

EXPOSE 7860

CMD ["make", "backend_local"]
