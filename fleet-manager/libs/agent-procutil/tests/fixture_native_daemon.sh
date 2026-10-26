#!/usr/bin/env bash
set -eu
if [[ "$1" == --daemon ]]; then exec sleep 300; fi
helper="$1"
area="$2"
source "$helper"
start_native_daemon "$0" "$area" "$area/daemon.log" --daemon
printf '%s\n' "$__native_daemon_pid" >"$area/daemon"
sleep 300 &
printf '%s\n' "$!" >"$area/control"
wait
