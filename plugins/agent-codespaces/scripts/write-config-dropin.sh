#!/usr/bin/env bash
# Write one attributed config-provider pointer without modifying other entries.
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 <name@marketplace> <plugin-root> <target-config>" >&2
  exit 2
fi

plugin=$1
plugin_root=$2
target=$3

for value in "$plugin" "$plugin_root" "$target"; do
  if [[ "$value" =~ [[:cntrl:]] ]]; then
    echo "plugin, plugin root, and target must not contain control characters" >&2
    exit 2
  fi
done

if [[ ! "$plugin" =~ ^[^@/\\[:space:]]+@[^@/\\[:space:]]+$ ]]; then
  echo "plugin must be an exact name@marketplace identity" >&2
  exit 2
fi

if [[ ! -d "$plugin_root" ]]; then
  echo "plugin root must be an existing directory" >&2
  exit 2
fi
if [[ ! -f "$target" || -L "$target" ]]; then
  echo "target must be an existing regular file" >&2
  exit 2
fi

canonical_path() {
  if command -v realpath >/dev/null 2>&1; then
    realpath "$1"
    return
  fi
  if [[ -d "$1" ]]; then
    (cd "$1" && pwd -P)
    return
  fi
  local parent
  parent=$(cd "$(dirname "$1")" && pwd -P)
  printf '%s/%s\n' "$parent" "$(basename "$1")"
}

plugin_root=$(canonical_path "$plugin_root")
target=$(canonical_path "$target")
case "$target" in
  "$plugin_root"/*) ;;
  *)
    echo "target must be contained by plugin root" >&2
    exit 2
    ;;
esac

name=${plugin%@*}
marketplace=${plugin#*@}
agent_home=${AGENT_HOME:-"$HOME"}
directory="$agent_home/.agent-codespaces/config.d"
entry="$directory/$name@$marketplace.json"
mkdir -p "$directory"

json_escape() {
  sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' <<<"$1"
}

temporary="$directory/.$(basename "$entry").$$.${RANDOM}.tmp"
trap 'rm -f "$temporary"' EXIT
printf '{"schema_version":1,"plugin":"%s","plugin_root":"%s","target":"%s"}\n' \
  "$(json_escape "$plugin")" \
  "$(json_escape "$plugin_root")" \
  "$(json_escape "$target")" >"$temporary"
mv -f "$temporary" "$entry"
trap - EXIT
printf '%s\n' "$entry"
