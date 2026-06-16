#!/usr/bin/env bash
# trust-registry-ca.sh — make THIS machine trust the Codie TLS registry.
#
# Run on every LAN *consumer* machine (i.e. any Bridge desktop that is NOT the
# registry host). The registry host itself is set up by the flag-day cutover and
# resolves registry.codie.lan -> 127.0.0.1; consumers resolve it -> the host's
# LAN IP. See docs/plans/2026-06-15-registry-tls-design.md (§6 step 5 / L6).
#
# It does two things, both idempotent:
#   1. Installs the registry CA so the local docker daemon trusts
#      https://registry.codie.lan:5000 via per-registry certs.d (NOT a global
#      insecure-registries entry — certs.d pins the CA to this one host:port and
#      is read LIVE, no daemon restart).
#   2. Maps registry.codie.lan -> <registry-host-lan-ip> in the relevant
#      /etc/hosts so the name resolves. The cert binds the NAME, so when the
#      host's LAN IP drifts you only re-run this with the new IP — the registry,
#      the certs, and every Bridge's registryUrl stay untouched.
#
# Usage:
#   ./trust-registry-ca.sh <registry-host-lan-ip> [path/to/ca.crt]
#
#   <registry-host-lan-ip>  LAN IP of the machine running the registry (e.g.
#                           10.10.32.64). registry.codie.lan resolves here.
#   ca.crt                  defaults to ../certs/ca.crt next to this script
#                           (copy docker-registry/certs/ca.crt from the host —
#                           the CA, never ca.key).
#
# Engines handled: colima (macOS) | native dockerd (Linux) | inside a WSL2
# distro. On Windows, run trust-registry-ca.ps1 instead (it edits the Windows
# hosts file and delegates the distro side to this logic via wsl).
#
# After running: in that machine's Bridge → Images page → switch to TLS mode,
# import the same CA (shield icon), set registryUrl = registry.codie.lan:5000.
set -euo pipefail

DOMAIN="registry.codie.lan"
PORT="5000"
HOSTPORT="${DOMAIN}:${PORT}"
CERTS_D="/etc/docker/certs.d/${HOSTPORT}"

log()  { printf '\033[36m[trust-ca]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[trust-ca] WARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[trust-ca] error:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

REG_IP="${1:-}"
CA_SRC="${2:-}"

case "$REG_IP" in
    ""|-h|--help) usage; [ -z "$REG_IP" ] && exit 1 || exit 0 ;;
esac

# Basic IPv4 sanity (a hostname is also acceptable, so only warn).
if ! printf '%s' "$REG_IP" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
    warn "'$REG_IP' is not a dotted IPv4 — proceeding (assuming resolvable name)"
fi

if [ -z "$CA_SRC" ]; then
    CA_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/certs/ca.crt"
fi
[ -f "$CA_SRC" ] || die "CA not found: $CA_SRC (copy docker-registry/certs/ca.crt from the registry host)"
openssl x509 -in "$CA_SRC" -noout -subject >/dev/null 2>&1 \
    || die "$CA_SRC is not a valid PEM certificate"
CA_PEM="$(cat "$CA_SRC")"
log "CA: $CA_SRC ($(openssl x509 -in "$CA_SRC" -noout -subject 2>/dev/null | sed 's/^subject=//'))"
log "mapping ${DOMAIN} -> ${REG_IP}"

# Rewrite an /etc/hosts (or remote-equivalent) entry idempotently. $1 = a shell
# prefix to run the mutation with the right privileges/host (e.g. "sudo sh -c"
# locally, or "colima ssh -- sudo sh -c" in the VM). Removes any prior line
# bearing the domain, then appends a fresh one.
_rewrite_hosts() {
    # shellcheck disable=SC2086
    $1 "tmp=\$(mktemp); grep -vF ' ${DOMAIN}' /etc/hosts > \"\$tmp\" 2>/dev/null || true; cat \"\$tmp\" > /etc/hosts; rm -f \"\$tmp\"; printf '%s %s\n' '${REG_IP}' '${DOMAIN}' >> /etc/hosts"
}

detect_engine() {
    if command -v colima >/dev/null 2>&1 && colima status >/dev/null 2>&1; then
        echo colima; return
    fi
    if grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
        echo wsl-inside; return
    fi
    if [ -d /etc/docker ] || [ -S /var/run/docker.sock ]; then
        echo linux; return
    fi
    echo unknown
}

install_certs_local() {  # sudo to local /etc/docker/certs.d
    log "installing CA -> ${CERTS_D}/ca.crt (sudo)"
    sudo mkdir -p "$CERTS_D"
    printf '%s' "$CA_PEM" | sudo tee "$CERTS_D/ca.crt" >/dev/null
}

ENGINE="$(detect_engine)"
log "engine: ${ENGINE}"

case "$ENGINE" in
    colima)
        log "installing CA into colima VM ${CERTS_D}/ca.crt"
        colima ssh -- sudo mkdir -p "$CERTS_D"
        printf '%s' "$CA_PEM" | colima ssh -- sudo tee "$CERTS_D/ca.crt" >/dev/null
        log "mapping name in colima VM /etc/hosts"
        _rewrite_hosts "colima ssh -- sudo sh -c"
        # The Bridge app (macOS host) probes the registry directly, not via the
        # VM, so the host must resolve the name too.
        if [ "$(uname)" = "Darwin" ]; then
            log "mapping name in macOS host /etc/hosts (sudo)"
            _rewrite_hosts "sudo sh -c"
        fi
        ;;
    linux|wsl-inside)
        install_certs_local
        log "mapping name in /etc/hosts (sudo)"
        _rewrite_hosts "sudo sh -c"
        [ "$ENGINE" = "wsl-inside" ] && warn "inside WSL: the Windows *host* hosts file is separate — run trust-registry-ca.ps1 on Windows (as admin) so the Bridge app resolves ${DOMAIN} too"
        ;;
    *)
        die "could not detect a docker engine (colima / native dockerd / WSL). On Windows run trust-registry-ca.ps1."
        ;;
esac

# Verify: TLS handshake + CA trust against the real IP (independent of hosts
# propagation, via --resolve). Non-fatal — the registry may just be down.
if command -v curl >/dev/null 2>&1; then
    log "verifying TLS + CA trust against ${REG_IP}:${PORT} ..."
    if curl -fsS --cacert "$CA_SRC" --resolve "${HOSTPORT}:${REG_IP}" "https://${HOSTPORT}/v2/" >/dev/null 2>&1; then
        log "OK — daemon will trust https://${HOSTPORT} (pull no longer x509-fails)"
    else
        warn "could not reach https://${HOSTPORT} at ${REG_IP} (registry down / wrong IP / firewall). CA + hosts are still installed."
    fi
fi

cat <<EOF

[trust-ca] done. Next, in this machine's Bridge:
  Images page -> switch to TLS mode -> import this CA (shield icon)
                -> set registryUrl = ${HOSTPORT}
(certs.d is read live; no docker restart needed.)
EOF
