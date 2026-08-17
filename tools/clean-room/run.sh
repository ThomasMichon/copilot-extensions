#!/usr/bin/env bash
# Linux/WSL/macOS host wrapper for the clean-room SCENARIO validation.
# Mirrors run.ps1. Drives a PERSISTENT container so you can run an automated
# scenario (optionally to a chosen stage) and then drop into an INTERACTIVE
# shell in the same box for headed `copilot` smoke tests.
#
#   ./run.sh                                    # base: full generic-single-plugin scenario
#   ./run.sh --scenario generic-single-plugin   # (the default) run a named scenario
#   ./run.sh --image pristine shell             # drop into a pristine fresh box
#   ./run.sh --until 1 --then shell run         # install the plugin, then hand off
#   ./run.sh --uv-index https://…/pypi/simple/  # opt-in uv-index fixture (governed box)
#   ./run.sh --image pristine down              # remove the pristine container
#
# The --scenario seam mounts a scenario dir (scenarios/<name>/ or an explicit
# path) plus the shared lib/ read-only into the box and runs scenario.sh, which
# sources lib/clean-room-lib.sh and reports a uniform cr-report.json.
#
# Feed policy: the host npm config is NEVER auto-forwarded (that would bias the
# fresh-machine experiment). Pass --npm-registry <feed> ONLY to install the
# Copilot CLI prereq on a governed box (a build-time given, not the experiment).
# --uv-index is the RUNTIME analog: opt-in, points the deploy stage's uv at an
# internal index; default off so the governed uv jam surfaces.
#
# Env overrides: CR_MARKETPLACE_REPO CR_MARKETPLACE_NAME CR_PRIMARY_PLUGIN
#                CR_EXPECT_DEPS CR_RESULTS_DIR CR_NPM_REGISTRY CR_UV_INDEX
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

IMAGE=base
NAME_SUFFIX=""
UNTIL=all
THEN=none
SCENARIO=generic-single-plugin
NPM_REGISTRY="${CR_NPM_REGISTRY:-}"
UV_INDEX="${CR_UV_INDEX:-}"
TOKEN_ACCOUNT=""
NO_TOKEN=0
PASS_ENV=()
MODE=run
while [ $# -gt 0 ]; do
    case "$1" in
        --image) IMAGE="$2"; shift 2 ;;
        --name-suffix) NAME_SUFFIX="$2"; shift 2 ;;
        --until) UNTIL="$2"; shift 2 ;;
        --then)  THEN="$2"; shift 2 ;;
        --scenario) SCENARIO="$2"; shift 2 ;;
        --npm-registry) NPM_REGISTRY="$2"; shift 2 ;;
        --uv-index) UV_INDEX="$2"; shift 2 ;;
        --token-account) TOKEN_ACCOUNT="$2"; shift 2 ;;
        --pass-env) PASS_ENV+=("$2"); shift 2 ;;
        --no-token) NO_TOKEN=1; shift ;;
        build|auth|run|shell|down|bridge-register|bridge-unregister|all) MODE="$1"; shift ;;
        *) echo "usage: $0 [--image base|pristine] [--name-suffix SUFFIX] [--scenario NAME|DIR] [--until N|all] [--then shell|down] [--npm-registry URL] [--uv-index URL] [--token-account USER] [--pass-env NAME]... [--no-token] {build|auth|run|shell|down|bridge-register|bridge-unregister|all}" >&2; exit 2 ;;
    esac
done

# Resolve the scenario dir: an explicit path, else scenarios/<name>/.
if [ -d "$SCENARIO" ]; then
    SCENARIO_DIR="$(cd "$SCENARIO" && pwd)"; SCENARIO_NAME="$(basename "$SCENARIO_DIR")"
elif [ -d "$HERE/scenarios/$SCENARIO" ]; then
    SCENARIO_DIR="$HERE/scenarios/$SCENARIO"; SCENARIO_NAME="$SCENARIO"
else
    echo "unknown --scenario '$SCENARIO' (not a dir, and no scenarios/$SCENARIO)" >&2; exit 2
fi
[ -f "$SCENARIO_DIR/scenario.sh" ] || { echo "scenario '$SCENARIO_NAME' has no scenario.sh" >&2; exit 2; }
LIB_DIR="$HERE/lib"

# $NAME_SUFFIX makes the CONTAINER + agent names unique (concurrent clean-rooms
# of the same image); the image/tag are shared (name-collision is only on the
# container). Docker-name-safe suffix only.
case "$IMAGE" in base) DOCKERFILE=Dockerfile ;; pristine) DOCKERFILE=Dockerfile.pristine ;; *) echo "bad --image: $IMAGE" >&2; exit 2 ;; esac
if [ -n "$NAME_SUFFIX" ] && ! printf '%s' "$NAME_SUFFIX" | grep -Eq '^[a-zA-Z0-9][a-zA-Z0-9_.-]*$'; then
    echo "bad --name-suffix: '$NAME_SUFFIX' (must be docker-name-safe)" >&2; exit 2
fi
BASE_TAG="copilot-cleanroom:$IMAGE"
if [ "$IMAGE" = base ]; then AUTH_TAG="copilot-cleanroom:authed"; else AUTH_TAG="copilot-cleanroom:$IMAGE-authed"; fi
NAME_TAIL=""; [ -n "$NAME_SUFFIX" ] && NAME_TAIL="-$NAME_SUFFIX"
CONTAINER="cr-$IMAGE$NAME_TAIL"
AGENT_NAME="cleanroom-$IMAGE$NAME_TAIL"   # agent-bridge agent + provider name for this box

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
    # Generic host->container value relay: forward each --pass-env NAME by name
    # (value from the runner's env, not on the docker CLI args). Only names set on
    # the host are forwarded; a missing one warns rather than silently dropping.
    local pass_args=()
    local _n
    for _n in "${PASS_ENV[@]}"; do
        [ -z "$_n" ] && continue
        if [ -z "${!_n:-}" ]; then
            echo "warn: --pass-env '$_n' is not set on the host -- not forwarding" >&2
            continue
        fi
        pass_args+=(-e "$_n")
    done
    docker run -d --name "$CONTAINER" \
        -v "$SCENARIO_DIR:/home/operator/scenario:ro" \
        -v "$LIB_DIR:/home/operator/lib:ro" \
        -v "$RESULTS:/home/operator/out" \
        -e "CR_LIB=/home/operator/lib/clean-room-lib.sh" \
        -e "CR_SCENARIO_NAME=$SCENARIO_NAME" \
        -e "CR_MARKETPLACE_REPO=${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}" \
        -e "CR_MARKETPLACE_NAME=${CR_MARKETPLACE_NAME:-copilot-extensions}" \
        -e "CR_PRIMARY_PLUGIN=${CR_PRIMARY_PLUGIN:-agent-codespaces}" \
        -e "CR_EXPECT_DEPS=${CR_EXPECT_DEPS:-agent-bridge agent-worktrees}" \
        -e "CR_UV_INDEX=$UV_INDEX" \
        -e "CR_LOGDIR=/home/operator/cr-logs" \
        -e "CR_REPORT=/home/operator/out/cr-report.json" \
        "${token_args[@]}" \
        "${pass_args[@]}" \
        --entrypoint sleep "$img" infinity >/dev/null
    [ -n "$token" ] && unset COPILOT_GITHUB_TOKEN
    echo "container $CONTAINER up (results -> $RESULTS)"
}
ensure_container() { is_running "$CONTAINER" || start_container; }
do_shell() {
    ensure_container
    echo "== entering $CONTAINER (interactive login shell; 'exit' leaves, container stays up) =="
    echo "   run '$0 --image $IMAGE${NAME_SUFFIX:+ --name-suffix $NAME_SUFFIX} down' to remove it."
    docker exec -it "$CONTAINER" /bin/bash -l
}
do_down() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; echo "removed $CONTAINER"; }
# Register/unregister the container as an agent-bridge agent (drive the
# in-container Copilot with `agent-bridge send $AGENT_NAME "<prompt>"`). Uses the
# runtime provider API via bridge_register.py (a docker-exec command agent).
_py() { command -v python3 || command -v python; }
do_bridge_register() {
    ensure_container
    "$(_py)" "$HERE/bridge_register.py" register --container "$CONTAINER" --name "$AGENT_NAME"
    echo "drive it:  agent-bridge send $AGENT_NAME \"<prompt>\""
}
do_bridge_unregister() { "$(_py)" "$HERE/bridge_register.py" unregister --name "$AGENT_NAME"; }
do_run() {
    start_container
    local until_env=()
    [ "$UNTIL" != all ] && until_env=(-e "CR_UNTIL=$UNTIL")
    echo "== running clean-room scenario '$SCENARIO_NAME' ($IMAGE, through stage ${UNTIL}) =="
    docker exec "${until_env[@]}" "$CONTAINER" /bin/bash -lc \
        'bash /home/operator/scenario/scenario.sh; rc=$?; cp -r $HOME/cr-logs /home/operator/out/ 2>/dev/null; exit $rc'
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
    bridge-register)   do_bridge_register ;;
    bridge-unregister) do_bridge_unregister ;;
    all)   do_build; do_run ;;
esac
