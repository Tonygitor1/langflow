FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
ENV TZ=UTC

WORKDIR /app

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install --no-install-recommends -y \
    build-essential \
    curl \
    git \
    xz-utils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 22.x (apt ships Node 18, but Vite requires 20.19+ / 22.12+)
RUN ARCH=$(dpkg --print-architecture) \
    && if [ "$ARCH" = "amd64" ]; then NODE_ARCH="x64"; \
       elif [ "$ARCH" = "arm64" ]; then NODE_ARCH="arm64"; \
       else NODE_ARCH="$ARCH"; fi \
    && NODE_VERSION=$(curl -fsSL https://nodejs.org/dist/latest-v22.x/ \
                    | sed -nE "s/.*node-v([0-9]+\.[0-9]+\.[0-9]+)-linux-${NODE_ARCH}\.tar\.xz.*/\1/p" \
                    | head -1) \
    && if [ -z "$NODE_VERSION" ]; then echo "ERROR: Could not determine Node.js version" && exit 1; fi \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz" \
    | tar -xJ -C /usr/local --strip-components=1

COPY . /app

# Install dependencies using uv
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=README.md,target=README.md \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=src/backend/base/README.md,target=src/backend/base/README.md \
    --mount=type=bind,source=src/backend/base/uv.lock,target=src/backend/base/uv.lock \
    --mount=type=bind,source=src/backend/base/pyproject.toml,target=src/backend/base/pyproject.toml \
    --mount=type=bind,source=src/lfx/README.md,target=src/lfx/README.md \
    --mount=type=bind,source=src/lfx/pyproject.toml,target=src/lfx/pyproject.toml \
    --mount=type=bind,source=src/sdk/README.md,target=src/sdk/README.md \
    --mount=type=bind,source=src/sdk/pyproject.toml,target=src/sdk/pyproject.toml \
    uv sync --frozen --no-install-project --no-dev --extra postgresql

EXPOSE 7860
EXPOSE 3000

CMD ["./docker/dev.start.sh"]
