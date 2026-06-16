# OpenHuman agent image — multi-stage build.
#
# Layout:
#   Stage 1 (builder): rust:1.93-bookworm — compiles openhuman-core from the
#                       upstream clone at docker-registry/agents/openhuman/.
#   Stage 2 (runtime): python:3.12-slim-bookworm — openhuman-core binary +
#                       Python shim that translates Bridge's WS Gateway RPC v3
#                       to OpenHuman's HTTP JSON-RPC.
#
# Build context: docker-registry/agents/openhuman/ (upstream clone).
# Build context (named "shim"): docker-registry/agent-dockerfiles/openhuman/
#                       (this directory) — provides shim/ and supervisor.sh.

# ── Stage 1: Rust builder ────────────────────────────────────────────────────
# trixie (Debian 13, gcc-14) — bookworm's gcc-12 hits a known fp16 NEON
# always_inline bug while building whisper-rs-sys on arm64; gcc-13+ fixed it.
# See docs/plans/2026-05-19-deerflow-to-openhuman-design.md §11.
FROM rust:1.93-trixie AS builder

ARG APT_MIRROR
RUN if [ -n "${APT_MIRROR}" ]; then \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null || true; \
    fi

# System deps needed by upstream openhuman-core. Notes:
#   - libclang-dev + clang: bindgen 0.72 panics at build time without them.
#   - cmake + build-essential: whisper-rs-sys vendors whisper.cpp and runs
#     cmake + g++ in its build.rs.
#   - protobuf-compiler: opentelemetry-proto transitive build dep generates
#     code from .proto via prost_build.
RUN apt-get update && apt-get install -y --no-install-recommends \
        pkg-config libssl-dev libasound2-dev libudev-dev libx11-dev \
        libxi-dev libxtst-dev libxinerama-dev libxrandr-dev libxcursor-dev \
        libxdo-dev libevdev-dev \
        clang libclang-dev cmake build-essential protobuf-compiler \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .

# Build only the openhuman-core binary; the Tauri shell + browser bits aren't
# needed in the container variant.
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/build/target \
    cargo build --release --bin openhuman-core \
    && cp /build/target/release/openhuman-core /usr/local/bin/openhuman-core

# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
# trixie keeps libssl ABI in sync with the builder stage (bookworm's libssl3
# is 3.0.x, trixie's is 3.5.x — mixing them would crash at startup).
FROM python:3.12-slim-trixie AS runtime

ARG APT_MIRROR
RUN if [ -n "${APT_MIRROR}" ]; then \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null || true; \
    fi

# tini reaps zombies + forwards signals so SIGTERM cleanly stops both
# openhuman-core (background) and the shim (foreground).
RUN apt-get update && apt-get install -y --no-install-recommends \
        tini libssl3 libasound2 libudev1 libxdo3 libevdev2 \
        libx11-6 libxtst6 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Comfortable baseline (bash, jq, tree, etc.) shared with other agent images.
# Sourced via buildx --build-context shared=<repo>/docker-registry/shared.
COPY --from=shared install-agent-baseline.sh /tmp/
RUN bash /tmp/install-agent-baseline.sh && rm /tmp/install-agent-baseline.sh

# Non-root user. UID/GID 10001 matches openhuman_manifest.dart's
# `chown` field — keep them in sync.
RUN groupadd --system --gid 10001 openhuman \
    && useradd --system --uid 10001 --gid 10001 \
        --home-dir /home/openhuman --shell /bin/bash openhuman \
    && mkdir -p /home/openhuman/.openhuman /opt/openhuman /opt/shim \
    && chown -R openhuman:openhuman /home/openhuman /opt/openhuman /opt/shim

COPY --from=builder /usr/local/bin/openhuman-core /usr/local/bin/openhuman-core

# Shim sources + supervisor are provided via the `shim` build context, which
# points at docker-registry/agent-dockerfiles/openhuman/ (this dir).
COPY --from=shim shim/shim/ /opt/shim/
COPY --from=shim supervisor.sh /opt/openhuman/supervisor.sh
COPY --from=shim write_config_toml.py /opt/openhuman/write_config_toml.py
RUN chmod +x /opt/openhuman/supervisor.sh /opt/openhuman/write_config_toml.py

# Install shim Python deps. uv would be faster but a one-time pip install
# keeps the image's tool surface narrow; shim has very few deps.
RUN pip install --no-cache-dir --root-user-action=ignore \
        "websockets>=13" "httpx>=0.27" "httpx-sse>=0.4"

# OpenHuman's named volume — matches upstream docker-compose.yml.
VOLUME ["/home/openhuman/.openhuman"]

USER openhuman
WORKDIR /home/openhuman

EXPOSE 8001

ENV OPENHUMAN_CORE_HOST=127.0.0.1 \
    OPENHUMAN_CORE_PORT=7788 \
    OPENHUMAN_WORKSPACE=/home/openhuman/.openhuman \
    SHIM_WS_HOST=0.0.0.0 \
    SHIM_WS_PORT=8001 \
    SHIM_MCP_CONFIG_PATH=/opt/shim/mcp_servers.json

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/openhuman/supervisor.sh"]
