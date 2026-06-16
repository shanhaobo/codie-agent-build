#!/bin/sh
# OpenHuman container supervisor.
#
# - Pre-seeds OPENHUMAN_CORE_TOKEN and starts `openhuman-core serve` in the
#   background; core adopts the env token (init_rpc_token Tauri-shell
#   fast-path) so core and shim share it by construction.
# - Waits for core's /health before starting the shim.
# - Execs the Python shim in the foreground; shim death → container death,
#   tini reaps openhuman-core.

set -e

# Translate /opt/shim/mcp_servers.json (codie-injector text-target) into the
# TOML shape openhuman-core expects at $OPENHUMAN_WORKSPACE/config.toml. Must
# happen BEFORE the daemon starts so it picks up our MCP server registration
# during config load. No-op if the injector didn't drop a manifest.
echo "[supervisor] translating mcp_servers.json → config.toml"
python /opt/openhuman/write_config_toml.py

# Pre-seed the RPC bearer token. Do NOT read $OPENHUMAN_WORKSPACE/core.token:
# core's standalone path regenerates that file on EVERY boot, and the
# workspace is a persistent volume — on container restart the previous boot's
# file is still there, so a file-wait exports a stale token and every shim
# /rpc call 401s (hit 2026-06-10). With the env pre-seeded, core adopts our
# token and never touches the file.
OPENHUMAN_CORE_TOKEN="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export OPENHUMAN_CORE_TOKEN

echo "[supervisor] starting openhuman-core on ${OPENHUMAN_CORE_HOST}:${OPENHUMAN_CORE_PORT}"
openhuman-core serve &
OH_PID=$!

# Wait up to ~30 seconds for core to serve /health (unauthenticated liveness).
if ! python -c "
import sys, time, urllib.request
url = 'http://${OPENHUMAN_CORE_HOST:-127.0.0.1}:${OPENHUMAN_CORE_PORT:-7788}/health'
for _ in range(60):
    try:
        urllib.request.urlopen(url, timeout=2)
        sys.exit(0)
    except Exception:
        time.sleep(0.5)
sys.exit(1)
"; then
    echo "[supervisor] ERROR: openhuman-core /health never came up (waited 30s)"
    kill "$OH_PID" 2>/dev/null || true
    exit 1
fi

echo "[supervisor] openhuman-core ready (pid=$OH_PID), starting shim on ${SHIM_WS_HOST}:${SHIM_WS_PORT}"
# Shim package layout: /opt/shim/{__init__,__main__,rpc_client,ws_server}.py.
# For `python -m shim` to resolve the package, /opt must be on sys.path so the
# `shim` directory is importable. PYTHONPATH avoids touching the package layout.
exec env PYTHONPATH=/opt python -m shim
