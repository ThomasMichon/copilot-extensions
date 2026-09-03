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
#                                  [--wait 40] [--out FILE] [--project NAME]
#                                  [--live] [--keep]
set -euo pipefail

pivot="CodeSpaces"; fmt="text"; wait_s="40"; out=""; project=""; live=""; keep=""; interactive=""
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
if [ -z "$project" ]; then
  echo "--project NAME is required so the preview targets the intended checkout." >&2
  exit 2
fi

# scripts/preview-picker.sh -> worktree-manager/scripts -> repo root is 2 up.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
manager="$repo_root/worktree-manager"
aw_plugin="$repo_root/plugins/agent-worktrees"

resolve_venv() {
  local plugin="$1" required="${2:-1}" scripts="$1/.venv/bin"
  if [ ! -x "$scripts/python" ]; then
    if [ "$required" = "0" ]; then
      echo "No worktree venv for '$plugin'; skipping its pivot manifests." >&2
      return 0
    fi
    echo "No worktree venv for '$plugin'. Create it once:" >&2
    echo "    cd '$plugin' && uv venv .venv && uv pip install --python .venv/bin/python -e '.[dev]'" >&2
    exit 1
  fi
  echo "$scripts"
}

manager_scripts="$(resolve_venv "$manager")"
aw_scripts="$(resolve_venv "$aw_plugin")"
preview_path="$manager_scripts:$aw_scripts"
engine_argv="$("$manager_scripts/python" -c \
  'import json,sys; print(json.dumps([sys.argv[1], "-m", "agent_worktrees"]))' \
  "$aw_scripts/python")"

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/agent-picker-preview-XXXXXXXX")"
pivots_dir="$sandbox/.agent-worktrees/pivots"
plugins_dir="$sandbox/plugins"
mkdir -p "$pivots_dir"
mkdir -p "$plugins_dir"

seeded=0
seeded_labels=$'Worktrees\n'
for m in "$repo_root"/plugins/*/pivots/*.json; do
  [ -e "$m" ] || continue
  plugin="$(cd "$(dirname "$m")/.." && pwd)"
  scripts="$(resolve_venv "$plugin" 0)"
  [ -n "$scripts" ] || continue
  case ":$preview_path:" in
    *":$scripts:"*) ;;
    *) preview_path="$preview_path:$scripts";;
  esac
  cp -f "$m" "$pivots_dir/"; seeded=$((seeded + 1))
  label="$("$manager_scripts/python" -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["label"])' \
    "$m")"
  seeded_labels+="$label"$'\n'
done
if [ -z "$interactive" ] && ! printf '%s' "$seeded_labels" | grep -Fxiq "$pivot"; then
  echo "Pivot '$pivot' is not available from a built worktree provider." >&2
  echo "Build that plugin's .venv first." >&2
  exit 1
fi

echo "Sandbox AGENT_HOME : $sandbox"
echo "Worktree binaries  : $preview_path"
echo "Seeded pivots      : $seeded manifest(s)"
echo "Capturing pivot    : $pivot  (format=$fmt, wait=${wait_s}s)"
echo

cleanup() { [ -n "$keep" ] && echo "Sandbox kept at: $sandbox" || rm -rf "$sandbox"; }
trap cleanup EXIT

export AGENT_HOME="$sandbox"
export AGENT_WORKTREES_PLUGINS_DIR="$plugins_dir"
export WORKTREE_MANAGER_PICKER_NO_PIVOT_MATERIALIZE=1
export WORKTREE_MANAGER_AGENT_WORKTREES_SRC="$aw_plugin/src"
export WORKTREE_MANAGER_ENGINE_ARGV="$engine_argv"
export PATH="$preview_path:$PATH"

if [ -n "$interactive" ]; then
  # Launch the DRAFT picker interactively (Textual TUI) INLINE in this terminal
  # (`picker mock` -- real data, mutating actions simulated). Keep the sandbox.
  keep="1"
  echo "Draft picker (mock) -- arrow to CODESPACES; q/Ctrl+C to exit. Sandbox: $sandbox"
  args=(-m worktree_manager picker mock)
  [ -n "$project" ] && args+=("$project")
  args+=(--local)
  exec "$manager_scripts/python" "${args[@]}"
fi

args=(-m worktree_manager picker screenshot)
[ -n "$project" ] && args+=("$project")
args+=(--pivot "$pivot" --wait "$wait_s" --format "$fmt")
[ -n "$live" ] && args+=("$live")
[ -n "$out" ] && args+=(--out "$out")

"$manager_scripts/python" "${args[@]}"
