# syntax=docker/dockerfile:1.7

# Opt-in extension dependencies at build time (space-separated directory names).
# Example: docker build --build-arg OPENCLAW_EXTENSIONS="diagnostics-otel matrix" .
#
# Multi-stage build produces a minimal runtime image without build tools,
# source code, or Bun. Works with Docker, Buildx, and Podman.
# The ext-deps stage extracts only the package.json files we need from the
# bundled plugin workspace tree, so the main build layer is not invalidated by
# unrelated plugin source changes.
#
# Two runtime variants:
#   Default (bookworm):      docker build .
#   Slim (bookworm-slim):    docker build --build-arg OPENCLAW_VARIANT=slim .
ARG OPENCLAW_EXTENSIONS=""
ARG OPENCLAW_VARIANT=default
ARG OPENCLAW_BUNDLED_PLUGIN_DIR=extensions
ARG OPENCLAW_DOCKER_APT_UPGRADE=1
ARG OPENCLAW_NODE_BOOKWORM_IMAGE="node:24-bookworm@sha256:3a09aa6354567619221ef6c45a5051b671f953f0a1924d1f819ffb236e520e6b"
ARG OPENCLAW_NODE_BOOKWORM_DIGEST="sha256:3a09aa6354567619221ef6c45a5051b671f953f0a1924d1f819ffb236e520e6b"
ARG OPENCLAW_NODE_BOOKWORM_SLIM_IMAGE="node:24-bookworm-slim@sha256:e8e2e91b1378f83c5b2dd15f0247f34110e2fe895f6ca7719dbb780f929368eb"
ARG OPENCLAW_NODE_BOOKWORM_SLIM_DIGEST="sha256:e8e2e91b1378f83c5b2dd15f0247f34110e2fe895f6ca7719dbb780f929368eb"

# Base images are pinned to SHA256 digests for reproducible builds.
# Trade-off: digests must be updated manually when upstream tags move.
# To update, run: docker buildx imagetools inspect node:24-bookworm (or podman)
# and replace the digest below with the current multi-arch manifest list entry.

FROM ${OPENCLAW_NODE_BOOKWORM_IMAGE} AS ext-deps
ARG OPENCLAW_EXTENSIONS
ARG OPENCLAW_BUNDLED_PLUGIN_DIR
COPY ${OPENCLAW_BUNDLED_PLUGIN_DIR} /tmp/${OPENCLAW_BUNDLED_PLUGIN_DIR}
# Copy package.json for opted-in extensions so pnpm resolves their deps.
# Note: events-emitter is NOT routed through here — it carries deps the upstream
# root pnpm-lock.yaml does not know about (typescript, vitest, @modelcontextprotocol/sdk).
# It is compiled standalone in the emitter-build stage below and shipped as
# pre-built JS under dist/extensions/events-emitter/ in the final stage.
RUN mkdir -p /out && \
    for ext in $OPENCLAW_EXTENSIONS; do \
      if [ -f "/tmp/${OPENCLAW_BUNDLED_PLUGIN_DIR}/$ext/package.json" ]; then \
        mkdir -p "/out/$ext" && \
        cp "/tmp/${OPENCLAW_BUNDLED_PLUGIN_DIR}/$ext/package.json" "/out/$ext/package.json"; \
      fi; \
    done

# ── Stage 1b: emitter-build ─────────────────────────────────────
# Compile the Codie events-emitter (TypeScript → JavaScript). OpenClaw's runtime
# plugin discovery (src/plugins/bundled-dir.ts) scans dist/extensions/ in a
# packaged (non-source-checkout) image, and the loader resolves each plugin
# entry to *.js — a raw .ts source tree under extensions/ is NEVER discovered.
# So the emitter must ship as compiled JS under dist/extensions/events-emitter/
# (spliced in the final stage). Build against the emitter's OWN pnpm-lock.yaml
# so the upstream OpenClaw root lockfile is untouched.
FROM ${OPENCLAW_NODE_BOOKWORM_IMAGE} AS emitter-build
WORKDIR /work
# Pin pnpm to OpenClaw's version. Without this, corepack pulls the latest
# (currently pnpm 11.x), which enforces a default minimumReleaseAge supply-chain
# policy and rejects any transitive dep published within the last ~24h
# (e.g. eventsource-parser 3.1.0 via @modelcontextprotocol/sdk).
RUN corepack enable && corepack prepare pnpm@10.32.1 --activate
COPY --from=emitter package.json pnpm-lock.yaml ./
# Full install (incl. devDependencies: typescript) — required to compile.
RUN --mount=type=cache,id=openclaw-emitter-pnpm-store,target=/root/.local/share/pnpm/store,sharing=locked \
    pnpm install --frozen-lockfile
COPY --from=emitter . ./
# Emit JS into build/ (index.js + src/*.js, no .ts), then stage the manifest
# files alongside so build/ is a self-contained bundled plugin directory:
#   build/{index.js, src/*.js, package.json, openclaw.plugin.json}
# @modelcontextprotocol/sdk (the sole runtime dep) is NOT bundled here — it
# resolves from the root /app/node_modules at runtime (OpenClaw depends on the
# same version). The loader rewrites package.json's "./index.ts" entry to
# index.js, and falls back to it since no .ts ships in build/.
RUN pnpm build && cp package.json openclaw.plugin.json build/

# ── Stage 2: Build ──────────────────────────────────────────────
FROM ${OPENCLAW_NODE_BOOKWORM_IMAGE} AS build
ARG OPENCLAW_BUNDLED_PLUGIN_DIR

# Install Bun (required for build scripts). Retry the whole bootstrap flow to
# tolerate transient 5xx failures from bun.sh/GitHub during CI image builds.
RUN set -eux; \
    for attempt in 1 2 3 4 5; do \
      if curl --retry 5 --retry-all-errors --retry-delay 2 -fsSL https://bun.sh/install | bash; then \
        break; \
      fi; \
      if [ "$attempt" -eq 5 ]; then \
        exit 1; \
      fi; \
      sleep $((attempt * 2)); \
    done
ENV PATH="/root/.bun/bin:${PATH}"

RUN corepack enable

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
COPY openclaw.mjs ./
COPY ui/package.json ./ui/package.json
COPY patches ./patches
# Copy the whole scripts/ tree (not just the postinstall helper) so pnpm
# lifecycle hooks resolve their full import closure. Upstream OpenClaw moves
# install-hook deps around between releases (e.g. preinstall-package-manager-
# warning.mjs, lib/package-dist-imports.mjs landed in 2026.5.x) — a selective
# copy silently breaks `pnpm install` on every such drift. scripts/ is small;
# the cost is busting the install layer cache when any script changes.
COPY scripts ./scripts

COPY --from=ext-deps /out/ ./${OPENCLAW_BUNDLED_PLUGIN_DIR}/

# Reduce OOM risk on low-memory hosts during dependency installation.
# Docker builds on small VMs may otherwise fail with "Killed" (exit 137).
RUN --mount=type=cache,id=openclaw-pnpm-store,target=/root/.local/share/pnpm/store,sharing=locked \
    NODE_OPTIONS=--max-old-space-size=2048 pnpm install --frozen-lockfile

COPY . .

# Note: the Codie events-emitter is NOT spliced into the source extensions/ tree.
# It ships pre-compiled under dist/extensions/events-emitter/ (final stage, from
# the emitter-build stage) — that is what OpenClaw's runtime discovery actually
# scans in a packaged image. Keeping it out of /app/extensions avoids polluting
# the upstream pnpm workspace during build:docker / ui:build / qa:lab:build.

# Normalize extension paths now so runtime COPY preserves safe modes
# without adding a second full extensions layer.
RUN for dir in /app/${OPENCLAW_BUNDLED_PLUGIN_DIR} /app/.agent /app/.agents; do \
      if [ -d "$dir" ]; then \
        find "$dir" -type d -exec chmod 755 {} +; \
        find "$dir" -type f -exec chmod 644 {} +; \
      fi; \
    done

# A2UI bundle may fail under QEMU cross-compilation (e.g. building amd64
# on Apple Silicon). CI builds natively per-arch so this is a no-op there.
# Stub it so local cross-arch builds still succeed.
#
# The bundle's canvas build hook fetches cross-platform native binaries
# (@napi-rs/canvas, @openai/codex, @lancedb for win32/darwin/riscv64) from npm.
# Through Colima's slow NAT one of those streams can stall and pnpm's retry loop
# then hangs indefinitely (observed: 15+ min no progress). Cap wall-clock and
# disable long retries so a stalled fetch deterministically falls through to the
# stub. The bundle is not needed for the Codie events-emitter path.
RUN timeout 480 env \
      npm_config_fetch_retries=1 \
      npm_config_fetch_retry_mintimeout=2000 \
      npm_config_fetch_retry_maxtimeout=15000 \
      npm_config_fetch_timeout=120000 \
      pnpm canvas:a2ui:bundle || \
    (echo "A2UI bundle: creating stub (non-fatal)" && \
     mkdir -p src/canvas-host/a2ui && \
     echo "/* A2UI bundle unavailable in this build */" > src/canvas-host/a2ui/a2ui.bundle.js && \
     echo "stub" > src/canvas-host/a2ui/.bundle.hash && \
     rm -rf vendor/a2ui apps/shared/OpenClawKit/Tools/CanvasA2UI)
RUN pnpm build:docker
# Force pnpm for UI build (Bun may fail on ARM/Synology architectures)
ENV OPENCLAW_PREFER_PNPM=1
RUN pnpm ui:build
RUN pnpm qa:lab:build
# Mark /app as a source checkout so postinstall-bundled-plugins.mjs takes
# the source-checkout path (no dist inventory prune). Our .dockerignore
# omits .git, which upstream's source-checkout detection requires.
RUN mkdir -p .git

# Prune dev dependencies and strip build-only metadata before copying
# runtime assets into the final image.
FROM build AS runtime-assets
ARG OPENCLAW_EXTENSIONS
ARG OPENCLAW_BUNDLED_PLUGIN_DIR
# Prune dev deps using upstream's offline + arch-scoped strategy (mirrors the
# bundled openclaw ./Dockerfile runtime-assets stage). Do NOT rewrite
# pnpm-workspace.yaml here: in OpenClaw 2026.5.x `pnpm prune` validates the
# lockfile's `overrides` against the on-disk workspace, so a rewritten workspace
# trips ERR_PNPM_LOCKFILE_CONFIG_MISMATCH. The pnpm store cache mount (same id as
# the install step) backs --config.offline=true so prune never hits the network.
# prune-docker-plugin-dist.mjs only removes dist for root-package-excluded
# plugins; our events-emitter is COPYed into dist in the final stage, after this,
# so it is untouched.
RUN --mount=type=cache,id=openclaw-pnpm-store,target=/root/.local/share/pnpm/store,sharing=locked \
    CI=true pnpm prune --prod \
      --config.offline=true \
      --config.supportedArchitectures.os=linux \
      --config.supportedArchitectures.cpu="$(node -p 'process.arch')" \
      --config.supportedArchitectures.libc=glibc && \
    node scripts/postinstall-bundled-plugins.mjs && \
    OPENCLAW_EXTENSIONS="$OPENCLAW_EXTENSIONS" node scripts/prune-docker-plugin-dist.mjs && \
    find dist -type f \( -name '*.d.ts' -o -name '*.d.mts' -o -name '*.d.cts' -o -name '*.map' \) -delete && \
    node scripts/check-package-dist-imports.mjs /app

# ── Runtime base images ─────────────────────────────────────────
FROM ${OPENCLAW_NODE_BOOKWORM_IMAGE} AS base-default
ARG OPENCLAW_NODE_BOOKWORM_DIGEST
LABEL org.opencontainers.image.base.name="docker.io/library/node:24-bookworm" \
  org.opencontainers.image.base.digest="${OPENCLAW_NODE_BOOKWORM_DIGEST}"

FROM ${OPENCLAW_NODE_BOOKWORM_SLIM_IMAGE} AS base-slim
ARG OPENCLAW_NODE_BOOKWORM_SLIM_DIGEST
LABEL org.opencontainers.image.base.name="docker.io/library/node:24-bookworm-slim" \
  org.opencontainers.image.base.digest="${OPENCLAW_NODE_BOOKWORM_SLIM_DIGEST}"

# ── Stage 3: Runtime ────────────────────────────────────────────
FROM base-${OPENCLAW_VARIANT}
ARG OPENCLAW_VARIANT
ARG OPENCLAW_BUNDLED_PLUGIN_DIR
ARG OPENCLAW_DOCKER_APT_UPGRADE

# OCI base-image metadata for downstream image consumers.
# If you change these annotations, also update:
# - docs/install/docker.md ("Base image metadata" section)
# - https://docs.openclaw.ai/install/docker
LABEL org.opencontainers.image.source="https://github.com/openclaw/openclaw" \
  org.opencontainers.image.url="https://openclaw.ai" \
  org.opencontainers.image.documentation="https://docs.openclaw.ai/install/docker" \
  org.opencontainers.image.licenses="MIT" \
  org.opencontainers.image.title="OpenClaw" \
  org.opencontainers.image.description="OpenClaw gateway and CLI runtime container image"

WORKDIR /app

# Install system utilities present in bookworm but missing in bookworm-slim.
# On the full bookworm image these are already installed (apt-get is a no-op).
# Smoke workflows can opt out of distro upgrades to cut repeated CI time while
# keeping the default runtime image behavior unchanged.
RUN --mount=type=cache,id=openclaw-bookworm-apt-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=openclaw-bookworm-apt-lists,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    if [ "${OPENCLAW_DOCKER_APT_UPGRADE}" != "0" ]; then \
      DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends; \
    fi && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      procps hostname curl git lsof openssl

# Comfortable baseline toolchain shared by all codie-agent agent images.
# Sourced via buildx --build-context shared=<repo>/docker-registry/shared
# (see scripts/build-openclaw-docker.sh). Added to the final runtime stage
# only — build stages already have their own toolchains. Delta over the
# apt line above is wget/jq/unzip/zip/xz-utils/tree/bc/ca-certificates.
# See docs/plans/2026-04-24-sidecar-tools-architecture.md.
COPY --from=shared install-agent-baseline.sh /tmp/
RUN bash /tmp/install-agent-baseline.sh && rm /tmp/install-agent-baseline.sh

RUN chown node:node /app

COPY --from=runtime-assets --chown=node:node /app/dist ./dist
COPY --from=runtime-assets --chown=node:node /app/node_modules ./node_modules
COPY --from=runtime-assets --chown=node:node /app/package.json .
COPY --from=runtime-assets --chown=node:node /app/openclaw.mjs .
COPY --from=runtime-assets --chown=node:node /app/${OPENCLAW_BUNDLED_PLUGIN_DIR} ./${OPENCLAW_BUNDLED_PLUGIN_DIR}
# Codie events-emitter — compiled bundled plugin. OpenClaw's runtime discovery
# (src/plugins/bundled-dir.ts) scans dist/extensions/ in a packaged image, so
# the emitter ships there as compiled JS + package.json + openclaw.plugin.json
# (built in the emitter-build stage). Its sole runtime dep
# (@modelcontextprotocol/sdk) resolves from the root /app/node_modules — OpenClaw
# depends on the same version — so no plugin-local node_modules is required.
# Spliced in the final stage to bypass the build-stage pnpm workspace and the
# runtime-assets prune (postinstall-bundled-plugins.mjs) entirely.
COPY --from=emitter-build --chown=node:node /work/build ./dist/extensions/events-emitter
COPY --from=runtime-assets --chown=node:node /app/skills ./skills
COPY --from=runtime-assets --chown=node:node /app/docs ./docs
COPY --from=runtime-assets --chown=node:node /app/qa ./qa

# Keep pnpm available in the runtime image for container-local workflows.
# Use a shared Corepack home so the non-root `node` user does not need a
# first-run network fetch when invoking pnpm.
ENV COREPACK_HOME=/usr/local/share/corepack
RUN install -d -m 0755 "$COREPACK_HOME" && \
    corepack enable && \
    for attempt in 1 2 3 4 5; do \
      if corepack prepare "$(node -p "require('./package.json').packageManager")" --activate; then \
        break; \
      fi; \
      if [ "$attempt" -eq 5 ]; then \
        exit 1; \
      fi; \
      sleep $((attempt * 2)); \
    done && \
    chmod -R a+rX "$COREPACK_HOME"

# Install additional system packages needed by your skills or extensions.
# Example: docker build --build-arg OPENCLAW_DOCKER_APT_PACKAGES="python3 wget" .
ARG OPENCLAW_DOCKER_APT_PACKAGES=""
RUN --mount=type=cache,id=openclaw-bookworm-apt-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=openclaw-bookworm-apt-lists,target=/var/lib/apt,sharing=locked \
    if [ -n "$OPENCLAW_DOCKER_APT_PACKAGES" ]; then \
      apt-get update && \
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $OPENCLAW_DOCKER_APT_PACKAGES; \
    fi

# Optionally install Chromium and Xvfb for browser automation.
# Build with: docker build --build-arg OPENCLAW_INSTALL_BROWSER=1 ...
# Adds ~300MB but eliminates the 60-90s Playwright install on every container start.
# Must run after node_modules COPY so playwright-core is available.
ARG OPENCLAW_INSTALL_BROWSER=""
RUN --mount=type=cache,id=openclaw-bookworm-apt-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=openclaw-bookworm-apt-lists,target=/var/lib/apt,sharing=locked \
    if [ -n "$OPENCLAW_INSTALL_BROWSER" ]; then \
      apt-get update && \
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends xvfb && \
      mkdir -p /home/node/.cache/ms-playwright && \
      PLAYWRIGHT_BROWSERS_PATH=/home/node/.cache/ms-playwright \
      node /app/node_modules/playwright-core/cli.js install --with-deps chromium && \
      chown -R node:node /home/node/.cache/ms-playwright; \
    fi

# Optionally install Docker CLI for sandbox container management.
# Build with: docker build --build-arg OPENCLAW_INSTALL_DOCKER_CLI=1 ...
# Adds ~50MB. Only the CLI is installed — no Docker daemon.
# Required for agents.defaults.sandbox to function in Docker deployments.
ARG OPENCLAW_INSTALL_DOCKER_CLI=""
ARG OPENCLAW_DOCKER_GPG_FINGERPRINT="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
RUN --mount=type=cache,id=openclaw-bookworm-apt-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=openclaw-bookworm-apt-lists,target=/var/lib/apt,sharing=locked \
    if [ -n "$OPENCLAW_INSTALL_DOCKER_CLI" ]; then \
      apt-get update && \
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg && \
      install -m 0755 -d /etc/apt/keyrings && \
      # Verify Docker apt signing key fingerprint before trusting it as a root key.
      # Update OPENCLAW_DOCKER_GPG_FINGERPRINT when Docker rotates release keys.
      curl -fsSL https://download.docker.com/linux/debian/gpg -o /tmp/docker.gpg.asc && \
      expected_fingerprint="$(printf '%s' "$OPENCLAW_DOCKER_GPG_FINGERPRINT" | tr '[:lower:]' '[:upper:]' | tr -d '[:space:]')" && \
      actual_fingerprint="$(gpg --batch --show-keys --with-colons /tmp/docker.gpg.asc | awk -F: '$1 == "fpr" { print toupper($10); exit }')" && \
      if [ -z "$actual_fingerprint" ] || [ "$actual_fingerprint" != "$expected_fingerprint" ]; then \
        echo "ERROR: Docker apt key fingerprint mismatch (expected $expected_fingerprint, got ${actual_fingerprint:-<empty>})" >&2; \
        exit 1; \
      fi && \
      gpg --dearmor -o /etc/apt/keyrings/docker.gpg /tmp/docker.gpg.asc && \
      rm -f /tmp/docker.gpg.asc && \
      chmod a+r /etc/apt/keyrings/docker.gpg && \
      printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable\n' \
        "$(dpkg --print-architecture)" > /etc/apt/sources.list.d/docker.list && \
      apt-get update && \
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        docker-ce-cli docker-compose-plugin; \
    fi

# Expose the CLI binary without requiring npm global writes as non-root.
RUN ln -sf /app/openclaw.mjs /usr/local/bin/openclaw \
 && chmod 755 /app/openclaw.mjs

ENV NODE_ENV=production

# Security hardening: Run as non-root user
# The node:24-bookworm image includes a 'node' user (uid 1000)
# This reduces the attack surface by preventing container escape via root privileges
USER node

# Bridge's codie injector runs as the image's USER before exec'ing the real
# gateway CMD. It must chown the seeded config file to node:node, which
# requires root. Mirror the hermes-agent pattern (which ends with `USER
# root` for the same reason) and drop privileges back to node in the
# manifest's on_complete via `runuser -u node --`.
USER root

# Start gateway server with default config.
# Binds to loopback (127.0.0.1) by default for security.
#
# IMPORTANT: With Docker bridge networking (-p 18789:18789), loopback bind
# makes the gateway unreachable from the host. Either:
#   - Use --network host, OR
#   - Override --bind to "lan" (0.0.0.0) and set auth credentials
#
# Built-in probe endpoints for container health checks:
#   - GET /healthz (liveness) and GET /readyz (readiness)
#   - aliases: /health and /ready
# For external access from host/ingress, override bind to "lan" and set auth.
HEALTHCHECK --interval=3m --timeout=10s --start-period=15s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:18789/healthz').then((r)=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "openclaw.mjs", "gateway", "--allow-unconfigured"]
