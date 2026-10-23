#!/usr/bin/env bash
set -eu
python="$1"
package="$2"
area="$3"
fixture="$4"
coproc retained {
    exec setsid bash --noprofile --norc -c '
        set -eu
        sleep 300 &
        printf "%s\n" "$!" > "$1/descendant"
        printf "ready\n"
        IFS= read -r _ || :
        kill -KILL -- -"$$"
    ' native-child "$area"
}
child="$retained_PID"
IFS= read -r -u "${retained[0]}" ready
[[ "$ready" == ready ]]
exec 8>&"${retained[1]}"
exec "$python" -I -S -u "$fixture" "$package" "$child" "$area"
