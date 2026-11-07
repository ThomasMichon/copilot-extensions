#!/usr/bin/env bash
# The selected payload's runtime gate refuses provisioning for this read.
set -u
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if output="$(AGENT_SSH_NO_SELFPROVISION=1 "$root/bin/agent-ssh" session-snapshot --guidance 2>/dev/null)"; then
  printf '%s\n' "$output"
else
  printf '%s\n' '{"additionalContext":"## SSH snapshot unavailable\n\nThe selected runtime is unavailable, refused or does not support session-snapshot. No provisioning or probe was attempted. Use the agent-ssh skill for inspection and bounded route repair."}'
fi
