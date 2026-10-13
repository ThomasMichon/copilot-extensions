#!/usr/bin/env bash
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"
export CR_SCENARIO_NAME=agent-ssh-snapshot
cr_init
phase 0 "fresh environment and exact source evidence"
envdump
SOURCE="${CR_HARNESS_MOUNT:-}"
if [ -z "$SOURCE" ] || [ ! -f "$SOURCE/plugins/agent-ssh/scripts/install.sh" ]; then
    jam "path-binstub" "candidate source is absent" "pass -HarnessMount <candidate checkout>"
    cr_finalize
fi
if [ -d "$HOME/.agent-ssh" ] || [ -d "$HOME/.local/bin" ]; then
    jam "path-binstub" "runtime or binstub already exists" "use a fresh scenario container"
    cr_finalize
fi
pass "no existing runtime or binstub"
cr_meta "source_installer_sha256" "$(sha256sum "$SOURCE/plugins/agent-ssh/scripts/install.sh" | cut -d' ' -f1)"
REGISTRY_SHA="$(sha256sum "$SOURCE/libs/machine-transport/src/machine_transport/registry.py" | cut -d' ' -f1)"
cr_meta "source_registry_sha256" "$REGISTRY_SHA"
if [ -n "${CR_UV_INDEX:-}" ]; then
    export UV_DEFAULT_INDEX="$CR_UV_INDEX" UV_INDEX_URL="$CR_UV_INDEX"
    export PIP_INDEX_URL="$CR_UV_INDEX"
fi
FRESH="$(mktemp -d)"
if ! capture "copy-candidate" -- python3 - "$SOURCE" "$FRESH" <<'PY'
import shutil, sys
from pathlib import Path
source, fresh = map(Path, sys.argv[1:])
ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist")
shutil.copytree(source / "plugins" / "agent-ssh", fresh / "plugins" / "agent-ssh", ignore=ignore)
for name in ("installer-engine", "agent-procutil", "ssh-manager", "venue-copilot",
             "zdd", "remote-login-shell", "fleet-contracts", "machine-transport"):
    shutil.copytree(source / "libs" / name, fresh / "libs" / name, ignore=ignore)
PY
then
    jam "path-binstub" "candidate copy failed" "inspect copy-candidate.log"
    cr_finalize
fi

phase 1 "stamp the candidate snapshot"
if ! capture "stamp" -- timeout 120 bash "$FRESH/plugins/agent-ssh/scripts/install.sh" stamp; then
    jam "path-binstub" "candidate stamp failed" "inspect stamp.log"
    cr_finalize
fi
SNAPSHOT="$(cat "$HOME/.agent-ssh/payload-dir")"
VERSION="$(python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["project"]["version"])' "$SNAPSHOT/pyproject.toml")"
RUNTIME="$HOME/.agent-ssh/versions/$VERSION"
if [ -d "$RUNTIME" ] || [ ! -f "$SNAPSHOT/libs/machine-transport/src/machine_transport/registry.py" ]; then
    jam "path-binstub" "stamp is not a fresh self-contained snapshot" "inspect the payload and runtime"
    cr_finalize
fi
pass "snapshot contains machine-transport; runtime is still absent"
if ! capture "remove-original-copy" -- python3 - "$FRESH" <<'PY'
import shutil, sys
from pathlib import Path
fresh = Path(sys.argv[1])
shutil.rmtree(fresh / "plugins")
shutil.rmtree(fresh / "libs")
PY
then
    jam "path-binstub" "original candidate copy could not be removed" "inspect remove-original-copy.log"
    cr_finalize
fi

phase 2 "first-use provision without the original source"
if ! capture "first-use" -- timeout 300 "$HOME/.local/bin/agent-ssh" version; then
    jam "toolchain-uv" "fresh first-use provisioning failed" "inspect first-use.log and the explicitly configured dependency feed"
    cr_finalize
fi
pass "first-use version command succeeded after source removal"

phase 3 "installed logical-machine dependency contract"
if capture "installed-dependency" -- "$RUNTIME/bin/python" -I - "$REGISTRY_SHA" <<'PY'
import hashlib, pathlib, sys
import agent_ssh, machine_transport
import machine_transport.registry as registry
from machine_transport import normalize_machine_entries, parse_machines_yaml, resolve_ssh_target
assert pathlib.Path(machine_transport.__file__).is_relative_to(pathlib.Path(sys.prefix))
assert hashlib.sha256(pathlib.Path(registry.__file__).read_bytes()).hexdigest() == sys.argv[1]
entries = normalize_machine_entries(parse_machines_yaml({"machines": {"desk": {"ssh": {
    "environments": [
        {"name": "windows", "alias": "desk-win", "shell": "pwsh"},
        {"name": "wsl", "alias": "desk-ubuntu", "shell": "bash"},
    ]
}}}}))
assert list(entries) == ["desk", "desk-wsl"]
assert resolve_ssh_target(entries["desk"]) == ("desk-win", "pwsh")
assert resolve_ssh_target(entries["desk-wsl"]) == ("desk-ubuntu", "bash")
assert entries["desk"].physical_host == entries["desk-wsl"].physical_host == "desk"
print("installed machine-transport owns independent Windows/WSL profiles")
PY
then
    pass "actual installed dependency owns logical machine profiles"
else
    jam "ssh-contract" "installed dependency contract failed" "inspect installed-dependency.log"
fi
cr_finalize
