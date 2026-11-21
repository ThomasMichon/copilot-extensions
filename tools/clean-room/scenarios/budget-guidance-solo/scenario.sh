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
sys.path.insert(0, str(source / "tools"))
from launch_wrapper_assets_ref import load_manifest_groups
from materialize_main import (
    materialize_file_pointers, materialize_installer_engine_ref_into, materialize_payload_invocation_into,
    materialize_uv_editable_ref_into,
)

ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist")
plugin = source / "plugins/budget-guidance"
candidate = fresh / "plugins/budget-guidance"
shutil.copytree(plugin, candidate, ignore=ignore)
results = materialize_file_pointers(candidate, canonical_root=source)
if any(line.startswith("SKIP ") for line in results):
    raise RuntimeError(results)
for assets in load_manifest_groups(plugin, manifest_name="packaged-assets.json"):
    destination = assets.payload_dir(plugin_dir=candidate)
    destination.mkdir(parents=True, exist_ok=True)
    for name in assets.files:
        shutil.copyfile(assets.canonical_dir(repo_root=source) / name, destination / name)
for materialize in (
    materialize_uv_editable_ref_into,
    materialize_installer_engine_ref_into, materialize_payload_invocation_into,
):
    results = materialize(source_consumer_dir=plugin, dest_consumer_dir=candidate, canonical_root=source)
    if any(line.startswith("SKIP ") for line in results):
        raise RuntimeError(results)
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
if (fresh / "libs").exists():
    shutil.rmtree(fresh / "libs")
PY
then
    jam "path-binstub" "original candidate copy could not be removed" "inspect remove-original-copy.log"
    cr_finalize
fi

phase 2 "explicit snapshot preparation after removing the original copy"
if capture "unprepared-command" -- timeout 30 "$BIN" --version; then
    jam "path-binstub" "unprepared command unexpectedly succeeded" "ordinary launch must not install a runtime"
    cr_finalize
fi
if [ -d "$RUNTIME" ]; then
    jam "path-binstub" "ordinary launch created a runtime" "inspect unprepared-command.log"
    cr_finalize
fi
if ! capture "explicit-provision" -- env UV_PYTHON=must-not-be-selected \
    UV_PYTHON_PREFERENCE=only-system UV_MANAGED_PYTHON=1 UV_NO_MANAGED_PYTHON=1 \
    VIRTUAL_ENV=/unrelated CONDA_PREFIX=/unrelated \
    timeout 300 bash "$SNAPSHOT/scripts/install.sh" provision; then
    jam "toolchain-uv" "explicit snapshot preparation failed" "inspect explicit-provision.log and the dependency feed"
    cr_finalize
fi
if ! capture "prepared-command" -- timeout 30 "$BIN" --version; then
    jam "path-binstub" "prepared command failed" "inspect prepared-command.log"
    cr_finalize
fi
pass "explicit snapshot setup and prepared command succeeded without the original source"

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
