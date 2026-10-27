#!/usr/bin/env bash
set -euo pipefail
[[ "$#" == 1 ]] || { printf '%s\n' 'Legacy uninstall requires exactly one runtime root; options are unsupported' >&2; exit 2; }
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
root="${1:?legacy runtime root required}"
case "/$root/" in */../*) printf '%s\n' 'Legacy runtime paths must not contain raw .. components' >&2; exit 1 ;; esac
[[ "$root" == /* ]] || root="$PWD/$root"
helper="$SCRIPT_DIR/installer_engine_uninstall.py"
if [[ ! -f "$helper" ]]; then
    printf '%s\n' 'Required canonical finite removal helper is unavailable; refusing any purge fallback' >&2
    exit 1
fi
_uninstall_external_python() {
    local root="${1:?runtime root required}" candidate selected probe
    probe='import sys
from pathlib import Path
assert sys.version_info >= (3, 10)
root = Path(sys.argv[1]).absolute()
assert ".." not in root.parts
roots = (root, root.resolve())
paths = (sys.argv[2], sys.executable, sys.prefix, sys.base_prefix,
         getattr(sys, "_base_executable", sys.executable))
for value in paths:
    path = Path(value).absolute()
    for actual in (path, path.resolve(strict=True)):
        assert all(not actual.is_relative_to(boundary) for boundary in roots)
sys.stdout.write(str(Path(sys.executable).resolve(strict=True)))'
    while IFS= read -r candidate; do
        case "$candidate" in "$root"/*|*WindowsApps*) continue ;; esac
        case "${OSTYPE:-}" in
            msys*|cygwin*) [[ "$candidate" == *.exe || ! -f "$candidate.exe" ]] || candidate="$candidate.exe" ;;
        esac
        selected="$("$candidate" -I -B -c "$probe" "$root" "$candidate" 2>/dev/null)" || continue
        [[ -n "$selected" && -x "$selected" ]] || continue
        printf '%s' "$selected"
        return 0
    done < <(type -aP python3 python 2>/dev/null)
    printf '%s\n' 'Uninstall requires an existing external Python outside runtime and prefix closure' >&2
    return 1
}
python="$(_uninstall_external_python "$root")" || exit 1
[[ -n "$python" ]] || { printf '%s\n' 'Legacy uninstall requires an existing external Python 3.10+ interpreter' >&2; exit 1; }
arguments=(-I -B "$helper" --payload-root "$(dirname -- "$SCRIPT_DIR")" \
    --plugin-id agent-containers --root "$root" --shell "$(command -v bash)" \
    --profile "$SCRIPT_DIR/uninstall-profile.json")
"$python" "${arguments[@]}" --check
[[ -d "$root" ]] || exit 0
. "$SCRIPT_DIR/legacy-transaction.sh"
INSTALL_DIR="$root"
_enter_install_lock || { printf '%s\n' 'Could not acquire the install transaction lock; refusing to proceed unlocked' >&2; exit 1; }
trap '_exit_install_lock' EXIT
"$python" "${arguments[@]}" --remove-runtime
