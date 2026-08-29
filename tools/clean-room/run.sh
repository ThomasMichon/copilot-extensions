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
HARNESS_MOUNT="${CR_HARNESS_MOUNT_HOST:-}"
MODE=run
RUNS_OVERRIDE=0
SKIP_TIER_P=0
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
        --harness-mount) HARNESS_MOUNT="$2"; shift 2 ;;
        --runs) RUNS_OVERRIDE="$2"; shift 2 ;;
        --skip-tier-p-gate) SKIP_TIER_P=1; shift ;;
        --no-token) NO_TOKEN=1; shift ;;
        build|auth|run|eval|shell|down|bridge-register|bridge-unregister|all) MODE="$1"; shift ;;
        *) echo "usage: $0 [--image base|pristine] [--name-suffix SUFFIX] [--scenario NAME|DIR] [--until N|all] [--then shell|down] [--npm-registry URL] [--uv-index URL] [--token-account USER] [--pass-env NAME]... [--harness-mount DIR] [--runs N] [--skip-tier-p-gate] [--no-token] {build|auth|run|eval|shell|down|bridge-register|bridge-unregister|all}" >&2; exit 2 ;;
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
# A Tier-P scenario drives scenario.sh; a Tier-E scenario drives setup.sh (its
# starting state) + an agent eval. Require the one that matches what's present.
if [ ! -f "$SCENARIO_DIR/scenario.sh" ] && [ ! -f "$SCENARIO_DIR/setup.sh" ]; then
    echo "scenario '$SCENARIO_NAME' has neither scenario.sh (Tier-P) nor setup.sh (Tier-E)" >&2; exit 2
fi
LIB_DIR="$HERE/lib"
source "$LIB_DIR/acp-command.sh"
# Optional per-suite shared helpers: if the selected scenario's parent dir holds
# a `_lib/`, it is mounted read-only at /home/operator/scenario-lib and exposed
# as $CR_SCENARIO_LIB, so sibling scenarios in a suite can source shared phase
# helpers instead of each duplicating them. Opt-in: absent -> unchanged.
SCENARIO_SHARED_LIB="$(dirname "$SCENARIO_DIR")/_lib"
[ -d "$SCENARIO_SHARED_LIB" ] || SCENARIO_SHARED_LIB=""

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
AGENT_NAME="cleanroom-$IMAGE$NAME_TAIL"   # legacy label (kept for logs)
DRIVE_AGENT="cleanroom:$CONTAINER"        # the namespaced agent-bridge address
# Keep Copilot's subprocess crashes from dirtying the fixture, and use the
# hidden distro rg because the bundled ARM64 binary rejects 16 KiB pages.
ACP_PREFIX='ulimit -c 0 && env USE_BUILTIN_RIPGREP=false PATH=/opt/copilot-cleanroom/bin:$PATH'
ACP_COMMAND="$ACP_PREFIX copilot --acp --stdio --allow-all-tools"  # eval may add --plugin-dir
BRIDGE_CONTAINER_ID=""

if [ -n "${CR_RESULTS_DIR:-}" ]; then
    RESULTS="$CR_RESULTS_DIR"
else
    RESULTS="${XDG_STATE_HOME:-$HOME/.local/state}/copilot-cleanroom/runs/$(date +%Y%m%d-%H%M%S)"
fi
RESULTS_PREEXISTED=0
[ -e "$RESULTS" ] && RESULTS_PREEXISTED=1

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
    if [ "$RESULTS_PREEXISTED" = 0 ]; then
        local docker_security
        docker_security="$(docker info --format '{{join .SecurityOptions "\n"}}' 2>/dev/null || true)"
        if printf '%s\n' "$docker_security" | grep -Eq '(^|=)(rootless|userns)($|,)'; then
            # Discover the host uid/gid backing the remapped operator identity,
            # then grant only that identity access. Keep the caller-owned tree
            # private rather than opening predictable transcript paths.
            command -v setfacl >/dev/null 2>&1 || {
                echo "rootless/userns Docker result sharing requires host setfacl" >&2
                exit 1
            }
            local operator_ids operator_uid operator_gid map_container map_pid
            operator_ids="$(docker run --rm --entrypoint /bin/sh "$img" -c \
                'printf "%s %s\n" "$(id -u operator)" "$(id -g operator)"')" || {
                echo "could not read the container operator identity" >&2
                exit 1
            }
            read -r operator_uid operator_gid <<< "$operator_ids"
            map_container="$(docker run -d --entrypoint sleep "$img" 30)" || {
                echo "could not start the container identity probe" >&2
                exit 1
            }
            map_pid="$(docker inspect --format '{{.State.Pid}}' "$map_container")"
            local mapped_uid mapped_gid
            mapped_uid="$(awk -v id="$operator_uid" \
                '$1 <= id && id < $1 + $3 { print $2 + (id - $1); exit }' \
                "/proc/$map_pid/uid_map")"
            mapped_gid="$(awk -v id="$operator_gid" \
                '$1 <= id && id < $1 + $3 { print $2 + (id - $1); exit }' \
                "/proc/$map_pid/gid_map")"
            docker rm -f "$map_container" >/dev/null 2>&1 || true
            [[ "$mapped_uid" =~ ^[0-9]+$ && "$mapped_gid" =~ ^[0-9]+$ ]] || {
                echo "could not read the remapped container operator identity" >&2
                exit 1
            }
            find "$RESULTS" -type d -exec chmod 0700 {} +
            find "$RESULTS" -type f -exec chmod 0600 {} +
            local host_uid
            host_uid="$(id -u)"
            find "$RESULTS" -type d -exec \
                setfacl -m \
                "u:$host_uid:rwx,u:$mapped_uid:rwx,d:u:$host_uid:rwx,d:u:$mapped_uid:rwx" \
                {} +
        else
            # On ordinary rootful Docker, give the in-container operator
            # ownership while retaining host access through the caller's group.
            docker run --rm --user root \
                -v "$RESULTS:/out" \
                -e "CR_HOST_GID=$(id -g)" \
                --entrypoint /bin/sh "$img" -c \
                'chown -R operator:"$CR_HOST_GID" /out &&
                 find /out -type d -exec chmod 2770 {} + &&
                 find /out -type f -exec chmod 0660 {} +'
        fi || {
            echo "could not prepare the new results directory for the container: $RESULTS" >&2
            exit 1
        }
        local host_probe="$RESULTS/.host-access-probe.$$"
        : > "$host_probe" && rm -f -- "$host_probe" || {
            echo "prepared results directory is not writable by the host caller: $RESULTS" >&2
            exit 1
        }
    fi
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
    # Optional per-suite shared-lib mount (see scenario resolution above).
    local scen_lib_args=()
    if [ -n "$SCENARIO_SHARED_LIB" ]; then
        scen_lib_args=(-v "$SCENARIO_SHARED_LIB:/home/operator/scenario-lib:ro" \
                       -e "CR_SCENARIO_LIB=/home/operator/scenario-lib")
    fi
    # Optional downstream-harness bind (Tier-E seam): mount a harness tree
    # read-only at /harness and expose CR_HARNESS_MOUNT so a name-ful eval
    # scenario reaches the operator's local plugins/skills. Host path from
    # --harness-mount or $CR_HARNESS_MOUNT_HOST; the container path is fixed.
    local harness_args=()
    if [ -n "$HARNESS_MOUNT" ]; then
        if [ ! -d "$HARNESS_MOUNT" ]; then
            echo "--harness-mount '$HARNESS_MOUNT' is not a directory" >&2; exit 2
        fi
        harness_args=(-v "$HARNESS_MOUNT:/harness:ro" -e "CR_HARNESS_MOUNT=/harness")
        echo "harness bind: $HARNESS_MOUNT -> /harness (ro)  [CR_HARNESS_MOUNT=/harness]"
    fi
    docker run -d --name "$CONTAINER" \
        -v "$SCENARIO_DIR:/home/operator/scenario:ro" \
        -v "$LIB_DIR:/home/operator/lib:ro" \
        -v "$RESULTS:/home/operator/out" \
        "${scen_lib_args[@]}" \
        "${harness_args[@]}" \
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
    if ! docker exec "$CONTAINER" /bin/sh -c \
        'dir=/home/operator/out; [ ! -d "$dir/eval" ] || dir="$dir/eval";
         probe="$dir/.container-access-probe.$$"; : > "$probe" && rm -f -- "$probe"'; then
        echo "prepared results directory is not writable by the container operator: $RESULTS" >&2
        docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
        exit 1
    fi
    echo "container $CONTAINER up (results -> $RESULTS)"
}
ensure_container() { is_running "$CONTAINER" || start_container; }
do_shell() {
    ensure_container
    echo "== entering $CONTAINER (interactive login shell; 'exit' leaves, container stays up) =="
    echo "   run '$0 --image $IMAGE${NAME_SUFFIX:+ --name-suffix $NAME_SUFFIX} down' to remove it."
    docker exec -it "$CONTAINER" /bin/bash -l
}
do_down() {
    local container_id="${BRIDGE_CONTAINER_ID:-}"
    if [ -z "$container_id" ]; then
        container_id="$(docker inspect -f '{{.Id}}' "$CONTAINER" 2>/dev/null || true)"
    fi
    if [ -z "$container_id" ]; then
        if ! do_bridge_unregister >/dev/null; then
            echo "warn: no container found and stale registration cleanup failed for $CONTAINER" >&2
            return 1
        fi
        echo "removed $CONTAINER"
        return 0
    fi
    if ! docker rm -f "$container_id" >/dev/null 2>&1; then
        echo "error: could not remove $CONTAINER ($container_id)" >&2
        return 1
    fi
    BRIDGE_CONTAINER_ID="$container_id"
    if ! do_bridge_unregister >/dev/null; then
        echo "warn: removed $CONTAINER but could not unregister it from agent-bridge" >&2
        return 1
    fi
    echo "removed $CONTAINER"
}
# Register/unregister the container with agent-bridge (drive the in-container
# Copilot with `agent-bridge create cleanroom:<container> ...`). Uses the
# declarative providers.d/ namespace-provider model (agent-bridge >= dev307; the
# old runtime provider POST API was retired, ce#582): bridge_register.py drops a
# `cleanroom` manifest and IS the provider CLI the daemon shells out to.
_py() { command -v python3 || command -v python; }
do_bridge_register() {
    ensure_container
    local bridge_args=(--acp-command "$ACP_COMMAND")
    local response
    [ -n "${ACP_CWD:-}" ] && bridge_args+=(--acp-cwd "$ACP_CWD")
    if ! response="$("$(_py)" "$HERE/bridge_register.py" "${bridge_args[@]}" register --container "$CONTAINER" --name "$AGENT_NAME")"; then
        return 1
    fi
    printf '%s\n' "$response"
    BRIDGE_CONTAINER_ID="$(printf '%s' "$response" | "$(_py)" -c '
import json, sys
print(json.load(sys.stdin).get("container_id", ""))
')"
    [ -n "$BRIDGE_CONTAINER_ID" ] || return 1
    echo "drive it:  agent-bridge create $DRIVE_AGENT \"<prompt>\""
}
do_bridge_unregister() {
    local container_id="${BRIDGE_CONTAINER_ID:-}"
    if [ -z "$container_id" ]; then
        container_id="$(docker inspect -f '{{.Id}}' "$CONTAINER" 2>/dev/null || true)"
    fi
    local unregister_args=(unregister --name "$AGENT_NAME" --container "$CONTAINER")
    if [ -n "$container_id" ]; then
        unregister_args+=(--container-id "$container_id")
    else
        unregister_args+=(--stale)
    fi
    "$(_py)" "$HERE/bridge_register.py" "${unregister_args[@]}"
}
# End any prior agent-bridge session for an agent so a fresh `create` isn't
# refused with "already has an active session". Idempotent + best-effort.
end_agent_sessions() {
    local agent="$1" js sid
    js="$(agent-bridge --json sessions 2>/dev/null)" || return 0
    [ -n "$js" ] || return 0
    while IFS= read -r sid; do
        [ -n "$sid" ] || continue
        echo "   (ending prior session $sid for $agent)"
        agent-bridge end "$sid" --force >/dev/null 2>&1 || true
    done < <(printf '%s' "$js" | "$(_py)" -c 'import sys,json
a=sys.argv[1]
try: d=json.load(sys.stdin)
except Exception: d=[]
[print(s.get("session_id","")) for s in d if s.get("agent_name")==a and s.get("session_id")]' "$agent")
}
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

# --- Tier-E (agent-driven eval): mirror of run.ps1 Invoke-Eval ---------------
# Establish the scenario's starting state (setup.sh), drive the in-container
# Copilot over agent-bridge with a literal-mode + stated-purpose prompt, and
# capture the transcript(s) as judge evidence. Produces eval/ artifacts and
# prints the judge-packet path; it does NOT itself judge (that is the
# `validating-in-clean-room` skill's clean-room-judge handoff). See
# TIER-E-EXECUTION.md. Manifest parsing + JSON assembly are delegated to python3
# (already required for bridge_register.py) so there is no jq dependency.

# Resolve a `timeout`-like binary (coreutils `timeout`, or macOS `gtimeout`).
_timeout_bin() { command -v timeout 2>/dev/null || command -v gtimeout 2>/dev/null || true; }

# Drive one agent turn with a wall-clock timeout. `agent-bridge create` has no
# --reply-timeout, so bound it host-side: on timeout, note it and append a marker
# so a hung agent is a FAIL, not an infinite wait. Echoes the transcript; sets
# DRIVE_TIMED_OUT (0/1) and DRIVE_DURATION (seconds).
drive_with_timeout() {  # <agent> <prompt_file> <timeout_sec>
    local agent="$1" pf="$2" tmo="$3" t0 out rc tb
    t0=$(date +%s); DRIVE_TIMED_OUT=0; tb="$(_timeout_bin)"
    if [ "${tmo:-0}" -gt 0 ] 2>/dev/null && [ -n "$tb" ]; then
        out="$("$tb" "${tmo}s" agent-bridge create "$agent" --prompt-file "$pf" --expand all --no-color 2>&1)"; rc=$?
        if [ "$rc" -eq 124 ]; then
            DRIVE_TIMED_OUT=1
            out="$out"$'\n'"[clean-room] TIMED OUT after ${tmo}s -- driven agent did not complete its turn."
        fi
    else
        out="$(agent-bridge create "$agent" --prompt-file "$pf" --expand all --no-color 2>&1)"; rc=$?
    fi
    DRIVE_DURATION=$(( $(date +%s) - t0 ))
    printf '%s' "$out"
}

do_eval() {
    local manifest="$SCENARIO_DIR/manifest.json"
    [ -f "$manifest" ] || { echo "eval: scenario '$SCENARIO_NAME' has no manifest.json" >&2; exit 2; }
    local lit="$LIB_DIR/literal-mode.md"
    [ -f "$lit" ] || { echo "eval: literal-mode fixture missing at $lit" >&2; exit 2; }
    local eval_dir="$RESULTS/eval"
    mkdir -p "$eval_dir"

    # --- read the manifest + assemble the full prompt (literal + task) --------
    # Writes eval/literal-mode.txt + eval/prompt.txt, computes the prompt hash,
    # and emits TAB-separated scalars we read into shell vars.
    local parsed
    parsed="$("$(_py)" - "$manifest" "$lit" "$eval_dir" "$SCENARIO_DIR" <<'PY'
import json, sys, hashlib, os
manifest_path, lit_path, eval_dir, scen_dir = sys.argv[1:5]
m = json.load(open(manifest_path, encoding="utf-8"))
ss = m.get("starting_state") or {}
setup_rel = ss.get("setup") or "setup.sh"
prompt = m.get("prompt")
if not prompt:
    pf = os.path.join(scen_dir, "prompt.md")
    prompt = open(pf, encoding="utf-8").read() if os.path.exists(pf) else ""
runs = m.get("runs") or {}
run_count = int(runs.get("count") or 1)
per_turn = int(runs.get("per_turn_timeout_s") or 0)
aggregate = runs.get("aggregate") or "unanimous"
post_check = m.get("post_check") or ("post_check.sh" if os.path.exists(os.path.join(scen_dir, "post_check.sh")) else "")
tierp = m.get("tier_p_precondition") or ""
installed = ss.get("installed_plugins") or []
if not tierp and installed:
    tierp = f"{installed[0]} --version"
literal = open(lit_path, encoding="utf-8").read()
full = f"{literal}\n\n--- TASK ---\n\n{prompt}"
open(os.path.join(eval_dir, "literal-mode.txt"), "w", encoding="utf-8").write(literal)
open(os.path.join(eval_dir, "prompt.txt"), "w", encoding="utf-8").write(full)
prompt_hash = hashlib.sha256(full.encode("utf-8")).hexdigest()[:16]
ev = m.get("eval") or {}
acp_dirs = [str(d) for d in (ev.get("acp_plugin_dirs") or []) if d]
acp_cwd = str(ev.get("acp_cwd") or "")
acp_cwd_file = str(ev.get("acp_cwd_file") or "")
for acp_dir in acp_dirs:
    if (
        not acp_dir.startswith("/")
        or any(character in acp_dir for character in "\0\r\n\t")
    ):
        raise ValueError(
            "eval.acp_plugin_dirs entries must be absolute in-container POSIX paths"
        )
if acp_cwd and (
    not acp_cwd.startswith("/")
    or any(character in acp_cwd for character in "\0\r\n\t")
):
    raise ValueError("eval.acp_cwd must be an absolute in-container POSIX path")
if acp_cwd_file and (
    not acp_cwd_file.startswith("/")
    or any(character in acp_cwd_file for character in "\0\r\n\t")
):
    raise ValueError("eval.acp_cwd_file must be an absolute in-container POSIX path")
if acp_cwd and acp_cwd_file:
    raise ValueError("eval.acp_cwd and eval.acp_cwd_file are mutually exclusive")
for k, v in (("tier", m.get("tier", "")), ("setup_rel", setup_rel), ("run_count", run_count),
             ("per_turn", per_turn), ("aggregate", aggregate), ("post_check", post_check),
             ("tierp", tierp), ("family", m.get("family", "")), ("prompt_hash", prompt_hash),
             ("acp_dirs", json.dumps(acp_dirs, separators=(",", ":"))),
             ("acp_cwd", acp_cwd),
             ("acp_cwd_file", acp_cwd_file)):
    print(f"{k}\t{v}")
PY
)" || { echo "eval: failed to parse manifest.json" >&2; exit 2; }

    local TIER="" SETUP_REL="" RUN_COUNT=1 PER_TURN=0 AGG="unanimous" POST_CHECK="" TIERP="" FAMILY="" PROMPT_HASH="" ACP_DIRS_JSON="[]" ACP_CWD="" ACP_CWD_FILE=""
    local _k _v
    while IFS=$'\t' read -r _k _v; do
        case "$_k" in
            tier) TIER="$_v" ;; setup_rel) SETUP_REL="$_v" ;; run_count) RUN_COUNT="$_v" ;;
            per_turn) PER_TURN="$_v" ;; aggregate) AGG="$_v" ;; post_check) POST_CHECK="$_v" ;;
            tierp) TIERP="$_v" ;; family) FAMILY="$_v" ;; prompt_hash) PROMPT_HASH="$_v" ;;
            acp_dirs) ACP_DIRS_JSON="$_v" ;; acp_cwd) ACP_CWD="$_v" ;;
            acp_cwd_file) ACP_CWD_FILE="$_v" ;;
        esac
    done <<< "$parsed"
    if [ "${RUNS_OVERRIDE:-0}" -gt 0 ] 2>/dev/null; then RUN_COUNT="$RUNS_OVERRIDE"; fi
    [ "$TIER" = E ] || echo "warn: scenario '$SCENARIO_NAME' is tier '$TIER', not 'E' -- eval expects a Tier-E scenario." >&2
    [ -f "$SCENARIO_DIR/$SETUP_REL" ] || { echo "eval: setup driver '$SETUP_REL' not found in scenario dir" >&2; exit 2; }

    # --- 1) start box + 2) establish starting state ---------------------------
    start_container
    echo "== eval: establishing starting state ($SETUP_REL) =="
    docker exec "$CONTAINER" /bin/bash -lc \
        "bash /home/operator/scenario/$SETUP_REL; rc=\$?; cp -r \$HOME/cr-logs /home/operator/out/ 2>/dev/null; exit \$rc" \
        || echo "warn: setup driver exited non-zero -- the starting state may be incomplete (see cr-report.json)."
    if [ -f "$RESULTS/cr-report.json" ]; then
        cp "$RESULTS/cr-report.json" "$eval_dir/setup-report.json"
    fi

    # Build the driven-agent ACP command after setup so a scenario whose
    # authoritative worktree path is generated at runtime can publish it through
    # acp_cwd_file. The resolved path drives both the shell and ACP session/new.
    if [ -n "$ACP_CWD_FILE" ]; then
        ACP_CWD="$(docker exec "$CONTAINER" python3 -c '
import pathlib, sys
p = pathlib.Path(sys.argv[1])
lines = p.read_text(encoding="utf-8").splitlines()
if len(lines) != 1 or not lines[0].startswith("/") or any(c in lines[0] for c in "\0\r\n\t"):
    raise SystemExit("invalid ACP cwd file")
cwd = pathlib.Path(lines[0])
if not cwd.is_dir():
    raise SystemExit("ACP cwd is not a directory")
print(cwd)
' "$ACP_CWD_FILE")" || {
            echo "eval: could not resolve a valid cwd from '$ACP_CWD_FILE'" >&2
            exit 2
        }
    fi
    ACP_COMMAND="$("$(_py)" -c '
import json, sys
for value in json.loads(sys.argv[1]):
    print(value)
' "$ACP_DIRS_JSON" | clean_room_build_acp_command)"
    ACP_COMMAND="$ACP_PREFIX $ACP_COMMAND"
    if [ -n "$ACP_CWD" ]; then
        local _quoted_cwd
        _quoted_cwd="$(clean_room_quote_bash "$ACP_CWD")"
        ACP_COMMAND="cd -- $_quoted_cwd && $ACP_COMMAND"
    fi
    echo "eval: ACP command -> $ACP_COMMAND"

    # --- Tier-P precondition: cheap in-box smoke of the plugin CLI ------------
    if [ "${SKIP_TIER_P:-0}" != 1 ] && [ -n "$TIERP" ]; then
        echo "== eval: Tier-P precondition ($TIERP) =="
        if ! docker exec "$CONTAINER" /bin/bash -lc "$TIERP" >/dev/null 2>&1; then
            echo "eval: Tier-P precondition '$TIERP' failed -- refusing to spend an eval on a broken CLI surface. Fix the plugin's *-solo Tier-P scenario first, or pass --skip-tier-p-gate to force." >&2
            exit 1
        fi
        echo "   precondition OK"
    fi

    # --- 3) register the box as a bridge agent -------------------------------
    if ! do_bridge_register; then
        echo "eval: could not register $CONTAINER with agent-bridge" >&2
        exit 1
    fi

    # --- reproducibility fingerprints (prompt hash computed above) -----------
    local docs_hash copilot_ver
    docs_hash="$(docker exec "$CONTAINER" python3 -c '
import hashlib
import json
import pathlib
import sys

roots = [pathlib.Path(value) for value in json.loads(sys.argv[1])]
if not roots:
    roots = [pathlib.Path.home() / ".copilot" / "installed-plugins"]
for root in roots:
    if not root.is_dir():
        raise SystemExit(f"evaluated payload root is not a directory: {root}")
ignored = {".git", ".pytest_cache", "__pycache__", "node_modules", "build", "dist"}
digest = hashlib.sha256()
for index, root in enumerate(roots):
    root = root.resolve()
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        relative = path.relative_to(root)
        if any(part in ignored or part.endswith(".egg-info") for part in relative.parts):
            continue
        if path.is_symlink():
            digest.update(f"L\0{index}\0{relative.as_posix()}\0".encode("utf-8"))
            digest.update(path.readlink().as_posix().encode("utf-8"))
            if path.is_file():
                digest.update(b"\0")
                digest.update(path.read_bytes())
        elif path.is_file() and path.suffix not in {".pyc", ".pyo"}:
            digest.update(f"F\0{index}\0{relative.as_posix()}\0".encode("utf-8"))
            digest.update(f"{path.stat().st_mode & 0o777:o}\0".encode("ascii"))
            digest.update(path.read_bytes())
print(digest.hexdigest()[:16])
' "$ACP_DIRS_JSON")" || {
        echo "eval: could not fingerprint the evaluated plugin payloads" >&2
        exit 1
    }
    [[ "$docs_hash" =~ ^[0-9a-f]{16}$ ]] || {
        echo "eval: evaluated plugin payload fingerprint is invalid" >&2
        exit 1
    }

    # --- 4/5) drive N times + capture transcripts ----------------------------
    echo "== eval: driving '$DRIVE_AGENT' x$RUN_COUNT (fresh session; literal-mode + stated purpose) =="
    [ "${PER_TURN:-0}" -gt 0 ] 2>/dev/null && echo "   per-turn timeout: ${PER_TURN}s"
    local prompt_txt="$eval_dir/prompt.txt" recs="$eval_dir/.runrecords"
    : > "$recs"
    local n run_dir transcript rel tag
    for n in $(seq 1 "$RUN_COUNT"); do
        if [ "$RUN_COUNT" -eq 1 ]; then run_dir="$eval_dir"; else run_dir="$eval_dir/run-$n"; mkdir -p "$run_dir"; fi
        transcript="$run_dir/transcript.txt"
        echo "   -- run $n/$RUN_COUNT --"
        end_agent_sessions "$DRIVE_AGENT"
        drive_with_timeout "$DRIVE_AGENT" "$prompt_txt" "${PER_TURN:-0}" > "$transcript"
        rel="${transcript#"$RESULTS"/}"
        printf '%s|%s|%s|%s\n' "$n" "$rel" "$DRIVE_DURATION" "$DRIVE_TIMED_OUT" >> "$recs"
        tag=""; [ "$DRIVE_TIMED_OUT" = 1 ] && tag=" -- TIMED OUT"
        echo "      transcript -> $transcript  (${DRIVE_DURATION}s)$tag"
    done

    # --- 6) optional programmatic post-check (ground-truth evidence) ---------
    if [ -n "$POST_CHECK" ] && [ -f "$SCENARIO_DIR/$POST_CHECK" ]; then
        echo "== eval: programmatic post-check ($POST_CHECK) =="
        docker exec "$CONTAINER" /bin/bash -lc \
            "bash /home/operator/scenario/$POST_CHECK; cp -r \$HOME/cr-logs /home/operator/out/ 2>/dev/null" >/dev/null 2>&1 || true
    fi

    # --- 7) unregister the bridge agent --------------------------------------
    local bridge_cleanup_error=""
    if ! do_bridge_unregister >/dev/null 2>&1; then
        bridge_cleanup_error="could not unregister $CONTAINER from agent-bridge"
        echo "warn: $bridge_cleanup_error" >&2
    fi

    # --- write the eval run-manifest (judge packet index) --------------------
    copilot_ver="$(docker exec "$CONTAINER" /bin/bash -lc 'copilot --version 2>/dev/null' 2>/dev/null | head -1)"
    CR_SCENARIO_NAME="$SCENARIO_NAME" CR_FAMILY="$FAMILY" CR_IMAGE="$IMAGE" CR_RUN_COUNT="$RUN_COUNT" \
    CR_AGG="$AGG" CR_COPILOT_VER="$copilot_ver" CR_PROMPT_HASH="$PROMPT_HASH" CR_DOCS_HASH="$docs_hash" \
    CR_ACP_DIRS_JSON="$ACP_DIRS_JSON" \
    CR_PER_TURN="${PER_TURN:-0}" CR_TIERP="$TIERP" CR_SKIP_TIER_P="${SKIP_TIER_P:-0}" \
    CR_BRIDGE_CLEANUP_ERROR="$bridge_cleanup_error" \
    "$(_py)" - "$recs" > "$eval_dir/eval-run.json" <<'PY'
import json, os, sys
recs_path = sys.argv[1]
runs = []
if os.path.exists(recs_path):
    for line in open(recs_path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line:
            continue
        n, transcript, dur, timed = line.split("|")
        runs.append({"n": int(n), "transcript": transcript, "duration_s": int(dur), "timed_out": timed == "1"})
e = os.environ
tierp = e["CR_TIERP"]
tierp_field = f"{tierp} (SKIPPED)" if e["CR_SKIP_TIER_P"] == "1" and tierp else tierp
meta = {
    "scenario": e["CR_SCENARIO_NAME"], "tier": "E", "family": e["CR_FAMILY"], "image": e["CR_IMAGE"],
    "prompt": "eval/prompt.txt", "literal_mode": "eval/literal-mode.txt",
    "runs": runs, "run_count": int(e["CR_RUN_COUNT"]), "aggregate_policy": e["CR_AGG"],
    "copilot_version": e["CR_COPILOT_VER"], "prompt_hash": e["CR_PROMPT_HASH"], "docs_hash": e["CR_DOCS_HASH"],
    "acp_plugin_dirs": json.loads(e["CR_ACP_DIRS_JSON"]),
    "per_turn_timeout_s": int(e["CR_PER_TURN"]),
    "tier_p_precondition": tierp_field,
    "bridge_cleanup_error": e["CR_BRIDGE_CLEANUP_ERROR"],
    "max_credits_note": "runs.max_credits is advisory: the agent-bridge create transport does not expose per-turn credits, so it cannot be hard-enforced from the runner (see TIER-E-EXECUTION.md).",
    "report": "cr-report.json", "cr_logs": "cr-logs/", "judged": False,
    "note": "Run clean-room-judge on this packet, then write cr-eval.json (see TIER-E-EXECUTION.md).",
}
json.dump(meta, sys.stdout, indent=2)
PY
    rm -f "$recs"

    # --- report the judge packet ---------------------------------------------
    echo; echo "== eval complete =="
    echo "judge packet (hand to clean-room-judge via the validating-in-clean-room skill):"
    echo "  expected outcome : $manifest (manifest.expected_outcome + prompt)"
    echo "  transcript(s)    : $eval_dir"
    echo "  report + logs    : $RESULTS"
    echo "  run index        : $eval_dir/eval-run.json"
    echo "results dir: $RESULTS"
    local teardown_error=""
    case "$THEN" in
        shell) do_shell ;;
        down)
            if ! do_down; then
                teardown_error="could not tear down $CONTAINER"
            fi
            ;;
    esac
    [ -z "$bridge_cleanup_error" ] && [ -z "$teardown_error" ] || return 1
}

case "$MODE" in
    build) do_build ;;
    auth)  do_auth ;;
    run)   do_run ;;
    eval)  do_eval ;;
    shell) do_shell ;;
    down)  do_down ;;
    bridge-register)   do_bridge_register ;;
    bridge-unregister) do_bridge_unregister ;;
    all)   do_build; do_run ;;
esac
