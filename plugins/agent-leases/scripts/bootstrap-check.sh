#!/usr/bin/env bash
# Session-start version drift check; first install remains explicit.

install_dir="$HOME/.agent-leases"
manifest="$install_dir/deploy-manifest.json"
binstub="$HOME/.local/bin/agent-leases"
[ -f "$manifest" ] || exit 0
python_cmd="$(command -v python3 || command -v python || true)"
[ -n "$python_cmd" ] || exit 0
plugin_dir="$("$python_cmd" -c 'import json,sys;print(json.load(open(sys.argv[1]))["source"]["path"])' "$manifest" 2>/dev/null)"
deployed="$("$python_cmd" -c 'import json,sys;print(json.load(open(sys.argv[1]))["source"]["version"])' "$manifest" 2>/dev/null)"
[ -d "$plugin_dir" ] || exit 0
current="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$plugin_dir/pyproject.toml" | head -n1)"
[ -x "$binstub" ] && [ "$deployed" = "$current" ] && exit 0
nohup bash "$plugin_dir/scripts/install.sh" update >/dev/null 2>&1 &
