#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=_lib.sh
source "$(dirname "$0")/_lib.sh"

# Upstream OpenHuman source — must be cloned by the operator first:
#   git clone https://github.com/tinyhumansai/openhuman \
#       docker-registry/agents/openhuman
CONTEXT="$REPO_ROOT/docker-registry/agents/openhuman"
# Our patched Dockerfile + Python shim live OUTSIDE the upstream clone so the
# clone can be `git pull`ed cleanly. The Dockerfile pulls them in via the
# `shim` build context.
DOCKERFILE="$REPO_ROOT/docker-registry/agent-dockerfiles/openhuman/Dockerfile"
SHARED_CONTEXT="$REPO_ROOT/docker-registry/shared"
SHIM_CONTEXT="$REPO_ROOT/docker-registry/agent-dockerfiles/openhuman"
IMAGE_BASE="openhuman"
TAG="${1:-latest}"

if [ ! -f "$DOCKERFILE" ]; then
    echo "error: Dockerfile not found at $DOCKERFILE"
    echo "       OpenHuman source should be cloned at docker-registry/agents/openhuman/"
    exit 1
fi

if [ ! -d "$CONTEXT" ]; then
    echo "error: upstream OpenHuman clone not found at $CONTEXT"
    echo "       run: git clone https://github.com/tinyhumansai/openhuman $CONTEXT"
    exit 1
fi

SOURCE_SHA=$(git -C "$CONTEXT" rev-parse --short HEAD 2>/dev/null || echo "(not a git repo)")
echo "Building $IMAGE_BASE from $CONTEXT @ $SOURCE_SHA"

# Codie patches over the pristine upstream clone (clone is gitignored + meant
# to `git pull` cleanly, so patches live here and are applied at build time).
# Idempotent: skip any patch that already applies in reverse (already present).
PATCH_DIR="$REPO_ROOT/docker-registry/agent-dockerfiles/openhuman/patches"
if [ -d "$PATCH_DIR" ]; then
    for patch in "$PATCH_DIR"/*.patch; do
        [ -e "$patch" ] || continue
        if git -C "$CONTEXT" apply --reverse --check "$patch" >/dev/null 2>&1; then
            echo "  patch already applied: $(basename "$patch")"
        elif git -C "$CONTEXT" apply --check "$patch" >/dev/null 2>&1; then
            git -C "$CONTEXT" apply "$patch"
            echo "  applied patch: $(basename "$patch")"
        else
            echo "error: patch does not apply cleanly: $(basename "$patch")"
            echo "       upstream likely moved — regenerate it against the current clone."
            exit 1
        fi
    done
fi

ensure_builder
compute_push_tags "$IMAGE_BASE" "$TAG"
build_output_args "$IMAGE_BASE"

# Optional Debian mirror override (APT_MIRROR=mirrors.tuna.tsinghua.edu.cn …).
# trixie's apt 3.x balloons to OOM when deb.debian.org is flaky (hash-mismatch
# retry loop) — on networks where debian.org is unreliable, set a mirror.
EXTRA_BUILD_ARGS=()
if [ -n "${APT_MIRROR:-}" ]; then
    EXTRA_BUILD_ARGS+=(--build-arg "APT_MIRROR=$APT_MIRROR")
    echo "(using APT mirror: $APT_MIRROR)"
fi

echo "Building $IMAGE_BASE:$TAG for $PLATFORMS → ${REGISTRIES_DISPLAY}..."
docker buildx build --builder "$BUILDER" \
    --platform "$PLATFORMS" \
    --build-context "shared=$SHARED_CONTEXT" \
    --build-context "shim=$SHIM_CONTEXT" \
    -f "$DOCKERFILE" \
    ${EXTRA_BUILD_ARGS[@]+"${EXTRA_BUILD_ARGS[@]}"} \
    "${OUTPUT_ARGS[@]}" \
    "$CONTEXT"

echo ""
echo "Done. Image: $IMAGE_BASE:$TAG ($PLATFORMS) → ${REGISTRIES_DISPLAY}"
