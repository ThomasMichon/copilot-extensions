#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
root="${1:?legacy runtime root required}"
case "/$root/" in */../*) printf '%s\n' 'Legacy runtime paths must not contain raw .. components' >&2; exit 1 ;; esac
[[ "$root" == /* ]] || root="$PWD/$root"
helper="$SCRIPT_DIR/installer_engine_uninstall.py"
if [[ ! -f "$helper" ]]; then
    printf '%s\n' 'Required canonical finite removal helper is unavailable; refusing any purge fallback' >&2
    exit 1
fi
python=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -I -B -c 'import sys; assert sys.version_info >= (3,10)' >/dev/null 2>&1; then
        python="$(command -v "$candidate")"
        break
    fi
done
[[ -n "$python" ]] || { printf '%s\n' 'Legacy uninstall requires an existing external Python 3.10+ interpreter' >&2; exit 1; }
arguments=(-I -B "$helper" --payload-root "$(dirname -- "$SCRIPT_DIR")" \
    --plugin-id agent-machines --root "$root" --shell "$(command -v bash)" \
    --profile "$SCRIPT_DIR/uninstall-profile.json")
"$python" "${arguments[@]}" --check
[[ -d "$root" ]] || exit 0
. "$SCRIPT_DIR/legacy-transaction.sh"
INSTALL_DIR="$root"
_enter_install_lock || { printf '%s\n' 'Could not acquire the install transaction lock; refusing to proceed unlocked' >&2; exit 1; }
trap '_exit_install_lock' EXIT
"$python" "${arguments[@]}" --remove-runtime
