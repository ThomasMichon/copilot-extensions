"""Bounded, UUID-scoped abandoned-input cleanup for trusted launches."""

from __future__ import annotations

import shlex


def abandoned_cleanup_script(launch_dir: str, executable_root: str | None = None) -> str:
    return (
        f"launch_dir={shlex.quote(launch_dir)}\n"
        f"executable_root={shlex.quote(executable_root or launch_dir)}\n"
    ) + r"""
old_input() {
    test ! -e "$1" || test -n "$(find "$1" -maxdepth 0 -mmin +60 -print -quit)"
}
process_start() {
    test -r "/proc/$1/stat" || return 1
    sed 's/.*) //' "/proc/$1/stat" | awk '{print $20}'
}
for item in "$launch_dir"/*.env "$launch_dir"/*.env.d "$executable_root"/*.env.d; do
    test -e "$item" || continue
    leaf="${item##*/}"
    stem="${leaf%.d}"
    stem="${stem%.env}"
    case "$stem" in ''|*[!0-9a-f]*) continue;; esac
    test "${#stem}" -eq 32 || continue
    owner="$launch_dir/$stem.env"
    directory="$executable_root/$stem.env.d"
    auth_directory="$owner.d"
    test ! -L "$owner" && test ! -L "$directory" && test ! -L "$auth_directory" || continue
    test ! -e "$owner" || test -f "$owner" || continue
    test ! -e "$directory" || test -d "$directory" || continue
    if test -f "$directory/active.pid"; then
        pid="" recorded_start=""
        read -r pid recorded_start < "$directory/active.pid" || true
        case "$pid" in ''|*[!0-9]*) continue;; esac
        current_start="$(process_start "$pid" || true)"
        if test -n "$recorded_start" && test "$current_start" = "$recorded_start"; then
            continue
        fi
    fi
    old_input "$owner" && old_input "$directory" && old_input "$auth_directory" || continue
    rm -f -- "$owner" "$auth_directory/auth.env" "$directory/copilot" \
        "$directory/result.json" "$directory/consumed" "$directory/active.pid" "$directory/active.pid.tmp"
    rmdir -- "$directory" 2>/dev/null || true
    rmdir -- "$auth_directory" 2>/dev/null || true
done
"""
