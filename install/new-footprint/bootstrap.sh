#!/usr/bin/env bash
set -euo pipefail
unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE GIT_OBJECT_DIRECTORY \
    GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_PREFIX GIT_CEILING_DIRECTORIES \
    GIT_DISCOVERY_ACROSS_FILESYSTEM GIT_SHALLOW_FILE GIT_GRAFT_FILE \
    GIT_REPLACE_REF_BASE GIT_CONFIG GIT_NAMESPACE GIT_QUARANTINE_PATH

root=$HOME/.copilot-extensions
srcroot=$HOME/src
repo=${HARNESS_REPO:-}
knowledge=${KNOWLEDGE_REPO:-}
revision=main
flags=()
cleanup=false
while (($#)); do
    case "$1" in
        --root) root=${2:?missing root}; shift 2 ;;
        --srcroot) srcroot=${2:?missing srcroot}; shift 2 ;;
        --repo) repo=${2:?missing repo}; shift 2 ;;
        --knowledge-repo) knowledge=${2:?missing knowledge repo}; shift 2 ;;
        --bootstrap-revision) revision=${2:?missing bootstrap revision}; shift 2 ;;
        --stage) flags+=("$1"); shift ;;
        --clear-trial) flags+=("$1"); cleanup=true; shift ;;
        *) printf 'Unknown bootstrap option: %s\n' "$1" >&2; exit 2 ;;
    esac
done
[[ $revision == main || $revision =~ ^[0-9a-f]{40}$ ]] || exit 2
args=(--root "$root" --srcroot "$srcroot")
[[ -z $repo ]] || args+=(--repo "$repo")
[[ -z $knowledge ]] || args+=(--knowledge-repo "$knowledge")
if ! "$cleanup"; then
    if [[ ! -t 1 || -n ${SSH_CONNECTION:-}${SSH_CLIENT:-}${SSH_TTY:-}${COPILOT_AGENT_SESSION_ID:-} ]]; then
        export GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never
        if [[ -n ${GH_TOKEN:-}${GITHUB_TOKEN:-} ]]; then
            export GIT_CONFIG_PARAMETERS="${GIT_CONFIG_PARAMETERS:-} 'credential.https://github.com.helper=' 'credential.https://github.com.helper=!gh auth git-credential'"
        fi
    fi
    for selected in "$repo" "$knowledge"; do
        [[ -z $selected || -d $selected ]] && continue
        if [[ $selected =~ ^[[:alnum:]][[:alnum:]._-]*/[[:alnum:]][[:alnum:]._-]*$ ]]; then
            url="https://github.com/$selected.git"
        elif [[ $selected =~ ^https://(github\.com/[^/?#]+/[^/?#]+|dev\.azure\.com/[^/?#]+/[^/?#]+/_git/[^/?#]+|[a-z0-9-]+\.visualstudio\.com/[^/?#]+/_git/[^/?#]+)$ ]]; then
            url=$selected
        else
            printf 'Unsupported remote repository locator\n' >&2; exit 2
        fi
        git ls-remote --exit-code "$url" HEAD >/dev/null 2>&1 || {
            printf 'Git could not read a selected repository\n' >&2; exit 1
        }
    done
fi
if [[ -n ${BASH_SOURCE[0]:-} && -f ${BASH_SOURCE[0]} ]]; then
    driver="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/driver.py"
else
    kit=$(mktemp -d)
    trap 'rm -rf -- "$kit"' EXIT
    git -c core.hooksPath=/dev/null init -q "$kit"
    git -C "$kit" -c core.hooksPath=/dev/null fetch --quiet --depth 1 --no-tags \
        https://github.com/ThomasMichon/copilot-extensions.git "$revision"
    git -C "$kit" -c core.hooksPath=/dev/null checkout --quiet --detach FETCH_HEAD
    driver=$kit/install/new-footprint/driver.py
fi
uv run --no-project --python 3.14 --managed-python python -I -B "$driver" "${args[@]}" ${flags[@]+"${flags[@]}"}
