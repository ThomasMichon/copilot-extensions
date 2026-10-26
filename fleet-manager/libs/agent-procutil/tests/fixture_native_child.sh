#!/usr/bin/env bash
set -eu
python="$1"
package="$2"
area="$3"
fixture="$4"
set +m
mkfifo "$area/lifetime"
exec 8<>"$area/lifetime"
setsid bash --noprofile --norc -c '
    set -eu
    sleep 300 &
    printf "%s\n" "$!" > "$1/descendant"
    printf "ready\n" > "$1/ready"
    IFS= read -r _ || :
    kill -KILL -- -"$$"
' native-child "$area" 8>&- <"$area/lifetime" &
child="$!"
for ((attempt = 0; attempt < 1000; attempt++)); do
    if [[ -s "$area/ready" ]]; then break; fi
    sleep .01
done
[[ -s "$area/ready" && -p /proc/self/fd/8 ]]
exec "$python" -I -S -u "$fixture" "$package" "$child" "$area"
