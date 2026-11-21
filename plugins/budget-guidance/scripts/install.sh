#!/usr/bin/env bash
# Install/update the budget-guidance runtime (Linux / WSL / macOS).
# Usage: ./install.sh [install|update|status|uninstall] [--force] [--install-dir DIR]

set -euo pipefail

_ok()   { printf '  [OK]   %s\n' "$1"; }
_skip() { printf '  [SKIP] %s\n' "$1"; }
_fail() { printf '  [FAIL] %s\n' "$1" >&2; }
_step() { printf '  ...    %s\n' "$1"; }

_remove_budget_build_artifacts() {
    local root="$1" artifact
    for artifact in "$root/build" "$root"/*.egg-info "$root"/src/*.egg-info; do
        [[ -e "$artifact" || -L "$artifact" ]] || continue
        if ! rm -rf -- "$artifact"; then
            _fail 'Cannot remove owned package build artifacts'
            return 1
        fi
    done
}

_budget_procutil_source() {
    local procutil="$PLUGIN_DIR/libs/agent-procutil"
    if [[ ! -f "$procutil/pyproject.toml" ]]; then
        procutil="$PLUGIN_DIR/../../libs/agent-procutil"
    fi
    [[ -f "$procutil/pyproject.toml" ]] || { _fail 'Owning agent-procutil source is missing' >&2; return 1; }
    (cd "$procutil" && pwd)
}

_install_budget_guidance_package() {
    local dependencies=()
    if [[ -z "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
        local procutil
        procutil="$(_budget_procutil_source)" || return 1
        _remove_budget_build_artifacts "$procutil" || return 1
        dependencies+=("$procutil")
    fi
    _remove_budget_build_artifacts "$PLUGIN_DIR" || return 1
    if [[ "$HAVE_UV" -eq 1 ]]; then
        local uv_rc=0 index_helper
        index_helper="$(_budget_pip_index_helper)" || return 1
        invoke_uv_bootstrap_environment "$VENV_PYTHON" -I "$(dirname "$index_helper")/installer_dependency_policy.py" \
            --uv "$(command -v uv)" -- --python "$VENV_PYTHON" "${dependencies[@]}" "$PLUGIN_DIR" --quiet || uv_rc=$?
        for dependency in "${dependencies[@]}"; do _remove_budget_build_artifacts "$dependency" || return 1; done
        _remove_budget_build_artifacts "$PLUGIN_DIR" || return 1
        return "$uv_rc"
    fi

    _fail 'uv is required; unrestricted pip fallback is not supported'
    return 1
}

ACTION="${BUDGET_GUIDANCE_ACTION:-install}"
FORCE="${BUDGET_GUIDANCE_FORCE:-0}"
INSTALL_DIR="${BUDGET_GUIDANCE_INSTALL_DIR:-}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        install|update|status|uninstall|stamp|provision) ACTION="$1"; shift ;;
        --force) FORCE=1; shift ;;
        --install-dir)
            [[ $# -ge 2 ]] || { _fail "--install-dir requires a value"; exit 2; }
            INSTALL_DIR="$2"
            shift 2
            ;;
        *) _fail "unknown argument: $1"; exit 2 ;;
    esac
done
# Preserve parsed options across the install-contract:v4 self-stage. The shared
# block re-execs after argument parsing, so the child reconstructs supported
# state from dedicated values rather than evaluating a shell command.
export BUDGET_GUIDANCE_ACTION="$ACTION"
export BUDGET_GUIDANCE_FORCE="$FORCE"
export BUDGET_GUIDANCE_INSTALL_DIR="$INSTALL_DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# === install-contract:v4 self-stage -- keep byte-identical across plugins ===
# dotfiles #935: a plugin installer reads its own payload (src/, libs/,
# pyproject.toml) to build the venv, so while it runs -- especially if it wedges
# or times out -- it holds the SINGLETON installed-plugins/<mkt>/<plugin> payload
# dir busy (cwd/open handles). A concurrent `copilot plugin update <plugin>` then
# fights it (os error 32 on Windows; POSIX is more forgiving, but the design must
# be uniform): the payload freezes at the old version and reconcile keeps
# reverting the runtime toward it (the version-drift saga). Fix: when running
# from the marketplace payload, copy the WHOLE payload into a UNIQUE
# per-invocation staging dir OUTSIDE the payload and re-exec from there, so the
# singleton is touched only for the fast copy. A stalled run then holds only its
# own throwaway stage dir, never blocking the next invocation or a `copilot
# plugin update`. COPILOT_PLUGIN_STAGED_FROM tells _source_kind the payload was
# really the marketplace (see below). Env-guarded against re-exec loops; the
# stage-dir path (not under installed-plugins) is a second guard. The staging
# parent doubles as a WATCHDOG: it launches the staged child in its OWN session/
# process group and, on a deadline, kills the WHOLE group (POSIX process-group
# kill -- the twin of Windows `taskkill /T`), so a stalled install (the
# session-start-hook failure class) self-terminates instead of leaking forever.
# Best-effort, pid-guarded reap of dead-owner stage dirs (a concurrent or wedged
# installer's dir is never touched -- it uses its own unique dir).
if [[ -z "${COPILOT_PLUGIN_INSTALL_STAGED:-}" ||
    "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" == */.copilot/installed-plugins/* ]]; then
    __ss_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    __ss_payload="$(cd "$__ss_self_dir/.." && pwd)"
    case "$(printf '%s' "$__ss_payload" | tr '\\' '/')" in
        */.copilot/installed-plugins/*)
            __ss_name="$(sed -n 's/.*"name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$__ss_payload/plugin.json" 2>/dev/null | head -1)"
            if [[ -n "$__ss_name" ]]; then
                case "${OSTYPE:-}:$PWD" in
                    msys*:*/.copilot/installed-plugins/*|cygwin*:*/.copilot/installed-plugins/*)
                        printf 'self-stage failed: Windows shell launch CWD pins the payload; launch from HOME or use install.ps1\n' >&2
                        exit 1
                        ;;
                esac
                # Maintenance keeps HOME as CWD; build inputs are absolute staged paths.
                if ! cd "$HOME"; then
                    printf 'self-stage failed: cannot leave the replaceable payload\n' >&2
                    exit 1
                fi
                __ss_root="$HOME/.$__ss_name/.install-stage"
                __ss_stage="$__ss_root/$(date -u +%Y%m%dT%H%M%S)-$$"
                if mkdir -p "$__ss_stage" && cp -a "$__ss_payload" "$__ss_stage/"; then
                    __ss_staged_payload="$__ss_stage/$(basename "$__ss_payload")"
                    __ss_entry="$__ss_staged_payload/scripts/$(basename "${BASH_SOURCE[0]}")"
                    # Reap prior stage dirs; NEVER touch a live one. Remove only a
                    # sibling whose owner pid (the -<pid> suffix) is DEAD, so a
                    # concurrent or wedged installer's dir is left alone.
                    if [[ -d "$__ss_root" ]]; then
                        for __ss_sib in "$__ss_root"/*; do
                            [[ -d "$__ss_sib" ]] || continue
                            if [[ "$__ss_sib" == "$__ss_stage" ]]; then continue; fi
                            __ss_owner="${__ss_sib##*-}"
                            if [[ "$__ss_owner" =~ ^[0-9]+$ ]] && kill -0 "$__ss_owner" 2>/dev/null; then continue; fi
                            rm -rf "$__ss_sib" 2>/dev/null || true
                        done
                    fi
                    # WATCHDOG deadline: <NAME>_INSTALL_DEADLINE_SEC, else
                    # COPILOT_PLUGIN_INSTALL_DEADLINE_SEC, else 480s; <=0 disables.
                    __ss_deadline=480
                    __ss_dl_var="$(printf '%s' "$__ss_name" | sed 's/[^A-Za-z0-9][^A-Za-z0-9]*/_/g' | tr '[:lower:]' '[:upper:]')_INSTALL_DEADLINE_SEC"
                    __ss_dl_raw="$(printenv "$__ss_dl_var" 2>/dev/null || true)"
                    if [[ -z "$__ss_dl_raw" ]]; then __ss_dl_raw="${COPILOT_PLUGIN_INSTALL_DEADLINE_SEC:-}"; fi
                    if [[ "$__ss_dl_raw" =~ ^-?[0-9]+$ ]]; then __ss_deadline="$__ss_dl_raw"; fi
                    export COPILOT_PLUGIN_INSTALL_STAGED=1
                    export COPILOT_PLUGIN_STAGED_FROM="$__ss_payload"
                    # Launch the staged child in its OWN process group (bash job
                    # control) so `wait` propagates its REAL exit code AND the
                    # watchdog can kill the WHOLE tree via a process-group signal
                    # (the POSIX twin of Windows `taskkill /T`). setsid -w is
                    # avoided: on some util-linux builds it swallows the child's
                    # exit code (returns 0), which would mask a failed install.
                    # Re-exec the watchdog without a payload script descriptor.
                    # Merely changing CWD leaves bash's script handle open on Windows.
                    __ss_watchdog="$(cat <<'INSTALL_WATCHDOG'
                    __ss_entry=$1
                    __ss_name=$2
                    __ss_stage=$3
                    __ss_deadline=$4
                    shift 4
                    set -m
                    bash "$__ss_entry" "$@" &
                    __ss_child=$!
                    set +m
                    __ss_watcher=""
                    __ss_cancel() {
                        local signal_rc="$1"
                        kill -- -"$__ss_child" 2>/dev/null ||
                            kill "$__ss_child" 2>/dev/null || true
                        wait "$__ss_child" 2>/dev/null || true
                        if [[ -n "$__ss_watcher" ]]; then
                            kill "$__ss_watcher" 2>/dev/null || true
                            wait "$__ss_watcher" 2>/dev/null || true
                        fi
                        trap - INT TERM
                        exit "$signal_rc"
                    }
                    trap '__ss_cancel 130' INT
                    trap '__ss_cancel 143' TERM
                    if [[ "$__ss_deadline" -gt 0 ]]; then
                        (
                            __ss_waited=0
                            while kill -0 "$__ss_child" 2>/dev/null; do
                                sleep 1
                                __ss_waited=$((__ss_waited + 1))
                                if [[ "$__ss_waited" -ge "$__ss_deadline" ]]; then
                                    : > "$__ss_stage/.watchdog-fired"
                                    printf '[%sZ] WATCHDOG-KILL %s: install exceeded %ss deadline (child pid %s); killed tree. Slot lacks a completion marker -> will be tossed + retried. Stage: %s\n' \
                                        "$(date -u +%Y-%m-%dT%H:%M:%S)" "$__ss_name" "$__ss_deadline" "$__ss_child" "$__ss_stage" \
                                        >> "$HOME/.$__ss_name/reconcile.err.log" 2>/dev/null || true
                                    kill -- -"$__ss_child" 2>/dev/null || kill "$__ss_child" 2>/dev/null || true
                                    break
                                fi
                            done
                        ) &
                        __ss_watcher=$!
                        if wait "$__ss_child"; then __ss_rc=0; else __ss_rc=$?; fi
                        kill "$__ss_watcher" 2>/dev/null || true
                        wait "$__ss_watcher" 2>/dev/null || true
                        if [[ -e "$__ss_stage/.watchdog-fired" ]]; then exit 124; fi
                        exit "$__ss_rc"
                    fi
                    if wait "$__ss_child"; then exit 0; else exit $?; fi
INSTALL_WATCHDOG
                    )"
                    exec bash -c "$__ss_watchdog" bash \
                        "$__ss_entry" "$__ss_name" "$__ss_stage" "$__ss_deadline" "$@"
                else
                    printf 'self-stage failed; refusing to run from the replaceable payload\n' >&2
                    exit 1
                fi
            else
                printf 'self-stage requires a plugin identity before leaving the payload\n' >&2
                exit 1
            fi
            ;;
    esac
fi
# === end install-contract:v4 self-stage ===

# === install-contract:v4 smoke seam (test-only) -- keep byte-identical ===
# #935 install-flow test hook. When COPILOT_PLUGIN_INSTALL_SMOKE is set, prove
# the self-stage/lock/watchdog behavior WITHOUT a heavy venv build: this
# (post-stage) process records where it runs from + the recorded marketplace
# origin, optionally spawns a grandchild sleeper in the SAME process group (so a
# watchdog test can prove the WHOLE tree is killed), then sleeps to simulate a
# slow/wedged install. Never set in production.
if [[ -n "${COPILOT_PLUGIN_INSTALL_SMOKE:-}" ]]; then
    __sm_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    __sm_payload="$(cd "$__sm_self_dir/.." && pwd)"
    __sm_name="$(sed -n 's/.*"name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$__sm_payload/plugin.json" 2>/dev/null | head -1)"
    __sm_home="$HOME/.$__sm_name"
    mkdir -p "$__sm_home"
    __sm_sleep=6
    if [[ "${COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP:-}" =~ ^[0-9]+$ ]]; then __sm_sleep="$COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP"; fi
    __sm_grand_pid=0
    if [[ -n "${COPILOT_PLUGIN_INSTALL_SMOKE_GRANDCHILD:-}" ]]; then
        __sm_grand_sleep="$__sm_sleep"
        if [[ "$__sm_grand_sleep" -lt 3600 ]]; then __sm_grand_sleep=3600; fi
        sleep "$__sm_grand_sleep" &
        __sm_grand_pid=$!
    fi
    __sm_staged=false
    if [[ -n "${COPILOT_PLUGIN_INSTALL_STAGED:-}" ]]; then __sm_staged=true; fi
    printf '{"ran_from":"%s","staged_from":"%s","staged":%s,"working_dir":"%s","child_pid":%s,"grandchild_pid":%s}\n' \
        "$__sm_self_dir" "${COPILOT_PLUGIN_STAGED_FROM:-}" "$__sm_staged" "$PWD" "$$" "$__sm_grand_pid" \
        > "$__sm_home/smoke.json"
    sleep "$__sm_sleep"
    exit 0
fi
# === end install-contract:v4 smoke seam ===

# #935: bound uv's per-request network wait so a hung index/download degrades to
# "failed + retryable" rather than wedging the install; the self-stage watchdog
# is the authoritative TOTAL bound, this just shortens single-request stalls.
if [[ -z "${UV_HTTP_TIMEOUT:-}" ]]; then export UV_HTTP_TIMEOUT=60; fi

PKG_SRC_DIR="$PLUGIN_DIR/src/budget_guidance"
. "$SCRIPT_DIR/installer-engine.sh"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.budget-guidance}"
VENV_DIR="$INSTALL_DIR/.venv"
LOCAL_BIN="$HOME/.local/bin"
VENV_PYTHON="$VENV_DIR/bin/python"
STUB="$LOCAL_BIN/budget-guidance"
MANIFEST_PATH="$INSTALL_DIR/deploy-manifest.json"

# === install-contract:v3 versioned-venv (budget-guidance: .venv-as-symlink) ===
# Immutable per-version runtime (#581): build into versions/<version> and make the
# `.venv` path a symlink into it, so the binstub + manifest resolve through the
# link. CLI (no daemon). LINK_DIR = stable `.venv`; VENV_DIR = the versions/<v>
# slot. ALWAYS versioned -- the env opt-out (COPILOT_EXT_NO_VERSIONED /
# BUDGET_GUIDANCE_VERSIONED) and the legacy in-place fork are retired;
# scripts/versioned_runtime.py owns the swap + migration + gc.
LINK_DIR="$VENV_DIR"
LINK_PYTHON="$VENV_PYTHON"
VERSIONED_RUNTIME=1
SRC_VERSION=""
if [[ -f "$PLUGIN_DIR/pyproject.toml" ]]; then
    SRC_VERSION="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" | head -n1)"
fi
if [[ -z "$SRC_VERSION" ]]; then
    echo "[FAIL] Cannot determine plugin version from pyproject.toml (required for the versioned runtime)." >&2
    exit 1
fi
PACKAGE_VERSION="$SRC_VERSION"
SOURCE_RUNTIME_IDENTITY=""
BUDGET_BOOTSTRAP_PYTHON=""
VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
VENV_PYTHON="$VENV_DIR/bin/python"
# Marker-only: retire the `.venv` symlink (uniform-runtime-resolution, #765).
# LINK_PYTHON now points at the versioned slot directly (the link is no longer
# created); LINK_DIR is kept ONLY to derive the `--link-name` for versioned_runtime
# so activate/gc can still find and REMOVE any pre-existing `.venv` link.
LINK_PYTHON="$VENV_PYTHON"

_versioned_activate() {
    _source_runtime_unchanged || return 1
    # CLI (no daemon): health-gate the slot, swap the `.venv` symlink onto it
    # (first migration moves a legacy real `.venv` aside), gc keeping current +
    # previous-good. Returns non-zero on failure. No-op in legacy mode.
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$VENV_DIR/bin/python"
    [[ -x "$py" ]] || py="$LINK_DIR/bin/python"
    [[ -x "$py" ]] || return 0
    if ! "$VENV_PYTHON" -c 'import budget_guidance' 2>/dev/null; then
        _fail "Fresh runtime slot failed its health gate (versions/$SRC_VERSION) -- not activating"
        return 1
    fi
    _versioned_mark_complete || return 1
    local prev
    prev="$("$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" current 2>/dev/null || echo "")"
    _source_runtime_unchanged || return 1
    if ! "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" activate "$SRC_VERSION" --replace-nonlink --no-link; then
        _fail "Failed to activate versioned runtime slot (versions/$SRC_VERSION; marker-only, no .venv link)"
        return 1
    fi
    _ok "Runtime version $SRC_VERSION active (marker-only; versions/$SRC_VERSION)"
    if [[ -n "$prev" ]]; then
        "$VENV_PYTHON" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids --keep "$prev" 2>&1 | sed 's/^/  gc: /' || true
    else
        "$VENV_PYTHON" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids 2>&1 | sed 's/^/  gc: /' || true
    fi
    return 0
}
# === end install-contract:v3 versioned-venv ===

_bootstrap_python() {
    if [[ -n "${BUDGET_BOOTSTRAP_PYTHON:-}" ]]; then
        printf '%s\n' "$BUDGET_BOOTSTRAP_PYTHON"
        return 0
    fi
    . "$SCRIPT_DIR/bootstrap-uv.sh"
    resolve_uv_bootstrap_python "$SCRIPT_DIR/bootstrap-python-version.txt"
}

_budget_pip_index_helper() {
    local helper="$SCRIPT_DIR/pip-index-config"
    if [[ -f "$helper" ]] && ! grep -Fqx \
        '<!-- VENDOR_POINTER: source=libs/installer-engine/installer_pip_index.py kind=file -->' "$helper"; then
        printf '%s\n' "$helper"
        return 0
    fi
    local canonical="$SCRIPT_DIR/../../../libs/installer-engine/installer_pip_index.py"
    if [[ -z "$SOURCE_RUNTIME_IDENTITY" && -f "$helper" && -f "$canonical" &&
          "$(basename "$(dirname "$PLUGIN_DIR")")" == plugins &&
          -f "$PLUGIN_DIR/../../.github/plugin/marketplace.json" &&
          -f "$SCRIPT_DIR/../../../tools/materialize_main.py" ]]; then
        printf '%s\n' "$canonical"
        return 0
    fi
    _fail 'The index reader is not prepared; use the owning source-preparation flow.'
    return 1
}

_budget_snapshot_path_unlinked() {
    local base="$1" relative="$2" component
    local -a components
    [[ "$relative" =~ ^(scripts|libs)/[A-Za-z0-9_./-]+$ &&
        ! "$relative" =~ (^|/)\.\.?(/|$) ]] || return 1
    IFS=/ read -ra components <<< "$relative"
    for component in "${components[@]}"; do
        base="$base/$component"
        [[ ! -L "$base" ]] || { _fail 'Linked source snapshot asset refused'; return 1; }
    done
}

_materialize_budget_source_snapshot() {
    local snapshot="$1" helper root operation source target extra directory previous="" index
    local request="" request_target=""
    local -a sources=() targets=() directories=() batch=()
    helper="$(_budget_pip_index_helper)" || return 1
    [[ "$helper" != "$SCRIPT_DIR/pip-index-config" ]] || return 0
    root="$(cd "$PLUGIN_DIR/../.." && pwd)" || return 1
    [[ -s "$SCRIPT_DIR/installer-source-assets.tsv" ]] || {
        _fail 'The owning native source asset plan is unavailable'
        return 1
    }
    [[ ! -L "$root/libs" && ! -L "$root/libs/installer-engine" &&
        ! -L "$snapshot/scripts" && ! -L "$snapshot/libs" ]] || return 1
    while IFS=$'\t' read -r operation source target extra; do
        [[ -z "$extra" && "$target" =~ ^(scripts|libs)/[A-Za-z0-9_./-]+$ &&
            ! "$target" =~ (^|/)\.\.?(/|$) ]] || return 1
        _budget_snapshot_path_unlinked "$snapshot" "$target" || return 1
        directories+=("$snapshot/${target%/*}")
        case "$operation" in
            copy)
                _budget_snapshot_path_unlinked "$root" "$source" || return 1
                [[ -f "$root/$source" && ( "$source" == "$target" ||
                    ( "$target" == scripts/* && "$source" == libs/installer-engine/* ) ) ]] || return 1
                sources+=("$source")
                targets+=("$target")
                ;;
            request)
                [[ "$target" == scripts/bootstrap-python-version.txt &&
                    "$source" =~ ^cpython@3\.[0-9]+\.[0-9]+$ ]] || return 1
                request="$source"
                request_target="$target"
                ;;
            *) return 1 ;;
        esac
    done < "$SCRIPT_DIR/installer-source-assets.tsv"
    mkdir -p "${directories[@]}" || return 1
    # Ordinary same-name files share one native copy per destination directory.
    for ((index=0; index<${#sources[@]}; index++)); do
        source="${sources[index]}" target="${targets[index]}"
        directory="$snapshot/${target%/*}"
        if [[ "$directory" != "$previous" && ${#batch[@]} -gt 0 ]]; then
            cp -p "${batch[@]}" "$previous/" || return 1
            batch=()
        fi
        previous="$directory"
        if [[ "${source##*/}" != "${target##*/}" ]]; then
            cp -p "$root/$source" "$snapshot/$target" || return 1
        else
            batch+=("$root/$source")
        fi
    done
    if [[ ${#batch[@]} -gt 0 ]]; then
        cp -p "${batch[@]}" "$previous/" || return 1
    fi
    if [[ -n "$request" ]]; then
        printf '%s\n' "$request" > "$snapshot/$request_target" || return 1
    fi
    for ((index=0; index<${#sources[@]}; index++)); do
        _budget_snapshot_path_unlinked "$root" "${sources[index]}" || return 1
        _budget_snapshot_path_unlinked "$snapshot" "${targets[index]}" || return 1
        [[ -f "$snapshot/${targets[index]}" ]] || return 1
    done
}

_source_runtime_identity() {
    local py
    py="$(_bootstrap_python)" || return 1
    "$py" -I -B "$SCRIPT_DIR/versioned_runtime_source.py" \
        --payload "${1:-$PLUGIN_DIR}" --package-version "$PACKAGE_VERSION" \
        --plugin budget-guidance --format json "${@:2}"
}

_initialize_source_runtime() {
    [[ -n "${COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN+x}" || -e "$PLUGIN_DIR/runtime-source.json" || -L "$PLUGIN_DIR/runtime-source.json" ]] || return 0
    SOURCE_RUNTIME_IDENTITY="$(_source_runtime_identity)" || return 1
    if [[ "$SOURCE_RUNTIME_IDENTITY" == '{}' ]]; then
        _fail 'Source runtime identity disappeared during runtime selection'
        return 1
    fi
    local py
    py="$(_bootstrap_python)" || return 1
    SRC_VERSION="$("$py" -I -c 'import json,sys; print(json.loads(sys.argv[1])["runtime_version"])' "$SOURCE_RUNTIME_IDENTITY")" || return 1
    VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
    VENV_PYTHON="$VENV_DIR/bin/python"
    LINK_PYTHON="$VENV_PYTHON"
}

_source_runtime_unchanged() {
    [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" || -e "$PLUGIN_DIR/runtime-source.json" || -L "$PLUGIN_DIR/runtime-source.json" ]] || return 0
    local identity expected="${SOURCE_RUNTIME_IDENTITY:-}"
    [[ -n "$expected" ]] || expected='{}'
    identity="$(_source_runtime_identity "$PLUGIN_DIR" --expected-runtime-version "$SRC_VERSION")" || return 1
    if [[ "$identity" != "$expected" ]]; then
        _fail 'Source runtime identity changed after runtime selection; refusing publication'
        return 1
    fi
}

_payload_hash() {
    # Cheap payload fingerprint for the completion marker (#935): sha256 of
    # pyproject.toml + the vendored-lib version set. Detects a dev-checkout that
    # changed the payload WITHOUT bumping the version. Fails on any error.
    local __parts=""
    if [[ -f "$PLUGIN_DIR/pyproject.toml" ]]; then __parts="$(cat "$PLUGIN_DIR/pyproject.toml")"; fi
    if [[ -d "$PLUGIN_DIR/libs" ]]; then
        local __f
        while IFS= read -r __f; do
            __parts="$__parts"$'\n'"$(cat "$__f")"
        done < <(find "$PLUGIN_DIR/libs" -name pyproject.toml 2>/dev/null | sort)
    fi
    if [[ ! -f "$PLUGIN_DIR/libs/agent-procutil/pyproject.toml" ]]; then
        local procutil
        procutil="$(_budget_procutil_source)" || return 1
        __parts="$__parts"$'\n'"$(cat "$procutil/pyproject.toml")"
    fi
    # Fails (no output) when no SHA-256 implementation works: the caller must
    # then refuse to publish a slot without its fingerprint.
    local __d
    if command -v sha256sum >/dev/null 2>&1; then
        __d="$(printf '%s' "$__parts" | sha256sum 2>/dev/null)" || return 1
    elif command -v shasum >/dev/null 2>&1; then
        __d="$(printf '%s' "$__parts" | shasum -a 256 2>/dev/null)" || return 1
    elif command -v openssl >/dev/null 2>&1; then
        __d="$(printf '%s' "$__parts" | openssl dgst -sha256 -r 2>/dev/null)" || return 1
    else
        return 1
    fi
    __d="${__d%% *}"
    [[ "$__d" =~ ^[0-9a-f]{64}$ ]] || return 1
    printf '%s\n' "$__d"
}

_versioned_slot_clean() {
    _source_runtime_unchanged || return 1
    # #935: ensure the target slot exists, tossing it first if a prior build left
    # it INCOMPLETE (no completion marker) so we never `uv venv --allow-existing`
    # over a corpse. The current/active slot is never tossed (the link-name is
    # derived from LINK_DIR so the current-slot guard works per plugin). No-op in
    # legacy mode.
    [[ "$VERSIONED_RUNTIME" == 1 ]] || { _fail 'Construction requires a versioned candidate'; return 1; }
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py
    py="$(_bootstrap_python)" || return 1
    [[ -n "$py" ]] || return 1
    if ! declare -F _runtime_construction_begin >/dev/null; then
        local helpers
        helpers="$("$py" -I "$vr" --root "$INSTALL_DIR" construction-shell --shell sh)" || return 1
        eval "$helpers"
    fi
    _runtime_construction_begin "$py" "$vr" "$INSTALL_DIR" "$SRC_VERSION" \
        "$(_payload_hash)" "$(basename "$LINK_DIR")" || return 1
    _source_runtime_unchanged
}

_versioned_mark_complete() {
    # #935: write the slot's completion marker AFTER its isolated health gate
    # passed, so "marker present" == "healthy, complete build". A crashed /
    # watchdog-killed install never reaches here, leaving its slot markerless and
    # thus tossable + retryable. No-op in legacy mode. Runs the stdlib-only
    # versioned_runtime.py via any bootstrap python (the marker is slot-scoped, so
    # this helper is portable byte-identically across plugins).
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local actual_hash
    actual_hash="$(_payload_hash)" || return 1
    _source_runtime_unchanged || return 1
    _runtime_construction_complete "$actual_hash"
}

# === install-contract:v4 source-kind -- keep byte-identical across plugins ===
# A runtime footprint's source is inferred from where the installer runs.
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local. #935: when the installer self-staged
# out of the marketplace payload, its live path is a throwaway stage dir, so
# infer the kind from the ORIGINAL payload path the self-stage prologue recorded
# in COPILOT_PLUGIN_STAGED_FROM (else the current path).
_source_kind() {
    case "$(printf '%s' "${COPILOT_PLUGIN_STAGED_FROM:-$1}" | tr '\\' '/')" in
        */.copilot/installed-plugins/*) printf 'marketplace' ;;
        *) printf 'local' ;;
    esac
}
# === end install-contract:v4 source-kind ===
_git_info() {
    local path="$1" commit branch dirty
    commit=$(git -C "$path" rev-parse --short HEAD 2>/dev/null || echo "unknown")
    branch=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    dirty="false"
    [[ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]] && dirty="true"
    echo "$commit $branch $dirty"
}

# --- self-provisioning (runtime-self-provisioning pattern) -------------------
# Vendor a standalone uv when absent (pristine box has neither uv nor pip/venv).
_ensure_uv() {
    command -v uv >/dev/null 2>&1 && return 0
    local tooldir="$LOCAL_BIN"
    if [[ -x "$tooldir/uv" ]]; then export PATH="$tooldir:$PATH"; return 0; fi
    _step "uv not found -- vendoring a standalone uv into $tooldir"
    mkdir -p "$tooldir"
    local url="https://astral.sh/uv/install.sh" script="$INSTALL_DIR/tool/uv-install.sh" got=""
    mkdir -p "$(dirname "$script")"
    if command -v curl >/dev/null 2>&1; then curl -LsSf "$url" -o "$script" 2>/dev/null && got=1; fi
    if [[ -z "$got" ]] && command -v wget >/dev/null 2>&1; then wget -qO "$script" "$url" 2>/dev/null && got=1; fi
    if [[ -n "$got" && -s "$script" ]]; then
        env UV_INSTALL_DIR="$tooldir" sh "$script" >/dev/null 2>&1 || true
    fi
    if [[ -x "$tooldir/uv" ]]; then export PATH="$tooldir:$PATH"; _ok "Vendored uv into $tooldir"; return 0; fi
    return 1
}
# Mirror pip's configured index to uv on a governed box (public PyPI TLS-blocked).
_ensure_uv_index() {
    [[ -n "${UV_INDEX_URL:-}${UV_DEFAULT_INDEX:-}" ]] && return 0
    local py idx helper site_prefix=""
    if [[ -n "${VIRTUAL_ENV:-}" && -f "$VIRTUAL_ENV/pyvenv.cfg" ]]; then
        site_prefix="$VIRTUAL_ENV"
    elif [[ -n "${CONDA_PREFIX:-}" && -d "$CONDA_PREFIX/conda-meta" ]]; then
        site_prefix="$CONDA_PREFIX"
    fi
    py="$(_bootstrap_python)" || return 1
    helper="$(_budget_pip_index_helper)" || return 1
    idx="$(invoke_uv_bootstrap_environment "$py" -I -B "$helper" \
        --uv "$(command -v uv)" --site-prefix "$site_prefix")" || return 1
    if [[ -n "$idx" ]]; then export UV_DEFAULT_INDEX="$idx"; _step "uv index derived from pip config (governed-feed bridge)"; fi
}
# Deploy the prepared-only binstub. The existing resolver selects the runtime;
# missing preparation reports the owning explicit provision action.
# Co-deploy the canonical marker-only resolver so the binstub (and any launcher)
# resolves the interpreter the ONE uniform way (uniform-runtime-resolution, #765).
deploy_resolver() {
    mkdir -p "$INSTALL_DIR/bin"
    for r in resolve-runtime.sh resolve-runtime.ps1; do
        [ -f "$SCRIPT_DIR/$r" ] && cp -f "$SCRIPT_DIR/$r" "$INSTALL_DIR/bin/$r"
    done
}

deploy_binstub() {
    mkdir -p "$LOCAL_BIN"
    deploy_resolver
    cat > "$STUB" << 'STUBEOF'
#!/usr/bin/env bash
# budget-guidance binstub -- prepared runtime only.
# Resolves the interpreter SOLELY via the junction-free versioned-runtime marker
# (the deployed resolve-runtime.sh; uniform-runtime-resolution, #765): current-
# version -> last-known-good -> newest complete slot. NEVER a `.venv` link, NEVER
# a PATH python. Missing setup is reported without changing the installation.
set -u
export PYTHONUTF8=1
unset UV_PYTHON UV_PYTHON_PREFERENCE UV_MANAGED_PYTHON UV_NO_MANAGED_PYTHON VIRTUAL_ENV CONDA_PREFIX UV_WORKING_DIR UV_WORKING_DIRECTORY UV_ISOLATED UV_ENV_FILE UV_PROJECT UV_FROZEN UV_LOCKED UV_NO_SYNC UV_CONFIG_FILE
_uv="$(command -v uv 2>/dev/null || true)"
case "$_uv" in /*) [ -x "$_uv" ] || _uv='' ;; *) _uv='' ;; esac
if [ -z "$_uv" ]; then
    printf 'budget-guidance: uv is unavailable; prepare the command prerequisites.\n' >&2
    exit 127
fi
_name="budget-guidance"
_root="$HOME/.$_name"
_resolver="$_root/bin/resolve-runtime.sh"
_resolve() {
    AGENT_RT_PY=""
    if [ -f "$_resolver" ]; then
        AGENT_RT_ROOT="$_root"
        . "$_resolver"
    fi
}
_resolve
if [ -z "$AGENT_RT_PY" ]; then
    printf 'budget-guidance: runtime is not prepared; run the owning scripts/install.sh install.\n' >&2
    exit 1
fi
exec "$_uv" run --no-project --no-config --no-env-file --no-python-downloads --managed-python \
    --python "$AGENT_RT_PY" python -B -m budget_guidance "$@"
STUBEOF
    chmod +x "$STUB"
    _ok "Binstub: $STUB (prepared uv runtime)"
}

__install_lock_link=""
__install_lock_fd=""
__owned_snapshot_tmp=""

_install_lock_timeout() {
    local __t="${COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC:-}"
    case "$__t" in
        ''|*[!0-9]*) __t=300 ;;
    esac
    [[ "$__t" -gt 0 ]] 2>/dev/null || __t=300
    printf '%s' "$__t"
}

_exit_install_lock() {
    if [[ -n "$__owned_snapshot_tmp" ]]; then
        rm -rf -- "$__owned_snapshot_tmp"
        __owned_snapshot_tmp=""
    fi
    if [[ -n "$__install_lock_link" ]]; then
        local __owner
        __owner="$(readlink "$__install_lock_link" 2>/dev/null || true)"
        [[ "$__owner" == "$$" ]] && rm -f "$__install_lock_link"
        __install_lock_link=""
    elif [[ -n "$__install_lock_fd" ]]; then
        flock -u "$__install_lock_fd" 2>/dev/null || true
        eval "exec $__install_lock_fd>&-"
        __install_lock_fd=""
    fi
}

_enter_install_lock() {
    local __timeout="${1:-$(_install_lock_timeout)}"
    mkdir -p "$INSTALL_DIR" 2>/dev/null || return 1
    if command -v flock >/dev/null 2>&1 && [[ "${COPILOT_EXT_NO_FLOCK:-}" != "1" ]]; then
        { exec 8>"$INSTALL_DIR/.install.lock"; } 2>/dev/null || return 1
        if ! flock -w "$__timeout" 8; then
            exec 8>&-
            return 1
        fi
        __install_lock_fd=8
        return 0
    fi
    local __link="$INSTALL_DIR/.install.lock.pid" __deadline __owner __live __now
    __deadline=$(( $(date +%s) + __timeout ))
    until ln -s "$$" "$__link" 2>/dev/null; do
        if [[ "$(date +%s)" -ge "$__deadline" ]]; then
            return 1
        fi
        __owner="$(readlink "$__link" 2>/dev/null || true)"
        if [[ -z "$__owner" ]]; then
            # An unattributable path or a vanished link never licenses unlinking.
            sleep 0.1
            continue
        fi
        case "$__owner" in
            *[!0-9]*) __live=0 ;;
            *) if kill -0 "$__owner" 2>/dev/null; then __live=1; else __live=0; fi ;;
        esac
        if [[ "$__live" == 1 ]]; then
            sleep 1
        elif ln -s "$$" "$__link.reap" 2>/dev/null; then
            # Serialize recovery and recheck ownership before removing a stale link.
            __now="$(readlink "$__link" 2>/dev/null || true)"
            if [[ -n "$__now" && "$__now" == "$__owner" ]]; then
                case "$__now" in
                    *[!0-9]*) rm -f "$__link" ;;
                    *) kill -0 "$__now" 2>/dev/null || rm -f "$__link" ;;
                esac
            fi
            rm -f "$__link.reap"
        else
            # Never reclaim a dead reaper's mutex with a check-then-unlink race.
            sleep 0.1
        fi
    done
    __install_lock_link="$__link"
    return 0
}

if [[ "$ACTION" == install || "$ACTION" == update || "$ACTION" == stamp || "$ACTION" == provision || "$ACTION" == uninstall ]]; then
    _enter_install_lock || {
        _fail "Could not acquire the install transaction lock within $(_install_lock_timeout)s -- refusing to proceed unlocked"
        exit 1
    }
    trap '_exit_install_lock' EXIT
    trap '_exit_install_lock; exit 130' INT
    trap '_exit_install_lock; exit 143' TERM
fi
if [[ "$ACTION" == install || "$ACTION" == update || "$ACTION" == provision ]]; then
    . "$SCRIPT_DIR/bootstrap-uv.sh"
    _ensure_uv || { _fail 'uv prerequisite setup failed'; exit 1; }
    BUDGET_BOOTSTRAP_PYTHON="$(resolve_uv_bootstrap_python "$SCRIPT_DIR/bootstrap-python-version.txt" Prepare)" || exit 1
fi
if [[ "$ACTION" == install || "$ACTION" == update || "$ACTION" == stamp || "$ACTION" == provision ]]; then
    _initialize_source_runtime || exit 1
    _source_runtime_unchanged || exit 1
fi

case "$ACTION" in
    install|update|provision)
        . "$SCRIPT_DIR/installer-instance-reconcile.sh"
        reconcile_installer_instances "$SCRIPT_DIR" "" "$INSTALL_DIR"
        ;;
esac
# Cheap 'stamp': snapshot the exact owning payload, then splat the binstub and
# defer the venv build to explicit provision. Preparation uses this
# immutable versioned snapshot and never searches other marketplace payloads.
if [[ "$ACTION" == "stamp" ]]; then
    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
    snap_dir="$INSTALL_DIR/snapshots/$SRC_VERSION"
    if [[ -n "$SOURCE_RUNTIME_IDENTITY" && ( -e "$snap_dir" || -L "$snap_dir" ) ]]; then
        snapshot_identity="$(_source_runtime_identity "$snap_dir" --expected-runtime-version "$SRC_VERSION")" || exit 1
        [[ "$snapshot_identity" == "$SOURCE_RUNTIME_IDENTITY" ]] || {
            _fail 'Existing source snapshot identity differs from the selected source; refusing replacement'
            exit 1
        }
    else
    snap_tmp="$snap_dir.tmp-$$"
    mkdir -p "${snap_dir%/*}"
    mkdir "$snap_tmp" || exit 1
    __owned_snapshot_tmp="$snap_tmp"
    cp -a "$PLUGIN_DIR/." "$snap_tmp/"
    if [[ -z "$SOURCE_RUNTIME_IDENTITY" ]]; then
    _materialize_budget_source_snapshot "$snap_tmp" || exit 1
    procutil="$(_budget_procutil_source)" || exit 1
    if [[ ! -d "$snap_tmp/libs/agent-procutil" ]]; then
        mkdir -p "$snap_tmp/libs"
        cp -a "$procutil" "$snap_tmp/libs/agent-procutil"
    fi
    awk '
        /^\[tool\.uv\.sources\][[:space:]]*$/ { sources=1; print; next }
        /^\[/ { sources=0 }
        sources && /^agent-procutil[[:space:]]*=/ {
            print "agent-procutil = { path = \"libs/agent-procutil\" }"; next
        }
        { print }
    ' "$snap_tmp/pyproject.toml" > "$snap_tmp/pyproject.toml.local" || exit 1
    mv "$snap_tmp/pyproject.toml.local" "$snap_tmp/pyproject.toml"
    rm -rf -- \
        "$snap_tmp/.git" \
        "$snap_tmp/.venv" \
        "$snap_tmp/__pycache__" \
        "$snap_tmp/build" \
        "$snap_tmp/dist" \
        "$snap_tmp/node_modules" \
        "$snap_tmp/tests" \
        "$snap_tmp/.pytest_cache" \
        "$snap_tmp/.mypy_cache"
    else
        snapshot_identity="$(_source_runtime_identity "$snap_tmp" --expected-runtime-version "$SRC_VERSION")" || exit 1
        [[ "$snapshot_identity" == "$SOURCE_RUNTIME_IDENTITY" ]] || { _fail 'Stamped source snapshot identity changed'; exit 1; }
    fi
    _source_runtime_unchanged || exit 1
    mkdir -p "$(dirname "$snap_dir")"
    rm -rf -- "$snap_dir"
    mv "$snap_tmp" "$snap_dir"
    __owned_snapshot_tmp=""
    if [[ -z "$SOURCE_RUNTIME_IDENTITY" ]]; then
        printf '%s\n' "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" > "$snap_dir/.payload-source"
    fi
    fi
    _source_runtime_unchanged || exit 1
    if [[ -n "$SOURCE_RUNTIME_IDENTITY" ]]; then
        source_python="$(_bootstrap_python)" || exit 1
        "$source_python" -I -B "$SCRIPT_DIR/versioned_runtime.py" --root "$INSTALL_DIR" \
            publish-redirects --payload-dir "$snap_dir" --version "$SRC_VERSION" \
            --expected-source-identity "$SOURCE_RUNTIME_IDENTITY" || exit 1
    else
        printf '%s\n' "$snap_dir" > "$INSTALL_DIR/payload-dir"
        printf '%s\n' "$SRC_VERSION" > "$INSTALL_DIR/stamped-version"
    fi
    _ok "Snapshot: $snap_dir"
    deploy_binstub
    _ok "Stamped: run the snapshot scripts/install.sh provision before command use; retain the same --install-dir."
    exit 0
fi

if [[ "$ACTION" == "status" ]]; then
    echo '=== budget-guidance status ==='
    [[ -x "$LINK_PYTHON" ]] && _ok "Runtime: $VENV_DIR" || _skip "Runtime missing: $VENV_DIR"
    [[ -x "$STUB" ]] && _ok "Binstub: $STUB" || _skip "Binstub missing: $STUB"
    [[ -f "$MANIFEST_PATH" ]] && _ok "Deploy manifest: $MANIFEST_PATH" || _skip "Deploy manifest missing"
    exit 0
fi

_remove_budget_runtime_code() {
    _finite_runtime_uninstall --remove-runtime
}

_remove_budget_helper_files() {
    local directory="$1" name
    shift
    [[ -d "$directory" && ! -L "$directory" ]] || { _fail "Refusing linked runtime helpers"; return 1; }
    for name in "$@"; do
        [[ -e "$directory/$name" || -L "$directory/$name" ]] || continue
        [[ -f "$directory/$name" && ! -L "$directory/$name" ]] || {
            _fail "Refusing linked/non-file runtime helper"; return 1;
        }
    done
    for name in "$@"; do rm -f -- "$directory/$name" || return 1; done
}

if [[ "$ACTION" == "uninstall" ]]; then
    . "$SCRIPT_DIR/uninstall-runtime.sh"
    _finite_runtime_uninstall --check || exit 1
    _remove_budget_runtime_code || exit 1
    _ok 'budget-guidance runtime removed (data, unknown files and coordination retained)'
    exit 0
fi

_step 'budget-guidance install'

if [[ ! -d "$PKG_SRC_DIR" ]]; then
    _fail "Package source not found at $PKG_SRC_DIR"
    exit 1
fi

HAVE_UV=0
if _ensure_uv; then HAVE_UV=1; fi

PYTHON_CMD="$(_bootstrap_python)" || exit 1

mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
_ok "Directories: $INSTALL_DIR"

# -- Deploy the session-start hook (version-gated runtime reconcile) --
# hooks.json runs ~/.budget-guidance/bin/bootstrap-check.sh at session start; it
# re-runs this installer only when the deployed version drifts from the payload.
BIN_HOOK_DIR="$INSTALL_DIR/bin"
mkdir -p "$BIN_HOOK_DIR"
for h in killswitch_mutation.py bootstrap-check.ps1 bootstrap-check.sh bootstrap-killswitch-guard.ps1 bootstrap-killswitch-guard.sh emit-mesh-pointer.ps1 emit-mesh-pointer.sh; do
    [ -f "$SCRIPT_DIR/$h" ] && cp -f "$SCRIPT_DIR/$h" "$BIN_HOOK_DIR/$h"
done
_ok "Session-start hook: $BIN_HOOK_DIR/bootstrap-check.sh"

_versioned_slot_clean || exit 1
if [[ "$_RUNTIME_CONSTRUCTION_REUSE" != 1 ]]; then
_ensure_uv_index
if [[ "$FORCE" -eq 1 || ! -x "$VENV_PYTHON" ]]; then
    _step 'Creating managed Python venv via uv...'
    _versioned_slot_clean
    invoke_uv_bootstrap_environment "$(command -v uv)" venv "$VENV_DIR" --python "$PYTHON_CMD" --managed-python \
        --no-python-downloads --allow-existing >/dev/null 2>&1 || {
        _fail 'uv could not construct the declared managed Python environment'
        exit 1
    }
    if [[ ! -x "$VENV_PYTHON" ]]; then
        _fail "Venv creation failed -- $VENV_PYTHON not found"
        exit 1
    fi
    _ok 'Venv created'
else
    _skip 'Venv already exists'
fi

if ! _install_budget_guidance_package; then
    _fail 'Failed to install budget-guidance package into venv'
    exit 1
fi
_ok 'Package installed: budget-guidance'
fi

# Versioned layout (#581): health-gate the slot + swap the `.venv` symlink.
# Source manifest inputs are captured before activation.
if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
    _source_runtime_unchanged || exit 1
    SOURCE_MANIFEST_GIT="$(_git_info "$(cd "$PLUGIN_DIR/../.." && pwd)")" || exit 1
fi
_versioned_activate || exit 1

deploy_binstub

KIND="$(_source_kind "$PLUGIN_DIR")"
if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
    VER="$PACKAGE_VERSION"
else
    VER="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"
fi
COMMIT="null"; BRANCH="null"; DIRTY="false"
if [[ "$KIND" == "local" ]]; then
    if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
        read -r _c _b _d <<< "$SOURCE_MANIFEST_GIT"
    else
        REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
        read -r _c _b _d <<< "$(_git_info "$REPO_ROOT")"
    fi
    COMMIT="\"$_c\""; BRANCH="\"$_b\""; DIRTY="$_d"
fi
TMP="$MANIFEST_PATH.tmp"
if [[ -z "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then _source_runtime_unchanged || exit 1; fi
SOURCE_FIELDS=""
if [[ -n "$SOURCE_RUNTIME_IDENTITY" ]]; then
    SOURCE_FIELDS=",${SOURCE_RUNTIME_IDENTITY:1:${#SOURCE_RUNTIME_IDENTITY}-2}"
fi
cat > "$TMP" << EOF
{
  "schema_version": 3,
  "service": "budget-guidance",
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$KIND",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "budget-guidance",
    "version": "$VER",
    "commit": $COMMIT,
    "branch": $BRANCH,
    "dirty": $DIRTY
  },
  "venv": "$VENV_DIR",
  "runtime": "python"$SOURCE_FIELDS
}
EOF
mv -f "$TMP" "$MANIFEST_PATH"
_ok "Deploy manifest written (source: $KIND)"

echo ''
if "$LINK_PYTHON" -c 'import budget_guidance' 2>/dev/null; then
    _ok 'Verification: module imports successfully'
else
    _fail 'Verification: module import failed'
    exit 1
fi

case ":$PATH:" in
    *":$LOCAL_BIN:"*) _ok "PATH: $LOCAL_BIN is on PATH" ;;
    *) _step "Add $LOCAL_BIN to your PATH (e.g. in ~/.bashrc): export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

_ok 'budget-guidance install'
echo '  Try: budget-guidance --version'
exit 0
