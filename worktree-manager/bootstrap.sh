#!/usr/bin/env bash
# bootstrap.sh — fetch, version-install, and launch the copilot-extensions Worktree Manager.
#
#   curl -fsSL https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/worktree-manager/bootstrap.sh | bash
#
# This is delivered OUTSIDE the plugin pipe: it does not require any Copilot
# plugin to be installed, and does not go through `copilot plugin`. It fetches
# the Worktree Manager payload, installs it under the SAME versioning convention as
# the harness's other installers — an immutable ~/.worktree-manager/versions/<ver>
# slot, a plain-text ~/.worktree-manager/current-version marker, and a
# ~/.local/bin/worktree-manager binstub — then launches it. Re-running is
# version-gated (a no-op when already current).
#
# Prerequisites are auto-provisioned: uv is installed user-local (no admin) when
# missing; git is installed best-effort where a package manager exists, otherwise
# the payload is fetched as a GitHub tarball so a bare machine still bootstraps.
# The git source (repo + ref) is taken from the user-level source config
# ($ROOT/config.toml [source]) when present — set it with `worktree-manager
# source set` to track a fork / canary branch — otherwise the canonical defaults
# below. Relocate the install root with $WORKTREE_MANAGER_ROOT.

set -euo pipefail

if [ "$#" -eq 1 ] && [ "${1-}" = --describe-source-inputs ]; then
    printf '%s\n' '{"schema":"copilot-extensions.manager-source-inputs","version":1,"capabilities":["install-source"]}'
    exit 0
fi

if [ "$#" -eq 1 ] && [ "${1-}" = --describe-toolchain-config ]; then
    printf '%s\n' '{"schema":"copilot-extensions.toolchain-config","version":1,"capabilities":["native-file","python-index","python-install-mirror","uv-install-mirror","npm-registry","request-context-v1","bootstrap-toolchain-only"]}'
    exit 0
fi

toolchain_config="${COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG:-}"
if [ "${1-}" = --toolchain-config ]; then
    if [ "$#" -lt 2 ] || [ -z "${2-}" ]; then
        printf '%s\n' '--toolchain-config requires an absolute file.' >&2; exit 2
    fi
    if [ -n "$toolchain_config" ] && [ "$toolchain_config" != "$2" ]; then
        printf '%s\n' 'Explicit toolchain configuration conflicts with the environment selection.' >&2
        exit 2
    fi
    toolchain_config="$2"
    shift 2
fi
for argument in "$@"; do
    if [ "$argument" = --toolchain-config ]; then
        printf '%s\n' '--toolchain-config must occur once, before other arguments.' >&2; exit 2
    fi
done
if [ -z "$toolchain_config" ]; then
    for candidate in "$(pwd -P)/.copilot-extensions/toolchain.conf" \
        "${WORKTREE_MANAGER_ROOT:-$HOME/.worktree-manager}/toolchain.conf"; do
        if [ -e "$candidate" ]; then toolchain_config="$candidate"; break; fi
    done
fi
if [ -n "$toolchain_config" ]; then
    case "$toolchain_config" in /*) ;; *) printf '%s\n' 'Toolchain configuration must be absolute.' >&2; exit 2 ;; esac
    if [ ! -f "$toolchain_config" ] || [ -L "$toolchain_config" ] ||
        [ "$(wc -c < "$toolchain_config")" -gt 16384 ]; then
        printf '%s\n' 'Toolchain configuration must be a small ordinary file.' >&2
        exit 2
    fi
    toolchain_values="$(LC_ALL=C awk '
        { if ($0 ~ /[^ -~\t\r]/) exit 1; sub(/\r$/, "") }
        /^[[:space:]]*(#.*)?$/ { next }
        {
            equal = index($0, "=")
            key = substr($0, 1, equal - 1); value = substr($0, equal + 1)
            if (!equal || seen[key]++ || key !~ /^(schema|python_index_url|python_install_mirror|uv_install_mirror|npm_registry)$/) exit 1
            if (key == "schema") { if (value != "1") exit 1 }
            else {
                if (length(value) > 2048 || value !~ /^https:\/\/[A-Za-z0-9.-]+(:[0-9]+)?(\/[A-Za-z0-9._~:\/+-]*)?$/) exit 1
                count++
            }
            print key "=" value
        }
        END { if (!seen["schema"] || !count) exit 1 }
    ' "$toolchain_config")" || { printf '%s\n' 'Malformed or unsupported toolchain configuration.' >&2; exit 2; }
    tc_index="" tc_python="" tc_uv="" tc_npm=""
    while IFS='=' read -r key value; do
        case "$key" in
            python_index_url) tc_index="$value" ;;
            python_install_mirror) tc_python="$value" ;;
            uv_install_mirror) tc_uv="$value" ;;
            npm_registry) tc_npm="$value" ;;
        esac
    done <<< "$toolchain_values"
    for mapping in "UV_DEFAULT_INDEX=$tc_index" "UV_INDEX_URL=$tc_index" "PIP_INDEX_URL=$tc_index" \
        "UV_PYTHON_INSTALL_MIRROR=$tc_python" "UV_INSTALLER_GITHUB_BASE_URL=$tc_uv" \
        "npm_config_registry=$tc_npm" "NPM_CONFIG_REGISTRY=$tc_npm"; do
        name="${mapping%%=*}" expected="${mapping#*=}"
        [ -n "$expected" ] || continue
        current="${!name:-}"
        if [ -n "$current" ] && [ "${current%/}" != "${expected%/}" ]; then
            printf '%s conflicts with toolchain configuration.\n' "$name" >&2
            exit 2
        fi
    done
    if [ -n "$tc_index" ]; then
        if [ "${PIP_CONFIG_FILE:-}" = /dev/null ]; then
            printf '%s\n' 'PIP_CONFIG_FILE disables native configuration.' >&2; exit 2
        fi
        for name in UV_INDEX UV_EXTRA_INDEX_URL UV_FIND_LINKS UV_INSECURE_HOST \
            UV_NO_INDEX PIP_EXTRA_INDEX_URL PIP_FIND_LINKS PIP_TRUSTED_HOST PIP_NO_INDEX; do
            if [ -n "${!name:-}" ]; then
                printf '%s requires explicit source reconciliation.\n' "$name" >&2
                exit 2
            fi
        done
    fi
    if [ -n "$tc_index$tc_python" ]; then
        for name in UV_CONFIG_FILE UV_NO_CONFIG; do
            if [ -n "${!name:-}" ]; then
                printf '%s requires explicit source reconciliation.\n' "$name" >&2; exit 2
            fi
        done
    fi
    if [ -n "$tc_uv" ]; then
        for name in UV_DOWNLOAD_URL INSTALLER_DOWNLOAD_URL UV_INSTALLER_GHE_BASE_URL; do
            if [ -n "${!name:-}" ]; then
                printf '%s requires explicit source reconciliation.\n' "$name" >&2
                exit 2
            fi
        done
    fi
    if [ -n "$tc_npm" ]; then
        for suffix in USERCONFIG GLOBALCONFIG; do
            upper="NPM_CONFIG_$suffix"
            case "$suffix" in USERCONFIG) lower=npm_config_userconfig ;; *) lower=npm_config_globalconfig ;; esac
            if [ -n "${!upper:-}" ] && [ -n "${!lower:-}" ] && [ "${!upper}" != "${!lower}" ]; then
                printf '%s\n' 'npm configuration-file selections conflict.' >&2; exit 2
            fi
        done
    fi
    tc_paths=("") tc_kinds=(none)
    if [ -n "$tc_index" ]; then
        if [ "$(uname -s)" = Darwin ]; then
            tc_paths+=("/Library/Application Support/pip/pip.conf"); tc_kinds+=(pip)
            pip_directory="$HOME/Library/Application Support/pip"
            [ -d "$pip_directory" ] || pip_directory="$HOME/.config/pip"
        else
            tc_paths+=(/etc/pip.conf); tc_kinds+=(pip)
            pip_directory="${XDG_CONFIG_HOME:-$HOME/.config}/pip"
            IFS=: read -r -a tc_dirs <<< "${XDG_CONFIG_DIRS:-/etc/xdg}"
            for directory in "${tc_dirs[@]}"; do tc_paths+=("$directory/pip/pip.conf"); tc_kinds+=(pip); done
        fi
        if [ -z "${PIP_CONFIG_FILE:-}" ]; then
            tc_paths+=("$HOME/.pip/pip.conf" "$pip_directory/pip.conf"); tc_kinds+=(pip pip)
        fi
        if [ -n "${VIRTUAL_ENV:-}" ]; then tc_paths+=("$VIRTUAL_ENV/pip.conf"); tc_kinds+=(pip); fi
        if [ -n "${PIP_CONFIG_FILE:-}" ]; then
            [ -f "$PIP_CONFIG_FILE" ] || [ "$PIP_CONFIG_FILE" = /dev/null ] || {
                printf '%s\n' 'Explicit pip configuration is unavailable.' >&2; exit 2;
            }
            if [ "$PIP_CONFIG_FILE" != /dev/null ]; then tc_paths+=("$PIP_CONFIG_FILE"); tc_kinds+=(pip); fi
        fi
    fi
    if [ -n "$tc_index$tc_python" ]; then
        tc_paths+=("${XDG_CONFIG_HOME:-$HOME/.config}/uv/uv.toml"); tc_kinds+=(uv)
        IFS=: read -r -a tc_system_dirs <<< "${XDG_CONFIG_DIRS:-/etc/xdg}"
        tc_system_dirs+=(/etc)
        for directory in "${tc_system_dirs[@]}"; do
            if [ -n "$directory" ] && [ -e "$directory/uv/uv.toml" ]; then
                tc_paths+=("$directory/uv/uv.toml"); tc_kinds+=(uv)
                break
            fi
        done
    fi
    if [ -n "$tc_npm" ]; then
        tc_paths+=("${NPM_CONFIG_USERCONFIG:-${npm_config_userconfig:-$HOME/.npmrc}}"); tc_kinds+=(npm)
        if [ -n "${NPM_CONFIG_GLOBALCONFIG:-${npm_config_globalconfig:-}}" ]; then
            tc_paths+=("${NPM_CONFIG_GLOBALCONFIG:-$npm_config_globalconfig}"); tc_kinds+=(npm)
        fi
    fi
    for ((i=0; i<${#tc_paths[@]}; i++)); do
        path="${tc_paths[$i]}"
        [ -n "$path" ] || continue
        [ -e "$path" ] || continue
        if [ ! -f "$path" ] || [ -L "$path" ] || [ "$(wc -c < "$path")" -gt 1048576 ]; then
            printf '%s\n' 'Native tool configuration is not a bounded ordinary file.' >&2; exit 2
        fi
        LC_ALL=C awk -v kind="${tc_kinds[$i]}" -v idx="$tc_index" -v mirror="$tc_python" -v registry="$tc_npm" '
            function same(a,b) { sub(/\/$/, "", a); sub(/\/$/, "", b); return a == b }
            function value(line) {
                if (kind == "pip") sub(/^[^=:]*[:=][[:space:]]*/, "", line)
                else sub(/^[^=]*=[[:space:]]*/, "", line)
                sub(/[[:space:]]*$/, "", line)
                return line
            }
            /^[[:space:]]*([#;].*)?$/ { next }
            {
                line=$0; sub(/\r$/, "", line)
                if (kind == "pip") {
                    if (tolower(line) ~ /^[[:space:]]*(extra[-_]index[-_]url|find[-_]links|trusted[-_]host|no[-_]index)[[:space:]]*[:=]/) exit 1
                    if (tolower(line) ~ /^[[:space:]]*index[-_]url[[:space:]]*[:=]/ && !same(value(line), idx)) exit 1
                } else if (kind == "uv") {
                    if (idx == "" && line !~ /^[[:space:]]*["\047]?python-install-mirror["\047]?[[:space:]]*[=.]/) next
                    if (mirror == "" && line ~ /^[[:space:]]*["\047]?python-install-mirror["\047]?[[:space:]]*[=.]/) next
                    if (line ~ /^[[:space:]]*["\047]?pip["\047]?[[:space:]]*[=.]/ && line ~ /index|find-links|insecure-host/) exit 1
                    if (line ~ /^[[:space:]]*["\047]?explicit["\047]?[[:space:]]*[=.]/ &&
                        line !~ /^[[:space:]]*["\047]?explicit["\047]?[[:space:]]*=[[:space:]]*false[[:space:]]*(#.*)?$/) exit 1
                    if (line ~ /^[[:space:]]*["\047]?(extra-index-url|find-links|no-index|no-default-index|allow-insecure-host|index)["\047]?[[:space:]]*=/) exit 1
                    if (line ~ /^[[:space:]]*\[\[[[:space:]]*["\047]?index["\047]?[[:space:]]*\]\][[:space:]]*(#.*)?$/) indexes++
                    if (line ~ /^[[:space:]]*["\047]?default["\047]?[[:space:]]*=[[:space:]]*true[[:space:]]*(#.*)?$/) defaults++
                    if (line ~ /^[[:space:]]*["\047]?(url|index-url|python-install-mirror)["\047]?[[:space:]]*=/) {
                        field=line; sub(/[[:space:]]*=.*/, "", field); sub(/^[[:space:]]*/, "", field)
                        gsub(/^["\047]|["\047]$/, "", field)
                        v=value(line); quote=substr(v,1,1)
                        if (quote != "\"" && quote != sprintf("%c",39)) exit 1
                        v=substr(v,2); end=index(v,quote); if (!end) exit 1
                        tail=substr(v,end+1); if (tail !~ /^[[:space:]]*(#.*)?$/) exit 1
                        v=substr(v,1,end-1); expected=(field == "python-install-mirror" ? mirror : idx)
                        if (expected != "" && !same(v,expected)) exit 1
                    } else if (line ~ /^[[:space:]]*["\047]?(url|index|index-url|python-install-mirror)["\047]?[[:space:]]*[=.]/) exit 1
                } else {
                    if (line ~ /^[[:space:]]*[^=]+:registry[[:space:]]*=/) exit 1
                    if (tolower(line) ~ /^[[:space:]]*registry[[:space:]]*=/ && !same(value(line),registry)) exit 1
                }
            }
            END { if (kind == "uv" && idx != "" && (indexes > 1 || indexes != defaults)) exit 1 }
        ' "$path" || { printf '%s\n' 'Native source configuration requires reconciliation.' >&2; exit 2; }
    done
    [ -z "$tc_index" ] || export UV_DEFAULT_INDEX="$tc_index" PIP_INDEX_URL="$tc_index"
    [ -z "$tc_python" ] || export UV_PYTHON_INSTALL_MIRROR="$tc_python"
    [ -z "$tc_uv" ] || export UV_INSTALLER_GITHUB_BASE_URL="$tc_uv"
    [ -z "$tc_npm" ] || export npm_config_registry="$tc_npm"
    for name in PIP_CONFIG_FILE NPM_CONFIG_USERCONFIG npm_config_userconfig NPM_CONFIG_GLOBALCONFIG npm_config_globalconfig; do
        current="${!name:-}"
        case "$current" in
            ""|/*) ;;
            *) export "$name=$(pwd -P)/$current" ;;
        esac
    done
    export COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG="$toolchain_config"
fi

source_anchor="${WORKTREE_MANAGER_SOURCE_ANCHOR:-$(pwd -P)}"
case "$source_anchor" in
    /*) ;;
    *) printf '%s\n' 'Update request source anchor must be absolute.' >&2; exit 2 ;;
esac

_bootstrap_progress() {
    local message="$1" state="${2:-running}" stamp line
    stamp="$(date -u '+%H:%M:%SZ' 2>/dev/null)" || stamp="time unavailable"
    while IFS= read -r line; do
        printf '[%s] update/bootstrap %s %s [elapsed unavailable]\n' "$stamp" "$state" "$line" >&2
    done <<< "$message"
}

_bootstrap_uv_supported() {
    local version
    version="$("$1" --version)" || return 1
    if [[ ! "$version" =~ ^uv[[:space:]]+([0-9]+)\.([0-9]+)\.([0-9]+) ]]; then
        return 1
    fi
    [ "${BASH_REMATCH[1]}" -gt 0 ] || [ "${BASH_REMATCH[2]}" -ge 8 ]
}

_bootstrap_find_uv() {
    local candidate
    while IFS= read -r candidate; do
        if _bootstrap_uv_supported "$candidate"; then
            case "$candidate" in
                /*) ;;
                *) candidate="$(pwd -P)/$candidate" ;;
            esac
            printf '%s\n' "$candidate"
            return 0
        fi
    done < <(type -ap uv)
    return 1
}

if [ "${1-}" = --system-python ]; then
    _bootstrap_progress '--system-python is a Windows WinGet bootstrap mode; unsupported on POSIX.' failed
    exit 2
fi

case "${BOOTSTRAP_KILLSWITCH_STATE_FILE:-}" in
    ""|/*|"~"*) ;;
    *)
        BOOTSTRAP_KILLSWITCH_STATE_FILE="$(pwd -P)/$BOOTSTRAP_KILLSWITCH_STATE_FILE"
        export BOOTSTRAP_KILLSWITCH_STATE_FILE
        ;;
esac

ROOT="${WORKTREE_MANAGER_ROOT:-$HOME/.worktree-manager}"
STAGING="$ROOT/staging"
CONFIG="$ROOT/config.toml"

# Source (repo + ref): user-level config file [source] overrides, else defaults.
REPO='https://github.com/ThomasMichon/copilot-extensions.git'
REF='main'
explicit_source=0
if [ "$#" -ge 4 ] && [ "${1-}" = --repair-update ] && [ "${4-}" = update ]; then
    explicit_source=1
fi
if [ "${1-}" = --install-source ]; then explicit_source=1; fi
if [ -f "$CONFIG" ] && [ "$explicit_source" -eq 0 ]; then
    source_config="$(awk '
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
            if ($0 ~ /^[[:space:]]*\[\[?[[:space:]]*(source|["\047]source["\047])([^A-Za-z0-9_-]|$)/ && !insrc) exit 1
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
        ' "$CONFIG")" || {
            _bootstrap_progress 'source config cannot be safely read by the shell bootstrap.' failed
            exit 126
        }
    while IFS= read -r line; do
        case "$line" in repo=*) REPO="${line#*=}" ;; ref=*) REF="${line#*=}" ;; esac
    done <<< "$source_config"
fi

installer_args=()
recovery_only=0
install_source=""
if [ "${1-}" = --install-source ]; then
    [ "$#" -ge 2 ] || { _bootstrap_progress '--install-source requires an absolute snapshot.' failed; exit 2; }
    case "$2" in /*) ;; *) _bootstrap_progress '--install-source requires an absolute snapshot.' failed; exit 2 ;; esac
    [ -d "$2" ] && [ ! -L "$2" ] || { _bootstrap_progress 'install-source must be an ordinary directory.' failed; exit 2; }
    install_source="$2"
    installer_args+=(--install-source "$install_source")
    shift 2
    case "${1-}" in
        --install-source|--recover-for-repair|--repair-update|--bootstrap-update|--system-python)
            _bootstrap_progress 'local install-source cannot be combined with another source or repair entry.' failed
            exit 2 ;;
    esac
fi
if [ "${1-}" = --recover-for-repair ]; then
    recovery_only=1
    installer_args+=(--recover-for-repair)
    shift
    if [ "${1-}" = --json ]; then
        installer_args+=(--json)
        shift
    fi
    [ "$#" -eq 0 ] || { _bootstrap_progress 'repair recovery accepts only --json.' failed; exit 2; }
fi
if [ "${1-}" = --bootstrap-update ]; then
    shift
    [ "${1-}" = update ] || { _bootstrap_progress 'bootstrap update only accepts update.' failed; exit 2; }
    installer_args+=(--repair-update)
elif [ "${1-}" = --repair-update ]; then
    [ "$#" -ge 4 ] || { _bootstrap_progress 'incomplete update bootstrap source.' failed; exit 2; }
    REPO="$2"
    REF="$3"
    shift 3
    [ "${1-}" = update ] || { _bootstrap_progress 'update bootstrap only accepts update.' failed; exit 2; }
    installer_args+=(--repair-update)
fi
case "$REPO" in ""|-*|*[[:cntrl:]]*) _bootstrap_progress 'invalid configured Git source.' failed; exit 126 ;; esac
case "$REF" in ""|-*|*[[:cntrl:]]*) _bootstrap_progress 'invalid configured Git ref.' failed; exit 126 ;; esac
# Anchor local runtime sources before staging changes Git's working directory.
source_prefix="${REPO%%:*}"
case "$REPO:$source_prefix" in
    /*:*|*://*:*) ;;
    *:*) case "$source_prefix" in
        *[/\\]*|"$REPO") REPO="$source_anchor/$REPO" ;;
    esac ;;
esac

toolchain_only=0
if [ "${1-}" = setup ]; then
    for argument in "$@"; do
        if [ "$argument" = --toolchain-only ]; then toolchain_only=1; fi
    done
fi
update_staging=""
if [ "$recovery_only" -eq 1 ]; then
    recovery_staging="$(mktemp -d -t manager-repair.XXXXXXXX)"
    STAGING="$recovery_staging/source"
    trap 'rm -rf -- "$recovery_staging"' EXIT
elif [ "${1-}" = update ] || [ "$toolchain_only" -eq 1 ]; then
    mkdir -p "$ROOT/bootstrap-acquisition"
    acquisition_kind=update
    if [ "$toolchain_only" -eq 1 ]; then acquisition_kind=toolchain; fi
    update_staging="$(mktemp -d "$ROOT/bootstrap-acquisition/$acquisition_kind.XXXXXXXX")"
    STAGING="$update_staging/source"
    trap 'status=$?; if [ "$status" -eq 0 ]; then rm -rf -- "$update_staging"; fi' EXIT
fi

_bootstrap_progress 'Worktree Manager bootstrap'

# -- Prerequisites (auto-provision; restart-aware; user-local first) ----------
# The one-liner must take a *bare* machine into the Manager (vision installer
# one-line-bootstrap). uv is auto-installed (user-local, no admin). git is
# installed best-effort where a package manager exists, else we fetch the payload
# as a GitHub tarball so the bootstrap never dead-ends without git.

LOCAL_BIN="$HOME/.local/bin"

if ! UV="$(_bootstrap_find_uv)"; then
    _bootstrap_progress 'uv 0.8.0+ required - preparing it (user-local, no admin)...'
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # uv installs into ~/.local/bin (or its env file); amend THIS session's PATH
    # so we can continue without a restart (the installer persists it for future
    # shells).
    [ -f "$LOCAL_BIN/env" ] && . "$LOCAL_BIN/env" || true
    export PATH="$LOCAL_BIN:$PATH"
    if ! UV="$(_bootstrap_find_uv)"; then
        _bootstrap_progress 'uv 0.8.0 or newer is required after prerequisite preparation.' failed
        exit 1
    fi
fi

have_git=0
command -v git >/dev/null 2>&1 && have_git=1
if [ "$have_git" -eq 0 ] && [ -z "$install_source" ]; then
    os="$(uname -s)"
    if [ "$os" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
        _bootstrap_progress 'git not found - installing via Homebrew...'
        brew install git || true
    elif [ "$os" = "Linux" ] && [ "$(id -u)" = "0" ]; then
        _bootstrap_progress 'git not found - installing via the system package manager...'
        if command -v apt-get >/dev/null 2>&1; then apt-get update -qq && apt-get install -y git || true
        elif command -v dnf >/dev/null 2>&1; then dnf install -y git || true
        elif command -v pacman >/dev/null 2>&1; then pacman -Sy --noconfirm git || true
        fi
    fi
    command -v git >/dev/null 2>&1 && have_git=1
    [ "$have_git" -eq 0 ] && _bootstrap_progress 'git unavailable - will fetch the payload as a tarball (no git).'
fi

# -- Fetch the payload (git clone/fetch when available, else GitHub tarball) ---
controller_dir=""
if [ -n "$install_source" ] && [ -n "${BASH_SOURCE[0]:-}" ]; then
    controller_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
fi
if [ -n "$controller_dir" ] && [ -f "$controller_dir/scripts/runtime_install.py" ]; then
    payload_parent="$(dirname -- "$controller_dir")"
elif [ "$have_git" -eq 1 ]; then
    mkdir -p "$STAGING"
    if [ ! -d "$STAGING/.git" ]; then
        rm -rf "$STAGING"; mkdir -p "$STAGING"
        git init --quiet -- "$STAGING"
    fi
    git -C "$STAGING" fetch --depth 1 -- "$REPO" "$REF"
    if [ -f "$STAGING/.git/info/sparse-checkout" ]; then
        # Fresh repair needs the engine SDK omitted by routine Manager self-update.
        git -C "$STAGING" -c core.hooksPath= sparse-checkout disable
    fi
    git -C "$STAGING" -c core.hooksPath= checkout -q --detach FETCH_HEAD
    if ! git -C "$STAGING" diff --quiet --no-ext-diff --no-textconv HEAD --; then
        _bootstrap_progress 'Git source checkout is incomplete or changed; refusing Manager construction.' failed
        exit 1
    fi
    payload_parent="$STAGING"
else
    # Derive the codeload tarball URL from the (GitHub) source; a non-GitHub
    # source genuinely needs git. POSIX parameter expansion (no lazy regex).
    norm="${REPO%/}"; norm="${norm%.git}"
    tar_url=""
    case "$norm" in
        *github.com*)
            rest="${norm#*github.com}"; rest="${rest#[:/]}"   # owner/name
            owner="${rest%%/*}"; name="${rest##*/}"
            tar_url="https://codeload.github.com/$owner/$name/tar.gz/$REF"
            ;;
    esac
    if [ -z "$tar_url" ]; then
        _bootstrap_progress "git is required for a non-GitHub source ($REPO). Install git and re-run." failed
        exit 1
    fi
    _bootstrap_progress 'fetching payload tarball (no git)...'
    mkdir -p "$STAGING"
    extract="$STAGING/extract"; rm -rf "$extract"; mkdir -p "$extract"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$tar_url" -o "$STAGING/payload.tar.gz"
    else
        wget -qO "$STAGING/payload.tar.gz" "$tar_url"
    fi
    tar -xzf "$STAGING/payload.tar.gz" -C "$extract"
    payload_parent="$(find "$extract" -mindepth 1 -maxdepth 1 -type d | head -n1)"
    rm -f "$STAGING/payload.tar.gz"
fi

cd "$payload_parent/worktree-manager"
export WORKTREE_MANAGER_SOURCE_ANCHOR="$source_anchor"
# Version-install the fetched payload (idempotent, version-gated): publishes the
# versions/<ver> slot + current-version marker + ~/.local/bin binstub.
if [ "$recovery_only" -eq 0 ]; then
    if ! grep -qx '# copilot-extensions:bootstrap-uv=1' scripts/runtime_install.py; then
        _bootstrap_progress 'Selected Manager source does not support qualified UV handoff. Select a newer source; installation was not started.' failed
        exit 2
    fi
    installer_args+=(--bootstrap-uv "$UV")
fi
if [ -n "$toolchain_config" ] || [ "$toolchain_only" -eq 1 ]; then
    for capability in toolchain-config toolchain-context; do
        if ! grep -qx "# copilot-extensions:$capability=1" scripts/runtime_install.py; then
            _bootstrap_progress 'Selected Manager source does not support toolchain configuration; installation was not started.' failed
            exit 2
        fi
    done
    if [ "$toolchain_only" -eq 1 ] &&
        ! grep -qx '# copilot-extensions:toolchain-bootstrap-only=1' scripts/runtime_install.py; then
        _bootstrap_progress 'Selected Manager source does not support toolchain configuration; installation was not started.' failed
        exit 2
    fi
fi
if ! PYTHON="$("$UV" python find --no-project --system --managed-python --no-python-downloads 3.11 2>/dev/null)"; then
    "$UV" python install --no-bin --no-registry 3.11
    PYTHON="$("$UV" python find --no-project --system --managed-python --no-python-downloads 3.11)"
fi
export COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON="$PYTHON"

case ":$PATH:" in
    *":$LOCAL_BIN:"*) : ;;
    *) _bootstrap_progress "add '$LOCAL_BIN' to your PATH (uv and the worktree-manager binstub live there), then restart your shell." warning ;;
esac

# Then run whatever the caller asked for through the freshly-installed app.
if [ "$recovery_only" -eq 1 ]; then
    "$PYTHON" -I -S -B -X utf8 scripts/runtime_install.py --root "$ROOT" --payload "$PWD" "${installer_args[@]}"
    exit "$?"
fi
if [ -n "$update_staging" ]; then
    "$PYTHON" -I -S -B -X utf8 scripts/runtime_install.py --root "$ROOT" --payload "$PWD" "${installer_args[@]}" -- "$@"
    exit "$?"
fi
if [ "$#" -eq 0 ]; then
    exec "$PYTHON" -I -S -B -X utf8 scripts/runtime_install.py --root "$ROOT" --payload "$PWD" "${installer_args[@]}"
fi
exec "$PYTHON" -I -S -B -X utf8 scripts/runtime_install.py --root "$ROOT" --payload "$PWD" "${installer_args[@]}" -- "$@"
