#!/usr/bin/env bash
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"
export CR_SCENARIO_NAME=repository-agent-exposure
cr_init

phase 0 "fresh credential-free candidate environment"
envdump
SOURCE="${CR_HARNESS_MOUNT:-}"
if [ -z "$SOURCE" ] || [ ! -f "$SOURCE/plugins/agent-worktrees/scripts/install.sh" ]; then
    jam "path-binstub" "candidate source is absent" "pass --harness-mount <candidate checkout>"
    cr_finalize
fi
if [ -d "$HOME/.agent-worktrees" ] || [ -e "$HOME/.local/bin/agent-worktrees" ]; then
    jam "path-binstub" "worktree runtime already exists" "use a fresh scenario container"
    cr_finalize
fi
if ! FRESH="$(mktemp -d)"; then
    jam "path-binstub" "candidate directory creation failed" "inspect the disposable filesystem"
    cr_finalize
fi
if [ -n "${CR_UV_INDEX:-}" ]; then
    export UV_DEFAULT_INDEX="$CR_UV_INDEX" UV_INDEX_URL="$CR_UV_INDEX"
fi
if ! capture "copy-candidate" -- python3 -I - "$SOURCE" "$FRESH" <<'PY'
import shutil
import sys
from pathlib import Path

source, fresh = map(Path, sys.argv[1:])
ignore = shutil.ignore_patterns(
    ".venv", ".test-venvs", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist",
)
shutil.copytree(source / "plugins/agent-worktrees", fresh / "plugins/agent-worktrees", ignore=ignore)
shutil.copytree(source / "libs", fresh / "libs", ignore=ignore)
PY
then
    jam "path-binstub" "candidate copy failed" "inspect copy-candidate.log"
    cr_finalize
fi
pass "candidate copied without credentials or a live session"

phase 1 "standalone stamp and first-use provision"
if ! capture "stamp" -- timeout 180 bash "$FRESH/plugins/agent-worktrees/scripts/install.sh" stamp; then
    jam "path-binstub" "candidate stamp failed" "inspect stamp.log"
    cr_finalize
fi
BIN="$HOME/.local/bin/agent-worktrees"
if ! capture "first-use" -- timeout 300 "$BIN" --version; then
    jam "toolchain-uv" "candidate first-use provisioning failed" "inspect first-use.log"
    cr_finalize
fi
pass "standalone runtime provisioned from the candidate"

phase 2 "installed policy and adoption boundaries"
if capture "policy-contract" -- timeout 120 python3 -I "$_SELF_DIR/verify-policy.py" "$BIN" "$FRESH/fixtures"; then
    pass "installed admission and adoption refusal preserve repository and registration state"
else
    jam "path-binstub" "installed policy contract failed" "inspect policy-contract.log"
fi
cr_finalize
