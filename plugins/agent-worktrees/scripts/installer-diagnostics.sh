# Sourced native bootstrap diagnostics; does not install or select Python.
INSTALLER_DIAGNOSTICS_NATIVE_HELPER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/installer-diagnostics.ps1"
installer_diagnostics_present() (
    # One finite native command, never a pipeline or a descendant-owning helper.
    # The caller keeps its descriptor modes; only our disposable writer can block.
    local writer
    local stop_writer=(kill -s KILL)
    case "${OSTYPE:-}" in
        msys*|cygwin*)
            # Emulated SIGKILL alone cannot interrupt Windows pipe WriteFile.
            [[ -x /usr/bin/kill ]] || exit 0
            stop_writer=(/usr/bin/kill -f -s KILL) ;;
    esac
    trap '' PIPE
    "$@" 2>/dev/null &
    writer=$!
    trap '"${stop_writer[@]}" "$writer" 2>/dev/null || :; wait "$writer" 2>/dev/null || :' EXIT
    trap 'exit 0' HUP INT TERM
    sleep 0.1 >/dev/null 2>&1 || :
    exit 0
) 2>/dev/null

installer_diagnostics_warn() {
    local directory="${COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY:-}"
    export COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING=1
    if [[ -n "${INSTALLER_DIAGNOSTICS_LOG:-}" && -n "$directory" ]]; then
        if installer_diagnostics_path "$directory/missing-diagnostics.json"; then
            (umask 077; printf '%s\n' '{"missing_diagnostics":true}' \
                > "$directory/missing-diagnostics.json") 2>/dev/null || :
        fi
    fi
    installer_diagnostics_present printf '%s\n' \
        'WARNING: installer diagnostics unavailable; continuing with missing diagnostics.' >&2
}

installer_diagnostics_path() {
    local path="$1" parent
    while [[ -n "$path" && "$path" != / ]]; do
        [[ ! -L "$path" ]] || return 1
        parent="${path%/*}"
        [[ "$parent" != "$path" ]] || break
        path="$parent"
    done
}

installer_diagnostics_private() {
    local mode links owner rest
    installer_diagnostics_path "$1" || return 1
    case "${OSTYPE:-}" in
        msys*|cygwin*)
            { INSTALLER_DIAGNOSTICS_NATIVE_HELPER="$(cygpath -w "$INSTALLER_DIAGNOSTICS_NATIVE_HELPER")" \
            INSTALLER_DIAGNOSTICS_PRIVATE_PATH="$(cygpath -w "$1")" \
                powershell.exe -NoProfile -NonInteractive -Command \
                '$ErrorActionPreference = "Stop"; . $env:INSTALLER_DIAGNOSTICS_NATIVE_HELPER; New-InstallerDiagnosticsDirectory $env:INSTALLER_DIAGNOSTICS_PRIVATE_PATH' \
                >/dev/null; } 2>/dev/null
            return $? ;;
    esac
    if [[ ! -e "$1" ]]; then (umask 077; mkdir "$1") 2>/dev/null || return 1; fi
    [[ -d "$1" ]] || return 1
    read -r mode links owner rest < <(LC_ALL=C ls -nd "$1" 2>/dev/null) || return 1
    [[ "$owner" == "$(id -u 2>/dev/null)" && "${mode:4:6}" == '------' ]]
}

installer_diagnostics_redact() {
    # Input is a finite message or a fixed file snapshot, never a live pipe.
    sed -E ':a
$!{
N
ba
}
s/^.*(UPDATE_DRIVER_WITNESS|--governance-generation|COPILOT_EXTENSIONS_CONTEXT).*$/[sensitive diagnostic record omitted]/
s/([Hh][Tt][Tt][Pp]\.extraheader[[:space:]]*=)[[:space:]]*([Aa][Uu][Tt][Hh][Oo][Rr][Ii][Zz][Aa][Tt][Ii][Oo][Nn][[:space:]]*:[[:space:]]*)*(([Bb][Ee][Aa][Rr][Ee][Rr]|[Bb][Aa][Ss][Ii][Cc])[[:space:]]+)*[^\r\n]*/\1***/g
s/([Aa][Uu][Tt][Hh][Oo][Rr][Ii][Zz][Aa][Tt][Ii][Oo][Nn][[:space:]]*:)[[:space:]]*(([Bb][Ee][Aa][Rr][Ee][Rr]|[Bb][Aa][Ss][Ii][Cc])[[:space:]]+)*[^\r\n]*/\1 ***/g
s/([Bb][Ee][Aa][Rr][Ee][Rr]|[Bb][Aa][Ss][Ii][Cc])([[:space:]]+)[A-Za-z0-9._~+\/=-]+/\1\2***/g
s/gh[pousr]_[A-Za-z0-9_]{20,}/***/g
s/github_pat_[A-Za-z0-9_]{20,}/***/g
s#([A-Za-z][A-Za-z0-9+.-]*://)[^/[:space:]@]+@#\1***@#g'
}

installer_diagnostics_write() {
    [[ -n "${INSTALLER_DIAGNOSTICS_LOG:-}" ]] || return 0
    if ! installer_diagnostics_path "$INSTALLER_DIAGNOSTICS_LOG" ||
        ! { { printf '%s\n' "$1" | installer_diagnostics_redact; } >> "$INSTALLER_DIAGNOSTICS_LOG"; } 2>/dev/null; then
        installer_diagnostics_warn
    fi
    return 0
}

installer_diagnostics_init() {
    INSTALLER_DIAGNOSTICS_LOG=''
    local plugin="$1" root directory attempt writer parent native line handoff metadata
    local fields=()
    [[ "$plugin" =~ ^[a-z][a-z0-9-]{0,63}$ ]] || { installer_diagnostics_warn; return 0; }
    case "${OSTYPE:-}" in
        msys*|cygwin*)
            { native="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"; } 2>/dev/null ||
                { installer_diagnostics_warn; return 0; }
            [[ "$native" =~ ^[a-f0-9]{32}$ ]] || { installer_diagnostics_warn; return 0; }
            handoff="$HOME/.copilot-extensions/installer-diagnostics/handoff-$native"
            # A finite file receipt, not a command-substitution pipe whose EOF
            # could be retained by a native descendant.
            if ! { INSTALLER_DIAGNOSTICS_NATIVE_HELPER="$(cygpath -w "$INSTALLER_DIAGNOSTICS_NATIVE_HELPER")" \
                INSTALLER_DIAGNOSTICS_PLUGIN="$plugin" USERPROFILE="$(cygpath -w "$HOME")" \
                INSTALLER_DIAGNOSTICS_HANDOFF="$native" powershell.exe -NoProfile -NonInteractive -Command \
                '$ErrorActionPreference = "Stop"; . $env:INSTALLER_DIAGNOSTICS_NATIVE_HELPER; Initialize-InstallerDiagnostics $env:INSTALLER_DIAGNOSTICS_PLUGIN; if ($env:COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING -ne "0") { exit 1 }; Publish-InstallerDiagnosticHandoff $env:INSTALLER_DIAGNOSTICS_HANDOFF' \
                >/dev/null; } 2>/dev/null; then installer_diagnostics_warn; return 0; fi
            if ! { while IFS= read -r line; do fields+=("$line"); done < "$handoff"; } 2>/dev/null; then
                installer_diagnostics_warn; return 0
            fi
            rm -- "$handoff" 2>/dev/null || installer_diagnostics_warn
            if [[ ${#fields[@]} != 4 || ! "${fields[0]}" =~ ^[a-f0-9]{32}$ ||
                  ! "${fields[1]}" =~ ^[a-f0-9]{32}$ ]]; then
                installer_diagnostics_warn; return 0
            fi
            export COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT="${fields[0]}"
            export COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER="${fields[1]}"
            directory="$(cygpath -u "${fields[3]}" 2>/dev/null)" || { installer_diagnostics_warn; return 0; }
            export COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY="$directory"
            export COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING=0
            INSTALLER_DIAGNOSTICS_LOG="$COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY/${fields[1]}.log"
            INSTALLER_DIAGNOSTICS_OWNER=false
            [[ "${fields[2]}" != True ]] || INSTALLER_DIAGNOSTICS_OWNER=true
            installer_diagnostics_present printf 'Installer diagnostics: %s\n' "$directory" >&2
            return 0 ;;
    esac
    root="$HOME/.copilot-extensions"
    installer_diagnostics_path "$root" || { installer_diagnostics_warn; return 0; }
    mkdir -p "$root" 2>/dev/null || { installer_diagnostics_warn; return 0; }
    root="$root/installer-diagnostics"
    installer_diagnostics_private "$root" || { installer_diagnostics_warn; return 0; }
    attempt="${COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT:-}"
    parent="${COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER:-}"
    INSTALLER_DIAGNOSTICS_OWNER=false
    if [[ "$attempt" =~ ^[a-f0-9]{32}$ ]]; then
        directory="$root/$attempt"
        installer_diagnostics_private "$directory" &&
            installer_diagnostics_path "$directory/attempt.json" || {
                installer_diagnostics_warn; return 0;
            }
        { metadata="$(< "$directory/attempt.json")"; } 2>/dev/null &&
            [[ "$metadata" =~ \"plugin\"[[:space:]]*:[[:space:]]*\"$plugin\" ]] || {
                installer_diagnostics_warn; return 0;
            }
    else
        { attempt="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"; } 2>/dev/null ||
            { installer_diagnostics_warn; return 0; }
        [[ "$attempt" =~ ^[a-f0-9]{32}$ ]] || { installer_diagnostics_warn; return 0; }
        directory="$root/$attempt"
        installer_diagnostics_private "$directory" || { installer_diagnostics_warn; return 0; }
        (umask 077; printf '{"schema":"copilot-extensions.installer-diagnostics","attempt":"%s","plugin":"%s","attribution":"UNATTRIBUTED"}\n' \
            "$attempt" "$plugin" > "$directory/attempt.json") 2>/dev/null || { installer_diagnostics_warn; return 0; }
        INSTALLER_DIAGNOSTICS_OWNER=true
    fi
    { writer="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"; } 2>/dev/null ||
        { installer_diagnostics_warn; return 0; }
    [[ "$writer" =~ ^[a-f0-9]{32}$ ]] || { installer_diagnostics_warn; return 0; }
    [[ "$parent" =~ ^[a-f0-9]{32}$ ]] || parent=''
    (umask 077; printf '{"writer":"%s","parent":"%s"}\n' "$writer" "$parent" > "$directory/$writer.start.json") 2>/dev/null ||
        { installer_diagnostics_warn; return 0; }
    INSTALLER_DIAGNOSTICS_LOG="$directory/$writer.log"
    if ! (umask 077; : > "$INSTALLER_DIAGNOSTICS_LOG") 2>/dev/null; then
        INSTALLER_DIAGNOSTICS_LOG=''
        installer_diagnostics_warn; return 0
    fi
    export COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT="$attempt"
    export COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER="$writer"
    export COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY="$directory"
    export COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING=0
    installer_diagnostics_write 'installer entered; attribution=UNATTRIBUTED'
    installer_diagnostics_prune "$root"
    installer_diagnostics_present printf 'Installer diagnostics: %s\n' "$directory" >&2
}

installer_diagnostics_prune() {
    local directory start settled matches
    for directory in "$1"/*; do
        [[ -d "$directory" && "${directory##*/}" =~ ^[a-f0-9]{32}$ ]] || continue
        installer_diagnostics_private "$directory" || continue
        [[ -f "$directory/completed.json" && ! -L "$directory/completed.json" ]] || continue
        matches="$(find "$directory/completed.json" -mtime +29 -print 2>/dev/null)" || continue
        [[ -n "$matches" ]] || continue
        [[ -f "$directory/attempt.json" && ! -L "$directory/attempt.json" ]] || continue
        grep -Fq 'copilot-extensions.installer-diagnostics' "$directory/attempt.json" 2>/dev/null || continue
        matches="$(find "$directory" -type l -print 2>/dev/null)" || continue
        [[ -z "$matches" ]] || continue
        matches="$(find "$directory" -name '*.pending' -print 2>/dev/null)" || continue
        [[ -z "$matches" ]] || continue
        settled=true
        for start in "$directory"/*.start.json; do
            [[ -f "$start" ]] || continue
            [[ -f "${start%.start.json}.completed.json" ]] || settled=false
        done
        if $settled; then rm -rf -- "$directory" 2>/dev/null || installer_diagnostics_warn; fi
    done
    return 0
}

installer_diagnostics_complete() {
    local rc="$1" directory="${COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY:-}" completed
    [[ -n "${INSTALLER_DIAGNOSTICS_LOG:-}" ]] || return 0
    installer_diagnostics_write "installer finished; exit=$rc"
    if ! { completed="$(date -u +%Y-%m-%dT%H:%M:%SZ)"; } 2>/dev/null; then
        installer_diagnostics_warn
        return 0
    fi
    if ! (umask 077; printf '{"exit":%s,"completed":"%s"}\n' "$rc" "$completed" \
        > "$directory/$COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER.completed.json") 2>/dev/null; then
        installer_diagnostics_warn
    elif $INSTALLER_DIAGNOSTICS_OWNER; then
        cp "$directory/$COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER.completed.json" "$directory/completed.json" 2>/dev/null ||
            installer_diagnostics_warn
    fi
    return 0
}

installer_diagnostics_size() {
    local bytes
    [[ -f "$1" && ! -L "$1" ]] || return 1
    bytes="$(wc -c < "$1")" || return 1
    bytes="${bytes//[[:space:]]/}"
    [[ "$bytes" =~ ^[0-9]+$ ]] || return 1
    printf '%s' "$bytes"
} 2>/dev/null

installer_diagnostics_run() {
    if [[ -z "${INSTALLER_DIAGNOSTICS_LOG:-}" ]]; then "$@"; return $?; fi
    local directory="$COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY" id rc path size missing=false
    local sizes=()
    local _installer_diagnostics_command_entered=false
    { id="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"; } 2>/dev/null || {
        installer_diagnostics_warn; "$@"; return $?;
    }
    [[ "$id" =~ ^[a-f0-9]{32}$ ]] || { installer_diagnostics_warn; "$@"; return $?; }
    if ! (umask 077; : > "$directory/$id.stdout.raw" && : > "$directory/$id.stderr.raw" &&
        printf '%s\n' 'Private raw transport; inherited writers may remain.' > "$directory/$id.pending") 2>/dev/null; then
        installer_diagnostics_warn; "$@"; return $?
    fi
    if {
        { _installer_diagnostics_command_entered=true; "$@"; } \
            > "$directory/$id.stdout.raw" 2> "$directory/$id.stderr.raw"
    } 2>/dev/null; then rc=0; else rc=$?; fi
    if ! $_installer_diagnostics_command_entered; then
        installer_diagnostics_warn; "$@"; return $?
    fi
    for path in "$directory/$id.stdout.raw" "$directory/$id.stderr.raw"; do
        if size="$(installer_diagnostics_size "$path")"; then sizes+=("$size")
        else sizes+=(''); missing=true; continue; fi
        # Fixed snapshots do not await EOF from a retained descendant.
        if ! { { head -c "$size" "$path" | installer_diagnostics_redact; } >> "$INSTALLER_DIAGNOSTICS_LOG"; } 2>/dev/null; then
            missing=true
        fi
    done
    if $missing; then installer_diagnostics_warn; fi
    if [[ -n "${sizes[0]}" && "${sizes[0]}" != 0 ]]; then
        installer_diagnostics_present head -c "${sizes[0]}" "$directory/$id.stdout.raw"
    fi
    if [[ -n "${sizes[1]}" && "${sizes[1]}" != 0 ]]; then
        installer_diagnostics_present head -c "${sizes[1]}" "$directory/$id.stderr.raw" >&2
    fi
    return "$rc"
}
