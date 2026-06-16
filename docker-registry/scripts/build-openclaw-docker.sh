#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=_lib.sh
source "$(dirname "$0")/_lib.sh"

CONTEXT="$REPO_ROOT/docker-registry/agents/openclaw"
# Patched Dockerfile tracked outside the upstream clone (see hermes script).
DOCKERFILE="$REPO_ROOT/docker-registry/agent-dockerfiles/openclaw/Dockerfile"
SHARED_CONTEXT="$REPO_ROOT/docker-registry/shared"
IMAGE_BASE="openclaw"
TAG="${1:-latest}"

if [ ! -f "$DOCKERFILE" ]; then
    echo "error: Dockerfile not found at $DOCKERFILE"
    echo "       OpenClaw source should be cloned at docker-registry/agents/openclaw/"
    exit 1
fi

SOURCE_SHA=$(git -C "$CONTEXT" rev-parse --short HEAD 2>/dev/null || echo "(not a git repo)")
echo "Building $IMAGE_BASE from $CONTEXT @ $SOURCE_SHA"

ensure_builder
compute_push_tags "$IMAGE_BASE" "$TAG"
build_output_args "$IMAGE_BASE"

echo "Building $IMAGE_BASE:$TAG for $PLATFORMS → ${REGISTRIES_DISPLAY}..."
docker buildx build --builder "$BUILDER" \
    --platform "$PLATFORMS" \
    --build-context "shared=$SHARED_CONTEXT" \
    --build-context "emitter=$REPO_ROOT/docker-registry/agent-dockerfiles/openclaw/extensions/events-emitter" \
    -f "$DOCKERFILE" \
    "${OUTPUT_ARGS[@]}" \
    "$CONTEXT"

echo ""
echo "Done. Image: $IMAGE_BASE:$TAG ($PLATFORMS) → ${REGISTRIES_DISPLAY}"
