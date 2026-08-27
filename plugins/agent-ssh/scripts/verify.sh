#!/usr/bin/env bash
# agent-ssh :: verify (POSIX wrapper)
# Runs the CLI through this payload's generated command, which resolves the
# interpreter solely via the junction-free marker and self-provisions on first
# use without selecting a same-named command through global PATH.
# Falls back to a source-tree python only for a raw checkout with no binstub.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
payload_command="$here/../bin/agent-ssh"
if [ -x "$payload_command" ]; then
    exec "$payload_command" verify "$@"
fi
export PYTHONPATH="$here/../src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m agent_ssh verify "$@"  # runtime-resolution: allow bootstrap: raw source checkout, no installed binstub
