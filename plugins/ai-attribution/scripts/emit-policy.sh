#!/usr/bin/env bash
# Emit the ai-attribution ambient policy for the session-start repository.

set -uo pipefail

plugin_version="0.1.0-dev9"
max_payload_bytes=65536
max_config_bytes=65536
max_config_lines=200
max_custom_dirs_length=65536
max_custom_dirs_entries=128
max_json_depth=64
disclosure="third-party"
owned_accounts=""
contribution_guides=""
contribution_guide_count=0
repo_root=""
json_text=""
json_pos=0
json_string=""
temp_files=()

cleanup() {
    local path
    for path in "${temp_files[@]}"; do
        rm -f -- "$path"
    done
}

trap cleanup EXIT
trap 'exit 0' HUP INT TERM

diag() {
    printf '[ai-attribution] %s\n' "$1" >&2
}

emit_empty() {
    printf '{}'
    exit 0
}

trim() {
    local value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}

append_line() {
    local current="$1"
    local value="$2"
    if [[ -n "$current" ]]; then
        printf '%s\n%s' "$current" "$value"
    else
        printf '%s' "$value"
    fi
}

host_is_valid() {
    local host="$1"
    local remaining label
    (( ${#host} >= 1 && ${#host} <= 253 )) || return 1
    [[ "$host" =~ ^[A-Za-z0-9.-]+$ ]] || return 1
    remaining="$host"
    while :; do
        label="${remaining%%.*}"
        (( ${#label} >= 1 && ${#label} <= 63 )) || return 1
        [[ "$label" =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$ ]] || return 1
        [[ "$remaining" == *.* ]] || break
        remaining="${remaining#*.}"
    done
}

owner_is_valid() {
    [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]
}

account_is_valid() {
    local value="$1"
    [[ "$value" == */* && "$value" != */*/* ]] || return 1
    host_is_valid "${value%%/*}" && owner_is_valid "${value#*/}"
}

contribution_guide_is_valid() {
    local value="$1"
    local segment remaining guide_path current_path

    (( ${#value} <= 160 )) || return 1
    [[ "$value" =~ ^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$ ]] || return 1

    remaining="$value"
    current_path="$repo_root"
    while :; do
        segment="${remaining%%/*}"
        [[ "$segment" != "." && "$segment" != ".." ]] || return 1
        current_path="$current_path/$segment"
        [[ ! -L "$current_path" ]] || return 1
        [[ "$remaining" == */* ]] || break
        remaining="${remaining#*/}"
    done

    guide_path="$repo_root/$value"
    [[ -f "$guide_path" ]]
}

path_contains_symlink() {
    local path="$1"
    local current="" segment remaining
    [[ "$path" == /* ]] || return 0
    remaining="${path#/}"
    while [[ -n "$remaining" ]]; do
        segment="${remaining%%/*}"
        remaining="${remaining#"$segment"}"
        remaining="${remaining#/}"
        [[ -n "$segment" ]] || continue
        current="$current/$segment"
        [[ ! -L "$current" ]] || return 0
    done
    return 1
}

utf8_is_valid() {
    local value="$1"
    local index=0 length byte lead continuation_count continuation
    local LC_ALL=C
    length=${#value}
    while (( index < length )); do
        printf -v lead '%d' "'${value:index:1}"
        if (( lead <= 0x7F )); then
            ((index += 1))
            continue
        fi
        if (( lead >= 0xC2 && lead <= 0xDF )); then
            continuation_count=1
        elif (( lead >= 0xE0 && lead <= 0xEF )); then
            continuation_count=2
        elif (( lead >= 0xF0 && lead <= 0xF4 )); then
            continuation_count=3
        else
            return 1
        fi
        (( index + continuation_count < length )) || return 1
        if (( lead == 0xE0 )); then
            printf -v byte '%d' "'${value:index+1:1}"
            (( byte >= 0xA0 && byte <= 0xBF )) || return 1
        elif (( lead == 0xED )); then
            printf -v byte '%d' "'${value:index+1:1}"
            (( byte >= 0x80 && byte <= 0x9F )) || return 1
        elif (( lead == 0xF0 )); then
            printf -v byte '%d' "'${value:index+1:1}"
            (( byte >= 0x90 && byte <= 0xBF )) || return 1
        elif (( lead == 0xF4 )); then
            printf -v byte '%d' "'${value:index+1:1}"
            (( byte >= 0x80 && byte <= 0x8F )) || return 1
        else
            printf -v byte '%d' "'${value:index+1:1}"
            (( byte >= 0x80 && byte <= 0xBF )) || return 1
        fi
        for ((continuation = 2; continuation <= continuation_count; continuation += 1)); do
            printf -v byte '%d' "'${value:index+continuation:1}"
            (( byte >= 0x80 && byte <= 0xBF )) || return 1
        done
        ((index += continuation_count + 1))
    done
}

read_config() {
    local path="$1"
    local authority="$2"
    local content="" raw line key value line_count=0 newline_free
    local byte_count config_buffer without_nul_count
    [[ -e "$path" || -L "$path" ]] || return 0
    if path_contains_symlink "$path" || [[ ! -f "$path" || ! -r "$path" ]]; then
        diag "could not safely read config; safe defaults remain active"
        return 0
    fi
    config_buffer="$(mktemp "${TMPDIR:-/tmp}/ai-attribution.XXXXXXXX")" || {
        diag "could not safely read config; safe defaults remain active"
        return 0
    }
    temp_files+=("$config_buffer")
    if ! LC_ALL=C head -c $((max_config_bytes + 1)) -- "$path" > "$config_buffer" 2>/dev/null ||
        ! byte_count="$(LC_ALL=C wc -c < "$config_buffer" 2>/dev/null)"; then
        diag "could not safely read config; safe defaults remain active"
        return 0
    fi
    byte_count="${byte_count//[[:space:]]/}"
    if [[ ! "$byte_count" =~ ^[0-9]+$ ]]; then
        diag "could not safely read config; safe defaults remain active"
        return 0
    fi
    if (( byte_count > max_config_bytes )); then
        diag "config exceeds the 65536-byte limit; safe defaults remain active"
        return 0
    fi
    if ! without_nul_count="$(
        LC_ALL=C tr -d '\000' < "$config_buffer" | LC_ALL=C wc -c
    )"; then
        diag "could not safely read config; safe defaults remain active"
        return 0
    fi
    without_nul_count="${without_nul_count//[[:space:]]/}"
    if [[ ! "$without_nul_count" =~ ^[0-9]+$ ]]; then
        diag "could not safely read config; safe defaults remain active"
        return 0
    fi
    if [[ "$without_nul_count" != "$byte_count" ]]; then
        diag "config contains NUL; safe defaults remain active"
        return 0
    fi
    IFS= LC_ALL=C read -r -d '' content < "$config_buffer" || true
    if ! utf8_is_valid "$content"; then
        diag "config is not valid UTF-8; safe defaults remain active"
        return 0
    fi
    content="${content//$'\r\n'/$'\n'}"
    content="${content//$'\r'/$'\n'}"
    if [[ -n "$content" ]]; then
        newline_free="${content//$'\n'/}"
        line_count=$(( ${#content} - ${#newline_free} ))
        [[ "$content" == *$'\n' ]] || ((line_count += 1))
    fi
    if (( line_count > max_config_lines )); then
        diag "config exceeds the 200-line limit; safe defaults remain active"
        return 0
    fi

    while IFS= read -r raw || [[ -n "$raw" ]]; do
        line="$(trim "$raw")"
        [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
        if [[ "$line" != *"="* ]]; then
            diag "ignored malformed line (expected key=value)"
            continue
        fi
        key="$(trim "${line%%=*}")"
        value="$(trim "${line#*=}")"
        if [[ -z "$key" || -z "$value" ]]; then
            diag "ignored malformed line (key and value are required)"
            continue
        fi

        case "$key" in
            disclosure)
                if [[ "$authority" == "repo" ]]; then
                    diag "ignored non-repo-delegable key 'disclosure'"
                elif [[ "$value" == "always" ]]; then
                    disclosure="always"
                elif [[ "$value" == "third-party" ]]; then
                    if [[ "$disclosure" == "always" ]]; then
                        diag "ignored disclosure=third-party because earlier policy requires always"
                    fi
                else
                    diag "ignored invalid disclosure value"
                fi
                ;;
            owned_account)
                if [[ "$authority" == "repo" ]]; then
                    diag "ignored non-repo-delegable key 'owned_account'"
                elif account_is_valid "$value"; then
                    owned_accounts="$(append_line "$owned_accounts" "$value")"
                else
                    diag "ignored invalid owned_account value"
                fi
                ;;
            contribution_guide)
                if [[ "$authority" != "repo" ]]; then
                    diag "ignored repo-only key 'contribution_guide'"
                elif ! contribution_guide_is_valid "$value"; then
                    diag "ignored invalid contribution_guide path"
                elif (( contribution_guide_count >= 4 )); then
                    diag "ignored contribution_guide beyond the four-entry limit"
                else
                    contribution_guides="$(append_line "$contribution_guides" "$value")"
                    ((contribution_guide_count += 1))
                fi
                ;;
            *)
                diag "ignored unknown config key"
                ;;
        esac
    done < <(printf '%s' "$content")
}

remote_account() {
    local url authority host path owner first
    url="$(git -C "$repo_root" remote get-url origin 2>/dev/null || true)"
    if [[ -z "$url" ]]; then
        first="$(git -C "$repo_root" remote 2>/dev/null | while IFS= read -r name; do printf '%s' "$name"; break; done)"
        [[ -n "$first" ]] && url="$(git -C "$repo_root" remote get-url "$first" 2>/dev/null || true)"
    fi
    case "$url" in
        *://*)
            authority="${url#*://}"
            authority="${authority%%/*}"
            host="${authority##*@}"
            path="${url#*://}"
            path="${path#*/}"
            ;;
        *@*:*)
            authority="${url%%:*}"
            host="${authority##*@}"
            path="${url#*:}"
            ;;
        *)
            return 0
            ;;
    esac
    path="${path#/}"
    [[ "$path" == */* ]] || return 0
    owner="${path%%/*}"
    if ! host_is_valid "$host" || ! owner_is_valid "$owner"; then
        diag "remote host or owner is invalid; ownership remains unresolved"
        return 0
    fi
    printf '%s/%s' "${host,,}" "$owner"
}

account_is_owned() {
    local candidate="${1,,}"
    local account
    while IFS= read -r account; do
        if [[ -n "$account" && "$candidate" == "${account,,}" ]]; then
            return 0
        fi
    done <<< "$owned_accounts"
    return 1
}

json_escape() {
    local value="$1"
    local output="" character escaped
    local code index
    local LC_ALL=C

    for ((index = 0; index < ${#value}; index += 1)); do
        character="${value:index:1}"
        case "$character" in
            '"') output+='\"' ;;
            '\') output+='\\' ;;
            $'\b') output+='\b' ;;
            $'\f') output+='\f' ;;
            $'\n') output+='\n' ;;
            $'\r') output+='\r' ;;
            $'\t') output+='\t' ;;
            *)
                printf -v code '%d' "'$character"
                if (( code < 32 )); then
                    printf -v escaped '\\u%04x' "$code"
                    output+="$escaped"
                else
                    output+="$character"
                fi
                ;;
        esac
    done
    printf '%s' "$output"
}

json_skip_space() {
    local character
    while (( json_pos < ${#json_text} )); do
        character="${json_text:json_pos:1}"
        case "$character" in
            ' '|$'\t'|$'\n'|$'\r') ((json_pos += 1)) ;;
            *) break ;;
        esac
    done
}

json_read_string() {
    local character escape hex low_hex decoded code codepoint unicode_escape
    [[ "${json_text:json_pos:1}" == '"' ]] || return 1
    ((json_pos += 1))
    json_string=""
    while (( json_pos < ${#json_text} )); do
        character="${json_text:json_pos:1}"
        ((json_pos += 1))
        case "$character" in
            '"') return 0 ;;
            '\')
                (( json_pos < ${#json_text} )) || return 1
                escape="${json_text:json_pos:1}"
                ((json_pos += 1))
                case "$escape" in
                    '"'|'\'|'/') json_string+="$escape" ;;
                    b) json_string+=$'\b' ;;
                    f) json_string+=$'\f' ;;
                    n) json_string+=$'\n' ;;
                    r) json_string+=$'\r' ;;
                    t) json_string+=$'\t' ;;
                    u)
                        hex="${json_text:json_pos:4}"
                        (( ${#hex} == 4 )) && [[ "$hex" =~ ^[0-9A-Fa-f]{4}$ ]] || return 1
                        (( 16#$hex != 0 )) || return 1
                        codepoint=$((16#$hex))
                        if (( codepoint >= 0xD800 && codepoint <= 0xDBFF )); then
                            [[ "${json_text:json_pos+4:2}" == '\u' ]] || return 1
                            low_hex="${json_text:json_pos+6:4}"
                            (( ${#low_hex} == 4 )) &&
                                [[ "$low_hex" =~ ^[0-9A-Fa-f]{4}$ ]] ||
                                return 1
                            (( 16#$low_hex >= 0xDC00 && 16#$low_hex <= 0xDFFF )) ||
                                return 1
                            codepoint=$((0x10000 + ((codepoint - 0xD800) << 10) + (16#$low_hex - 0xDC00)))
                            printf -v unicode_escape '\\U%08x' "$codepoint"
                            printf -v decoded '%b' "$unicode_escape"
                            ((json_pos += 10))
                        elif (( codepoint >= 0xDC00 && codepoint <= 0xDFFF )); then
                            return 1
                        else
                            printf -v decoded '%b' "\\u$hex"
                            ((json_pos += 4))
                        fi
                        json_string+="$decoded"
                        ;;
                    *) return 1 ;;
                esac
                ;;
            *)
                printf -v code '%d' "'$character"
                (( code >= 32 )) || return 1
                json_string+="$character"
                ;;
        esac
    done
    return 1
}

json_skip_value() {
    local depth="${1:-0}"
    local character token
    (( depth <= max_json_depth )) || return 1
    json_skip_space
    character="${json_text:json_pos:1}"
    if [[ "$character" == '"' ]]; then
        json_read_string
        return
    fi
    if [[ "$character" == '{' ]]; then
        ((json_pos += 1))
        json_skip_space
        if [[ "${json_text:json_pos:1}" == '}' ]]; then
            ((json_pos += 1))
            return 0
        fi
        while :; do
            json_read_string || return 1
            json_skip_space
            [[ "${json_text:json_pos:1}" == ':' ]] || return 1
            ((json_pos += 1))
            json_skip_value $((depth + 1)) || return 1
            json_skip_space
            if [[ "${json_text:json_pos:1}" == ',' ]]; then
                ((json_pos += 1))
                json_skip_space
                [[ "${json_text:json_pos:1}" != '}' ]] || return 1
                continue
            fi
            [[ "${json_text:json_pos:1}" == '}' ]] || return 1
            ((json_pos += 1))
            return 0
        done
    fi
    if [[ "$character" == '[' ]]; then
        ((json_pos += 1))
        json_skip_space
        if [[ "${json_text:json_pos:1}" == ']' ]]; then
            ((json_pos += 1))
            return 0
        fi
        while :; do
            json_skip_value $((depth + 1)) || return 1
            json_skip_space
            if [[ "${json_text:json_pos:1}" == ',' ]]; then
                ((json_pos += 1))
                json_skip_space
                [[ "${json_text:json_pos:1}" != ']' ]] || return 1
                continue
            fi
            [[ "${json_text:json_pos:1}" == ']' ]] || return 1
            ((json_pos += 1))
            return 0
        done
    fi
    token="${json_text:json_pos}"
    if [[ "$token" == true* ]]; then
        ((json_pos += 4))
    elif [[ "$token" == false* ]]; then
        ((json_pos += 5))
    elif [[ "$token" == null* ]]; then
        ((json_pos += 4))
    elif [[ "$token" =~ ^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)? ]]; then
        ((json_pos += ${#BASH_REMATCH[0]}))
    else
        return 1
    fi
}

extract_payload_cwd() {
    local key cwd="" found=0
    json_pos=0
    json_skip_space
    [[ "${json_text:json_pos:1}" == '{' ]] || return 1
    ((json_pos += 1))
    while :; do
        json_skip_space
        if [[ "${json_text:json_pos:1}" == '}' ]]; then
            ((json_pos += 1))
            break
        fi
        json_read_string || return 1
        key="$json_string"
        json_skip_space
        [[ "${json_text:json_pos:1}" == ':' ]] || return 1
        ((json_pos += 1))
        json_skip_space
        if [[ "$key" == "cwd" ]]; then
            (( found == 0 )) || return 1
            json_read_string || return 1
            cwd="$json_string"
            found=1
        else
            json_skip_value 1 || return 1
        fi
        json_skip_space
        if [[ "${json_text:json_pos:1}" == ',' ]]; then
            ((json_pos += 1))
            json_skip_space
            [[ "${json_text:json_pos:1}" != '}' ]] || return 1
            continue
        fi
        [[ "${json_text:json_pos:1}" == '}' ]] || return 1
        ((json_pos += 1))
        break
    done
    json_skip_space
    (( json_pos == ${#json_text} && found == 1 )) && [[ -n "$cwd" ]] || return 1
    [[ "$cwd" != *$'\r'* && "$cwd" != *$'\n'* ]] || return 1
    printf '%s' "$cwd"
}

resolve_config_dir() {
    local configured
    configured="$(trim "$1")"
    case "$configured" in
        '~') configured="${HOME:-}" ;;
        '~/'*|'~\'*) configured="${HOME:-}/${configured:2}" ;;
    esac
    [[ "$configured" == /* ]] || return 1
    [[ -d "$configured" ]] || return 1
    path_contains_symlink "$configured" && return 1
    (cd -P -- "$configured" 2>/dev/null && pwd -P)
}

path_at_or_below() {
    [[ "$1" == "$2" || "$2" == "/" && "$1" == /* || "$1" == "$2/"* ]]
}

read_operator_config() {
    local configured_dir="$1"
    local relative_path="$2"
    local resolved_dir
    if ! resolved_dir="$(resolve_config_dir "$configured_dir")"; then
        return 0
    fi
    if path_at_or_below "$resolved_dir" "$repo_root"; then
        diag "ignored operator config path at or beneath the session-start repository"
        return 0
    fi
    read_config "$resolved_dir/$relative_path" "operator"
}

read_custom_instruction_configs() {
    local raw_dirs="${COPILOT_CUSTOM_INSTRUCTIONS_DIRS:-}"
    local remaining_dirs config_dir resolved_dir entry_count=0
    if (( ${#raw_dirs} > max_custom_dirs_length )); then
        diag "ignored custom instruction directories beyond the 65536-character limit"
        return 0
    fi
    remaining_dirs="${raw_dirs//,/:}"
    entry_count=1
    local separators="${remaining_dirs//[^:]/}"
    ((entry_count += ${#separators}))
    if (( entry_count > max_custom_dirs_entries )); then
        diag "ignored custom instruction directories beyond the 128-entry limit"
        return 0
    fi
    while :; do
        if [[ "$remaining_dirs" == *:* ]]; then
            config_dir="${remaining_dirs%%:*}"
            remaining_dirs="${remaining_dirs#*:}"
        else
            config_dir="$remaining_dirs"
            remaining_dirs=""
        fi
        if [[ -n "$(trim "$config_dir")" ]]; then
            if ! resolved_dir="$(resolve_config_dir "$config_dir")"; then
                diag "ignored unresolved custom instruction directory"
            elif path_at_or_below "$resolved_dir" "$repo_root"; then
                diag "ignored custom instruction directory at or beneath the session-start repository"
            else
                read_config "$resolved_dir/ai-attribution.conf" "operator"
            fi
        fi
        [[ -n "$remaining_dirs" ]] || break
    done
}

main() {
    local config_home account guide kernel payload_cwd payload_nul=0

    IFS= LC_ALL=C read -r -d '' -n $((max_payload_bytes + 1)) json_text && payload_nul=1
    if (( payload_nul || ${#json_text} > max_payload_bytes )) ||
        [[ -z "$json_text" ]] ||
        ! utf8_is_valid "$json_text" ||
        ! payload_cwd="$(extract_payload_cwd)"; then
        diag "missing or malformed sessionStart payload; no policy context emitted"
        emit_empty
    fi
    [[ "$payload_cwd" == /* &&
        "$payload_cwd" != *$'\r'* &&
        "$payload_cwd" != *$'\n'* ]] || {
        diag "missing or malformed sessionStart payload; no policy context emitted"
        emit_empty
    }
    payload_cwd="$(cd -P -- "$payload_cwd" 2>/dev/null && pwd -P)" || {
        diag "missing or malformed sessionStart payload; no policy context emitted"
        emit_empty
    }
    repo_root="$(git -C "$payload_cwd" rev-parse --show-toplevel 2>/dev/null || true)"
    [[ -n "$repo_root" ]] || emit_empty
    repo_root="$(cd -P -- "$repo_root" 2>/dev/null && pwd -P)" || emit_empty

    read_operator_config "${HOME:-}/.copilot" "ai-attribution.conf"
    if [[ -n "${XDG_CONFIG_HOME:-}" ]]; then
        config_home="$XDG_CONFIG_HOME"
    else
        config_home="${HOME:-}/.config"
    fi
    read_operator_config "$config_home/ai-attribution" "config.conf"

    if [[ -n "${COPILOT_CUSTOM_INSTRUCTIONS_DIRS:-}" ]]; then
        read_custom_instruction_configs
    fi

    read_config "$repo_root/.github/ai-attribution.conf" "repo"

    if [[ "${1:-}" == "--aggregate" ]]; then
        kernel="[owner: ai-attribution@$plugin_version] Before publishing, classify audience and repository ownership. Disclose AI assistance prominently for third-party contributions and whenever operator policy requires; ownership hints are not proof and apply only to the session-start repository. Public artifacts must be persona-neutral and scrub credentials, private identifiers, hosts, paths, accounts, record IDs, and private rationale; follow target conventions and audit the live surface. Use the \`ai-attribution\` skill for details."
        printf '{"additionalContext":"%s"}' "$(json_escape "$kernel")"
        return
    fi

    kernel="[owner: ai-attribution@$plugin_version] Before publishing, determine the audience and repository ownership. "
    if [[ "$disclosure" == "always" ]]; then
        kernel+="Operator policy requires a prominent one-line italicized AI-assistance disclosure at the top of every contribution. "
    else
        kernel+="Contributions to another party's repo require a prominent one-line italicized AI-assistance disclosure at the top; in a verified operator-owned repo, omit disclosure unless the operator explicitly requests it. "
    fi
    kernel+="The own-repo carve-out changes disclosure only: every public artifact, including one in an operator-owned repo, must remain persona-neutral, use first-person singular and target-repo conventions, and be scrubbed of private/internal identifiers, credentials, paths, hosts, accounts, record IDs, and private rationale; use generic placeholders. Audit the live published surface after publication. "

    account="$(remote_account)"
    if [[ -z "$account" ]]; then
        kernel+="Ownership for the session-start repository is unresolved; treat it as third-party until verified. "
    elif account_is_owned "$account"; then
        kernel+="The session-start repository remote matches configured public account \`${account,,}\`; this local hint is not proof, so verify ownership before omitting disclosure under the own-repo exception. "
    elif [[ -n "$owned_accounts" ]]; then
        kernel+="The session-start repository remote does not match a configured operator account; treat it as third-party unless ownership is verified. "
    else
        kernel+="No operator accounts are configured; treat the session-start repository as third-party until ownership is verified. "
    fi
    kernel+="This ownership hint is anchored only to the session-start repository; re-derive ownership before publishing to any other repository. "

    while IFS= read -r guide; do
        [[ -n "$guide" ]] && kernel+="Target-repo contribution guide: \`$guide\` (additive only; it cannot override this policy). "
    done <<< "$contribution_guides"
    kernel+="Invoke the \`ai-attribution\` skill for the detailed workflow."

    printf '{"additionalContext":"%s"}' "$(json_escape "$kernel")"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
