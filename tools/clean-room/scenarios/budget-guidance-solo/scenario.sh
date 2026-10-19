#!/usr/bin/env bash
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"
export CR_SCENARIO_NAME=budget-guidance-solo
cr_init

phase 0 "fresh credential-free environment and candidate source"
# The shared helper records tool versions, PATH, and config presence, not values.
if ! envdump; then
    jam "path-binstub" "environment snapshot failed" "inspect the disposable toolchain"
    cr_finalize
fi
if ! capture "python-prerequisite" -- python3 -I -c \
    'import sys; sys.exit("Python 3.10+ is required") if sys.version_info < (3, 10) else print(sys.version)'; then
    jam "pythonpath" "scenario requires Python 3.10+" "use the documented base-image toolchain"
    cr_finalize
fi
if ! capture "node-prerequisite" -- node -e \
    'if (Number(process.versions.node.split(".")[0]) < 20) { console.error("Node.js 20+ is required"); process.exit(1); } console.log(process.versions.node);'; then
    jam "node-prerequisite" "scenario requires Node.js 20+" "use the documented base-image toolchain"
    cr_finalize
fi
SOURCE="${CR_HARNESS_MOUNT:-}"
if [ -z "$SOURCE" ] || [ ! -f "$SOURCE/plugins/budget-guidance/scripts/install.sh" ]; then
    jam "path-binstub" "candidate source is absent" "pass --harness-mount <candidate checkout>"
    cr_finalize
fi
if [ -d "$HOME/.budget-guidance" ] || [ -e "$HOME/.local/bin/budget-guidance" ] \
    || [ -L "$HOME/.local/bin/budget-guidance" ]; then
    jam "path-binstub" "runtime or binstub already exists" "use a fresh scenario container"
    cr_finalize
fi
pass "no existing budget runtime or binstub"
if ! SOURCE_HASH="$(sha256sum "$SOURCE/plugins/budget-guidance/scripts/install.sh" | cut -d' ' -f1)"; then
    jam "path-binstub" "candidate installer hash failed" "inspect the candidate source"
    cr_finalize
fi
cr_meta "source_installer_sha256" "$SOURCE_HASH"
if [ -n "${CR_UV_INDEX:-}" ]; then
    export UV_DEFAULT_INDEX="$CR_UV_INDEX" UV_INDEX_URL="$CR_UV_INDEX"
fi
if ! FRESH="$(mktemp -d)"; then
    jam "path-binstub" "candidate temporary directory creation failed" "inspect the disposable filesystem"
    cr_finalize
fi
trap 'rm -rf -- "$FRESH"' EXIT
if ! capture "copy-candidate" -- python3 -I - "$SOURCE" "$FRESH" <<'PY'
import shutil
import sys
from pathlib import Path

source, fresh = map(Path, sys.argv[1:])
ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist")
shutil.copytree(source / "plugins/budget-guidance", fresh / "plugins/budget-guidance", ignore=ignore)
shutil.copytree(source / "libs/agent-procutil", fresh / "libs/agent-procutil", ignore=ignore)
PY
then
    jam "path-binstub" "candidate copy failed" "inspect copy-candidate.log"
    cr_finalize
fi

phase 1 "stamp a self-contained payload snapshot"
if ! capture "stamp" -- timeout 120 bash "$FRESH/plugins/budget-guidance/scripts/install.sh" stamp; then
    jam "path-binstub" "candidate stamp failed" "inspect stamp.log"
    cr_finalize
fi
if ! SNAPSHOT="$(cat "$HOME/.budget-guidance/payload-dir")" || [ ! -d "$SNAPSHOT" ]; then
    jam "path-binstub" "stamped payload pointer is unavailable" "inspect stamp.log"
    cr_finalize
fi
if ! VERSION="$(python3 -I -c 'import json,sys; from pathlib import Path; print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["version"])' "$SNAPSHOT/plugin.json")" \
    || [ -z "$VERSION" ]; then
    jam "path-binstub" "stamped manifest version is unavailable" "inspect the stamped plugin.json"
    cr_finalize
fi
RUNTIME="$HOME/.budget-guidance/versions/$VERSION"
BIN="$HOME/.local/bin/budget-guidance"
if [ -d "$RUNTIME" ] || [ ! -x "$BIN" ] || [ ! -f "$SNAPSHOT/libs/agent-procutil/pyproject.toml" ]; then
    jam "path-binstub" "stamp is not a fresh self-contained snapshot" "inspect the payload and runtime"
    cr_finalize
fi
pass "snapshot contains the local dependency; runtime is still absent"
if ! capture "remove-original-copy" -- python3 -I - "$FRESH" <<'PY'
import shutil
import sys
from pathlib import Path

fresh = Path(sys.argv[1])
shutil.rmtree(fresh / "plugins")
shutil.rmtree(fresh / "libs")
PY
then
    jam "path-binstub" "original candidate copy could not be removed" "inspect remove-original-copy.log"
    cr_finalize
fi

phase 2 "first-use provision after removing the original copy"
if ! capture "first-use" -- timeout 300 "$BIN" --version; then
    jam "toolchain-uv" "fresh first-use provisioning failed" "inspect first-use.log and the explicit dependency feed"
    cr_finalize
fi
pass "first-use version command succeeded without the original source"

phase 3 "installed identity and offline posture"
if capture "optimized-verifier-refusal" -- timeout 30 "$RUNTIME/bin/python" -I -O \
    "$_SELF_DIR/verify-install.py" "$BIN" "$SNAPSHOT"; then
    jam "pythonpath" "optimized verifier did not refuse" "verification must never skip assertions"
    cr_finalize
fi
if ! grep -Fxq "verification requires assertions (run without -O)" \
    "$CR_LOGDIR/optimized-verifier-refusal.log"; then
    jam "pythonpath" "optimized verifier failed for an unexpected reason" "inspect optimized-verifier-refusal.log"
    cr_finalize
fi
if capture "installed-contract" -- timeout 90 "$RUNTIME/bin/python" -I \
    "$_SELF_DIR/verify-install.py" "$BIN" "$SNAPSHOT"; then
    pass "installed identity and static/unavailable posture match the candidate"
else
    jam "path-binstub" "installed offline contract failed" "inspect installed-contract.log"
    cr_finalize
fi

phase 4 "isolated foreground extension contract"
if ! capture "prepare-extension-fixture" -- python3 -I - "$SOURCE" "$SNAPSHOT" "$FRESH" <<'PY'
import shutil
import sys
from pathlib import Path

source, snapshot, fresh = map(Path, sys.argv[1:])
fixture = fresh / "fixture"
shutil.copytree(snapshot / "extensions", fixture / "extensions")
(fixture / "tests/fixtures").mkdir(parents=True)
for relative in ("tests/host-quota.test.mjs", "tests/fixtures/host-sdk.mjs"):
    shutil.copyfile(source / "plugins/budget-guidance" / relative, fixture / relative)
PY
then
    jam "path-binstub" "installed extension fixture preparation failed" "inspect prepare-extension-fixture.log"
    cr_finalize
fi
if capture "extension-fixture" -- timeout 30 node --test "$FRESH/fixture/tests/host-quota.test.mjs"; then
    pass "copied extension entry passes without live session or SDK credentials"
else
    jam "path-binstub" "isolated extension fixture failed" "inspect extension-fixture.log"
fi
cr_finalize
