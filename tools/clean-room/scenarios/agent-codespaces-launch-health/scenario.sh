#!/usr/bin/env bash
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"
export CR_SCENARIO_NAME=agent-codespaces-launch-health
cr_init

phase 0 "fresh environment and exact candidate"
envdump
SOURCE="${CR_HARNESS_MOUNT:-}"
INSTALLER="$SOURCE/plugins/agent-codespaces/scripts/install.sh"
if [ -z "$SOURCE" ] || [ ! -f "$INSTALLER" ]; then
    jam "path-binstub" "exact candidate is absent" "pass -HarnessMount <candidate checkout>"
    cr_finalize
fi
if [ -d "$HOME/.agent-codespaces" ]; then
    jam "path-binstub" "runtime already exists" "use a fresh scenario container"
    cr_finalize
fi
pass "runtime is absent"
cr_meta "source_installer_sha256" "$(sha256sum "$INSTALLER" | cut -d' ' -f1)"
FRESH="$(mktemp -d)"
if ! capture "copy-candidate" -- python3 - "$SOURCE" "$FRESH" <<'PY'
import shutil, sys
from pathlib import Path
source, fresh = map(Path, sys.argv[1:])
ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache", "*.egg-info", "build", "dist")
shutil.copytree(source / "plugins" / "agent-codespaces", fresh / "plugins" / "agent-codespaces", ignore=ignore)
shutil.copytree(source / "libs", fresh / "libs", ignore=ignore)
assert (fresh / "plugins/agent-codespaces/scripts/install.sh").read_bytes() == (source / "plugins/agent-codespaces/scripts/install.sh").read_bytes()
PY
then
    jam "path-binstub" "candidate copy failed" "inspect copy-candidate.log"
    cr_finalize
fi
INSTALLER="$FRESH/plugins/agent-codespaces/scripts/install.sh"
if [ -n "${CR_UV_INDEX:-}" ]; then
    export UV_DEFAULT_INDEX="$CR_UV_INDEX" UV_INDEX_URL="$CR_UV_INDEX"
fi
VERSION="$(python3 -c 'import sys,tomllib; print(tomllib.load(open(sys.argv[1],"rb"))["project"]["version"])' "$SOURCE/plugins/agent-codespaces/pyproject.toml")"
ROOT="$HOME/.agent-codespaces"
RUNTIME="$ROOT/versions/$VERSION"
PYTHON="$RUNTIME/bin/python"

phase 1 "fresh real runtime provisioning"
if ! capture "provision" -- timeout 300 bash "$INSTALLER" provision; then
    jam "toolchain-uv" "candidate provisioning failed" "inspect provision.log and the explicitly configured package index"
    cr_finalize
fi
if ! capture "launch" -- "$PYTHON" -I -m agent_codespaces --help; then
    jam "path-binstub" "fresh runtime module launch failed" "inspect launch.log"
    cr_finalize
fi
if [ ! -f "$RUNTIME/.install-complete.json" ] || [ "$(cat "$ROOT/current-version")" != "$VERSION" ]; then
    jam "path-binstub" "fresh runtime was not completed and selected" "inspect runtime publication"
    cr_finalize
fi
pass "fresh candidate provisions and launches through its real installed module"
if ! capture "installed-origin" -- "$PYTHON" -I - "$FRESH/plugins/agent-codespaces/src/agent_codespaces/__main__.py" <<'PY'
import hashlib, pathlib, sys
import agent_codespaces
package = pathlib.Path(agent_codespaces.__file__).parent
assert package.is_relative_to(pathlib.Path(sys.prefix))
assert hashlib.sha256((package / "__main__.py").read_bytes()).digest() == hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).digest()
PY
then
    jam "path-binstub" "installed module differs from the exact candidate" "inspect installed-origin.log"
    cr_finalize
fi

phase 2 "namespace package is not a launchable runtime"
STATE="$(mktemp)"
if ! capture "seed-namespace" -- "$PYTHON" -I - "$ROOT" "$STATE" <<'PY'
import hashlib, json, pathlib, sys
import agent_codespaces
root, state = map(pathlib.Path, sys.argv[1:])
package = pathlib.Path(agent_codespaces.__file__).parent
assert package.is_relative_to(pathlib.Path(sys.prefix))
for name in ("__init__.py", "__main__.py"):
    (package / name).unlink()
paths = [root / "current-version", pathlib.Path(sys.prefix) / ".install-complete.json"]
state.write_text(json.dumps({str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}))
PY
then
    jam "path-binstub" "namespace fixture could not be created inside the disposable runtime" "inspect seed-namespace.log"
    cr_finalize
fi
if ! capture "namespace-import" -- "$PYTHON" -I -c 'import agent_codespaces; assert agent_codespaces.__spec__.origin is None'; then
    jam "path-binstub" "fixture is not an importable namespace package" "inspect namespace-import.log"
    cr_finalize
fi
if capture "broken-launch" -- "$PYTHON" -I -m agent_codespaces --help; then
    fail "namespace package unexpectedly launched"
    cr_finalize
fi
pass "package import succeeds while the actual module launch fails"

phase 3 "forced update refuses before rewriting completion or selection"
if capture "refuse-update" -- timeout 120 bash "$INSTALLER" update --force; then
    fail "forced update accepted an unlaunchable completed runtime"
else
    if grep -q "Refusing an in-place rebuild" "$CR_LOGDIR/refuse-update.log"; then
        pass "forced update reports explicit immutable-slot repair guidance"
    else
        jam "path-binstub" "update failed without the required health refusal" "inspect refuse-update.log"
    fi
fi
if capture "unchanged-publication" -- python3 - "$STATE" <<'PY'
import hashlib, json, pathlib, sys
for name, expected in json.loads(pathlib.Path(sys.argv[1]).read_text()).items():
    assert hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest() == expected
PY
then
    pass "completion evidence and selection remain byte-identical"
else
    fail "failed health refusal rewrote completion or selection"
fi
rm -f "$STATE"
cr_finalize
