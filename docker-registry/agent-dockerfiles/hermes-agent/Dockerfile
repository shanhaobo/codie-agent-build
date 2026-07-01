FROM ghcr.io/astral-sh/uv:0.11.6-python3.13-trixie@sha256:b3c543b6c4f23a5f2df22866bd7857e5d304b67a564f4feab6ac22044dde719b AS uv_source
FROM debian:13.4

# Disable Python stdout buffering to ensure logs are printed immediately
ENV PYTHONUNBUFFERED=1

# Install system dependencies in one layer, clear APT cache.
# python3-yaml is required by Bridge's codie injector (mounted at /opt/codie
# at runtime) which uses system python3, not the hermes uv venv.
# NOTE: nodejs/npm kept for any remaining Node-based Hermes tools; Playwright
# and WhatsApp bridge removed — browser automation goes to browser_mcp sidecar
# (Phase 3), WhatsApp bridge dropped (needed openssh-client + SSH key for its
# git+ssh dep, not worth carrying).
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential nodejs npm python3 python3-yaml ripgrep ffmpeg gcc python3-dev libffi-dev procps git gosu && \
    rm -rf /var/lib/apt/lists/*

# Comfortable baseline toolchain shared by all codie-agent agent images.
# Sourced via buildx --build-context shared=<repo>/docker-registry/shared.
# See docs/plans/2026-04-24-sidecar-tools-architecture.md. Mostly a no-op for
# hermes (ffmpeg/git/ripgrep already installed above); adds curl/wget/jq/
# unzip/tree/bc for behavioral parity with other agent images.
# NOTE: ffmpeg baked in upstream is preserved during Phase 1; will migrate
# to media_mcp sidecar in Phase 2 and remove from this image.
COPY --from=shared install-agent-baseline.sh /tmp/
RUN bash /tmp/install-agent-baseline.sh && rm /tmp/install-agent-baseline.sh

# Non-root user for runtime; UID can be overridden via HERMES_UID at runtime
RUN useradd -u 10000 -m -d /opt/data hermes

COPY --chmod=0755 --from=uv_source /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/

COPY . /opt/hermes
WORKDIR /opt/hermes

# Install top-level Node deps only (no Playwright browsers, no whatsapp-bridge).
# PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD blocks agent-browser / playwright npm package
# from auto-fetching chromium during install — browsers live in browser_mcp
# sidecar (Phase 3).
RUN PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --prefer-offline --no-audit && \
    npm cache clean --force

# Hand ownership to hermes user, then install Python deps in a virtualenv
RUN chown -R hermes:hermes /opt/hermes
USER hermes

RUN uv venv && \
    uv pip install --no-cache-dir -e ".[all]"

# --- Codie boundary-events emitter (runtime shim, no Hermes source edits) ---
# Installs an importable package + drops a .pth so it auto-activates at
# interpreter startup (gated on CODIE_INSTANCE_ID, set by Bridge at run time).
# Sourced via buildx --build-context emitter=<repo>/docker-registry/
# agent-dockerfiles/hermes-agent/extensions/hermes-codie-emitter — the package
# lives outside the upstream clone (the build context), so it cannot be COPYed
# by a path relative to the context (mirrors the OpenClaw events-emitter wiring).
# Install with `uv pip` (not venv pip): `uv venv` above does not seed pip into
# /opt/hermes/.venv, so `python -m pip` would not exist; uv targets the venv via
# --python. Use sysconfig purelib (robust in venvs where getsitepackages may be
# unavailable) to locate the .pth drop directory.
# --chown=hermes:hermes is REQUIRED: USER is `hermes` here, and `uv pip install`
# builds the sdist in-tree (setuptools writes hermes_codie_emitter.egg-info/ and
# build/ into the source dir). A root-owned COPY makes that write fail with
# "Permission denied". (useradd line above creates the hermes user+group.)
COPY --chown=hermes:hermes --from=emitter . /opt/codie-emitter/hermes-codie-emitter
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-deps /opt/codie-emitter/hermes-codie-emitter && \
    SP="$(/opt/hermes/.venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')" && \
    printf 'import hermes_codie_emitter.autostart\n' > "$SP/hermes_codie_emitter.pth" && \
    echo "[codie-emitter] installed; .pth at $SP/hermes_codie_emitter.pth"

USER root
# Overwrite upstream's docker/entrypoint.sh with codie's pre-s6 entrypoint.
#
# Upstream migrated container startup to s6-overlay: the real ENTRYPOINT is now
# /init and docker/entrypoint.sh is a deprecated shim that runs only the
# cont-init bootstrap (via `s6-setuidgid`) and does NOT exec the CMD. codie's
# launch path bypasses /init entirely — Bridge's codie_inject.py runs
# `exec /opt/hermes/docker/entrypoint.sh gateway run` (see hermes_manifest.dart)
# expecting the pre-s6 "bootstrap + gosu-drop + exec hermes $@" contract. Under
# the s6 shim that crashes with `s6-setuidgid: not found` (exit 127) — s6 is not
# installed here — and never starts the gateway. codie-entrypoint.sh restores
# that contract using gosu (installed above). Sourced via the `codie` build
# context (see build-hermes-docker.sh). See project_hermes_image_s6_broken.
COPY --chmod=0755 --from=codie codie-entrypoint.sh /opt/hermes/docker/entrypoint.sh

ENV HERMES_HOME=/opt/data
VOLUME [ "/opt/data" ]
ENTRYPOINT [ "/opt/hermes/docker/entrypoint.sh" ]
