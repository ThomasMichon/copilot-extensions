#!/usr/bin/env bash
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"
export CR_SCENARIO_NAME=agent-machines-installation-repair
cr_init
phase 0 "fresh environment and exact candidate"
envdump
SOURCE="${CR_HARNESS_MOUNT:-}"
FRESH="$(mktemp -d)"
ARCHIVED_SOURCE=0
if [ -n "$SOURCE" ] && [ -f "$SOURCE/candidate.tar" ]; then
    if ! capture "extract-candidate" -- timeout 120 python3 - "$SOURCE/candidate.tar" "$FRESH/source" <<'PY'
import sys, tarfile
from pathlib import Path
archive, target = map(Path, sys.argv[1:])
target.mkdir()
with archive.open("rb", buffering=1024 * 1024) as stream:
    with tarfile.open(fileobj=stream, mode="r|") as source:
        source.extractall(target, filter="data")
PY
    then
        jam "path-binstub" "candidate snapshot extraction failed" "inspect extract-candidate.log"
        cr_finalize
    fi
    SOURCE="$FRESH/source"
    ARCHIVED_SOURCE=1
fi
if [ -z "$SOURCE" ] || [ ! -f "$SOURCE/plugins/agent-machines/scripts/init.sh" ]; then
    jam "path-binstub" "candidate source is absent" "pass -HarnessMount <candidate checkout>"
    cr_finalize
fi
if [ -d "$HOME/.agent-machines" ] || [ -d "$HOME/.local/bin" ]; then
    jam "path-binstub" "runtime or binstub already exists" "use a fresh scenario container"
    cr_finalize
fi
pass "no existing runtime or binstub"
cr_meta "candidate_repair_sha256" "$(sha256sum "$SOURCE/plugins/agent-machines/src/agent_machines/installation_repair.py" | cut -d' ' -f1)"
if [ -n "${CR_UV_INDEX:-}" ]; then
    export UV_DEFAULT_INDEX="$CR_UV_INDEX" UV_INDEX_URL="$CR_UV_INDEX" PIP_INDEX_URL="$CR_UV_INDEX"
fi
if ! capture "materialize-candidate" -- python3 "$SOURCE/tools/preview_release.py" agent-machines --workdir "$FRESH"; then
    jam "path-binstub" "canonical candidate materialization failed" "inspect materialize-candidate.log"
    cr_finalize
fi
PAYLOAD="$HOME/.copilot/installed-plugins/fixture/agent-machines"
if ! capture "copy-candidate" -- python3 - "$FRESH/agent-machines" "$PAYLOAD" <<'PY'
import json, shutil, sys
from pathlib import Path
source, payload = map(Path, sys.argv[1:])
report = json.loads((source / "PREVIEW.json").read_text())
assert not any(row.startswith("SKIP") for key, rows in report.items()
               if key.endswith("_log") for row in rows), report
shutil.copytree(source, payload)
PY
then
    jam "path-binstub" "candidate copy failed" "inspect copy-candidate.log"
    cr_finalize
fi
phase 1 "stamp self-contained payload and remove original preview"
if ! capture "stamp" -- timeout 120 bash "$PAYLOAD/scripts/init.sh" stamp; then
    jam "path-binstub" "candidate stamp failed" "inspect stamp.log"
    cr_finalize
fi
SNAPSHOT="$(cat "$HOME/.agent-machines/payload-dir")"
if [ ! -f "$SNAPSHOT/libs/installer-readiness/pyproject.toml" ] ||
   [ ! -f "$SNAPSHOT/libs/installation-context/pyproject.toml" ]; then
    jam "path-binstub" "owning payload lacks declared repair libraries" "inspect candidate materialization"
    cr_finalize
fi
if ! capture "remove-original" -- python3 - "$FRESH" <<'PY'
import shutil, sys
from pathlib import Path
fresh = Path(sys.argv[1])
shutil.rmtree(fresh / "agent-machines")
PY
then
    jam "path-binstub" "original candidate removal failed" "inspect remove-original.log"
    cr_finalize
fi
pass "self-contained owning payload; original preview copy removed"
phase 2 "first-use runtime provision"
if ! capture "first-use" -- timeout 300 "$HOME/.local/bin/agent-machines" version; then
    jam "toolchain-uv" "candidate runtime first-use failed" "inspect first-use.log"
    cr_finalize
fi
pass "actual candidate binstub self-provisioned without original source"
phase 3 "installed native repair contract"
if capture "installed-repair" -- timeout 120 python3 "$_SELF_DIR/verify.py" "$HOME/.local/bin/agent-machines"; then
    pass "installed native CLI satisfies the ordinary repair contract"
else
    jam "machines-config" "installed repair contract failed" "inspect installed-repair.log"
    cr_finalize
fi
phase 4 "native source and packaging regression contracts"
TEST_SOURCE="$FRESH/test-source"
if [ "$ARCHIVED_SOURCE" -eq 1 ]; then
    TEST_SOURCE="$SOURCE"
elif ! capture "copy-test-source" -- timeout 600 python3 - "$SOURCE" "$TEST_SOURCE" <<'PY'
import shutil, sys
from pathlib import Path
source, target = map(Path, sys.argv[1:])
sys.path.insert(0, str(source / "tools"))
from standalone_consumers import STANDALONE_CONSUMERS
ignore = shutil.ignore_patterns(
    ".git", ".venv", ".test-venvs", "node_modules", "__pycache__", ".pytest_cache",
    ".ruff_cache", "build", "dist", "*.pyc", "*.pyo", "*.egg-info",
)
for name in ("tools", "libs", "plugins", *STANDALONE_CONSUMERS):
    if (source / name).is_dir():
        shutil.copytree(source / name, target / name, ignore=ignore)
PY
then
    jam "machines-config" "native test source copy failed" "inspect copy-test-source.log"
    cr_finalize
fi
TEST_ENV="$FRESH/test-env"
if ! capture "prepare-tests" -- bash -c '
    set -e
    uv venv "$1"
    "$1/bin/python" -I "$2/scripts/installer_dependency_policy.py" --uv uv -- \
        --python "$1/bin/python" -e "$2[dev]" "pytest-timeout>=2.3,<3"
' _ "$TEST_ENV" "$PAYLOAD"; then
    jam "toolchain-uv" "declared test dependencies unavailable" "inspect prepare-tests.log"
    cr_finalize
fi
export COPILOT_EXTENSIONS_TEST_CONTAINED=1
if capture "packaging-tests" -- timeout 180 "$TEST_ENV/bin/python" -m pytest -q --timeout=30 \
    "$TEST_SOURCE/tools/test_materialize_main.py" "$TEST_SOURCE/tools/test_sync_vendored_libs.py" \
    "$TEST_SOURCE/tools/test_check_vendored_libs_sync.py" \
    "$TEST_SOURCE/tools/test_installation_context_packaging.py"; then
    pass "native direct/nested source guards pass, including real symlink refusal"
else
    jam "machines-config" "native packaging regression tests failed" "inspect packaging-tests.log"
    cr_finalize
fi
if capture "trusted-materializer-tests" -- timeout 180 env \
    PYTHONPATH="$TEST_SOURCE/worktree-manager/src:$TEST_SOURCE/tools" \
    "$TEST_ENV/bin/python" -m pytest -q --timeout=30 \
    "$TEST_SOURCE/worktree-manager/tests/test_trusted_materializer_parity.py"; then
    pass "installed trusted materializer preserves canonical parity and native link refusals"
else
    jam "machines-config" "trusted materializer parity failed" "inspect trusted-materializer-tests.log"
    cr_finalize
fi
if capture "repair-tests" -- timeout 180 "$TEST_ENV/bin/python" -m pytest -q --timeout=30 \
    "$TEST_SOURCE/plugins/agent-machines/tests/test_installation_repair.py" \
    "$TEST_SOURCE/plugins/agent-machines/tests/test_installation_repair_restarts.py" \
    "$TEST_SOURCE/plugins/agent-machines/tests/test_installation_repair_boundaries.py"; then
    pass "native Linux repair, active-cell observation/refusal and descendant cleanup pass"
else
    jam "machines-config" "native repair regression tests failed" "inspect repair-tests.log"
fi
cr_finalize
