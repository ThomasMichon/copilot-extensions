#!/usr/bin/env bash
# Emit opt-in worktree-focus guidance for an applicable sessionStart payload.

set -uo pipefail

emit_empty() {
    printf '{}'
    exit 0
}

python="$(command -v python3 || command -v python || true)"
git="$(command -v git || true)"
agent_worktrees="$(command -v agent-worktrees || true)" # marketplace-isolation: allow agent-worktrees-management
[[ -n "$python" && -n "$git" && -n "$agent_worktrees" ]] || emit_empty

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" 2>/dev/null && pwd -P)" ||
    emit_empty
plugin_version="$("$python" - "$script_dir/../plugin.json" 2>/dev/null <<'PY'
import json
import re
import sys

with open(sys.argv[1], "rb") as stream:
    raw = stream.read(4097)
if len(raw) > 4096 or b"\0" in raw:
    raise ValueError
manifest = json.loads(raw.decode("utf-8", errors="strict"))
version = manifest.get("version") if isinstance(manifest, dict) else None
if (
    not isinstance(version, str)
    or len(version) > 64
    or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-dev[0-9]+)?", version) is None
):
    raise ValueError
print(version, end="")
PY
)" || emit_empty
[[ -n "$plugin_version" ]] || emit_empty

kernel="[owner: agent-dispatch@${plugin_version}]
Before starting work likely to overlap another worktree, use the agent-dispatch session command catalog's exact \`argv[0]\` with \`focus --list\`. At the start of substantial operator-led or task-less work, and when its direction changes, advertise it early with that same command plus \`focus \"<one-line subject>\"\`; this is shorthand for writing the same agent-worktrees status-core summary, not a separate store. Agent-worktrees conduct and regular \`agent-worktrees status --summary\` remain authoritative for ongoing disposition, and their normal update cadence still applies."

git_env=(
    GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE
    GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
    GIT_CEILING_DIRECTORIES GIT_DISCOVERY_ACROSS_FILESYSTEM
    GIT_PREFIX GIT_SUPER_PREFIX GIT_QUARANTINE_PATH GIT_NAMESPACE
    GIT_CONFIG GIT_CONFIG_SYSTEM GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM
    GIT_CONFIG_COUNT
)
while IFS= read -r name; do
    [[ "$name" == GIT_CONFIG_KEY_* || "$name" == GIT_CONFIG_VALUE_* ]] &&
        git_env+=("$name")
done < <(compgen -e)

clean_git_env() {
    local args=() name
    for name in "${git_env[@]}"; do
        args+=(-u "$name")
    done
    env "${args[@]}" "$@"
}

cwd="$("$python" -c '
import json, os, sys
try:
    raw = sys.stdin.buffer.read(65537)
    if len(raw) > 65536 or b"\0" in raw:
        raise ValueError
    payload = json.loads(raw.decode("utf-8", errors="strict"))
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        raise ValueError
    if any(ord(ch) < 32 for ch in cwd):
        raise ValueError
    print(cwd, end="")
except Exception:
    pass
' 2>/dev/null)" || emit_empty
[[ -n "$cwd" && -d "$cwd" ]] || emit_empty

root="$(clean_git_env "$git" -C "$cwd" rev-parse --show-toplevel 2>/dev/null)" ||
    emit_empty
[[ -n "$root" && -d "$root" ]] || emit_empty
root="$(cd "$root" 2>/dev/null && pwd -P)" || emit_empty

config="$root/.agent-dispatch/session-guidance.json"
enabled="$("$python" - "$root" "$config" <<'PY'
import json
import os
import stat
import sys

try:
    root = os.path.realpath(sys.argv[1])
    path = os.path.abspath(sys.argv[2])
    if os.path.commonpath((root, path)) != root:
        raise ValueError

    current = os.path.sep
    for component in os.path.relpath(path, os.path.sep).split(os.path.sep):
        current = os.path.join(current, component)
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode):
            raise ValueError

    parent = os.path.realpath(os.path.dirname(path))
    if os.path.commonpath((root, parent)) != root:
        raise ValueError

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            raise ValueError
        raw = os.read(fd, 4097)
        if len(raw) > 4096 or b"\0" in raw:
            raise ValueError
        proc_path = f"/proc/self/fd/{fd}"
        if os.path.exists(proc_path):
            opened = os.path.realpath(proc_path)
            if os.path.commonpath((root, opened)) != root:
                raise ValueError
    finally:
        os.close(fd)

    data = json.loads(raw.decode("utf-8", errors="strict"))
    if (
        not isinstance(data, dict)
        or set(data) != {"session_guidance"}
        or not isinstance(data["session_guidance"], dict)
        or set(data["session_guidance"]) != {"focus"}
        or data["session_guidance"]["focus"] is not True
    ):
        raise ValueError
    print("yes", end="")
except Exception:
    pass
PY
)" || emit_empty
[[ "$enabled" == "yes" ]] || emit_empty

project="$(cd "$cwd" &&
    clean_git_env "$agent_worktrees" get project 2>/dev/null)" || emit_empty
[[ -n "$project" ]] || emit_empty
(cd "$cwd" &&
    clean_git_env "$agent_worktrees" status --help >/dev/null 2>&1) || emit_empty

output="$("$python" - "$kernel" <<'PY'
import json
import sys

print(
    json.dumps(
        {"additionalContext": sys.argv[1]},
        ensure_ascii=False,
        separators=(",", ":"),
    ),
    end="",
)
PY
)" || emit_empty
[[ -n "$output" ]] || emit_empty
printf '%s' "$output"
exit 0
