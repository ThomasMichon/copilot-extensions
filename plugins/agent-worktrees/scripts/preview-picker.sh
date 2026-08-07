#!/usr/bin/env bash
# Preview the Worktree Picker (and a chosen pivot/tab) from WORKTREE SOURCE, in
# an isolated sandbox -- without publishing, installing, or touching the active
# deployment. POSIX sibling of preview-picker.ps1 (see it for the full rationale).
#
# It stands up a throwaway state root via the AGENT_HOME override (relocates the
# whole ~/.agent-* tree to a temp dir; the real ~/ -- gh/ssh/git auth -- is left
# alone so live data still resolves), puts the WORKTREE plugin builds on PATH,
# seeds the shipped pivot manifests, and runs
#   agent-worktrees picker screenshot --pivot <tab> --wait
#
# Usage: scripts/preview-picker.sh [--pivot CodeSpaces] [--format text|ansi|svg]
#                                  [--wait 40] [--out FILE] [--project preview]
#                                  [--live] [--keep]
set -euo pipefail

pivot="CodeSpaces"; fmt="text"; wait_s="40"; out=""; project="preview"; live=""; keep=""; interactive=""
while [ $# -gt 0 ]; do
  case "$1" in
    --pivot) pivot="$2"; shift 2;;
    --format) fmt="$2"; shift 2;;
    --wait) wait_s="$2"; shift 2;;
    --out) out="$2"; shift 2;;
    --project) project="$2"; shift 2;;
    --live) live="--live"; shift;;
    --interactive) interactive="1"; shift;;
    --keep) keep="1"; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

# scripts/preview-picker.sh -> plugins/agent-worktrees/scripts -> repo root is 3 up.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../../.." && pwd)"
aw_plugin="$repo_root/plugins/agent-worktrees"
cs_plugin="$repo_root/plugins/agent-codespaces"

resolve_venv() {
  local plugin="$1" scripts="$1/.venv/bin"
  if [ ! -x "$scripts/python" ]; then
    echo "No worktree venv for '$plugin'. Create it once:" >&2
    echo "    cd '$plugin' && uv venv .venv && uv pip install --python .venv/bin/python -e '.[dev]'" >&2
    exit 1
  fi
  echo "$scripts"
}

aw_scripts="$(resolve_venv "$aw_plugin")"
cs_scripts="$(resolve_venv "$cs_plugin")"

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/agent-picker-preview-XXXXXXXX")"
pivots_dir="$sandbox/.agent-worktrees/pivots"
mkdir -p "$pivots_dir"

seeded=0
for m in "$repo_root"/plugins/*/pivots/*.json; do
  [ -e "$m" ] || continue
  cp -f "$m" "$pivots_dir/"; seeded=$((seeded + 1))
done

echo "Sandbox AGENT_HOME : $sandbox"
echo "Worktree binaries  : $aw_scripts ; $cs_scripts"
echo "Seeded pivots      : $seeded manifest(s)"
echo "Capturing pivot    : $pivot  (format=$fmt, wait=${wait_s}s)"
echo

cleanup() { [ -n "$keep" ] && echo "Sandbox kept at: $sandbox" || rm -rf "$sandbox"; }
trap cleanup EXIT

export AGENT_HOME="$sandbox"
export WORKTREE_PROJECT="$project"
export PATH="$aw_scripts:$cs_scripts:$PATH"

if [ -n "$interactive" ]; then
  # Launch the DRAFT picker interactively (Textual TUI) INLINE in this terminal
  # (`picker mock` -- real data, mutating actions simulated). Keep the sandbox.
  keep="1"
  echo "Draft picker (mock) -- arrow to CODESPACES; q/Ctrl+C to exit. Sandbox: $sandbox"
  exec "$aw_scripts/agent-worktrees" picker mock --local
fi

args=(picker screenshot --pivot "$pivot" --wait "$wait_s" --format "$fmt")
[ -n "$live" ] && args+=("$live")
[ -n "$out" ] && args+=(--out "$out")

"$aw_scripts/agent-worktrees" "${args[@]}"
