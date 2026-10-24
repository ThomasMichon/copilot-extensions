#!/usr/bin/env bash
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$SELF_DIR/../../lib/clean-room-lib.sh}"
: "${CR_SCENARIO_NAME:=fresh-installer-failure}"
export CR_SCENARIO_NAME
cr_init

SOURCE_REPO="${CR_SOURCE_REPO:-https://github.com/ThomasMichon/copilot-extensions.git}"
SOURCE_COMMIT="${CR_SOURCE_COMMIT:-}"
PLUGIN="${CR_SOURCE_PLUGIN:-agent-bridge}"
CHECKOUT="$HOME/failure-source"
RUNTIME="$HOME/.$PLUGIN"
WORK="$HOME/failure-fixture"
INSTALL_ARGS=()
case "$PLUGIN" in
    agent-bridge) ;;
    agent-dispatch) INSTALL_ARGS=(--no-service --no-supervisor) ;;
    *)
        jam "repo-config" "Only agent-bridge and agent-dispatch are qualified by this scenario"
        cr_finalize
        ;;
esac

phase 0 "verify fresh Linux fixture and prerequisites"
envdump
if [ "$(uname -s)" != Linux ] || ! [[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
    jam "repo-config" "This Linux witness requires an explicit full merged dev commit"
    cr_finalize
fi
if [ "${CR_UV_INDEX:-}" != "https://packagefeedproxy.microsoft.io/pypi/simple/" ]; then
    jam "repo-config" "This bounded witness requires the runner's standard anonymous public Python feed"
    cr_finalize
fi
for tool in git python3 uv timeout; do
    if ! command -v "$tool" >/dev/null; then
        jam "repo-config" "Required base-image tool is unavailable: $tool"
        cr_finalize
    fi
done
if ! capture "feed-isolation" -- python3 -I -c '
import socket
for name in ("pypi.org", "files.pythonhosted.org", "packagefeedproxy.microsoft.io"):
    addresses = {entry[4][0] for entry in socket.getaddrinfo(name, 443)}
    assert addresses == {"127.0.0.1"}, f"package feed is not isolated: {name}"
print("Package feed names resolve only to the disposable container loopback")
'; then
    jam "repo-config" "Required package-feed isolation is absent" "Run with --block-public-feeds."
    cr_finalize
fi
for path in "$RUNTIME" "$CHECKOUT" "$WORK" "$HOME/.local/bin/$PLUGIN"; do
    if [ -e "$path" ] || [ -L "$path" ]; then
        jam "repo-config" "Fixture is not fresh: $path" "Use a new, uniquely named container."
        cr_finalize
    fi
done
if ! mkdir "$WORK" "$WORK/uv-cache"; then
    jam "repo-config" "Cannot create the fresh fixture and empty UV cache"
    cr_finalize
fi
pass "Fresh Linux fixture has the real base-image tools and no installed runtime"

phase 1 "prepare one clean merged source payload"
if ! capture "source-clone" -- git -c credential.helper= clone \
    --filter=blob:none --single-branch --branch dev "$SOURCE_REPO" "$CHECKOUT"; then
    jam "repo-config" "Source clone failed; this is not installer-failure evidence"
    cr_finalize
fi
if ! git -C "$CHECKOUT" merge-base --is-ancestor "$SOURCE_COMMIT" refs/remotes/origin/dev ||
    ! capture "source-select" -- git -C "$CHECKOUT" checkout --detach "$SOURCE_COMMIT"; then
    jam "repo-config" "Cannot select the requested merged dev commit"
    cr_finalize
fi
if ! SOURCE_STATUS="$(git -C "$CHECKOUT" status --porcelain=v1 --untracked-files=all --ignored)" ||
    [ ! -d "$CHECKOUT/.git" ] || [ -n "$SOURCE_STATUS" ]; then
    jam "repo-config" "Source is not a clean standalone checkout"
    cr_finalize
fi
_prepare_payload() {
    python3 -I -B "$CHECKOUT/tools/prepare_runtime_source.py" \
        --source "$CHECKOUT" --commit "$SOURCE_COMMIT" --plugin "$PLUGIN" \
        --output "$WORK/payloads" > "$CR_LOGDIR/prepared-source.json"
}
if ! capture "source-prepare" -- _prepare_payload; then
    jam "runtime-install" "Source preparation failed; this is not installer-failure evidence"
    cr_finalize
fi
if ! PAYLOAD="$(python3 -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["payload"])' \
    "$CR_LOGDIR/prepared-source.json")"; then
    jam "repo-config" "Preparation did not return a valid payload"
    cr_finalize
fi
cr_meta "source_commit" "$SOURCE_COMMIT"
cr_meta "plugin" "$PLUGIN"
pass "Canonical source preparation completed for the pinned merged commit"

phase 2 "run the real installer with an empty offline dependency cache"
# UV_OFFLINE conflicts with the installer's refresh flags. The runner isolates
# package feeds instead, leaving the real installer and UV arguments unchanged.
export UV_CACHE_DIR="$WORK/uv-cache" UV_PYTHON_DOWNLOADS=never
export UV_HTTP_TIMEOUT=5 UV_HTTP_RETRIES=0
if [ -n "$(find "$UV_CACHE_DIR" -mindepth 1 -print -quit)" ]; then
    jam "repo-config" "Dependency cache is not empty before the first installer invocation"
    cr_finalize
fi
# exec preserves the observed PID as the real installer's $$, not timeout's PID.
capture "source-install" -- timeout 120 bash -c \
    'printf "%s\n" "$$" > "$1"; shift; exec bash "$@"' \
    installer-pid "$CR_LOGDIR/installer.pid" "$PAYLOAD/scripts/install.sh" install "${INSTALL_ARGS[@]}"
INSTALL_RC=$?
printf '%s\n' "$INSTALL_RC" > "$CR_LOGDIR/installer.exit"
cr_meta "installer_exit" "$INSTALL_RC"
cr_meta "dependency_mode" "empty-cache-isolated-feed"
if [ "$INSTALL_RC" -eq 0 ] || [ "$INSTALL_RC" -ge 124 ]; then
    jam "runtime-install" "Installer returned $INSTALL_RC; success, timeout, launch failure or signal is not the expected failure"
    cr_finalize
fi
pass "Real installer returned a native nonzero status before the watchdog deadline"

phase 3 "verify native failure, lifecycle identity and absent publication"
if ! cp "$RUNTIME/lifecycle.log" "$CR_LOGDIR/lifecycle.log"; then
    jam "runtime-install" "Installer did not leave a readable durable lifecycle log"
    cr_finalize
fi
if ! capture "verify-failure" -- python3 -I -B "$SELF_DIR/verify.py" \
    "$CR_LOGDIR" "$RUNTIME" "$PLUGIN" "$SOURCE_COMMIT"; then
    jam "runtime-install" "Failure did not satisfy the dependency, PID, lifecycle or no-publication contract; see verify-failure.log"
    cr_finalize
fi
if ! SOURCE_STATUS="$(git -C "$CHECKOUT" status --porcelain=v1 --untracked-files=all --ignored)" ||
    [ -n "$SOURCE_STATUS" ]; then
    jam "repo-config" "The source checkout changed during the failure experiment"
    cr_finalize
fi
pass "Offline dependency resolution left exactly one install begin/fail pair for the native installer PID and no published runtime"
cr_finalize
