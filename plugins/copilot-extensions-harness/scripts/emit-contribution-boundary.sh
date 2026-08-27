#!/usr/bin/env bash
set -u
plugin_root="${COPILOT_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
guide="$plugin_root/references/contribution-ground-rules.md"
manifest="$plugin_root/plugin.json"
if [[ ! -f "$guide" ]] || [[ ! -f "$manifest" ]]; then
  printf '{}'
  exit 0
fi
python_bin="$(command -v python3 || command -v python || true)"
if [[ -z "$python_bin" ]]; then
  printf '{}'
  exit 0
fi
if ! output="$("$python_bin" - "$guide" "$manifest" 2>/dev/null <<'PY'
import json
import sys

guide, manifest = sys.argv[1:3]
with open(manifest, encoding="utf-8") as stream:
    version = json.load(stream)["version"]
context = (
    f"[owner: copilot-extensions-harness@{version}] copilot-extensions accepts "
    "only general-purpose, organization-neutral "
    "capabilities; personal needs belong in the adopter's private control repo "
    f"and organization-specific work in its internal marketplace. Read: {guide}"
)
print(json.dumps({"additionalContext": context}, separators=(",", ":")))
PY
)"; then
  printf '{}'
  exit 0
fi
printf '%s' "$output"
