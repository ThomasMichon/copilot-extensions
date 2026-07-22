#!/usr/bin/env bash
# agent-ssh :: emit-profile (POSIX wrapper)
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$here/../src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m agent_ssh emit-profile "$@"
