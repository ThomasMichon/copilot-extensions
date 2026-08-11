#!/usr/bin/env bash
# Linux/WSL/macOS host wrapper for the clean-room install-flow validation.
# Mirrors run.ps1. Drives a PERSISTENT container so you can run the automated
# validate.sh (optionally to a chosen phase) and then drop into an INTERACTIVE
# shell in the same box for headed `copilot` smoke tests.
#
#   ./run.sh                              # base: full validate against :authed
#   ./run.sh --image pristine shell       # drop into a pristine fresh box
#   ./run.sh --until 1 --then shell run   # install the plugin, then hand off
#   ./run.sh --image pristine down        # remove the pristine container
#
# Feed policy: the host npm config is NEVER auto-forwarded (that would bias the
# fresh-machine experiment). Pass --npm-registry <feed> ONLY to install the
# Copilot CLI prereq on a governed box (a build-time given, not the experiment).
#
# Env overrides: CR_MARKETPLACE_REPO CR_MARKETPLACE_NAME CR_PRIMARY_PLUGIN
#                CR_EXPECT_DEPS CR_RESULTS_DIR CR_NPM_REGISTRY
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

IMAGE=base
UNTIL=6
THEN=none
NPM_REGISTRY="${CR_NPM_REGISTRY:-}"
TOKEN_ACCOUNT=""
NO_TOKEN=0
MODE=run
while [ $# -gt 0 ]; do
    case "$1" in
        --image) IMAGE="$2"; shift 2 ;;
        --until) UNTIL="$2"; shift 2 ;;
        --then)  THEN="$2"; shift 2 ;;
        --npm-registry) NPM_REGISTRY="$2"; shift 2 ;;
        --token-account) TOKEN_ACCOUNT="$2"; shift 2 ;;
        --no-token) NO_TOKEN=1; shift ;;
        build|auth|run|shell|down|all) MODE="$1"; shift ;;
        *) echo "usage: $0 [--image base|pristine] [--until N] [--then shell|down] [--npm-registry URL] [--token-account USER] [--no-token] {build|auth|run|shell|down|all}" >&2; exit 2 ;;
    esac
done

case "$IMAGE" in base) DOCKERFILE=Dockerfile ;; pristine) DOCKERFILE=Dockerfile.pristine ;; *) echo "bad --image: $IMAGE" >&2; exit 2 ;; esac
BASE_TAG="copilot-cleanroom:$IMAGE"
if [ "$IMAGE" = base ]; then AUTH_TAG="copilot-cleanroom:authed"; else AUTH_TAG="copilot-cleanroom:$IMAGE-authed"; fi
CONTAINER="cr-$IMAGE"

if [ -n "${CR_RESULTS_DIR:-}" ]; then
    RESULTS="$CR_RESULTS_DIR"
else
    RESULTS="${XDG_STATE_HOME:-$HOME/.local/state}/copilot-cleanroom/runs/$(date +%Y%m%d-%H%M%S)"
fi

img_exists()  { [ -n "$(docker images -q "$1" 2>/dev/null)" ]; }
is_running()  { [ -n "$(docker ps -q -f "name=^$1\$" 2>/dev/null)" ]; }

do_build() {
    echo "== building $IMAGE image ($BASE_TAG) from $DOCKERFILE =="
    # No host-config auto-forward: pass a feed ONLY when explicitly requested
    # (installs the Copilot CLI prereq on a governed box). Public by default.
    local reg="${NPM_REGISTRY:-https://registry.npmjs.org/}"
    [ -z "$reg" ] && reg='https://registry.npmjs.org/'
    echo "   npm registry (build-time, Copilot install only): $reg"
    if ! docker build --build-arg "NPM_REGISTRY=$reg" -f "$HERE/$DOCKERFILE" -t "$BASE_TAG" "$HERE"; then
        echo "docker build failed. On a governed box the public npm feed is TLS-blocked;" >&2
        echo "re-run with --npm-registry https://<your-internal-npm-feed>/ to install Copilot." >&2
        exit 1
    fi
}
do_auth() {
    img_exists "$BASE_TAG" || do_build
    echo "== one-time device-code login ($AUTH_TAG) =="
    echo "Run '/login' if not prompted, authorize the device code, then '/exit'."
    docker rm -f cr-auth >/dev/null 2>&1 || true
    docker run -it --name cr-auth --entrypoint /bin/bash "$BASE_TAG" -lc 'copilot; echo "--- login session ended ---"'
    echo "== committing authed image ($AUTH_TAG) =="
    docker commit cr-auth "$AUTH_TAG" >/dev/null
    docker rm -f cr-auth >/dev/null
    echo "cached $AUTH_TAG"
}
# Resolve a Copilot token from the host (unless --no-token): $COPILOT_GITHUB_TOKEN
# then `gh auth token [--user ACCT]`. Empty when none is available.
resolve_token() {
    [ "$NO_TOKEN" = 1 ] && return 0
    if [ -n "${COPILOT_GITHUB_TOKEN:-}" ]; then echo "$COPILOT_GITHUB_TOKEN"; return 0; fi
    if [ -n "$TOKEN_ACCOUNT" ]; then gh auth token --user "$TOKEN_ACCOUNT" 2>/dev/null | head -1; else gh auth token 2>/dev/null | head -1; fi
}
start_container() {
    # Auth: prefer a host-grabbed Copilot token (COPILOT_GITHUB_TOKEN) -- no
    # interactive step, runs against the plain unauthed image. Fall back to the
    # committed device-code :authed image only when no token is available.
    local token img; token="$(resolve_token)"
    local token_args=()
    if [ -n "$token" ]; then
        img_exists "$BASE_TAG" || do_build
        img="$BASE_TAG"
        echo "auth: injecting COPILOT_GITHUB_TOKEN from host gh (${TOKEN_ACCOUNT:-active gh account}) -- no device-code needed"
        export COPILOT_GITHUB_TOKEN="$token"   # value from env, not on the docker CLI args
        token_args=(-e COPILOT_GITHUB_TOKEN)
    else
        img_exists "$AUTH_TAG" || { echo "no host token and no $AUTH_TAG -- device-code auth"; do_auth; }
        img="$AUTH_TAG"
    fi
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    mkdir -p "$RESULTS"
    docker run -d --name "$CONTAINER" \
        -v "$HERE/validate.sh:/home/operator/validate.sh:ro" \
        -v "$RESULTS:/home/operator/out" \
        -e "CR_MARKETPLACE_REPO=${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}" \
        -e "CR_MARKETPLACE_NAME=${CR_MARKETPLACE_NAME:-copilot-extensions}" \
        -e "CR_PRIMARY_PLUGIN=${CR_PRIMARY_PLUGIN:-agent-codespaces}" \
        -e "CR_EXPECT_DEPS=${CR_EXPECT_DEPS:-agent-bridge agent-worktrees}" \
        -e "CR_REPORT=/home/operator/out/cr-report.json" \
        "${token_args[@]}" \
        --entrypoint sleep "$img" infinity >/dev/null
    [ -n "$token" ] && unset COPILOT_GITHUB_TOKEN
    echo "container $CONTAINER up (results -> $RESULTS)"
}
ensure_container() { is_running "$CONTAINER" || start_container; }
do_shell() {
    ensure_container
    echo "== entering $CONTAINER (interactive login shell; 'exit' leaves, container stays up) =="
    echo "   run '$0 --image $IMAGE down' to remove it."
    docker exec -it "$CONTAINER" /bin/bash -l
}
do_down() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; echo "removed $CONTAINER"; }
do_run() {
    start_container
    echo "== running clean-room validation ($IMAGE, through phase $UNTIL) =="
    docker exec -e "CR_UNTIL=$UNTIL" "$CONTAINER" /bin/bash -lc \
        'cp -r $HOME/cr-logs /home/operator/out/ 2>/dev/null; bash /home/operator/validate.sh; rc=$?; cp -r $HOME/cr-logs /home/operator/out/ 2>/dev/null; exit $rc'
    local rc=$?
    echo; echo "== report =="; cat "$RESULTS/cr-report.json" 2>/dev/null || true
    echo "results dir: $RESULTS"
    case "$THEN" in
        shell) do_shell ;;
        down)  do_down ;;
    esac
    [ "$THEN" = shell ] || return $rc
}

case "$MODE" in
    build) do_build ;;
    auth)  do_auth ;;
    run)   do_run ;;
    shell) do_shell ;;
    down)  do_down ;;
    all)   do_build; do_run ;;
esac
