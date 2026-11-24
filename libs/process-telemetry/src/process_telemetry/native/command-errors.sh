#!/usr/bin/env bash
# Source by an absolute provider-qualified path. Import performs no external work.
_ce_native_write() (
    local command="$1" source="$2" cause="$3" result="$4" producer="$5" directory="$6"
    local support="${BASH_SOURCE[0]%/*}" code text message= uuid timestamp day active lockfd size first last archive count=0
    local LC_ALL=C
    [[ "$command" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$ &&
       "$source" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$ ]] || return 1
    [[ "$result" == null || "$result" =~ ^-?(0|[1-9][0-9]{0,9})$ ]] || return 1
    [[ "$cause" =~ ^[a-z][a-z-]{0,63}$ ]] || return 1
    while IFS=$'\t' read -r -n 1700 code text; do
        (( count+=1 )); (( count <= 32 )) || return 1
        if [[ "$code" == "$cause" ]]; then message="$text"; break; fi
    done < "$support/command-causes.tsv"
    [[ -n "$message" && ${#message} -le 1600 && "$message" != *[![:print:]]* ]] || return 1
    # The catalog is shipped producer text, not an arbitrary message/stream API.
    [[ "$message" != *['"\']* ]] || return 1
    command -v flock >/dev/null && command -v stat >/dev/null || return 1
    . "$support/private-paths.sh" || return 1
    ce_private_directory_prepare "$directory" || return 1
    umask 077
    active="$directory/command-errors.jsonl"
    local lock="$directory/.command-errors.lock"
    if [[ ! -e "$lock" && ! -L "$lock" ]]; then
        (set -o noclobber; : > "$lock") 2>/dev/null || :
    fi
    ce_private_path_check "$lock" file || return 1
    exec {lockfd}<>"$lock" || return 1
    flock -x -w 2 "$lockfd" || return 1
    [[ "$(stat -c '%d:%i' -- "$lock")" == \
       "$(stat -Lc '%d:%i' -- "/proc/self/fd/$lockfd")" ]] || return 1
    IFS= read -r uuid < /proc/sys/kernel/random/uuid || return 1
    uuid="${uuid//-/}"
    [[ "$uuid" =~ ^[0-9a-f]{32}$ ]] || return 1
    timestamp="$(date -u '+%Y-%m-%dT%H:%M:%S+00:00')" || return 1
    [[ "$timestamp" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+00:00$ ]] || return 1
    day="${timestamp:0:10}"
    if [[ -e "$active" || -L "$active" ]]; then
        ce_private_path_check "$active" file || return 1
        size="$(stat -c '%s' -- "$active")" || return 1
        [[ "$size" =~ ^[0-9]{1,18}$ ]] || return 1
        if (( size > 0 )); then
            first=; IFS= read -r -n 4096 first < "$active" || :
            last="$(tail -c 1 -- "$active"; printf .)" || return 1
            if (( size >= 5000000 )) || [[ "$last" != $'\n.' ||
                ! "$first" =~ \"ts\"[[:space:]]*:[[:space:]]*\""$day"T ]]; then
                archive="$(date -u '+%Y%m%dT%H%M%S000000Z')" || return 1
                [[ "$archive" =~ ^[0-9]{8}T[0-9]{12}Z$ ]] || return 1
                archive="$archive-$uuid"
                [[ ! -e "$directory/command-errors.$archive.jsonl" ]] || return 1
                mv -- "$active" "$directory/command-errors.$archive.jsonl" || return 1
            fi
        fi
    fi
    if [[ ! -e "$active" && ! -L "$active" ]]; then
        (set -o noclobber; : > "$active") 2>/dev/null || return 1
    fi
    ce_private_path_check "$active" file || return 1
    printf '{"schema":"command-error.v1","ts":"%s","command":"%s","source":"%s","phase":"entry","code":"%s","message":"%s","exit_code":%s,"correlation_id":"%s","completeness":"producer-shaped","pid":%s}\n' \
        "$timestamp" "$command" "$source" "$cause" "$message" "$result" "$uuid" "$producer" >> "$active"
)

command_error_write() {
    # A best-effort diagnostic never becomes the command's primary result.
    local directory="${5:-}"
    if [[ -z "$directory" && "${HOME:-}" == /* ]]; then
        directory="${HOME%/}/.copilot-extensions/logs/diagnostics/commands"
    fi
    if [[ -n "$directory" && $# -ge 4 && $# -le 5 ]] &&
        _ce_native_write "$1" "$2" "$3" "$4" "$BASHPID" \
            "$directory" 2>/dev/null; then
        :
    else
        (printf '%s\n' 'command evidence unavailable (native writer); inspect command-error discovery' >&2) || :
    fi
    return 0
}
