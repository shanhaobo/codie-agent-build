#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=_lib.sh
source "$(dirname "$0")/_lib.sh"

CONTEXT="$REPO_ROOT/docker-registry/agents/hermes-agent"
# Our patched Dockerfile lives outside the upstream clone so git can track it
# (each clone carries its own .git/ which blocks the outer repo from adding
# files inside it). Build uses this path via -f, context stays at the clone.
DOCKERFILE="$REPO_ROOT/docker-registry/agent-dockerfiles/hermes-agent/Dockerfile"
# Shared toolchain scripts baked into every agent image via a named build
# context. Dockerfile references it as `COPY --from=shared ...`.
# See docs/plans/2026-04-24-sidecar-tools-architecture.md.
SHARED_CONTEXT="$REPO_ROOT/docker-registry/shared"
IMAGE_BASE="hermes-agent"
TAG="${1:-latest}"

if [ ! -f "$DOCKERFILE" ]; then
    echo "error: Dockerfile not found at $DOCKERFILE"
    echo "       Hermes source should be cloned at docker-registry/agents/hermes-agent/"
    echo "       (per reference_container_registry convention — full local clone, gitignored)"
    exit 1
fi

# Echo the source clone HEAD so build logs show what got built.
SOURCE_SHA=$(git -C "$CONTEXT" rev-parse --short HEAD 2>/dev/null || echo "(not a git repo)")
echo "Building $IMAGE_BASE from $CONTEXT @ $SOURCE_SHA"

ensure_builder
compute_push_tags "$IMAGE_BASE" "$TAG"
build_output_args "$IMAGE_BASE"

echo "Building $IMAGE_BASE:$TAG for $PLATFORMS → ${REGISTRIES_DISPLAY}..."
docker buildx build --builder "$BUILDER" \
    --platform "$PLATFORMS" \
    --build-context "shared=$SHARED_CONTEXT" \
    --build-context "emitter=$REPO_ROOT/docker-registry/agent-dockerfiles/hermes-agent/extensions/hermes-codie-emitter" \
    --build-context "codie=$REPO_ROOT/docker-registry/agent-dockerfiles/hermes-agent" \
    -f "$DOCKERFILE" \
    "${OUTPUT_ARGS[@]}" \
    "$CONTEXT"

echo ""
echo "Done. Image: $IMAGE_BASE:$TAG ($PLATFORMS) → ${REGISTRIES_DISPLAY}"
