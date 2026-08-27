#!/usr/bin/env bash
set -u
guide="${COPILOT_PLUGIN_ROOT:-}/references/contribution-ground-rules.md"
if [[ ! -f "$guide" ]] || ! command -v python3 >/dev/null 2>&1; then
  printf '{}'
  exit 0
fi
python3 - "$guide" <<'PY'
import json
import sys

guide = sys.argv[1]
context = (
    "Copilot-extensions accepts only general-purpose, organization-neutral "
    "capabilities; personal needs belong in the adopter's private control repo "
    f"and organization-specific work in its internal marketplace. Read: {guide}"
)
print(json.dumps({"additionalContext": context}, separators=(",", ":")))
PY
