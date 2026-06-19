#!/usr/bin/env bash
# context-handoff extension installer -- payload runtime (non-Python).
#
# Deploys the context-handoff Copilot CLI session extension. The runtime is a
# single JavaScript extension file plus a Copilot CLI settings flag -- no venv,
# binstub, or service. The install contract's payload-runtime variant applies
# (see docs/install-contract.md).
#
# Actions:
#   install / update  Copy extension.mjs to ~/.copilot/extensions/, ensure
#                     experimental:true in settings.json, write the manifest.
#   uninstall         Remove the deployed extension + manifest (experimental
#                     flag left intact).
#   status            Report deployment state.
#
# Run from the plugin source dir (marketplace vendor copy or local checkout):
#   bash scripts/install.sh update
#   bash scripts/install.sh status
set -euo pipefail

ACTION="${1:-status}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PAYLOAD_SRC="$PLUGIN_DIR/extension/context-handoff/extension.mjs"
EXT_DIR="$HOME/.copilot/extensions/context-handoff"
EXT_TARGET="$EXT_DIR/extension.mjs"
RUNTIME_DIR="$HOME/.context-handoff"
SETTINGS_PATH="$HOME/.copilot/settings.json"

_ok()   { printf '[OK]   %s\n' "$1"; }
_info() { printf '[..]   %s\n' "$1"; }
_warn() { printf '[WARN] %s\n' "$1"; }
_err()  { printf '[FAIL] %s\n' "$1"; }

_plugin_version() {
    if [[ -f "$PLUGIN_DIR/plugin.json" ]]; then
        python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version","0.0.0"))' \
            "$PLUGIN_DIR/plugin.json" 2>/dev/null || echo "0.0.0"
    else
        echo "0.0.0"
    fi
}

_git_info() {
    local path="$1"
    local commit branch dirty
    commit=$(git -C "$path" rev-parse --short HEAD 2>/dev/null || echo "unknown")
    branch=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    dirty="false"
    if [[ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]]; then
        dirty="true"
    fi
    echo "$commit $branch $dirty"
}

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
# A runtime footprint's source is inferred from where the installer runs.
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local.
_source_kind() {
    case "$(printf '%s' "$1" | tr '\\' '/')" in
        */.copilot/installed-plugins/*) printf 'marketplace' ;;
        *) printf 'local' ;;
    esac
}
# === end install-contract:v3 source-kind ===

# Unified schema_version 3 manifest writer. Self-contained per plugin (no shared
# module -- plugins are pulled independently from the marketplace). Records the
# source footprint (local vs marketplace) and is written atomically (temp+move).
# Payload-runtime variant: no venv; `runtime` is the extension load path.
_write_deploy_manifest() {
    local manifest="$RUNTIME_DIR/deploy-manifest.json"
    local kind ver
    kind="$(_source_kind "$PLUGIN_DIR")"
    ver="$(_plugin_version)"

    # Git provenance only applies to a local checkout.
    local commit="null" branch="null" dirty="false"
    if [[ "$kind" == "local" ]]; then
        local c b d
        read -r c b d <<< "$(_git_info "$(cd "$PLUGIN_DIR/.." && pwd)")"
        commit="\"$c\""; branch="\"$b\""; dirty="$d"
    fi

    mkdir -p "$RUNTIME_DIR"
    local tmp="$manifest.tmp"
    cat > "$tmp" << EOF
{
  "schema_version": 3,
  "service": "context-handoff",
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$kind",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "context-handoff",
    "version": "$ver",
    "commit": $commit,
    "branch": $branch,
    "dirty": $dirty
  },
  "venv": null,
  "runtime": "extension",
  "extension_path": "$EXT_TARGET"
}
EOF
    mv -f "$tmp" "$manifest"
    _ok "Deploy manifest written (source: $kind, version: $ver)"
}

# Ensure experimental:true in settings.json. Extensions are gated behind it --
# the COPILOT_FEATURE_FLAGS env var alone is insufficient. Idempotent; preserves
# all other settings; atomic temp+move write.
_set_experimental_flag() {
    if [[ -f "$SETTINGS_PATH" ]]; then
        if python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("experimental") is True else 1)' \
            "$SETTINGS_PATH" 2>/dev/null; then
            _info "experimental:true already set in settings.json"
            return
        fi
        local tmp="$SETTINGS_PATH.tmp"
        if python3 -c '
import json, sys
p = sys.argv[1]
try:
    d = json.load(open(p))
except Exception:
    sys.exit(2)
d["experimental"] = True
json.dump(d, open(sys.argv[2], "w"), indent=2)
' "$SETTINGS_PATH" "$tmp" 2>/dev/null; then
            mv -f "$tmp" "$SETTINGS_PATH"
            _ok "Set experimental:true in settings.json"
        else
            rm -f "$tmp"
            _warn "Could not parse settings.json -- set experimental:true manually"
        fi
    else
        mkdir -p "$(dirname "$SETTINGS_PATH")"
        printf '{\n  "experimental": true\n}\n' > "$SETTINGS_PATH"
        _ok "Created settings.json with experimental:true"
    fi
}

_install() {
    if [[ ! -f "$PAYLOAD_SRC" ]]; then
        _err "Extension payload not found at $PAYLOAD_SRC"
        exit 1
    fi
    mkdir -p "$EXT_DIR"
    cp -f "$PAYLOAD_SRC" "$EXT_TARGET"
    _ok "Deployed extension.mjs -> $EXT_TARGET"
    _set_experimental_flag
    _write_deploy_manifest
    _info "Activates on the NEXT Copilot CLI session (extensions are scanned at startup)."
}

_uninstall() {
    [[ -f "$EXT_TARGET" ]] && { rm -f "$EXT_TARGET"; _ok "Removed $EXT_TARGET"; }
    [[ -d "$EXT_DIR" ]] && rmdir "$EXT_DIR" 2>/dev/null || true
    local manifest="$RUNTIME_DIR/deploy-manifest.json"
    [[ -f "$manifest" ]] && { rm -f "$manifest"; _ok "Removed deploy manifest"; }
    [[ -d "$RUNTIME_DIR" ]] && rmdir "$RUNTIME_DIR" 2>/dev/null || true
    _info "Left experimental:true in settings.json untouched (other extensions may need it)."
}

_status() {
    echo "context-handoff extension status"
    echo "  source dir   : $PLUGIN_DIR"
    echo "  source kind  : $(_source_kind "$PLUGIN_DIR")"
    echo "  plugin ver   : $(_plugin_version)"
    if [[ -f "$EXT_TARGET" ]]; then _ok "deployed     : $EXT_TARGET"; else _warn "deployed     : NOT deployed ($EXT_TARGET missing)"; fi
    if [[ -f "$SETTINGS_PATH" ]] && python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("experimental") is True else 1)' "$SETTINGS_PATH" 2>/dev/null; then
        _ok "experimental : true"
    else
        _warn "experimental : NOT set -- extension will not load"
    fi
    local manifest="$RUNTIME_DIR/deploy-manifest.json"
    if [[ -f "$manifest" ]]; then _ok "manifest     : $manifest"; else _warn "manifest     : none"; fi
}

case "$ACTION" in
    install|update) _install ;;
    uninstall)      _uninstall ;;
    status)         _status ;;
    *) _err "Unknown action: $ACTION (expected install|update|uninstall|status)"; exit 2 ;;
esac
