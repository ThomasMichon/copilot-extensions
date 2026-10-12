#!/usr/bin/env bash
# Tier-P/F1: released payload, explicit repository/cell adoption, no engine.
set -uo pipefail
_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"
MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
RELEASE_REF="${CR_RELEASE_REF:-main}"
RELEASE="$HOME/released-marketplace"
REPO="$HOME/ai-repo"
PAYLOAD="$RELEASE/plugins/agent-index"
PAYLOAD_CMD="$PAYLOAD/bin/agent-index"
WITNESS="$_SELF_DIR/witness.py"
: "${CR_SCENARIO_NAME:=agent-index-solo}"
export CR_SCENARIO_NAME AGENT_INDEX_NO_ENGINE_DEPS=1
if [ -n "${CR_UV_INDEX:-}" ]; then
    export UV_INDEX_URL="$CR_UV_INDEX" UV_DEFAULT_INDEX="$CR_UV_INDEX"
fi
cr_init
cr_meta plugin agent-index
cr_meta engine_required no
cr_meta release_ref "$RELEASE_REF"

_stop() {
    jam "$1" "$2" "${3:-Stop at the literal obstacle; do not repair the product in the witness.}"
    cr_meta reached_phase "$CR_CUR_PHASE"
    cr_finalize
}
_run() {
    local label="$1"; shift
    capture "$label" -- "$@" || _stop index-config "$label failed (see cr-logs/$label.log)"
}
_check() {
    local label="$1" description="$2"; shift 2
    _run "$label" python3 "$WITNESS" "$@"
    pass "$description"
}
_json() { python3 "$WITNESS" field "$CR_LOGDIR/$1.log" "$2"; }
_state() {
    local label="$1" state="$2" runtime="${3:-}"
    _run "$label" "$PAYLOAD_CMD" status
    _check "$label-check" "$label reports $state/$runtime" state "$CR_LOGDIR/$label.log" "$state" "$runtime"
}
_no_runtime() {
    _check "$1" "no runtime, service, engine or scheduler artifacts" dormant "$HOME" "${CELL_ROOT:-}"
}

phase 0 "environment (fresh machine)"
envdump
_no_runtime fresh

phase 1 "install ONLY agent-index from a released marketplace"
if [ -n "${CR_HARNESS_MOUNT:-}" ]; then
    _run candidate-copy cp -a "$CR_HARNESS_MOUNT" "$RELEASE"
    _run candidate-source python3 "$WITNESS" candidate "$RELEASE" "${CR_CANDIDATE_HEAD:-}"
    RELEASE_SHA="$CR_CANDIDATE_HEAD"
    cr_meta product_kind materialized-candidate-not-shipped
else
_run release-clone git clone --quiet --depth 1 --branch main "https://github.com/$MARKETPLACE_REPO.git" "$RELEASE"
if [ "$RELEASE_REF" != main ]; then
    _run release-fetch git -C "$RELEASE" fetch --quiet --depth 1 origin "$RELEASE_REF"
    _run release-checkout git -C "$RELEASE" checkout --quiet --detach FETCH_HEAD
fi
RELEASE_SHA="$(git -C "$RELEASE" rev-parse HEAD)" || _stop repo-config "release HEAD unavailable"
if [ "$RELEASE_REF" != main ] && [ "$RELEASE_SHA" != "$RELEASE_REF" ]; then
    _stop repo-config "release pin did not resolve to the requested commit"
fi
fi
cr_meta release_sha "$RELEASE_SHA"
mkdir -p "$HOME/.copilot"
_run marketplace-add copilot plugin marketplace add "$RELEASE"
_run install copilot plugin install "agent-index@$MARKETPLACE_NAME"
_check provenance "installed payload and vendored engine pair match released bytes" provenance "$RELEASE" "$PAYLOAD" "${CR_EXPECT_VERSION:-}"
VERSION="$(python3 "$WITNESS" field "$PAYLOAD/plugin.json" version)" || _stop index-config "payload version missing"
cr_meta payload_version "$VERSION"
_check solo-payload "only Index installed; CLI-direct scope hook remains present" payload "$PAYLOAD"

phase 2 "inactive startup is dormant; opted-in startup stamps only metadata"
_run fixture-repo git init --quiet "$REPO"
cd "$REPO" || _stop repo-config "fixture repository unavailable"
export AGENT_INDEX_REPO="$REPO"
_state status-inactive inactive
_no_runtime inactive-nonmutating
mkdir -p "$REPO/.agent-index"
printf 'corpus:\n  sources:\n    - name: git:fixture\n      repo: "%s"\n' "$REPO" > "$REPO/.agent-index/config.yaml"
_run session-first copilot -p "Reply with the single word: ready." --allow-all --experimental --plugin-dir "$PAYLOAD"
_check session-stamped-only "hook stamps metadata/binstub without deployment" stamped "$HOME" "$PAYLOAD"
_state status-opted-in setup_required stamped
if capture setup-no-choice -- "$PAYLOAD_CMD" setup --yes --json; then
    _stop index-config "setup without an explicit role unexpectedly succeeded"
else
    rc=$?
    [ "$rc" = 2 ] || _stop index-config "setup without role returned $rc, expected 2"
    _check setup-choice-check "setup requires an explicit role without provisioning" state "$CR_LOGDIR/setup-no-choice.log" setup_required stamped
fi

phase 3 "explicit installation-mode policy and activation"
# These are the shipped management primitives, not a forged context/receipt.
_run cell-adopt python3 "$WITNESS" adopt "$RELEASE" "$PAYLOAD" "$MARKETPLACE_NAME" "$VERSION"
CONTEXT="$(python3 "$WITNESS" field "$HOME/cell-witness.json" context)" || _stop index-config "cell context missing"
CELL_ROOT="$(python3 "$WITNESS" field "$HOME/cell-witness.json" pluginRoot)" || _stop index-config "cell root missing"
MARKETPLACE_ID="$(python3 "$WITNESS" field "$HOME/cell-witness.json" marketplaceId)" || _stop index-config "marketplace identity missing"
export COPILOT_EXTENSIONS_CONTEXT="$CONTEXT"
cr_meta installation_mode namespaced
_state status-cell setup_required absent
_check cell-nonmutating "cell activation creates no runtime or service" dormant "$HOME" "$CELL_ROOT" stamped
# Roles are resolved from canonical repo configuration before runtime creation.
cp "$REPO/.agent-index/config.yaml" "$HOME/corpus-only.yaml"
printf 'indexer:\n  machine: remote-fixture\n  ssh: remote-fixture\n' > "$REPO/.agent-index/config.yaml"
_run role-client "$PAYLOAD_CMD" role --json
_check client-role-check "configured client role is explicit without a local service" role "$CR_LOGDIR/role-client.log" client
printf 'indexer:\n  machine: "%s"\n' "$(hostname -s)" > "$REPO/.agent-index/config.yaml"
_run role-host "$PAYLOAD_CMD" role --json
_check host-role-check "configured host role is explicit before installation" role "$CR_LOGDIR/role-host.log" host
cp "$HOME/corpus-only.yaml" "$REPO/.agent-index/config.yaml"
_check roles-nonmutating "role reads create no runtime or service" dormant "$HOME" "$CELL_ROOT" stamped

phase 4 "owned incomplete slot and explicit setup/provision"
_run partial-slot python3 "$WITNESS" partial "$PAYLOAD" "$CONTEXT" "$MARKETPLACE_ID" "$VERSION" "$CELL_ROOT"
_state status-partial setup_required broken
if capture search-before-setup -- "$PAYLOAD_CMD" search "cold start" --json; then
    _stop index-config "operational read bypassed explicit setup"
else
    rc=$?
    [ "$rc" = 2 ] || _stop index-config "pre-setup read returned $rc, expected 2"
    _check search-gate-check "incomplete slot cannot dispatch or provision" state "$CR_LOGDIR/search-before-setup.log" setup_required broken
fi
_run setup-host "$PAYLOAD_CMD" setup --single --yes --repo "$REPO" --json
_check setup-role-check "explicit setup selected the host role" role "$CR_LOGDIR/setup-host.log" host
# The first admitted setup invocation must provision through the real runtime gate.
grep -q "::agent-provisioning::" "$CR_LOGDIR/setup-host.log" ||
    _stop index-config "first-use setup did not reach the runtime provisioning gate"
_check runtime-ready "real completed venv imports from its slot and reports the released CLI version" runtime "$CELL_ROOT" "$VERSION"

phase 5 "stock PATH payload command, service health and MCP read surfaces"
_run stock-path bash -lc 'printf "%s\n" "$PATH"; command -v python3; test -x "$1"; "$1" --version' _ "$PAYLOAD_CMD"
_check cli-version-check "payload-local command works on unmodified login-shell PATH" version "$CR_LOGDIR/stock-path.log" "$VERSION"
_run read-status "$PAYLOAD_CMD" status
_run read-engine-status "$PAYLOAD_CMD" engine status
_run read-role "$PAYLOAD_CMD" role --json
_check read-role-check "installed role remains host" role "$CR_LOGDIR/read-role.log" host
_check service-health "one live installation-owned service answers real /health and /status" health "$CELL_ROOT" "$MARKETPLACE_ID" "$VERSION"
RUNTIME_PY="$(python3 "$WITNESS" interpreter "$CELL_ROOT")" || _stop path-binstub "no selected runtime interpreter"
_check mcp-tools "direct MCP registers the five documented tools without a model build" mcp "$RUNTIME_PY"
# A completed role-specific slot is not authority to adopt a different/no role.
mv "$CELL_ROOT/config/config.yaml" "$CELL_ROOT/config/config.yaml.saved"
mv "$REPO/.copilot-extensions/agent-index/config.yaml" "$REPO/.copilot-extensions/agent-index/config.yaml.saved"
if capture status-complete-no-role -- "$PAYLOAD_CMD" status; then
    _stop index-config "completed host profile unexpectedly accepted no-role configuration"
else
    rc=$?
    [ "$rc" = 126 ] || _stop index-config "no-role profile rejection returned $rc, expected 126"
    grep -q "dependency profile does not match" "$CR_LOGDIR/status-complete-no-role.log" ||
        _stop index-config "no-role failure was not the documented strict profile boundary"
    pass "completed host slot rejects no-role dispatch through strict dependency-profile validation"
fi
mv "$CELL_ROOT/config/config.yaml.saved" "$CELL_ROOT/config/config.yaml"
mv "$REPO/.copilot-extensions/agent-index/config.yaml.saved" "$REPO/.copilot-extensions/agent-index/config.yaml"
_check engine-absent "engine, models, torch and scheduler artifacts remain absent" engine-absent "$HOME" "$RUNTIME_PY"
cr_meta reached_phase "$CR_CUR_PHASE"
cr_finalize
