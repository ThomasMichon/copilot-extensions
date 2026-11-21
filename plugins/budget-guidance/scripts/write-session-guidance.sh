#!/usr/bin/env bash
set -uo pipefail

emit_session_start_json() {
    printf '{}'
}
trap 'emit_session_start_json' EXIT

root="${COPILOT_PLUGIN_ROOT:-${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)}}}"
script="$root/scripts/write_session_guidance.py"
provider="$root/scripts/bootstrap-uv.sh"
capture="$root/scripts/bootstrap_uv_capture.py"
request="$root/scripts/bootstrap-python-version.txt"
query="$root/scripts/installation-context/json-query.awk"
install_root="${HOME:+$HOME/.budget-guidance}"

unavailable() {
    printf 'budget-guidance: managed cue prerequisites are unavailable; run the owning scripts/install.sh install\n' >&2
}

if (($#)); then
    if [[ $# != 2 || "$1" != --install-root ]]; then
        unavailable
        exit 0
    fi
    install_root="$2"
fi
for asset in "$script" "$provider" "$capture" "$request" "$query" \
    "$root/scripts/daemon_instance_runtime.pyz"; do
    if [[ ! -f "$asset" ]]; then
        unavailable
        exit 0
    fi
done
uv="$(command -v uv 2>/dev/null || true)"
if [[ "$uv" != /* || ! -x "$uv" ]]; then
    unavailable
    exit 0
fi
python="${COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON:-}"
if [[ -z "$python" ]]; then
    manifest="$install_root/deploy-manifest.json"
    if [[ -z "$install_root" || ! -f "$manifest" || -L "$manifest" ]]; then
        unavailable
        exit 0
    fi
    size="$(LC_ALL=C wc -c 2>/dev/null < "$manifest")" || {
        unavailable
        exit 0
    }
    if [[ "$size" -gt 65536 ]]; then
        unavailable
        exit 0
    fi
    service="$(LC_ALL=C awk -f "$query" -v mode=deployment-string -v query_path=service "$manifest" 2>/dev/null && printf x)" || {
        unavailable
        exit 0
    }
    if [[ "${service%x}" != budget-guidance ]]; then
        unavailable
        exit 0
    fi
    python="$(LC_ALL=C awk -f "$query" -v mode=deployment-string -v query_path=bootstrap_python "$manifest" 2>/dev/null && printf x)" || {
        unavailable
        exit 0
    }
    python="${python%x}"
fi
if [[ "$python" != /* || ! -x "$python" || "$python" == *$'\n'* || "$python" == *$'\r'* ]]; then
    unavailable
    exit 0
fi
. "$provider" 2>/dev/null || {
    unavailable
    exit 0
}
status=0
invoke_uv_bootstrap_environment "$python" -I -S -B "$capture" \
    --timeout 6 --cleanup-reserve 1 --input-stdin --parent-pid "$$" -- \
    "$BASH" -c '
        set -uo pipefail
        . "$1" || exit 78
        selected="$(resolve_uv_bootstrap_python "$2" Inspect </dev/null)" || exit 78
        invoke_uv_bootstrap_environment "$selected" -I -S "$3"
    ' budget-guidance-cue "$provider" "$request" "$script" >/dev/null 2>/dev/null || status=$?
if [[ "$status" == 78 ]]; then
    unavailable
elif [[ "$status" != 0 ]]; then
    printf 'budget-guidance: session cue could not be written\n' >&2
fi
exit 0
