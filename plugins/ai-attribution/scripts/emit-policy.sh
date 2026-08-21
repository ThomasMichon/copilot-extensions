#!/usr/bin/env bash
# Emit the ai-attribution ambient policy for the session-start repository.

set -uo pipefail

plugin_version="0.1.0-dev1"
max_payload_bytes=65536
max_config_bytes=65536
max_config_lines=200
disclosure="third-party"
owned_accounts=""
contribution_guides=""
contribution_guide_count=0
repo_root=""
json_text=""
json_pos=0
json_string=""

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

read_config() {
    local path="$1"
    local authority="$2"
    local content raw line key value line_count=0
    [[ -e "$path" || -L "$path" ]] || return 0
    if [[ -L "$path" || ! -f "$path" || ! -r "$path" ]]; then
        diag "$path: could not safely read config; safe defaults remain active"
        return 0
    fi
    if ! content="$(LC_ALL=C head -c $((max_config_bytes + 1)) -- "$path" 2>/dev/null)"; then
        diag "$path: could not safely read config; safe defaults remain active"
        return 0
    fi
    if (( ${#content} > max_config_bytes )); then
        diag "$path: config exceeds the 65536-byte limit; safe defaults remain active"
        return 0
    fi
    while IFS= read -r raw || [[ -n "$raw" ]]; do
        ((line_count += 1))
        if (( line_count > max_config_lines )); then
            diag "$path: config exceeds the 200-line limit; safe defaults remain active"
            return 0
        fi
    done <<< "$content"

    line_count=0
    while IFS= read -r raw || [[ -n "$raw" ]]; do
        ((line_count += 1))
        line="$(trim "$raw")"
        [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
        if [[ "$line" != *"="* ]]; then
            diag "$path: ignored malformed line (expected key=value)"
            continue
        fi
        key="$(trim "${line%%=*}")"
        value="$(trim "${line#*=}")"
        if [[ -z "$key" || -z "$value" ]]; then
            diag "$path: ignored malformed line (key and value are required)"
            continue
        fi

        case "$key" in
            disclosure)
                if [[ "$authority" == "repo" ]]; then
                    diag "$path: ignored non-repo-delegable key 'disclosure'"
                elif [[ "$value" == "always" ]]; then
                    disclosure="always"
                elif [[ "$value" == "third-party" ]]; then
                    if [[ "$disclosure" == "always" ]]; then
                        diag "$path: ignored disclosure=third-party because earlier policy requires always"
                    fi
                else
                    diag "$path: ignored invalid disclosure value"
                fi
                ;;
            owned_account)
                if [[ "$authority" == "repo" ]]; then
                    diag "$path: ignored non-repo-delegable key 'owned_account'"
                elif account_is_valid "$value"; then
                    owned_accounts="$(append_line "$owned_accounts" "$value")"
                else
                    diag "$path: ignored invalid owned_account value"
                fi
                ;;
            contribution_guide)
                if [[ "$authority" != "repo" ]]; then
                    diag "$path: ignored repo-only key 'contribution_guide'"
                elif ! contribution_guide_is_valid "$value"; then
                    diag "$path: ignored invalid contribution_guide path"
                elif (( contribution_guide_count >= 4 )); then
                    diag "$path: ignored contribution_guide beyond the four-entry limit"
                else
                    contribution_guides="$(append_line "$contribution_guides" "$value")"
                    ((contribution_guide_count += 1))
                fi
                ;;
            *)
                diag "$path: ignored unknown key '$key'"
                ;;
        esac
    done <<< "$content"
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
    local character token
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
            json_skip_value || return 1
            json_skip_space
            if [[ "${json_text:json_pos:1}" == ',' ]]; then
                ((json_pos += 1))
                json_skip_space
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
            json_skip_value || return 1
            json_skip_space
            if [[ "${json_text:json_pos:1}" == ',' ]]; then
                ((json_pos += 1))
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
            json_skip_value || return 1
        fi
        json_skip_space
        if [[ "${json_text:json_pos:1}" == ',' ]]; then
            ((json_pos += 1))
            continue
        fi
        [[ "${json_text:json_pos:1}" == '}' ]] || return 1
        ((json_pos += 1))
        break
    done
    json_skip_space
    (( json_pos == ${#json_text} && found == 1 )) && [[ -n "$cwd" ]] || return 1
    printf '%s' "$cwd"
}

resolve_config_dir() {
    local configured
    configured="$(trim "$1")"
    case "$configured" in
        '~') configured="${HOME:-}" ;;
        '~/'*|'~\'*) configured="${HOME:-}/${configured:2}" ;;
    esac
    [[ -d "$configured" ]] || return 1
    (cd -P -- "$configured" 2>/dev/null && pwd -P)
}

read_custom_instruction_configs() {
    local remaining_dirs config_dir resolved_dir
    remaining_dirs="${COPILOT_CUSTOM_INSTRUCTIONS_DIRS//,/:}"
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
            elif [[ "$resolved_dir" == "$repo_root" || "$resolved_dir" == "$repo_root/"* ]]; then
                diag "ignored custom instruction directory at or beneath the session-start repository"
            else
                read_config "$resolved_dir/ai-attribution.conf" "operator"
            fi
        fi
        [[ -n "$remaining_dirs" ]] || break
    done
}

main() {
    local config_home account guide kernel payload_cwd

    json_text="$(LC_ALL=C head -c $((max_payload_bytes + 1)) 2>/dev/null || true)"
    if (( ${#json_text} > max_payload_bytes )) ||
        [[ -z "$json_text" ]] ||
        ! payload_cwd="$(extract_payload_cwd)"; then
        diag "missing or malformed sessionStart payload; no policy context emitted"
        emit_empty
    fi
    repo_root="$(git -C "$payload_cwd" rev-parse --show-toplevel 2>/dev/null || true)"
    [[ -n "$repo_root" ]] || emit_empty
    repo_root="$(cd -P -- "$repo_root" 2>/dev/null && pwd -P)" || emit_empty

    read_config "${HOME:-}/.copilot/ai-attribution.conf" "operator"
    if [[ -n "${XDG_CONFIG_HOME:-}" ]]; then
        config_home="$XDG_CONFIG_HOME"
    else
        config_home="${HOME:-}/.config"
    fi
    read_config "$config_home/ai-attribution/config.conf" "operator"

    if [[ -n "${COPILOT_CUSTOM_INSTRUCTIONS_DIRS:-}" ]]; then
        read_custom_instruction_configs
    fi

    read_config "$repo_root/.github/ai-attribution.conf" "repo"

    kernel="[owner: ai-attribution@$plugin_version] Before publishing, determine the audience and repository ownership. "
    if [[ "$disclosure" == "always" ]]; then
        kernel+="Operator policy requires a prominent one-line italicized AI-assistance disclosure at the top of every contribution. "
    else
        kernel+="Contributions to another party's repo require a prominent one-line italicized AI-assistance disclosure at the top; disclosure in the operator's own repos is optional. "
    fi
    kernel+="The own-repo carve-out changes disclosure only: every public artifact, including one in an operator-owned repo, must remain persona-neutral, use first-person singular and target-repo conventions, and be scrubbed of private/internal identifiers, credentials, paths, hosts, accounts, record IDs, and private rationale; use generic placeholders. Audit the live published surface after publication. "

    account="$(remote_account)"
    if [[ -z "$account" ]]; then
        kernel+="Ownership for the session-start repository is unresolved; treat it as third-party until verified. "
    elif account_is_owned "$account"; then
        kernel+="The session-start repository remote matches configured public account \`${account,,}\`; this local hint is not proof, so verify ownership before using the disclosure-only own-repo exception. "
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
    main
fi
