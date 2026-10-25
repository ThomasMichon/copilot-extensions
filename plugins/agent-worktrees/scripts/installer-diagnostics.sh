# Sourced native bootstrap diagnostics; does not install or select Python.
INSTALLER_DIAGNOSTICS_NATIVE_HELPER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/installer-diagnostics.ps1"
installer_diagnostics_warn() {
    export COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING=1
    if [[ -n "${INSTALLER_DIAGNOSTICS_LOG:-}" ]]; then
        if installer_diagnostics_path "$COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY/missing-diagnostics.json"; then
            (umask 077; printf '%s\n' '{"missing_diagnostics":true}' \
                > "$COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY/missing-diagnostics.json") || :
        fi
    fi
    printf '%s\n' 'WARNING: installer diagnostics unavailable; continuing with missing diagnostics.' >&2 || :
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
            INSTALLER_DIAGNOSTICS_NATIVE_HELPER="$(cygpath -w "$INSTALLER_DIAGNOSTICS_NATIVE_HELPER")" \
            INSTALLER_DIAGNOSTICS_PRIVATE_PATH="$(cygpath -w "$1")" \
                powershell.exe -NoProfile -NonInteractive -Command \
                '$ErrorActionPreference = "Stop"; . $env:INSTALLER_DIAGNOSTICS_NATIVE_HELPER; New-InstallerDiagnosticsDirectory $env:INSTALLER_DIAGNOSTICS_PRIVATE_PATH' \
                >/dev/null
            return $? ;;
    esac
    if [[ ! -e "$1" ]]; then (umask 077; mkdir "$1") || return 1; fi
    [[ -d "$1" ]] || return 1
    read -r mode links owner rest < <(LC_ALL=C ls -nd "$1") || return 1
    [[ "$owner" == "$(id -u)" && "${mode:4:6}" == '------' ]]
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
        ! printf '%s\n' "$1" | installer_diagnostics_redact >> "$INSTALLER_DIAGNOSTICS_LOG"; then
        installer_diagnostics_warn
    fi
    return 0
}

installer_diagnostics_init() {
    INSTALLER_DIAGNOSTICS_LOG=''
    local plugin="$1" root directory attempt writer parent native line handoff
    local fields=()
    [[ "$plugin" =~ ^[a-z][a-z0-9-]{0,63}$ ]] || { installer_diagnostics_warn; return 0; }
    case "${OSTYPE:-}" in
        msys*|cygwin*)
            native="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
            [[ "$native" =~ ^[a-f0-9]{32}$ ]] || { installer_diagnostics_warn; return 0; }
            handoff="$HOME/.copilot-extensions/installer-diagnostics/handoff-$native"
            # A finite file receipt, not a command-substitution pipe whose EOF
            # could be retained by a native descendant.
            if ! INSTALLER_DIAGNOSTICS_NATIVE_HELPER="$(cygpath -w "$INSTALLER_DIAGNOSTICS_NATIVE_HELPER")" \
                INSTALLER_DIAGNOSTICS_PLUGIN="$plugin" USERPROFILE="$(cygpath -w "$HOME")" \
                INSTALLER_DIAGNOSTICS_HANDOFF="$native" powershell.exe -NoProfile -NonInteractive -Command \
                '$ErrorActionPreference = "Stop"; . $env:INSTALLER_DIAGNOSTICS_NATIVE_HELPER; Initialize-InstallerDiagnostics $env:INSTALLER_DIAGNOSTICS_PLUGIN; if ($env:COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING -ne "0") { exit 1 }; Publish-InstallerDiagnosticHandoff $env:INSTALLER_DIAGNOSTICS_HANDOFF' \
                >/dev/null; then installer_diagnostics_warn; return 0; fi
            if ! while IFS= read -r line; do fields+=("$line"); done < "$handoff"; then
                installer_diagnostics_warn; return 0
            fi
            rm -- "$handoff" || installer_diagnostics_warn
            if [[ ${#fields[@]} != 4 || ! "${fields[0]}" =~ ^[a-f0-9]{32}$ ||
                  ! "${fields[1]}" =~ ^[a-f0-9]{32}$ ]]; then
                installer_diagnostics_warn; return 0
            fi
            export COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT="${fields[0]}"
            export COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER="${fields[1]}"
            directory="$(cygpath -u "${fields[3]}")" || { installer_diagnostics_warn; return 0; }
            export COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY="$directory"
            export COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING=0
            INSTALLER_DIAGNOSTICS_LOG="$COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY/${fields[1]}.log"
            INSTALLER_DIAGNOSTICS_OWNER=false
            [[ "${fields[2]}" != True ]] || INSTALLER_DIAGNOSTICS_OWNER=true
            return 0 ;;
    esac
    root="$HOME/.copilot-extensions"
    installer_diagnostics_path "$root" || { installer_diagnostics_warn; return 0; }
    mkdir -p "$root" || { installer_diagnostics_warn; return 0; }
    root="$root/installer-diagnostics"
    installer_diagnostics_private "$root" || { installer_diagnostics_warn; return 0; }
    attempt="${COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT:-}"
    parent="${COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER:-}"
    INSTALLER_DIAGNOSTICS_OWNER=false
    if [[ "$attempt" =~ ^[a-f0-9]{32}$ ]]; then
        directory="$root/$attempt"
        installer_diagnostics_private "$directory" &&
            installer_diagnostics_path "$directory/attempt.json" &&
            grep -Fq "\"plugin\":\"$plugin\"" "$directory/attempt.json" || {
                installer_diagnostics_warn; return 0;
            }
    else
        attempt="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')" || { installer_diagnostics_warn; return 0; }
        [[ "$attempt" =~ ^[a-f0-9]{32}$ ]] || { installer_diagnostics_warn; return 0; }
        directory="$root/$attempt"
        installer_diagnostics_private "$directory" || { installer_diagnostics_warn; return 0; }
        (umask 077; printf '{"schema":"copilot-extensions.installer-diagnostics","attempt":"%s","plugin":"%s","attribution":"UNATTRIBUTED"}\n' \
            "$attempt" "$plugin" > "$directory/attempt.json") || { installer_diagnostics_warn; return 0; }
        INSTALLER_DIAGNOSTICS_OWNER=true
    fi
    writer="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')" || { installer_diagnostics_warn; return 0; }
    [[ "$writer" =~ ^[a-f0-9]{32}$ ]] || { installer_diagnostics_warn; return 0; }
    [[ "$parent" =~ ^[a-f0-9]{32}$ ]] || parent=''
    (umask 077; printf '{"writer":"%s","parent":"%s"}\n' "$writer" "$parent" > "$directory/$writer.start.json") ||
        { installer_diagnostics_warn; return 0; }
    INSTALLER_DIAGNOSTICS_LOG="$directory/$writer.log"
    (umask 077; : > "$INSTALLER_DIAGNOSTICS_LOG") || { installer_diagnostics_warn; return 0; }
    export COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT="$attempt"
    export COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER="$writer"
    export COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY="$directory"
    export COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING=0
    installer_diagnostics_write 'installer entered; attribution=UNATTRIBUTED'
    installer_diagnostics_prune "$root"
    printf 'Installer diagnostics: %s\n' "$directory" >&2 || :
}

installer_diagnostics_prune() {
    local directory start settled
    for directory in "$1"/*; do
        [[ -d "$directory" && "${directory##*/}" =~ ^[a-f0-9]{32}$ ]] || continue
        installer_diagnostics_private "$directory" || continue
        [[ -f "$directory/completed.json" && ! -L "$directory/completed.json" ]] || continue
        [[ -n "$(find "$directory/completed.json" -mtime +29 -print)" ]] || continue
        [[ -f "$directory/attempt.json" && ! -L "$directory/attempt.json" ]] || continue
        grep -Fq 'copilot-extensions.installer-diagnostics' "$directory/attempt.json" || continue
        [[ -z "$(find "$directory" -type l -print)" ]] || continue
        [[ -z "$(find "$directory" -name '*.pending' -print)" ]] || continue
        settled=true
        for start in "$directory"/*.start.json; do
            [[ -f "$start" ]] || continue
            [[ -f "${start%.start.json}.completed.json" ]] || settled=false
        done
        if $settled; then rm -rf -- "$directory" || installer_diagnostics_warn; fi
    done
    return 0
}

installer_diagnostics_complete() {
    local rc="$1" directory="${COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY:-}"
    [[ -n "${INSTALLER_DIAGNOSTICS_LOG:-}" ]] || return 0
    installer_diagnostics_write "installer finished; exit=$rc"
    if ! (umask 077; printf '{"exit":%s,"completed":"%s"}\n' "$rc" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        > "$directory/$COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER.completed.json"); then
        installer_diagnostics_warn
    elif $INSTALLER_DIAGNOSTICS_OWNER; then
        cp "$directory/$COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER.completed.json" "$directory/completed.json" ||
            installer_diagnostics_warn
    fi
    return 0
}

installer_diagnostics_run() {
    if [[ -z "${INSTALLER_DIAGNOSTICS_LOG:-}" ]]; then "$@"; return $?; fi
    local directory="$COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY" id rc path size
    id="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')" || {
        installer_diagnostics_warn; "$@"; return $?;
    }
    if ! (umask 077; : > "$directory/$id.stdout.raw"; : > "$directory/$id.stderr.raw"
        printf '%s\n' 'Private raw transport; inherited writers may remain.' > "$directory/$id.pending"); then
        installer_diagnostics_warn; "$@"; return $?
    fi
    if "$@" > "$directory/$id.stdout.raw" 2> "$directory/$id.stderr.raw"; then rc=0; else rc=$?; fi
    for path in "$directory/$id.stdout.raw" "$directory/$id.stderr.raw"; do
        size="$(wc -c < "$path")"
        # Fixed snapshots do not await EOF from a retained descendant.
        if ! head -c "$size" "$path" | installer_diagnostics_redact >> "$INSTALLER_DIAGNOSTICS_LOG"; then
            installer_diagnostics_warn
        fi
    done
    size="$(wc -c < "$directory/$id.stdout.raw")"
    head -c "$size" "$directory/$id.stdout.raw" || :
    size="$(wc -c < "$directory/$id.stderr.raw")"
    head -c "$size" "$directory/$id.stderr.raw" >&2 || :
    return "$rc"
}
