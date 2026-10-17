#!/usr/bin/env bash
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$SELF_DIR/../../lib/clean-room-lib.sh}"

: "${CR_SCENARIO_NAME:=merged-dev-runtime}"
export CR_SCENARIO_NAME
cr_init

if [ -n "${CR_UV_INDEX:-}" ]; then
    export UV_INDEX_URL="$CR_UV_INDEX" UV_DEFAULT_INDEX="$CR_UV_INDEX" UV_EXTRA_INDEX_URL="$CR_UV_INDEX"
fi

SOURCE_REPO="${CR_SOURCE_REPO:-https://github.com/ThomasMichon/copilot-extensions.git}"
SOURCE_COMMIT="${CR_SOURCE_COMMIT:-}"
PLUGIN="${CR_SOURCE_PLUGIN:-agent-bridge}"
CHECKOUT="$HOME/merged-source"
RUNTIME="$HOME/.$PLUGIN"

case "$PLUGIN" in
    agent-bridge) ;;
    *)
        jam "repo-config" "This scenario currently proves agent-bridge only" \
            "Use the declared source-install reference plugin."
        cr_finalize
        ;;
esac

phase 0 "fresh machine without installed runtime"
envdump
if [ -e "$RUNTIME" ] || [ -e "$HOME/.local/bin/$PLUGIN" ] || [ -e "$CHECKOUT" ]; then
    jam "repo-config" "Source-install fixture is not fresh" \
        "Start a fresh, uniquely named clean-room container."
    cr_finalize
fi
pass "No runtime, installed command or source checkout is inherited"

phase 1 "acquire and pin clean merged dev source"
if ! capture "source-clone" -- git -c credential.helper= clone \
    --filter=blob:none --single-branch --branch dev "$SOURCE_REPO" "$CHECKOUT"; then
    jam "repo-config" "Cannot acquire the declared dev source; see source-clone.log"
    cr_finalize
fi
if [ -z "$SOURCE_COMMIT" ]; then
    SOURCE_COMMIT="$(git -C "$CHECKOUT" rev-parse refs/remotes/origin/dev)"
fi
if ! [[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] ||
    ! git -C "$CHECKOUT" merge-base --is-ancestor "$SOURCE_COMMIT" refs/remotes/origin/dev; then
    jam "repo-config" "Selected source is not an exact merged dev commit"
    cr_finalize
fi
if ! capture "source-select" -- git -C "$CHECKOUT" checkout --detach "$SOURCE_COMMIT"; then
    jam "repo-config" "Cannot select the pinned source commit; see source-select.log"
    cr_finalize
fi
if ! SOURCE_STATUS="$(git -C "$CHECKOUT" status --porcelain=v1 --untracked-files=all --ignored)"; then
    jam "repo-config" "Cannot verify source checkout cleanliness"
    cr_finalize
fi
if [ ! -d "$CHECKOUT/.git" ] || [ -n "$SOURCE_STATUS" ]; then
    jam "repo-config" "Selected source is dirty or is a linked worktree"
    cr_finalize
fi
cr_meta "source_commit" "$SOURCE_COMMIT"
cr_meta "plugin" "$PLUGIN"
pass "Selected a clean standalone checkout at $SOURCE_COMMIT"

phase 2 "materialize source and run the real installer"
_prepare_payload() {
    python3 -I -B "$CHECKOUT/tools/prepare_runtime_source.py" \
        --source "$CHECKOUT" --commit "$SOURCE_COMMIT" --plugin "$PLUGIN" \
        --output "$HOME/runtime-payloads" > "$HOME/prepared-source.json"
}
if ! capture "source-prepare" -- _prepare_payload; then
    jam "runtime-install" "Source materialization failed; see source-prepare.log" \
        "Use a merged source commit that supports immutable source installation."
    cr_finalize
fi
if ! PAYLOAD="$(python3 -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["payload"])' \
    "$HOME/prepared-source.json")"; then
    jam "runtime-install" "Source preparation returned an invalid payload result"
    cr_finalize
fi
if ! capture "source-install" -- timeout 600 \
    bash "$PAYLOAD/scripts/install.sh" install; then
    jam "runtime-install" "Non-editable source installer failed; see source-install.log" \
        "Fix source assembly in the owning installer; do not hand-install missing libraries."
    cr_finalize
fi
pass "The actual source installer completed without fixture repair"

phase 3 "verify completed source-qualified runtime and installed command"
if [ ! -s "$RUNTIME/current-version" ]; then
    jam "path-binstub" "Installer did not publish a runtime selection"
    cr_finalize
fi
GENERATION="$(tr -d '\r\n' < "$RUNTIME/current-version")"
if ! [[ "$GENERATION" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]]; then
    jam "path-binstub" "Installer published an invalid runtime generation"
    cr_finalize
fi
cr_meta "runtime_generation" "$GENERATION"
if [ ! -s "$RUNTIME/versions/$GENERATION/.install-complete.json" ]; then
    jam "path-binstub" "Selected runtime lacks completion evidence"
    cr_finalize
fi
if ! capture "runtime-source-identity" -- python3 - "$RUNTIME" "$GENERATION" "$SOURCE_COMMIT" <<'PY'
from __future__ import annotations

from pathlib import Path
import json
import sys

root, generation, commit = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
assert commit in generation, "runtime generation is not qualified by the full source commit"
assert (root / "versions" / generation / "bin" / "python").is_file()
manifest = json.loads((root / "deploy-manifest.json").read_text())
assert manifest["runtime_version"] == generation
assert manifest["runtime_source"]["source_commit"] == commit
assert manifest["source"]["version"] == manifest["runtime_source"]["package_version"]
PY
then
    jam "path-binstub" "Runtime selection does not identify the selected source commit"
    cr_finalize
fi
if ! capture "installed-version" -- bash -lc 'agent-bridge --version'; then
    jam "path-binstub" "Installed command is not usable on a fresh login-shell PATH"
    cr_finalize
fi
pass "Installed command resolves a completed source-qualified generation"

phase 4 "invoke the runtime with the source checkout unavailable"
if ! SOURCE_STATUS="$(git -C "$CHECKOUT" status --porcelain=v1 --untracked-files=all --ignored)" ||
    [ -n "$SOURCE_STATUS" ]; then
    jam "repo-config" "Source installation modified the input checkout"
    cr_finalize
fi
if ! mv "$CHECKOUT" "$HOME/merged-source-unavailable"; then
    jam "repo-config" "Cannot make the source fixture unavailable"
    cr_finalize
fi
if ! capture "independent-imports" -- "$RUNTIME/versions/$GENERATION/bin/python" -I - <<'PY'
from __future__ import annotations

from pathlib import Path
import sys
import agent_bridge
import credential_relay
import ssh_manager
import zdd

runtime = Path(sys.prefix).resolve()
for module in (agent_bridge, credential_relay, ssh_manager, zdd):
    location = Path(module.__file__).resolve()
    assert runtime in location.parents, f"{module.__name__} depends on source outside its runtime"
PY
then
    jam "pythonpath" "Installed dependency closure still depends on the source checkout"
    cr_finalize
fi
if ! capture "independent-command" -- bash -lc 'agent-bridge --version'; then
    jam "path-binstub" "Installed command failed after the source checkout became unavailable"
    cr_finalize
fi
pass "Non-editable installed code and vendor imports work without the source checkout"

phase 5 "verify the source-qualified build is actually serving"
if ! capture "service-first-use" -- bash -lc 'agent-bridge agents'; then
    jam "bridge-service" "Installed first-use command could not reach its service"
    cr_finalize
fi
if ! capture "serving-source-identity" -- "$RUNTIME/versions/$GENERATION/bin/python" -I - "$RUNTIME" "$GENERATION" <<'PY'
from __future__ import annotations

import json
from pathlib import Path
import sys

from agent_bridge.client import BridgeClient
from zdd.diagnostics import process_start_time

root, expected = Path(sys.argv[1]), sys.argv[2]
health = BridgeClient.from_config().health()
assert health["status"] == "ok" and health["ready"] is True
assert health["version"] == expected, "healthy daemon is serving another build"
slot = health["slot"]
assert slot["role"] == "active" and slot["active"]["pid"] == slot["pid"]
assert slot["active"]["process_start_time"] == process_start_time(slot["pid"])
marker = json.loads((root / "running-version.json").read_text())
assert marker["pid"] == slot["pid"] and marker["version"] == expected
result = {"pid": slot["pid"], "runtime_generation": health["version"]}
(Path.home() / "source-serving.json").write_text(json.dumps(result))
print(json.dumps(result))
PY
then
    jam "bridge-service" "Serving process does not match the selected source build"
    cr_finalize
fi
cr_meta "serving_generation" "$GENERATION"
pass "Live authenticated health, routed PID identity and running marker confirm the selected build"
cr_finalize
