#!/usr/bin/env bash
# Emit coordinator-first delegation guidance for every enabled session.

set -uo pipefail

plugin_root="${COPILOT_PLUGIN_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P)}"
manifest="$plugin_root/plugin.json"
skill="$plugin_root/skills/delegating-work/SKILL.md"

emit_empty() {
    printf '%s\n' '[delegation-guidance] no guidance context emitted' >&2
    printf '{}'
    exit 0
}

[[ -f "$manifest" && -f "$skill" ]] || emit_empty
version="$(
    sed -n 's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)".*$/\1/p' "$manifest" |
        head -n 1
)" || emit_empty
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-dev[0-9]+)?$ ]] || emit_empty

context="[owner: delegation-guidance@$version]\\nTreat the main agent as coordinator and task-master. Before broad code/file research, comparisons, evaluations, or separable bulk edits, estimate context cost and separability. Delegate early when independent source bodies or tool payloads would materially consume coordinator context; keep small bounded lookups and one genuinely continuous trace direct. Give each delegate one bounded, non-overlapping scope, required evidence, compact cited output, and exclusive edit ownership. Do not duplicate delegated investigation or ingest its full sources without a concrete verification reason. Keep decomposition, synthesis, integration, final decisions, cohesive implementation, and completion judgment with the coordinator. Prefer domain sub-agents for domain MCP/service calls so verbose catalogs and payloads remain in their context; compact shared research and orchestration tools may stay with the coordinator. If you were invoked as a sub-agent, execute only your assigned scope directly and do not create child agents unless your prompt explicitly authorizes it. Run each required independent review role once per unchanged artifact; repeat only after a defect or material change. Use the \`delegating-work\` skill for routing details."

context_bytes="$(printf '%b' "$context" | LC_ALL=C wc -c)" || emit_empty
context_bytes="${context_bytes//[[:space:]]/}"
if [[ ! "$context_bytes" =~ ^[0-9]+$ ]] || (( context_bytes >= 2048 )); then
    emit_empty
fi
printf '{"additionalContext":"%s"}' "$context"
