#!/usr/bin/env bash
# agent-ssh :: emit-profile (POSIX wrapper)
# Runs the CLI through the uniform versioned-runtime resolver by delegating to the
# installed binstub, which resolves the interpreter SOLELY via the junction-free
# marker (uniform-runtime-resolution, #765) and self-provisions on first use.
# Falls back to a source-tree python only for a raw checkout with no binstub.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -x "$HOME/.local/bin/agent-ssh" ]; then
    exec "$HOME/.local/bin/agent-ssh" emit-profile "$@"
fi
export PYTHONPATH="$here/../src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m agent_ssh emit-profile "$@"  # runtime-resolution: allow bootstrap: raw source checkout, no installed binstub
