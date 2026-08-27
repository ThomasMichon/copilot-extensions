#!/usr/bin/env bash
set -u
guide="${COPILOT_PLUGIN_ROOT:-}/references/contribution-ground-rules.md"
if [[ ! -f "$guide" ]]; then
  printf '{}'
  exit 0
fi
python_bin="$(command -v python3 || command -v python || true)"
if [[ -z "$python_bin" ]]; then
  printf '{}'
  exit 0
fi
if ! output="$("$python_bin" - "$guide" 2>/dev/null <<'PY'
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
)"; then
  printf '{}'
  exit 0
fi
printf '%s' "$output"
