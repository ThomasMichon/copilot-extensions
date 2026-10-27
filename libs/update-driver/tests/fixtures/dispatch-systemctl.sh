#!/usr/bin/env bash
set -eu
base="$(dirname -- "${BASH_SOURCE[0]}")"
printf '{"operation":"systemctl","arguments":"%s"}\n' "$*" >>"$base/effects.jsonl"
[[ "${1:-}" == --user ]] || { echo 'fixture requires user service scope' >&2; exit 91; }
shift
operation="${1:-}"
shift
if [[ "$operation" == daemon-reload && $# -eq 0 ]]; then exit 0; fi
unit="${1:-}"
case "$unit" in
    agent-dispatch.service|agent-dispatch-supervisor.service) ;;
    *) echo 'fixture refuses foreign service' >&2; exit 91 ;;
esac
shift
if [[ "$operation" == show && "$*" == '-p MainPID --value' ]]; then echo 0; exit 0; fi
[[ $# -eq 0 ]] || { echo 'fixture refuses additional service arguments' >&2; exit 91; }
if [[ "$operation" == disable && "$unit" == agent-dispatch-supervisor.service &&
      -f "$base/mode" && "$(cat "$base/mode")" == supervisor-disable-denied ]]; then
    printf '{"operation":"supervisor-disable-denied"}\n' >>"$base/effects.jsonl"
    echo 'fixture supervisor disable denied' >&2
    exit 37
fi
case "$operation" in
    enable) printf enabled >"$base/$unit.enabled" ;;
    disable) printf disabled >"$base/$unit.enabled" ;;
    start|restart) printf active >"$base/$unit.active" ;;
    stop) printf inactive >"$base/$unit.active" ;;
    reset-failed) ;;
    is-active)
        if [[ -f "$base/$unit.active" ]]; then cat "$base/$unit.active"; else echo inactive; fi
        [[ -f "$base/$unit.active" && "$(cat "$base/$unit.active")" == active ]] ;;
    is-enabled)
        if [[ -f "$base/$unit.enabled" ]]; then cat "$base/$unit.enabled"; else echo absent; fi
        [[ -f "$base/$unit.enabled" && "$(cat "$base/$unit.enabled")" == enabled ]] ;;
    *) echo 'fixture refuses unexpected service operation' >&2; exit 91 ;;
esac
