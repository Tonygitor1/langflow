# langflow/docker/local_flow.Dockerfile
#
# Self-contained flow execution image for deployed agent containers.
# - Uses SQLite (not PostgreSQL) — no external DB dependency
# - Flow JSON is imported from /opt/langflow/flow.json (ConfigMap mount) on startup
# - /flows is a container-local directory (emptyDir, NOT host-mounted) for
#   hot-reloadable flow definitions
# - Unlike local_backend.Dockerfile, there is no source PV mount for this
#   deployment target — the full backend source is baked into the image so
#   `langflow run` works standalone.
#
# Build:
#   docker build \
#     -f docker/local_flow.Dockerfile \
#     -t agents-market-local-langflow-flow:latest \
#     --build-arg HOST_UID=$(id -u) \
#     .

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ARG HOST_UID=1000

# Install system dependencies
# - lsof: used to kill stale processes
# - libpq5: langflow's postgresql extra pulls in psycopg2, which needs this
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

# SQLite-based config — no PostgreSQL dependency
ENV LANGFLOW_DATABASE_URL=sqlite:///./app/data/langflow.db
ENV LANGFLOW_SAVE_DB_IN_CONFIG_DIR=true
ENV LANGFLOW_CONFIG_DIR=/app/data
ENV LANGFLOW_BACKEND_ONLY=true

# Create directories for SQLite data and hot-reloadable flows
RUN mkdir -p /app/data /flows

# Copy the full backend source — no frontend, no source PV mount for this
# target, so the project must be installed in full at build time.
COPY pyproject.toml uv.lock README.md ./
COPY src/backend ./src/backend
COPY src/lfx ./src/lfx
COPY src/sdk ./src/sdk

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra postgresql

# Entrypoint: import the flow snapshot, then run langflow with hot reload on
# /flows. COPY+chmod run as root (before the USER switch below) — COPY always
# writes as root regardless of the active USER, so chmod must also run as
# root or it silently fails to modify the file's permissions.
COPY docker/local_flow.entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create non-root user matching host UID
RUN useradd --uid ${HOST_UID} --gid 0 --no-create-home --home-dir /app/data user \
    && chown -R ${HOST_UID}:0 /app /flows

USER user

EXPOSE 7860

ENTRYPOINT ["/entrypoint.sh"]
