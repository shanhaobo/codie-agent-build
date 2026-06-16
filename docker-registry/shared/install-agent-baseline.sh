#!/usr/bin/env bash
# Install the "comfortable baseline" toolchain shared by all codie-agent agent
# images (hermes, codieClaw/CodieAgent, openclaw, openhuman).
#
# Single source of truth — modify this file to add/remove baseline tools;
# every agent image picks up the change on next rebuild.
#
# This script is intentionally minimal:
#   - Heavy tools (ffmpeg, yt-dlp, pandoc, chromium, ...) live in MCP sidecars,
#     NOT here. See docs/plans/2026-04-24-sidecar-tools-architecture.md.
#   - Only Debian/Ubuntu (apt) is supported. Non-Debian bases are a no-op
#     (the script exits 0 with a warning so non-Debian agent builds don't
#     fail).
#
# Inputs (env, optional):
#   INSTALL_AGENT_BASELINE_EXTRA  space-separated extra apt packages
#   INSTALL_AGENT_BASELINE_SKIP   set non-empty to skip entirely (useful for
#                                 agents that already include the baseline
#                                 via their upstream image)
#
# Usage (inside a Dockerfile):
#   COPY docker-registry/shared/install-agent-baseline.sh /tmp/
#   RUN bash /tmp/install-agent-baseline.sh && rm /tmp/install-agent-baseline.sh

set -euo pipefail

if [[ -n "${INSTALL_AGENT_BASELINE_SKIP:-}" ]]; then
    echo "[install-agent-baseline] skipped via INSTALL_AGENT_BASELINE_SKIP"
    exit 0
fi

# Detect apt availability. Non-Debian bases: no-op (warn, exit 0).
if ! command -v apt-get >/dev/null 2>&1; then
    echo "[install-agent-baseline] WARN: apt-get not found; this base is not Debian/Ubuntu. Skipping baseline install."
    exit 0
fi

# Baseline — keep small, stable, foundational.
BASELINE=(
    bash
    coreutils
    git
    curl
    wget
    jq
    unzip
    zip
    xz-utils
    ca-certificates
    # small-but-high-value additions:
    tree
    bc
)

# Optional per-image extras (space-separated).
read -r -a EXTRA <<< "${INSTALL_AGENT_BASELINE_EXTRA:-}"

export DEBIAN_FRONTEND=noninteractive
echo "[install-agent-baseline] installing: ${BASELINE[*]} ${EXTRA[*]:-}"

apt-get update
apt-get install -y --no-install-recommends "${BASELINE[@]}" "${EXTRA[@]}"

# CA-certs post-install hook isn't guaranteed to run before apt cleanup on some
# minimal bases; force-refresh so HTTPS works in subsequent RUN layers.
update-ca-certificates || true

# Cleanup apt cache to keep image layers slim.
apt-get clean
rm -rf /var/lib/apt/lists/*

echo "[install-agent-baseline] done."
