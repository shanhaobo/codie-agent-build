#!/bin/bash
# codie-owned Hermes container entrypoint (pre-s6 contract).
#
# WHY THIS FILE EXISTS
# --------------------
# Upstream Hermes migrated its container startup to s6-overlay: the image's real
# ENTRYPOINT became /init, and docker/entrypoint.sh was demoted to a deprecated
# shim that only runs the cont-init bootstrap (via `s6-setuidgid`) and NO LONGER
# execs the container CMD.
#
# codie does NOT use the image's /init entrypoint. Bridge launches Hermes with an
# entrypoint override (codie_inject.py) whose on_complete step runs:
#     exec /opt/hermes/docker/entrypoint.sh gateway run
# (see codie-agent-bridge lib/services/agent_manifests/hermes_manifest.dart).
# That path depends on the ORIGINAL pre-s6 contract: "bootstrap the volume, drop
# to the hermes user via gosu, then exec `hermes <args>`". Under upstream's s6
# shim it crashes with `s6-setuidgid: not found` (exit 127) — s6-overlay is not
# installed in codie's stripped image — and never starts the gateway.
#
# The Dockerfile overwrites the upstream shim with THIS script, restoring the
# exact contract codie was built and verified against. Privilege drop uses gosu
# (installed in the Dockerfile). No s6 dependency.
set -e

HERMES_HOME="${HERMES_HOME:-/opt/data}"
INSTALL_DIR="/opt/hermes"

# --- Privilege dropping via gosu ---
# When started as root (the default), optionally remap the hermes user/group to
# host-side ownership, fix volume permissions, then re-exec as hermes.
if [ "$(id -u)" = "0" ]; then
    if [ -n "${HERMES_UID:-}" ] && [ "$HERMES_UID" != "$(id -u hermes)" ]; then
        echo "Changing hermes UID to $HERMES_UID"
        usermod -u "$HERMES_UID" hermes
    fi

    if [ -n "${HERMES_GID:-}" ] && [ "$HERMES_GID" != "$(id -g hermes)" ]; then
        echo "Changing hermes GID to $HERMES_GID"
        # -o allows a non-unique GID (e.g. macOS GID 20 "staff" may already
        # exist as "dialout" in the Debian-based image).
        groupmod -o -g "$HERMES_GID" hermes 2>/dev/null || true
    fi

    actual_hermes_uid=$(id -u hermes)
    if [ "$(stat -c %u "$HERMES_HOME" 2>/dev/null)" != "$actual_hermes_uid" ]; then
        echo "$HERMES_HOME is not owned by $actual_hermes_uid, fixing"
        chown -R hermes:hermes "$HERMES_HOME" 2>/dev/null || \
            echo "Warning: chown failed (rootless container?) — continuing anyway"
    fi

    echo "Dropping root privileges"
    exec gosu hermes "$0" "$@"
fi

# --- Running as hermes from here ---
# shellcheck disable=SC1091
. "${INSTALL_DIR}/.venv/bin/activate"

# Essential per-profile directory structure (upstream issue #4426): "home/" is a
# per-profile HOME for subprocesses (git/ssh/gh/npm) so they don't write to the
# ephemeral /root.
mkdir -p "$HERMES_HOME"/{cron,sessions,logs,hooks,memories,skills,skins,plans,workspace,home}

# Seed config files into the volume on first run. Each copy is guarded on source
# existence so an upstream rename can't `set -e`-abort the gateway launch. When
# codie's injector (codie_inject.py) already wrote config.yaml / SOUL.md, the
# `! -f` checks skip and the injected files are preserved (Bridge relies on this
# — see hermes_manifest.dart's SOUL.md note).
if [ ! -f "$HERMES_HOME/.env" ] && [ -f "$INSTALL_DIR/.env.example" ]; then
    cp "$INSTALL_DIR/.env.example" "$HERMES_HOME/.env"
fi
if [ ! -f "$HERMES_HOME/config.yaml" ] && [ -f "$INSTALL_DIR/cli-config.yaml.example" ]; then
    cp "$INSTALL_DIR/cli-config.yaml.example" "$HERMES_HOME/config.yaml"
fi
if [ ! -f "$HERMES_HOME/SOUL.md" ] && [ -f "$INSTALL_DIR/docker/SOUL.md" ]; then
    cp "$INSTALL_DIR/docker/SOUL.md" "$HERMES_HOME/SOUL.md"
fi

# Sync bundled skills (best-effort; never block the gateway on it).
if [ -f "$INSTALL_DIR/tools/skills_sync.py" ]; then
    python3 "$INSTALL_DIR/tools/skills_sync.py" || \
        echo "Warning: skills_sync.py failed; continuing"
fi

# Final exec. A first arg that resolves to an executable runs directly
# (e.g. `sleep infinity`, `bash`); otherwise treat the args as a hermes
# subcommand and wrap with `hermes` (so `... gateway run` -> `hermes gateway run`).
if [ $# -gt 0 ] && command -v "$1" >/dev/null 2>&1; then
    exec "$@"
fi
exec hermes "$@"
