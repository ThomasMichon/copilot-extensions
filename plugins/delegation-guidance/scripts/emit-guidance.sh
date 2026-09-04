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

context="[owner: delegation-guidance@$version]\\nTreat the main agent as coordinator and task-master. Before broad code/file research, comparisons, evaluations, or separable bulk edits, estimate context cost and separability. Hard rule: when a comparison or evaluation has three or more independent implementation/subsystem tracks and each needs multiple source bodies, launch one bounded evidence agent per track before reading those bodies; a reviewer is not an evidence-track substitute. Delegate other broad work early when independent source bodies or tool payloads would materially consume coordinator context; keep small bounded lookups, small comparisons, and one genuinely continuous trace direct. Give each delegate one bounded, non-overlapping scope, required evidence, compact cited output, and exclusive edit ownership. Do not duplicate delegated investigation or ingest its full sources without a concrete verification reason. Keep decomposition, synthesis, integration, final decisions, cohesive implementation, and completion judgment with the coordinator. Prefer domain sub-agents for domain MCP/service calls so verbose catalogs and payloads remain in their context; compact shared research and orchestration tools may stay with the coordinator. If you were invoked as a sub-agent, execute only your assigned scope directly and do not create child agents unless your prompt explicitly authorizes it. Run each required independent review role once per unchanged artifact; repeat only after a defect or material change. Before delegating, use the \`delegating-work\` skill to load model routing: select a demonstrated model for the purpose and execution surface; candidates require explicit trials."
aggregate_context="[owner: delegation-guidance@$version]\\nHard rule: if a comparison/evaluation has 3+ independent tracks and each needs multiple source bodies, launch one bounded evidence agent per track before reading them; a reviewer is not a track substitute. Keep small lookups and comparisons direct. Delegate other broad separable research, domain-tool work, and disjoint edits early. Give delegates bounded non-overlapping scopes and compact evidence; the coordinator retains synthesis, integration, decisions, cohesive implementation, and completion. Sub-agents do not spawn children unless explicitly authorized. Before delegating, use the \`delegating-work\` skill to load model routing: select a demonstrated model for the purpose/surface; candidates require explicit trials."

if [[ "${1:-}" == "--aggregate" ]]; then
    output_context="$aggregate_context"
    output_budget=1024
else
    output_context="$context"
    output_budget=2048
fi

context_bytes="$(printf '%b' "$output_context" | LC_ALL=C wc -c)" || emit_empty
context_bytes="${context_bytes//[[:space:]]/}"
if [[ ! "$context_bytes" =~ ^[0-9]+$ ]] || (( context_bytes >= output_budget )); then
    emit_empty
fi
printf '{"additionalContext":"%s"}' "$output_context"
