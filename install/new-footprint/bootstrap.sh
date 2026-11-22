#!/usr/bin/env bash
set -euo pipefail

root=$HOME/.copilot-extensions
srcroot=$HOME/src
repo=${HARNESS_REPO:-}
knowledge=${KNOWLEDGE_REPO:-}
revision=main
flags=()
while (($#)); do
    case "$1" in
        --root) root=${2:?missing root}; shift 2 ;;
        --srcroot) srcroot=${2:?missing srcroot}; shift 2 ;;
        --repo) repo=${2:?missing repo}; shift 2 ;;
        --knowledge-repo) knowledge=${2:?missing knowledge repo}; shift 2 ;;
        --bootstrap-revision) revision=${2:?missing bootstrap revision}; shift 2 ;;
        --stage|--clear-trial) flags+=("$1"); shift ;;
        *) printf 'Unknown bootstrap option: %s\n' "$1" >&2; exit 2 ;;
    esac
done
[[ $revision == main || $revision =~ ^[0-9a-f]{40}$ ]] || exit 2
args=(--root "$root" --srcroot "$srcroot")
[[ -z $repo ]] || args+=(--repo "$repo")
[[ -z $knowledge ]] || args+=(--knowledge-repo "$knowledge")
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
