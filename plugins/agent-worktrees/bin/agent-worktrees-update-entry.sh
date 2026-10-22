#!/bin/sh
# Sourced by both front doors before runtime resolution. Non-update calls return.
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
            MSYS2_ARG_CONV_EXCL='*' exec "$_windows_host" -NoProfile -ExecutionPolicy Bypass \
                -File "$_windows_entry" "$@"
            ;;
    esac
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
            --no-manager|--auth-report|--help|-h|--request-status|--request-status=*|--recovery-status|--recover-after-restart)
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
    # Publication-owned bootstrap defaults, not selected-runtime state.
    _repo='https://github.com/ThomasMichon/copilot-extensions.git'
    _ref='main'
    # Match bootstrap's source-config contract, but refuse unrepresentable values
    # instead of silently fetching canonical code for an explicit source choice.
    if [ -f "$_root/config.toml" ]; then
        _source="$(awk '
            function value(text, quote, out, i, c, e, tail) {
                quote = substr(text, 1, 1)
                if (quote != "\"" && quote != sprintf("%c", 39)) exit 1
                for (i = 2; i <= length(text); i++) {
                    c = substr(text, i, 1)
                    if (c == quote) {
                        tail = substr(text, i + 1)
                        if (tail !~ /^[[:space:]]*(#.*)?$/ || out == "") exit 1
                        return out
                    }
                    if (c ~ /[[:cntrl:]]/) exit 1
                    if (quote == "\"" && c == "\\") {
                        e = substr(text, ++i, 1)
                        if (e != "\\" && e != "\"") exit 1
                        c = e
                    }
                    out = out c
                }
                exit 1
            }
            /^[[:space:]]*\[/ {
                insrc = ($0 ~ /^[[:space:]]*\[source\][[:space:]]*(#.*)?$/)
                if ($0 ~ /source/ && !insrc) exit 1
                if (insrc && source_seen++) exit 1
                next
            }
            !insrc && /^[[:space:]]*["\047]?source["\047]?[[:space:].=]/ { exit 1 }
            insrc && /^[[:space:]]*(repo|ref)[[:space:]]*=/ {
                line=$0
                sub(/^[[:space:]]*/, "", line)
                key=line; sub(/[[:space:]]*=.*/, "", key)
                sub(/^[^=]*=[[:space:]]*/, "", line)
                if (seen[key]++) exit 1
                print key "=" value(line)
                next
            }
            insrc && $0 !~ /^[[:space:]]*(#.*)?$/ { exit 1 }
            ' "$_root/config.toml")" || {
                printf '%s\n' '[agent-worktrees] source config cannot be safely read by the shell bootstrap.' >&2
                exit 126
            }
        while IFS= read -r _line; do
            case "$_line" in repo=*) _repo="${_line#*=}" ;; ref=*) _ref="${_line#*=}" ;; esac
        done <<EOF
$_source
EOF
    fi
    case "$_repo" in ""|-*) printf '%s\n' '[agent-worktrees] invalid configured Git source.' >&2; exit 126 ;; esac
    case "$_ref" in ""|-*) printf '%s\n' '[agent-worktrees] invalid configured Git ref.' >&2; exit 126 ;; esac
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
        bash "$_bootstrap" --repair-update "$_repo" "$_ref" update --project "$_project" "$@"
    else
        bash "$_bootstrap" --repair-update "$_repo" "$_ref" update "$@"
    fi
    exit $?
)
# Return 0 only for calls not handled here; handled updates terminate the caller.
case "${1-}:${3-}" in
    update:*|--project:update|-p:update|--project=*:*)
        _aw_is_update=0
        case "${1-}" in update) _aw_is_update=1 ;; --project=*) [ "${2-}" != update ] || _aw_is_update=1 ;; *) [ "${3-}" != update ] || _aw_is_update=1 ;; esac
        if [ "$_aw_is_update" = 1 ]; then
            _aw_bypass=0
            for _aw_arg do
                case "$_aw_arg" in
                    --no-manager|--auth-report|--help|-h|--request-status|--request-status=*|--recovery-status|--recover-after-restart)
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
