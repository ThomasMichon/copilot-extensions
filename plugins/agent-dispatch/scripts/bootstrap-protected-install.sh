#!/bin/bash -p
# protected-installation-provider: linux-root/v1
# An installation owner's trusted native launcher must select this installed file.
# Do not elevate a controller-owned copy of this script.
set -euo pipefail

if [[ $# != 0 || $UID != 0 || $EUID != 0 || $OSTYPE != linux-gnu* ]]; then
    printf '%s\n' 'Protected installation requires its Linux root-owned native entry, without arguments.' >&2
    exit 2
fi

export PATH=/usr/bin:/bin
export LC_ALL=C
unset CDPATH ENV BASH_ENV
umask 077

protected_path() {
    local path="$1" kind="$2" info owner mode type filesystem
    [[ $path == /* && $path != *$'\n'* && $path != *$'\r'* ]] || return 1
    while :; do
        [[ ! -L "$path" ]] || return 1
        info="$(/usr/bin/stat --format='%u %a %F' -- "$path")" || return 1
        read -r owner mode type <<<"$info"
        [[ $owner == 0 && $mode =~ ^[0-7]{3,4}$ ]] || return 1
        (( (8#$mode & 8#022) == 0 )) || return 1
        [[ $type == "$kind" ]] || return 1
        filesystem="$(/usr/bin/stat -f --format='%T' -- "$path")" || return 1
        case "$filesystem" in
            ext2/ext3|xfs|btrfs|tmpfs) ;;
            *) return 1 ;;
        esac
        [[ $path != / ]] || break
        path="${path%/*}"
        [[ -n $path ]] || path=/
        kind=directory
    done
}

entry="${BASH_SOURCE[0]}"
[[ $entry == /* && $entry != *'/../'* && $entry != *'/./'* && $entry != *'//'* ]] || exit 2
protected_path "$entry" 'regular file' || exit 2
directory="${entry%/*}"
provider="$directory/installer_protected_source.py"
protected_path "$provider" 'regular file' || exit 2
protected_path "$directory/installer_native_executable.py" 'regular file' || exit 2

# These are OS installation tools, never PATH, policy or controller selections.
python="$(/usr/bin/readlink -e -- /usr/bin/python3)" || exit 2
protected_path "$python" 'regular file' || exit 2
cd /
exec /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    "$python" -I -S -B "$provider" </dev/null
