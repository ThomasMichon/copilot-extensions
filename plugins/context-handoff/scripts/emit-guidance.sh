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

context="[owner: context-handoff@$version]\\nThis session has context-handoff enabled. When you own the active objective, it can span multiple agent sessions. Work thoroughly across context windows: do not narrow investigation, planning, implementation, validation, or landing merely to fit one session. A context boundary is a relay point, not a stopping condition. If the active plan is unfinished, finish the planning needed to act, then begin execution immediately, subject to any required safety, review, approval, or confirmation gate; do not stop at a plan unless the user requested planning only. Consuming or producing a handoff is setup or progress, never completion. Near token pressure, preserve the objective, remaining work, decisions, and in-flight state in a precise baton, transfer it through the available handoff path, and keep going in the successor. Bounded delegates remain within their assigned scope, and a session superseded by cutover stops work and assists the successor rather than competing. The session owning the objective stops only when its completion gate is met, an explicit scope or required confirmation stops progress, or a real blocker needs input. Use the \`context-handoff\` skill for handoff mechanics."
aggregate_context="[owner: context-handoff@$version]\\nAn owned objective may span sessions: a context boundary or handoff is progress, never completion. Continue until the objective's completion gate, a required confirmation, or a real blocker. Near token pressure preserve the objective, remaining work, decisions, and in-flight state in the handoff; after cutover the predecessor stops competing. Use the \`context-handoff\` skill for mechanics."

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
python="$(command -v python3 || command -v python || true)"
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
