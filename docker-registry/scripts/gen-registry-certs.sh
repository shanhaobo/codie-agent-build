#!/usr/bin/env bash
# Generate a self-signed CA + leaf cert for the TLS registry.
#
# The leaf cert is bound to a stable DOMAIN NAME (not an IP) so the registry
# address survives DHCP LAN-IP drift: every consumer connects by name, the
# cert validates by name, and only the name→IP resolution differs per machine
# (the single drift knob). See docs/plans/2026-06-15-registry-tls-design.md.
#
# Outputs into docker-registry/certs/ (gitignored):
#   ca.key  ca.crt          — the internal CA (ca.key is the trust root; keep offline, 600)
#   registry.key registry.crt — the registry leaf, signed by the CA
#
# ca.crt is the ONLY file distributed to consumers (drop into docker certs.d).
# Re-running is idempotent for the leaf (regenerates) but PRESERVES an existing
# CA unless FORCE_CA=1 — rotating the CA invalidates every distributed copy.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CERT_DIR="$REPO_ROOT/docker-registry/certs"
DOMAIN="${REGISTRY_DOMAIN:-registry.codie.lan}"
# In-compose-network alias used by the buildx builder, plus loopback for the
# registry-host machine's own daemon. NO LAN IP here — that would re-couple the
# cert to a drifting address.
ALT_NAMES=(
    "DNS:${DOMAIN}"
    "DNS:docker-registry-registry-1"
    "DNS:localhost"
    "IP:127.0.0.1"
)

mkdir -p "$CERT_DIR"
chmod 700 "$CERT_DIR"
cd "$CERT_DIR"

CA_DAYS="${CA_DAYS:-3650}"
LEAF_DAYS="${LEAF_DAYS:-730}"

if [[ ! -f ca.crt || ! -f ca.key || "${FORCE_CA:-0}" == "1" ]]; then
    echo "Generating internal CA (${CA_DAYS}d)..."
    openssl genrsa -out ca.key 4096
    chmod 600 ca.key
    openssl req -x509 -new -nodes -key ca.key -sha256 -days "$CA_DAYS" \
        -subj "/CN=Codie Registry CA/O=Codie" \
        -out ca.crt
else
    echo "Reusing existing CA (set FORCE_CA=1 to rotate — invalidates all distributed copies)."
fi

# Build the SAN block.
SAN_LINE="$(IFS=,; echo "${ALT_NAMES[*]}")"

echo "Generating registry leaf for: ${SAN_LINE}"
openssl genrsa -out registry.key 2048
chmod 600 registry.key
openssl req -new -key registry.key \
    -subj "/CN=${DOMAIN}/O=Codie" \
    -out registry.csr

openssl x509 -req -in registry.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -days "$LEAF_DAYS" -sha256 \
    -extfile <(printf 'subjectAltName=%s\nextendedKeyUsage=serverAuth\n' "$SAN_LINE") \
    -out registry.crt
rm -f registry.csr

echo ""
echo "Done. Wrote into $CERT_DIR:"
echo "  ca.crt        — distribute to every consumer (docker certs.d / Bridge dataDir)"
echo "  ca.key        — TRUST ROOT, keep offline, never commit"
echo "  registry.crt/.key — mounted into the registry container"
echo ""
echo "Verify leaf SANs:"
openssl x509 -in registry.crt -noout -ext subjectAltName
