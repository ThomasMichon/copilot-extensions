#!/bin/sh
# Sourced by both front doors before runtime resolution. Non-update calls return.
if [ "${AGENT_WORKTREES_INTERNAL_SKIP_UPDATE_BOOTSTRAP:-}" = 1 ]; then
    unset AGENT_WORKTREES_INTERNAL_SKIP_UPDATE_BOOTSTRAP
    return 0
fi
_aw_project_controller_selected() (
    _runtime_root="$HOME/.agent-worktrees"
    if [ ! -e "$_runtime_root/current-version" ] && [ ! -e "$_runtime_root/last-known-good" ] &&
       [ ! -d "$_runtime_root/versions" ]; then
        if [ "${_aw_update_payload:-0}" != 1 ] || {
            [ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" ] &&
            [ ! -f "$HOME/.copilot-extensions/installation-mode.json" ]
        }; then
            return 1
        fi
    fi
    _probe_project=""
    _probe_has_project=0
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --no-project-controller|--repair-engine|--repair-engine=*) return 1 ;;
            --project|-p)
                if [ "$_probe_has_project" = 0 ] && [ "$#" -gt 1 ]; then
                    _probe_project="$2"; _probe_has_project=1; shift
                fi ;;
            --project=*)
                if [ "$_probe_has_project" = 0 ]; then
                    _probe_project="${1#*=}"; _probe_has_project=1
                fi ;;
        esac
        shift
    done
    _probe_payload="$(CDPATH= cd -- "$_aw_update_entry_dir/.." && pwd -P)" || return 125
    if [ ! -f "$_probe_payload/plugin.json" ]; then
        _probe_payload="$(cat "$_runtime_root/payload-dir" 2>/dev/null)" || _probe_payload=""
    fi
    _probe_worker="$_probe_payload/scripts/update_controller_probe.py"
    [ -n "$_probe_payload" ] && [ -f "$_probe_worker" ] || return 1
    _probe_python=""
    for _candidate in python3 python; do
        _candidate="$(command -v "$_candidate" 2>/dev/null)" || continue
        case "$_candidate" in *WindowsApps*|*windowsapps*) continue ;; esac
        _probe_python="$_candidate"
        break
    done
    if [ -z "$_probe_python" ] && [ -f "$_runtime_root/bin/resolve-runtime.sh" ]; then
        . "$_runtime_root/bin/resolve-runtime.sh"
        _probe_python="${AW_PY:-}"
    fi
    if [ -z "$_probe_python" ]; then
        printf '%s\n' '[agent-worktrees] controller probe requires Python 3; no update ran.' >&2
        return 125
    fi
    if [ "$_probe_has_project" = 1 ]; then
        _controller="$("$_probe_python" -I -S -B "$_probe_worker" --timeout 30 \
            --caller "$_aw_update_caller" --project "$_probe_project")" && _probe_result=0 || _probe_result=$?
    else
        _controller="$("$_probe_python" -I -S -B "$_probe_worker" --timeout 30 \
            --caller "$_aw_update_caller")" && _probe_result=0 || _probe_result=$?
    fi
    case "$_probe_result" in
        0) ;;
        10) return 1 ;;
        124|125) return "$_probe_result" ;;
        *) printf '%s\n' '[agent-worktrees] controller probe failed; no update ran.' >&2; return 125 ;;
    esac
    [ -n "$_controller" ]
)
_aw_update_entry() (
    # The global sh front door also serves Git's bundled Windows shell. Use
    # its matching native entry before interpreting Windows registration data.
    case "$(uname -s 2>/dev/null)" in
        MINGW*|MSYS*|CYGWIN*)
            command -v cygpath >/dev/null 2>&1 || {
                printf '%s\n' '[agent-worktrees] Windows Git-sh update requires cygpath.' >&2
                exit 127
            }
            _windows_entry="$(cygpath -w -- "$_aw_update_entry_dir/agent-worktrees-update-entry.ps1")" || exit $?
            _windows_host=""
            for _candidate in pwsh pwsh.exe powershell.exe; do
                if command -v "$_candidate" >/dev/null 2>&1; then
                    _windows_host="$_candidate"
                    break
                fi
            done
            if [ -z "$_windows_host" ]; then
                _system_root="${SYSTEMROOT:-${SystemRoot:-}}"
                if [ -n "$_system_root" ]; then
                    _system_root="$(cygpath -u -- "$_system_root")" || exit $?
                    _candidate="$_system_root/System32/WindowsPowerShell/v1.0/powershell.exe"
                    [ ! -f "$_candidate" ] || _windows_host="$_candidate"
                fi
            fi
            [ -n "$_windows_host" ] || {
                printf '%s\n' '[agent-worktrees] Windows Git-sh update requires PowerShell; no POSIX fallback.' >&2
                exit 127
            }
            AGENT_WORKTREES_INTERNAL_UPDATE_PAYLOAD="${_aw_update_payload:-0}" \
                MSYS2_ARG_CONV_EXCL='*' exec "$_windows_host" -NoProfile -ExecutionPolicy Bypass \
                -File "$_windows_entry" "$@"
            ;;
    esac
    if _aw_project_controller_selected "$@"; then
        _probe_rc=0
    else
        _probe_rc=$?
    fi
    case "$_probe_rc" in
        124|125) exit "$_probe_rc" ;;
    esac
    if [ "$_probe_rc" = 0 ]; then
        AGENT_WORKTREES_INTERNAL_SKIP_UPDATE_BOOTSTRAP=1 \
            AGENT_WORKTREES_INTERNAL_PROJECT_CONTROLLER=1 \
            exec bash "$_aw_update_caller" "$@"
    fi
    _project=""
    _has_project=0
    case "${1-}" in
        --project|-p) [ "$#" -ge 3 ] || return 0; _project="$2"; _has_project=1; shift 2 ;;
        --project=*) _project="${1#*=}"; _has_project=1; shift ;;
    esac
    [ "${1-}" = update ] || return 0
    shift
    for _arg do
        case "$_arg" in
            --phase|--phase=*|--plugin|--plugin=*|--inventory|--target|--target=*|--runtime-backend|--runtime-backend=*|--no-manager|--auth-report|--help|-h|--request-status|--request-status=*|--recovery-status|--recover-after-restart|--recover-for-repair)
                return 0 ;;
        esac
    done
    # A selected alternate provider is not authority to install this Manager.
    case "${AGENT_WORKTREES_CONTROL_PLANE_PROVIDER:-worktree-manager}" in
        worktree-manager) ;;
        *) printf '%s\n' '[agent-worktrees] update bootstrap requires the selected Manager provider.' >&2; exit 126 ;;
    esac
    _root="${WORKTREE_MANAGER_ROOT:-$HOME/.worktree-manager}"
    _registry="${AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR:-$HOME/.agent-worktrees/control-plane-providers.d}"
    _registrations=0
    _manager_root=""
    for _manifest in "$_registry"/*.json; do
        [ -e "$_manifest" ] || continue
        _registrations=$((_registrations + 1))
        _query="$_aw_update_entry_dir/agent-worktrees-update-json.awk"
        [ -f "$_query" ] || _query="$_aw_update_entry_dir/../scripts/installation-context/json-query.awk"
        [ -f "$_query" ] || { printf '%s\n' '[agent-worktrees] update source reader is unavailable.' >&2; exit 126; }
        _provider="$(LC_ALL=C awk -v mode=get -v query_path=provider -f "$_query" "$_manifest")" || exit 126
        if [ "$_provider" = worktree-manager ]; then
            [ -z "$_manager_root" ] || { printf '%s\n' '[agent-worktrees] duplicate Manager registration.' >&2; exit 126; }
            _manager_root="$(LC_ALL=C awk -v mode=get -v query_path=provider_root -f "$_query" "$_manifest")" || exit 126
        elif [ -z "${AGENT_WORKTREES_CONTROL_PLANE_PROVIDER:-}" ]; then
            printf '%s\n' '[agent-worktrees] select the Manager provider explicitly; provider preserved.' >&2
            exit 126
        fi
    done
    if [ -n "$_manager_root" ]; then
        case "$_manager_root" in /*) ;; *) printf '%s\n' '[agent-worktrees] Manager root is not absolute.' >&2; exit 126 ;; esac
        if [ -n "${WORKTREE_MANAGER_ROOT:-}" ] && [ "$WORKTREE_MANAGER_ROOT" != "$_manager_root" ]; then
            printf '%s\n' '[agent-worktrees] Manager root disagrees with its registration.' >&2
            exit 126
        fi
        _root="$_manager_root"
        export WORKTREE_MANAGER_ROOT="$_root"
    elif [ "$_registrations" -gt 0 ] || [ -n "${AGENT_WORKTREES_CONTROL_PLANE_PROVIDER:-}" ]; then
        printf '%s\n' '[agent-worktrees] selected Manager is not registered.' >&2
        exit 126
    fi
    # Bootstrap transport is independent of the runtime source read by bootstrap.
    _repo='https://github.com/ThomasMichon/copilot-extensions.git'
    _ref='main'
    _repo="${WORKTREE_MANAGER_BOOTSTRAP_REPO-$_repo}"
    _ref="${WORKTREE_MANAGER_BOOTSTRAP_REF-$_ref}"
    case "$_repo" in ""|-*|*[[:cntrl:]]*) printf '%s\n' '[agent-worktrees] invalid bootstrap transport source.' >&2; exit 126 ;; esac
    case "$_ref" in ""|-*|*[[:cntrl:]]*) printf '%s\n' '[agent-worktrees] invalid bootstrap transport ref.' >&2; exit 126 ;; esac
    _url=""
    case "$_repo" in
        https://github.com/*)
            _slug="${_repo#https://github.com/}"; _slug="${_slug%/}"; _slug="${_slug%.git}"
            _owner="${_slug%%/*}"; _name="${_slug#*/}"
            case "$_slug" in
                */*)
                    case "$_owner/$_name" in
                        *[!A-Za-z0-9._/-]*|*/*/*|/*|*/) ;;
                        *) case "$_ref" in
                            *[!A-Za-z0-9._/-]*) ;;
                            *) _url="https://raw.githubusercontent.com/$_owner/$_name/$_ref/worktree-manager/bootstrap.sh" ;;
                        esac ;;
                    esac ;;
            esac
            ;;
    esac
    export AGENT_WORKTREES_UPDATE_CONTEXT="${AGENT_WORKTREES_UPDATE_CONTEXT:-$(pwd -P)}"
    # Match the shared acquisition boundary; retain SSH/HTTPS credential inputs.
    for _git_var in GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE GIT_PREFIX GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES; do
        unset "$_git_var"
    done
    _scratch_base="${AGENT_SCRATCH_ROOT:-${TMPDIR:-/tmp}}"
    _scratch="$(mktemp -d "$_scratch_base/agent-update-$(date -u +%Y%m%d-%H%M%S)-XXXXXX")" || exit $?
    trap 'rm -f "$_scratch/bootstrap.sh"; rm -rf -- "$_scratch/source"; rmdir "$_scratch"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    _bootstrap="$_scratch/bootstrap.sh"
    if [ -n "$_url" ]; then
        if curl -fLsS "$_url" -o "$_bootstrap"; then
            :
        else
            _download_rc=$?
            command -v git >/dev/null 2>&1 || exit "$_download_rc"
            printf '%s\n' '[agent-worktrees] raw bootstrap download failed; fetching the same configured Git source.' >&2
            _url=""
        fi
    fi
    if [ -z "$_url" ]; then
        command -v git >/dev/null 2>&1 || {
            printf '%s\n' '[agent-worktrees] Git is required for the configured bootstrap source; source preserved.' >&2
            exit 127
        }
        # git -C changes how relative filesystem sources are interpreted.
        _prefix="${_repo%%:*}"
        case "$_repo:$_prefix" in
            /*:*|*://*:*) ;;
            *:*) case "$_prefix" in
                *[/\\]*|"$_repo") _repo="$(pwd -P)/$_repo" ;;
            esac ;;
        esac
        git init --quiet -- "$_scratch/source" || exit $?
        git -C "$_scratch/source" fetch --quiet --depth 1 -- "$_repo" "$_ref" || exit $?
        git -C "$_scratch/source" -c core.hooksPath= checkout --quiet --detach FETCH_HEAD || exit $?
        _bootstrap="$_scratch/source/worktree-manager/bootstrap.sh"
        if [ ! -f "$_bootstrap" ] || [ -L "$_bootstrap" ] || [ -L "$_scratch/source/worktree-manager" ]; then
            printf '%s\n' '[agent-worktrees] configured Git source has no ordinary Manager bootstrap.sh.' >&2
            exit 126
        fi
    fi
    if [ "$_has_project" = 1 ]; then
        bash "$_bootstrap" --bootstrap-update update --project "$_project" "$@"
    else
        bash "$_bootstrap" --bootstrap-update update "$@"
    fi
    exit $?
)
# Return 0 only for calls not handled here; handled updates terminate the caller.
_aw_update_diagnostic=0
case "${1-}:${3-}" in
    update:*|--project:update|-p:update|--project=*:*)
        _aw_is_update=0
        case "${1-}" in update) _aw_is_update=1 ;; --project=*) [ "${2-}" != update ] || _aw_is_update=1 ;; *) [ "${3-}" != update ] || _aw_is_update=1 ;; esac
        if [ "$_aw_is_update" = 1 ]; then
            _aw_bypass=0
            for _aw_arg do
                case "$_aw_arg" in
                    --phase|--phase=*|--plugin|--plugin=*|--inventory|--target|--target=*|--runtime-backend|--runtime-backend=*)
                        _aw_bypass=1
                        _aw_update_diagnostic=1 ;;
                    --no-manager)
                        case "$(uname -s 2>/dev/null)" in
                            MINGW*|MSYS*|CYGWIN*) ;;
                            *) _aw_bypass=1 ;;
                        esac ;;
                    --auth-report|--help|-h|--request-status|--request-status=*|--recovery-status|--recover-after-restart|--recover-for-repair)
                        _aw_bypass=1 ;;
                esac
            done
            if [ "$_aw_bypass" = 0 ]; then
                _aw_update_entry "$@"
                exit $?
            fi
        fi
        ;;
esac
