#!/usr/bin/env bash
# Emit the ambient continuity contract for every enabled session.

set -uo pipefail

max_kernel_bytes=2048
max_combined_bytes=3072
plugin_root="${COPILOT_PLUGIN_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P)}"
manifest="$plugin_root/plugin.json"
skill="$plugin_root/skills/context-handoff/SKILL.md"

emit_empty() {
    printf '%s\n' '[context-handoff] no guidance context emitted' >&2
    printf '{}'
    exit 0
}

[[ -f "$manifest" && -f "$skill" ]] || emit_empty
version="$(
    sed -n 's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)".*$/\1/p' "$manifest" |
        head -n 1
)" || emit_empty
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-dev[0-9]+)?$ ]] || emit_empty

context="[owner: context-handoff@$version]\\nThis session has context-handoff enabled whether or not this session began from a handoff; it is available from turn one. When you own the active objective, it can span multiple agent sessions: do not narrow investigation, planning, implementation, validation, or landing to fit one window. First select continuity: contextManagementTools enabled or all four native context tools offered without explicit false selects native-first. Confirmed unavailable tools permit custom recovery; unknown metadata requires diagnostics, not invented tools. For native context-only rollover, use get_context_remaining, the current owner's revision-bound session_artifacts checkpoint, bounded session_history, and terminal new_context. Preserve the parent gate, current slice, unresolved requests, decisions, live obligations, and exactly one next action. Verify persistence/snapshot binding, then rollover; read the bound checkpoint and refresh durable guidance. No custom pickup, raw clear, guessed artifact files, or policy bypass; inspect partial-clear errors before retrying. For custom new-owner/process transfer only: quiesce+sync first if pressure-driven, then compose and store the baton safely; call trigger_handoff if work remains, do not ask first. If ending the turn with proposed follow-ups, ask before trigger unless autopilot/pre-authorized. trigger_handoff always stores/seeds; never performs process management. Live signaling needs mode: auto (default: manual-only). Let one session own one slice of the larger effort. Consuming or producing a handoff is setup or progress, never completion. The session owning the objective stops only at its completion gate, explicit scope/confirmation gate, or real blocker; never for lateness or a stopping point alone. Use the \`context-handoff\` skill for mechanics."
aggregate_context="[owner: context-handoff@$version]\\nAn objective may span sessions; a handoff is progress, never completion. Native flag/tool selection uses a verified current-owner checkpoint and terminal new_context, not custom pickup; preserve the parent gate, obligations, and one next action, then refresh guidance. Near token pressure use the \`context-handoff\` skill. For custom transfer only: quiesce/sync/store, then trigger_handoff if work remains. Turn-end follow-ups ask before trigger unless pre-authorized. Live signaling needs mode: auto (default: manual-only). One session owns one slice; never stop for lateness alone."

context_bytes="$(printf '%b' "$context" | LC_ALL=C wc -c)" || emit_empty
context_bytes="${context_bytes//[[:space:]]/}"
if [[ ! "$context_bytes" =~ ^[0-9]+$ ]] || (( context_bytes >= max_kernel_bytes )); then
    emit_empty
fi

own_json="$(printf '{"additionalContext":"%s"}' "$context")"
if [[ "${1:-}" == "--aggregate" ]]; then
    printf '{"additionalContext":"%s"}' "$aggregate_context"
    exit 0
fi
if [[ "${1:-}" == "--own-only" ]]; then
    printf '%s' "$own_json"
    exit 0
fi

agent_worktrees_root="$(cd -- "$plugin_root/../agent-worktrees" 2>/dev/null && pwd -P)" || agent_worktrees_root=""
agent_worktrees_manifest="$agent_worktrees_root/plugin.json"
agent_worktrees_command="$agent_worktrees_root/bin/payload/agent-worktrees"
agent_worktrees_installer="$agent_worktrees_root/scripts/install.sh"
python=""
for candidate in python3 python; do
    candidate_path="$(command -v "$candidate" 2>/dev/null || true)"
    if [[ -n "$candidate_path" ]] &&
       "$candidate_path" -c 'raise SystemExit(0)' >/dev/null 2>&1; then
        python="$candidate_path"
        break
    fi
done
if [[ -n "$agent_worktrees_root" && -f "$agent_worktrees_manifest" &&
      -n "$python" ]]; then
    availability="unavailable"
    [[ -x "$agent_worktrees_command" && -f "$agent_worktrees_installer" ]] &&
        availability="ready"
    catalog_json="$(
        "$python" - "$agent_worktrees_manifest" "$agent_worktrees_command" "$availability" <<'PY'
import json
import pathlib
import sys

manifest_path = pathlib.Path(sys.argv[1]).resolve()
command_path = pathlib.Path(sys.argv[2]).resolve()
availability = sys.argv[3]
root = manifest_path.parent
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("name") != "agent-worktrees" or not command_path.is_relative_to(root):
    raise SystemExit(1)
catalog = {
    "schema": "copilot-extensions.session-command-catalog",
    "version": 1,
    "plugin": "agent-worktrees",
    "payload": {"provenance": "adjacent-compatibility"},
    "commands": [{
        "id": "agent-worktrees",
        "argv": [str(command_path)],
        "shell": "direct",
        "purpose": "Manage worktrees and project lifecycle",
        "availability": availability,
    }],
}
context = (
    "## agent-worktrees session command catalog\n\n"
    "Invoke the exact `argv` below. Do not search `PATH` or substitute a "
    "same-named command from another payload.\n\n"
    "```json\n"
    + json.dumps(catalog, sort_keys=True)
    + "\n```"
)
print(json.dumps({"additionalContext": context}, separators=(",", ":")))
PY
    )" || catalog_json=""
    if merged_json="$(
        "$python" - "$own_json" "$catalog_json" "$max_combined_bytes" <<'PY'
import json
import sys

contexts = []
for raw in sys.argv[1:3]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        continue
    context = value.get("additionalContext") if isinstance(value, dict) else None
    if isinstance(context, str) and context.strip() and context not in contexts:
        contexts.append(context)

combined = "\n\n".join(contexts)
if not combined or len(combined.encode("utf-8")) >= int(sys.argv[3]):
    raise SystemExit(1)
print(json.dumps({"additionalContext": combined}, separators=(",", ":")))
PY
    )"; then
        printf '%s' "$merged_json"
        exit 0
    fi
fi

printf '%s' "$own_json"
