#!/usr/bin/env bash
# Linux/WSL/macOS host wrapper for the clean-room install-flow validation.
# Mirrors run.ps1. Subcommands: build | auth | run (default) | all.
#
#   ./run.sh all                # build -> auth (once) -> run
#   ./run.sh run                # run against the cached :authed image
#
# Env overrides: CR_MARKETPLACE_REPO CR_MARKETPLACE_NAME CR_PRIMARY_PLUGIN
#                CR_EXPECT_DEPS
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS="$HERE/results"; mkdir -p "$RESULTS"
BASE_TAG="${CR_BASE_TAG:-copilot-cleanroom:base}"
AUTH_TAG="${CR_AUTH_TAG:-copilot-cleanroom:authed}"
MODE="${1:-run}"

img_exists() { [ -n "$(docker images -q "$1" 2>/dev/null)" ]; }

do_build() {
    echo "== building credential-free base image ($BASE_TAG) =="
    # Governed machines block the public npm registry at the TLS layer; forward
    # the host's configured registry so the in-image Copilot CLI install matches.
    local reg="${CR_NPM_REGISTRY:-$(npm config get registry 2>/dev/null | head -1)}"
    case "$reg" in ''|undefined|null) reg='https://registry.npmjs.org/' ;; esac
    echo "   npm registry: $reg"
    docker build --build-arg "NPM_REGISTRY=$reg" -t "$BASE_TAG" "$HERE"
}
do_auth() {
    img_exists "$BASE_TAG" || do_build
    echo "== one-time device-code login =="
    echo "Run '/login' if not prompted, authorize the device code, then '/exit'."
    docker rm -f cr-auth >/dev/null 2>&1 || true
    docker run -it --name cr-auth --entrypoint /bin/bash "$BASE_TAG" -lc 'copilot; echo "--- login session ended ---"'
    echo "== committing authed image ($AUTH_TAG) =="
    docker commit cr-auth "$AUTH_TAG" >/dev/null
    docker rm -f cr-auth >/dev/null
    echo "cached $AUTH_TAG"
}
do_run() {
    img_exists "$AUTH_TAG" || { echo "no $AUTH_TAG yet -- authing first"; do_auth; }
    echo "== running clean-room validation =="
    docker run --rm \
        -v "$HERE/validate.sh:/home/operator/validate.sh:ro" \
        -v "$RESULTS:/home/operator/out" \
        -e "CR_MARKETPLACE_REPO=${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}" \
        -e "CR_MARKETPLACE_NAME=${CR_MARKETPLACE_NAME:-copilot-extensions}" \
        -e "CR_PRIMARY_PLUGIN=${CR_PRIMARY_PLUGIN:-agent-codespaces}" \
        -e "CR_EXPECT_DEPS=${CR_EXPECT_DEPS:-agent-bridge agent-worktrees}" \
        -e "CR_REPORT=/home/operator/out/cr-report.json" \
        --entrypoint /bin/bash \
        "$AUTH_TAG" -lc 'bash /home/operator/validate.sh; rc=$?; cp -r $HOME/cr-logs /home/operator/out/ 2>/dev/null; exit $rc'
    local rc=$?
    echo; echo "== report =="; cat "$RESULTS/cr-report.json" 2>/dev/null || true
    echo "results dir: $RESULTS"
    return $rc
}
case "$MODE" in
    build) do_build ;;
    auth)  do_auth ;;
    run)   do_run ;;
    all)   do_build; img_exists "$AUTH_TAG" || do_auth; do_run ;;
    *) echo "usage: $0 {build|auth|run|all}" >&2; exit 2 ;;
esac
