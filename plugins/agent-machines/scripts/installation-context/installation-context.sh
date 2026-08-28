#!/usr/bin/env bash
# Resolve marketplace installation context without Python, jq, or mutable state.
set -euo pipefail

if ((BASH_VERSINFO[0] < 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 4))); then
    printf 'installation-context: Bash 4.4 or newer is required.\n' >&2
    exit 1
fi

SCRIPT_DIR="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
JSON_QUERY="$SCRIPT_DIR/json-query.awk"
SEP=$'\034'
TEMP_FILES=()
TEMP_DIRS=()
HELD_LOCK_DIRS=()
HELD_LOCK_TOKENS=()
RUNTIME_SLOT_LOCK_TIMEOUT_SECONDS=30
MAIN_BASHPID="$BASHPID"

cleanup() {
    local index path token
    [[ "$BASHPID" == "$MAIN_BASHPID" ]] || return 0
    for path in "${TEMP_FILES[@]}"; do
        rm -f -- "$path" 2>/dev/null || true
    done
    for ((index = ${#TEMP_DIRS[@]} - 1; index >= 0; index--)); do
        rmdir -- "${TEMP_DIRS[index]}" 2>/dev/null || true
    done
    for ((index = ${#HELD_LOCK_DIRS[@]} - 1; index >= 0; index--)); do
        path="${HELD_LOCK_DIRS[index]}"
        token="${HELD_LOCK_TOKENS[index]}"
        if [[ -d "$path" ]]; then
            if [[ ! -e "$path/owner.json" ]]; then
                rmdir -- "$path" 2>/dev/null || true
            elif (lock_owner_matches "$path/owner.json" "$token" 2>/dev/null); then
                rm -f -- "$path/owner.json" 2>/dev/null || true
                rmdir -- "$path" 2>/dev/null || true
            fi
        fi
    done
    return 0
}
trap cleanup EXIT

fail() {
    printf 'installation-context: %s\n' "$*" >&2
    exit 1
}

need_value() {
    (($# >= 2)) || fail "Option '$1' requires a value."
}

path_join() {
    local left="$1" right="$2"
    if [[ -z "$left" ]]; then
        printf '%s' "$right"
    else
        printf '%s%s%s' "$left" "$SEP" "$right"
    fi
}

json_query() {
    local mode="$1" file="$2" path="$3" variable
    if [[ "$file" == @* ]]; then
        variable="${file#@}"
        LC_ALL=C awk -f "$JSON_QUERY" -v "mode=$mode" -v "query_path=$path" - <<<"${!variable-}"
        return
    fi
    LC_ALL=C awk -f "$JSON_QUERY" -v "mode=$mode" -v "query_path=$path" "$file"
}

json_source_label() {
    case "$1" in
        @SOURCE_JSON) printf -- '--source-json' ;;
        @LEGACY_PROBE_JSON) printf -- '--legacy-probe-json' ;;
        @*) printf 'inline JSON' ;;
        *) printf '%s' "$1" ;;
    esac
}

json_try_query() {
    local target="$1" mode="$2" file="$3" path="$4" output status
    set +e
    output="$(json_query "$mode" "$file" "$path")"
    status=$?
    set -e
    printf -v "$target" '%s' "$output"
    return "$status"
}

json_object_keys_query() {
    json_query keys "$1" "$2"
}

json_object_keys() {
    local file="$1" path="$2" raw status
    set +e
    raw="$(json_object_keys_query "$file" "$path")"
    status=$?
    set -e
    case "$status" in
        0) ;;
        2) fail "Invalid JSON in '$(json_source_label "$file")'." ;;
        3) fail "Missing JSON object in '$(json_source_label "$file")'." ;;
        4) fail "Expected a JSON object in '$(json_source_label "$file")'." ;;
        5) fail "JSON property must use exact case in '$(json_source_label "$file")'." ;;
        *) fail "Cannot read JSON object keys in '$(json_source_label "$file")'." ;;
    esac
    printf '%s\n' "$raw"
}

json_optional_into() {
    local target="$1" file="$2" path="$3" default_value="${4-}" encoded status escapes="" decoded=""
    if json_try_query encoded hex "$file" "$path"; then
        status=0
    else
        status=$?
    fi
    case "$status" in
        0)
            while [[ -n "$encoded" ]]; do
                [[ "${encoded:0:2}" != 00 ]] ||
                    fail "JSON strings containing NUL are unsupported."
                escapes+="\\x${encoded:0:2}"
                encoded="${encoded:2}"
            done
            printf -v decoded '%b' "$escapes"
            printf -v "$target" '%s' "$decoded"
            ;;
        3) printf -v "$target" '%s' "$default_value" ;;
        2) fail "Invalid JSON in '$(json_source_label "$file")'." ;;
        4) fail "Expected a scalar JSON value in '$(json_source_label "$file")'." ;;
        5) fail "JSON property must use exact case in '$(json_source_label "$file")'." ;;
        *) fail "Cannot read JSON value in '$(json_source_label "$file")'." ;;
    esac
}

json_optional_path() {
    local result
    json_optional_into result "$1" "$2" "${3-}"
    printf '%s' "$result"
}

json_optional_string_into() {
    local target="$1" file="$2" path="$3" default_value="${4-}" type
    type="$(json_optional_type_path "$file" "$path")"
    case "$type" in
        "")
            printf -v "$target" '%s' "$default_value"
            ;;
        null)
            printf -v "$target" '%s' "$default_value"
            ;;
        string)
            json_optional_into "$target" "$file" "$path" "$default_value"
            ;;
        *)
            fail "Source field '${path##*"$SEP"}' must be a string."
            ;;
    esac
}

json_type_path() {
    local file="$1" path="$2" result status
    if json_try_query result type "$file" "$path"; then
        status=0
    else
        status=$?
    fi
    case "$status" in
        0) printf '%s' "$result" ;;
        3) ;;
        2) fail "Invalid JSON in '$(json_source_label "$file")'." ;;
        5) fail "JSON property must use exact case in '$(json_source_label "$file")'." ;;
        *) fail "Cannot read JSON type in '$(json_source_label "$file")'." ;;
    esac
    return 0
}

json_optional_type_path() {
    local file="$1" path="$2" result status
    if json_try_query result type "$file" "$path"; then
        status=0
    else
        status=$?
    fi
    case "$status" in
        0) printf '%s' "$result" ;;
        3) ;;
        2) fail "Invalid JSON in '$(json_source_label "$file")'." ;;
        5) fail "JSON property must use exact case in '$(json_source_label "$file")'." ;;
        *) fail "Cannot read JSON type in '$(json_source_label "$file")'." ;;
    esac
    return 0
}

json_length_path() {
    local file="$1" path="$2" result status
    if json_try_query result len "$file" "$path"; then
        status=0
    else
        status=$?
    fi
    case "$status" in
        0) printf '%s' "$result" ;;
        2) fail "Invalid JSON in '$(json_source_label "$file")'." ;;
        3) fail "Missing JSON array or object in '$(json_source_label "$file")'." ;;
        4) fail "Expected a JSON array or object in '$(json_source_label "$file")'." ;;
        5) fail "JSON property must use exact case in '$(json_source_label "$file")'." ;;
        *) fail "Cannot read JSON length in '$(json_source_label "$file")'." ;;
    esac
}

assert_json_type() {
    local file="$1" path="$2" expected="$3" label="$4"
    [[ "$(json_type_path "$file" "$path")" == "$expected" ]] ||
        fail "$label must be a JSON $expected."
}

json_quote() {
    JSON_VALUE="$1" LC_ALL=C awk '
        BEGIN {
            value = ENVIRON["JSON_VALUE"]
            printf "\""
            for (index_value = 1; index_value <= length(value); index_value++) {
                character = substr(value, index_value, 1)
                code = 0
                for (candidate = 1; candidate < 256; candidate++) {
                    if (sprintf("%c", candidate) == character) {
                        code = candidate
                        break
                    }
                }
                if (character == "\"") printf "\\\""
                else if (character == "\\") printf "\\\\"
                else if (character == "\b") printf "\\b"
                else if (character == "\f") printf "\\f"
                else if (character == "\n") printf "\\n"
                else if (character == "\r") printf "\\r"
                else if (character == "\t") printf "\\t"
                else if (code > 0 && code < 32) printf "\\u%04x", code
                else printf "%s", character
            }
            printf "\""
        }
    '
}

utc_now() {
    date -u '+%Y-%m-%dT%H:%M:%SZ'
}

random_token() {
    local token=""
    if [[ -r /dev/urandom ]]; then
        token="$(od -An -N16 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n')"
    fi
    [[ "$token" =~ ^[0-9a-f]{32}$ ]] ||
        token="$(printf '%08x%08x%08x%08x' "$$" "${PPID:-0}" "${RANDOM:-0}" "${RANDOM:-0}")"
    printf '%s' "${token:0:32}"
}

atomic_write_json() {
    local path="$1" content="$2" skip_lock="${3:-false}" directory temporary
    directory="$(dirname -- "$path")"
    mkdir -p -- "$directory"
    temporary="$(mktemp "$directory/.$(basename -- "$path").tmp.XXXXXX")"
    TEMP_FILES+=("$temporary")
    printf '%s\n' "$content" >"$temporary"
    if ((${#HELD_LOCK_DIRS[@]} > 0)) && [[ "$skip_lock" != true ]]; then
        assert_all_locks_owned
    fi
    mv -f -- "$temporary" "$path"
}

lock_owner_matches() {
    local owner="$1" expected_token="$2" token
    [[ -f "$owner" ]] || return 1
    json_optional_string_into token "$owner" token
    [[ "$token" == "$expected_token" ]]
}

assert_lock_owned() {
    local index path token
    index=$((${#HELD_LOCK_DIRS[@]} - 1))
    ((index >= 0)) || fail "Installation lock is not held."
    path="${HELD_LOCK_DIRS[index]}"
    token="${HELD_LOCK_TOKENS[index]}"
    [[ -d "$path" ]] ||
        fail "Installation lock is not held."
    lock_owner_matches "$path/owner.json" "$token" ||
        fail "Installation lock '$path' ownership changed during mutation."
}

assert_all_locks_owned() {
    local index path token
    ((${#HELD_LOCK_DIRS[@]} > 0)) || fail "Installation lock is not held."
    for ((index = 0; index < ${#HELD_LOCK_DIRS[@]}; index++)); do
        path="${HELD_LOCK_DIRS[index]}"
        token="${HELD_LOCK_TOKENS[index]}"
        [[ -d "$path" ]] ||
            fail "Installation lock '$path' is not held."
        lock_owner_matches "$path/owner.json" "$token" ||
            fail "Installation lock '$path' ownership changed during mutation."
    done
}

acquire_lock() {
    local path="$1" kind="$2" marketplace_id="$3" plugin_id="${4-}"
    local timeout_seconds="${5:-5}"
    local deadline owner owner_snapshot owner_host owner_pid host token owner_schema owner_version
    local owner_kind owner_marketplace owner_plugin owner_token
    host="$(hostname)"
    host="${host%%.*}"
    host="${host,,}"
    token="$(random_token)"
    mkdir -p -- "$(dirname -- "$path")"
    deadline=$((SECONDS + timeout_seconds))
    while ((SECONDS < deadline)); do
        if mkdir -- "$path" 2>/dev/null; then
            HELD_LOCK_DIRS+=("$path")
            HELD_LOCK_TOKENS+=("$token")
            owner="$path/owner.json"
            atomic_write_json "$owner" "{
  \"schema\":\"copilot-extensions.installation-lock\",
  \"version\":1,
  \"kind\":$(json_quote "$kind"),
  \"marketplaceId\":$(json_quote "$marketplace_id"),
  \"pluginId\":$(json_quote "$plugin_id"),
  \"token\":$(json_quote "$token"),
  \"host\":$(json_quote "$host"),
  \"pid\":$$,
  \"acquiredAt\":$(json_quote "$(utc_now)")
}" true
            return
        fi
        owner="$path/owner.json"
        if [[ ! -f "$owner" ]]; then
            sleep 0.01
            continue
        fi
        owner_snapshot="$(mktemp "${TMPDIR:-/tmp}/installation-context-lock.XXXXXX")"
        TEMP_FILES+=("$owner_snapshot")
        if ! cp -- "$owner" "$owner_snapshot" 2>/dev/null; then
            sleep 0.01
            continue
        fi
        json_optional_string_into owner_schema "$owner_snapshot" schema
        owner_version="$(json_optional_path "$owner_snapshot" version)"
        json_optional_string_into owner_kind "$owner_snapshot" kind
        json_optional_string_into owner_marketplace "$owner_snapshot" marketplaceId
        json_optional_string_into owner_plugin "$owner_snapshot" pluginId
        json_optional_string_into owner_token "$owner_snapshot" token
        json_optional_string_into owner_host "$owner_snapshot" host
        owner_pid="$(json_optional_path "$owner_snapshot" pid)"
        [[ "$owner_schema" == copilot-extensions.installation-lock &&
           "$owner_version" == 1 &&
           "$owner_kind" == "$kind" &&
           "$owner_marketplace" == "$marketplace_id" &&
           "$owner_plugin" == "$plugin_id" &&
           -n "$owner_token" ]] ||
            fail "Installation lock owner receipt '$owner' is invalid."
        assert_json_type "$owner_snapshot" version number "installation lock version in '$owner'"
        assert_json_type "$owner_snapshot" pid number "installation lock pid in '$owner'"
        [[ "$owner_pid" =~ ^[1-9][0-9]*$ ]] ||
            fail "Installation lock owner receipt '$owner' is invalid."
        if [[ "$owner_host" == "$host" ]]; then
            if ! kill -0 "$owner_pid" 2>/dev/null && [[ ! -d "/proc/$owner_pid" ]]; then
                sleep 0.01
                if [[ ! -d "$path" ]] ||
                    ! lock_owner_matches "$owner" "$owner_token"; then
                    continue
                fi
                fail "Installation lock '$path' has a stale owner (host=$owner_host, pid=$owner_pid); explicit repair is required."
            fi
            sleep 0.01
            continue
        fi
        fail "Installation lock '$path' is busy (host=$owner_host, pid=$owner_pid)."
    done
    if [[ ! -f "$path/owner.json" ]]; then
        local modified now
        if modified="$(stat -c %Y -- "$path" 2>/dev/null)"; then
            now="$(date +%s)"
            if ((now - modified >= 5)); then
                fail "Installation lock '$path' has no owner receipt; explicit repair is required."
            fi
        fi
        fail "Installation lock '$path' remained busy."
    fi
    fail "Installation lock '$path' remained busy."
}

release_lock() {
    local index path
    assert_lock_owned
    index=$((${#HELD_LOCK_DIRS[@]} - 1))
    path="${HELD_LOCK_DIRS[index]}"
    rm -f -- "$path/owner.json"
    rmdir -- "$path" ||
        fail "Cannot release installation lock '$path'."
    unset 'HELD_LOCK_DIRS[index]'
    unset 'HELD_LOCK_TOKENS[index]'
    HELD_LOCK_DIRS=("${HELD_LOCK_DIRS[@]}")
    HELD_LOCK_TOKENS=("${HELD_LOCK_TOKENS[@]}")
}

is_absolute() {
    [[ "$1" == /* ]]
}

canonical_path() {
    local value="$1" must_exist="${2:-false}" result
    [[ -n "${value//[[:space:]]/}" ]] || fail "A required path is empty."
    value="${value/#\~/$HOME}"
    if [[ "$must_exist" == true && ! -e "$value" ]]; then
        fail "Path does not exist: $value"
    fi
    if command -v realpath >/dev/null 2>&1; then
        if result="$(realpath -m -- "$value" 2>/dev/null)"; then
            printf '%s' "$result"
            return
        fi
    fi
    if command -v readlink >/dev/null 2>&1; then
        result="$(readlink -f -- "$value")" || fail "Cannot resolve path: $value"
    else
        fail "Cannot canonicalize paths: realpath or readlink is required."
    fi
    printf '%s' "$result"
}

paths_equal() {
    [[ "$(canonical_path "$1")" == "$(canonical_path "$2")" ]]
}

path_is_within() {
    local child parent
    child="$(canonical_path "$1")"
    parent="$(canonical_path "$2")"
    [[ "$child" == "$parent" || "$child" == "$parent/"* ]]
}

utf8_length() {
    LC_ALL=C printf '%s' "$1" | wc -c | tr -d '[:space:]'
}

digest_record() {
    local record="$1" digest
    if command -v sha256sum >/dev/null 2>&1; then
        digest="$(printf '%s' "$record" | sha256sum | awk '{print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
        digest="$(printf '%s' "$record" | shasum -a 256 | awk '{print $1}')"
    elif command -v openssl >/dev/null 2>&1; then
        digest="$(printf '%s' "$record" | openssl dgst -sha256 | awk '{print $NF}')"
    else
        fail "No SHA-256 implementation is available (sha256sum, shasum, or openssl)."
    fi
    [[ "$digest" =~ ^[0-9a-fA-F]{64}$ ]] || fail "SHA-256 implementation returned an invalid digest."
    printf '%s' "${digest,,}"
}

digest_file() {
    local path="$1" digest
    if command -v sha256sum >/dev/null 2>&1; then
        digest="$(sha256sum -- "$path" | awk '{print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
        digest="$(shasum -a 256 -- "$path" | awk '{print $1}')"
    elif command -v openssl >/dev/null 2>&1; then
        digest="$(openssl dgst -sha256 -- "$path" | awk '{print $NF}')"
    else
        fail "No SHA-256 implementation is available (sha256sum, shasum, or openssl)."
    fi
    [[ "$digest" =~ ^[0-9a-fA-F]{64}$ ]] || fail "SHA-256 output is invalid."
    printf '%s' "${digest,,}"
}

slugify() {
    local value
    value="$(printf '%s' "$1" | LC_ALL=C tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz' |
        LC_ALL=C sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')"
    printf '%s' "${value:-marketplace}"
}

percent_decode_unreserved() {
    local value="$1" prefix hex code character
    while [[ "$value" =~ ^([^%]*)%([0-9A-Fa-f]{2})(.*)$ ]]; do
        prefix="${BASH_REMATCH[1]}"
        hex="${BASH_REMATCH[2]}"
        value="${BASH_REMATCH[3]}"
        code=$((16#$hex))
        printf -v character '%b' "\\x$hex"
        if ((code >= 48 && code <= 57)) ||
            ((code >= 65 && code <= 90)) ||
            ((code >= 97 && code <= 122)) ||
            [[ "$character" == [-._~] ]]; then
            printf '%s%s' "$prefix" "$character"
        else
            printf '%s%%%s' "$prefix" "${hex^^}"
        fi
    done
    printf '%s' "$value"
}

percent_encode_path() {
    JSON_VALUE="$1" LC_ALL=C awk '
        function byte_value(character, candidate) {
            for (candidate = 1; candidate < 256; candidate++) {
                if (sprintf("%c", candidate) == character) return candidate
            }
            return 0
        }
        BEGIN {
            value = ENVIRON["JSON_VALUE"]
            for (index_value = 1; index_value <= length(value); index_value++) {
                character = substr(value, index_value, 1)
                code = byte_value(character)
                if ((code >= 48 && code <= 57) || (code >= 65 && code <= 90) || (code >= 97 && code <= 122) || character ~ /^[-._~\/!$&'\''()*+,;=:@%\[\]]$/) {
                    printf "%s", character
                } else {
                    printf "%%%02X", code
                }
            }
        }
    '
}

normalize_url_path() {
    local path="$1" part result="" separator=""
    local -a parts=() stack=()
    path="$(percent_encode_path "$path")"
    path="$(percent_decode_unreserved "$path")"
    IFS=/ read -r -a parts <<<"$path"
    for part in "${parts[@]}"; do
        case "$part" in
            "")
                ((${#stack[@]} > 0)) && stack+=("")
                ;;
            .) ;;
            ..)
                ((${#stack[@]} > 0)) && unset "stack[$((${#stack[@]} - 1))]"
                ;;
            *) stack+=("$part") ;;
        esac
    done
    while ((${#stack[@]} > 0)) && [[ -z "${stack[-1]}" ]]; do
        unset "stack[$((${#stack[@]} - 1))]"
    done
    for part in "${stack[@]}"; do
        result+="$separator$part"
        separator=/
    done
    printf '/%s' "$result"
}

valid_ipv6() {
    local address="$1" left right group count=0 compressed=false
    local -a groups=()
    [[ "$address" =~ ^[0-9A-Fa-f:]+$ && "$address" != *:::* ]] || return 1
    if [[ "$address" == *::* ]]; then
        compressed=true
        left="${address%%::*}"
        right="${address#*::}"
        [[ "$right" != *::* ]] || return 1
        IFS=: read -r -a groups <<<"$left"
        for group in "${groups[@]}"; do
            [[ -z "$group" || "$group" =~ ^[0-9A-Fa-f]{1,4}$ ]] || return 1
            [[ -z "$group" ]] || count=$((count + 1))
        done
        IFS=: read -r -a groups <<<"$right"
        for group in "${groups[@]}"; do
            [[ -z "$group" || "$group" =~ ^[0-9A-Fa-f]{1,4}$ ]] || return 1
            [[ -z "$group" ]] || count=$((count + 1))
        done
    else
        IFS=: read -r -a groups <<<"$address"
        for group in "${groups[@]}"; do
            [[ "$group" =~ ^[0-9A-Fa-f]{1,4}$ ]] || return 1
            count=$((count + 1))
        done
    fi
    if [[ "$compressed" == true ]]; then
        ((count < 8))
    else
        ((count == 8))
    fi
}

normalize_git_url() {
    local candidate="$1" scheme authority path host port="" port_marker="" raw_port="" port_number percent_tail normalized_port
    [[ ! "$candidate" =~ [[:cntrl:]] ]] ||
        fail "A git source URL may not contain control characters."
    [[ -n "${candidate//[[:space:]]/}" ]] || fail "A git source requires url."
    candidate="${candidate#"${candidate%%[![:space:]]*}"}"
    candidate="${candidate%"${candidate##*[![:space:]]}"}"
    percent_tail="$candidate"
    while [[ "$percent_tail" == *%* ]]; do
        percent_tail="${percent_tail#*%}"
        [[ "$percent_tail" =~ ^[0-9A-Fa-f]{2} ]] ||
            fail "Git URL has a malformed percent-escape."
        percent_tail="${percent_tail:2}"
    done
    if [[ "$candidate" =~ ^[^/@:]+@([^/:]+):(.+)$ ]]; then
        candidate="ssh://${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
    fi
    candidate="${candidate%%\#*}"
    candidate="${candidate%%\?*}"
    [[ "$candidate" =~ ^([A-Za-z][A-Za-z0-9+.-]*)://([^/?#]+)(/.*)?$ ]] ||
        fail "Git URL must be absolute and include a host: $1"
    scheme="${BASH_REMATCH[1],,}"
    authority="${BASH_REMATCH[2]}"
    path="${BASH_REMATCH[3]:-/}"
    authority="${authority##*@}"
    if [[ "$authority" =~ ^(\[[^]]+\])(:([0-9]*))?$ ]]; then
        host="${BASH_REMATCH[1],,}"
        port_marker="${BASH_REMATCH[2]:-}"
        raw_port="${BASH_REMATCH[3]:-}"
    elif [[ "$authority" =~ ^([^:]+)(:([0-9]*))?$ ]]; then
        host="${BASH_REMATCH[1],,}"
        port_marker="${BASH_REMATCH[2]:-}"
        raw_port="${BASH_REMATCH[3]:-}"
    else
        fail "Invalid git URL '$1'."
    fi
    if [[ "$host" == \[*\] ]]; then
        valid_ipv6 "${host:1:${#host}-2}" ||
            fail "Git URL has an invalid host: $1"
    else
        [[ "$host" =~ ^[a-z0-9._-]+$ ]] ||
            fail "Git URL has an invalid host: $1"
    fi
    if [[ -n "$port_marker" && -n "$raw_port" ]]; then
        normalized_port="$raw_port"
        while [[ ${#normalized_port} -gt 1 && "$normalized_port" == 0* ]]; do
            normalized_port="${normalized_port#0}"
        done
        if ((${#normalized_port} > 5)); then
            fail "Invalid git URL '$1'."
        fi
        port_number=$((10#$normalized_port))
        ((port_number <= 65535)) || fail "Invalid git URL '$1'."
        port=":$port_number"
        if [[ "$scheme" == http && "$port_number" == 80 ]] ||
            [[ "$scheme" == https && "$port_number" == 443 ]]; then
            port=""
        fi
    fi
    path="${path//\\//}"
    path="$(normalize_url_path "$path")"
    while [[ "$path" != / && "$path" == */ ]]; do
        path="${path%/}"
    done
    [[ "${path,,}" == *.git ]] && path="${path:0:${#path}-4}"
    [[ "$path" == /* ]] || path="/$path"
    printf '%s://%s%s%s' "$scheme" "$host" "$port" "$path"
}

SOURCE_KIND=""
SOURCE_CANONICAL=""
SOURCE_REF=""

normalize_source() {
    local file="$1" prefix="$2" base_directory="${3:-}" from_receipt="${4:-false}"
    local kind canonical_input repository git_url opaque_id stable_id directory_path receipt_id
    json_optional_string_into kind "$file" "$(path_join "$prefix" kind)"
    if [[ -z "$kind" ]]; then
        json_optional_string_into kind "$file" "$(path_join "$prefix" source)"
    fi
    kind="${kind,,}"
    kind="${kind#"${kind%%[![:space:]]*}"}"
    kind="${kind%"${kind##*[![:space:]]}"}"
    [[ "$kind" == local ]] && kind=directory
    [[ "$kind" == url ]] && kind=git
    json_optional_string_into SOURCE_REF "$file" "$(path_join "$prefix" ref)"
    json_optional_string_into canonical_input "$file" "$(path_join "$prefix" canonical)"
    if [[ "$from_receipt" == true && -z "$canonical_input" ]]; then
        fail "A receipt source requires canonical identity."
    fi

    case "$kind" in
        github)
            if [[ -n "$canonical_input" ]]; then
                [[ "$canonical_input" == github:* ]] ||
                    fail "Invalid canonical GitHub source '$canonical_input'."
                repository="${canonical_input#github:}"
            else
                json_optional_string_into repository "$file" "$(path_join "$prefix" repo)"
                if [[ -z "$repository" ]]; then
                    json_optional_string_into repository "$file" "$(path_join "$prefix" url)"
                fi
            fi
            repository="${repository#"${repository%%[![:space:]]*}"}"
            repository="${repository%"${repository##*[![:space:]]}"}"
            shopt -s nocasematch
            if [[ "$repository" =~ ^https?://github\.com/(.*)$ ]]; then
                repository="${BASH_REMATCH[1]}"
            elif [[ "$repository" =~ ^ssh://git@github\.com/(.*)$ ]]; then
                repository="${BASH_REMATCH[1]}"
            elif [[ "$repository" =~ ^git@github\.com:(.*)$ ]]; then
                repository="${BASH_REMATCH[1]}"
            fi
            shopt -u nocasematch
            repository="${repository#/}"
            repository="${repository%/}"
            [[ "${repository,,}" == *.git ]] && repository="${repository:0:${#repository}-4}"
            [[ "$repository" =~ ^[^/]+/[^/]+$ ]] ||
                fail "GitHub source requires owner/repository, got '$repository'."
            SOURCE_CANONICAL="github:${repository,,}"
            ;;
        git)
            if [[ -n "$canonical_input" ]]; then
                [[ "$canonical_input" == git:* ]] ||
                    fail "Invalid canonical git source '$canonical_input'."
                git_url="${canonical_input#git:}"
            else
                json_optional_string_into git_url "$file" "$(path_join "$prefix" url)"
            fi
            SOURCE_CANONICAL="git:$(normalize_git_url "$git_url")"
            ;;
        opaque)
            if [[ -n "$canonical_input" ]]; then
                SOURCE_CANONICAL="$canonical_input"
            else
                json_optional_string_into opaque_id "$file" "$(path_join "$prefix" id)"
                if [[ -z "$opaque_id" ]]; then
                    json_optional_string_into opaque_id "$file" "$(path_join "$prefix" value)"
                fi
                [[ -n "${opaque_id//[[:space:]]/}" ]] ||
                    fail "An opaque source requires a non-empty id."
                SOURCE_CANONICAL="opaque:$opaque_id"
            fi
            [[ "$SOURCE_CANONICAL" == opaque:* ]] ||
                fail "Invalid canonical opaque source '$SOURCE_CANONICAL'."
            ;;
        directory)
            json_optional_string_into stable_id "$file" "$(path_join "$prefix" stableId)"
            stable_id="${stable_id#"${stable_id%%[![:space:]]*}"}"
            stable_id="${stable_id%"${stable_id##*[![:space:]]}"}"
            if [[ -n "$canonical_input" ]]; then
                if [[ "$canonical_input" == directory-id:* ]]; then
                    receipt_id="${canonical_input#directory-id:}"
                    receipt_id="${receipt_id#"${receipt_id%%[![:space:]]*}"}"
                    receipt_id="${receipt_id%"${receipt_id##*[![:space:]]}"}"
                    [[ -n "$receipt_id" ]] ||
                        fail "A canonical directory-id source requires a non-empty id."
                    SOURCE_CANONICAL="directory-id:$receipt_id"
                elif [[ "$canonical_input" == directory:* ]]; then
                    directory_path="${canonical_input#directory:}"
                    SOURCE_CANONICAL="directory:$(canonical_path "$directory_path" "$([[ "$from_receipt" == true ]] && printf false || printf true)")"
                else
                    fail "Invalid canonical directory source '$canonical_input'."
                fi
            elif [[ -n "$stable_id" ]]; then
                SOURCE_CANONICAL="directory-id:$stable_id"
            else
                json_optional_string_into directory_path "$file" "$(path_join "$prefix" path)"
                [[ -n "${directory_path//[[:space:]]/}" ]] ||
                    fail "A directory source requires a non-empty path or stableId."
                if ! is_absolute "$directory_path"; then
                    [[ -n "$base_directory" ]] ||
                        fail "A relative directory source requires a declaration base directory."
                    directory_path="$base_directory/$directory_path"
                fi
                SOURCE_CANONICAL="directory:$(canonical_path "$directory_path" true)"
            fi
            ;;
        *) fail "Unsupported source kind '$kind'." ;;
    esac
    SOURCE_KIND="$kind"
}

SOURCE_RECORD=""
SOURCE_SHA256=""
SOURCE_FINGERPRINT=""
MARKETPLACE_ID=""

derive_identity() {
    local readable_name="$1" slug
    printf -v SOURCE_RECORD \
        'version:%s:%s\nkind:%s:%s\nsource:%s:%s\nref:%s:%s\n' \
        "$(utf8_length 1)" 1 \
        "$(utf8_length "$SOURCE_KIND")" "$SOURCE_KIND" \
        "$(utf8_length "$SOURCE_CANONICAL")" "$SOURCE_CANONICAL" \
        "$(utf8_length "$SOURCE_REF")" "$SOURCE_REF"
    SOURCE_SHA256="$(digest_record "$SOURCE_RECORD")"
    SOURCE_FINGERPRINT="sha256:$SOURCE_SHA256"
    slug="$(slugify "$readable_name")"
    MARKETPLACE_ID="$slug--${SOURCE_SHA256:0:16}"
}

assert_plugin_id() {
    local basename="${1%%.*}"
    [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$ && "$1" != "." && "$1" != ".." ]] ||
        fail "Invalid filesystem-safe plugin id '$1'."
    case "${basename^^}" in
        CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])
            fail "Invalid filesystem-safe plugin id '$1'."
            ;;
    esac
}

assert_marketplace_id() {
    [[ "$1" =~ ^[a-z0-9]+(-[a-z0-9]+)*--[0-9a-f]{16}$ ]] ||
        fail "Invalid source-derived marketplace id '$1'."
}

assert_positive_integer() {
    [[ "$1" =~ ^[1-9][0-9]*$ ]] || fail "$2 must be an integer of at least 1."
}

assert_receipt_generation() {
    local value="$1" name="$2"
    assert_positive_integer "$value" "$name"
    if ((${#value} > 19)) ||
        { ((${#value} == 19)) && [[ "$value" > 9223372036854775807 ]]; }; then
        fail "$name exceeds the portable signed 64-bit maximum."
    fi
}

normalize_expected_generation_into() {
    local target="$1" value="$2" name="$3"
    [[ "$value" =~ ^[0-9]+$ ]] ||
        fail "Expected $name generation must be a non-negative integer."
    while [[ ${#value} -gt 1 && "${value:0:1}" == 0 ]]; do
        value="${value:1}"
    done
    if ((${#value} > 19)) ||
        { ((${#value} == 19)) && [[ "$value" > 9223372036854775807 ]]; }; then
        fail "Expected $name generation exceeds the portable signed 64-bit maximum."
    fi
    printf -v "$target" '%s' "$value"
}

assert_receipt_state() {
    case "$1" in
        active|inactive|orphaned|removing) ;;
        *) fail "$2 must be active, inactive, orphaned, or removing." ;;
    esac
}

EVIDENCE_PLUGIN_ID=""
EVIDENCE_READABLE_NAME=""
EVIDENCE_FOUND=false
LOCATOR_KIND=""
LOCATOR_COPILOT_HOME=""
LOCATOR_MARKETPLACE_KEY=""
LOCATOR_MARKETPLACE_ROOT=""
LOCATOR_DECLARED_IN=()

resolve_installed_evidence() {
    local payload="$1" copilot_home="$2" project_root="$3"
    local installed relative key plugin first_identity="" source_identity settings_path declaration_prefix prefix type
    local -a settings_paths=() labels=() bases=()
    EVIDENCE_FOUND=false
    installed="$(canonical_path "$copilot_home/installed-plugins")"
    [[ "$payload" == "$installed/"* ]] || return 0
    relative="${payload#"$installed/"}"
    [[ "$relative" != */*/* && "$relative" == */* ]] ||
        fail "Installed payload must be exactly <copilot-home>/installed-plugins/<key>/<plugin>: $payload"
    key="${relative%%/*}"
    plugin="${relative#*/}"
    [[ -n "$key" && -n "$plugin" ]] ||
        fail "Installed payload must be exactly <copilot-home>/installed-plugins/<key>/<plugin>: $payload"

    settings_paths+=("$copilot_home/settings.json" "$copilot_home/settings.local.json")
    labels+=("user" "user-local")
    bases+=("$copilot_home" "$copilot_home")
    if [[ -n "$project_root" ]]; then
        settings_paths+=(
            "$project_root/.claude/settings.json"
            "$project_root/.claude/settings.local.json"
            "$project_root/.github/copilot/settings.json"
            "$project_root/.github/copilot/settings.local.json"
        )
        labels+=(
            "project:$project_root"
            "project:$project_root"
            "project:$project_root"
            "project:$project_root"
        )
        bases+=("$project_root" "$project_root" "$project_root" "$project_root")
    fi

    for ((index_value = 0; index_value < ${#settings_paths[@]}; index_value++)); do
        settings_path="${settings_paths[index_value]}"
        [[ -f "$settings_path" ]] || continue
        declaration_prefix="$(path_join extraKnownMarketplaces "$key")"
        type="$(json_optional_type_path "$settings_path" "$declaration_prefix")"
        if [[ -z "$type" ]]; then
            continue
        fi
        prefix="$(path_join "$declaration_prefix" source)"
        [[ "$(json_optional_type_path "$settings_path" "$prefix")" == object ]] ||
            fail "Marketplace '$key' has no source in '$settings_path'."
        normalize_source "$settings_path" "$prefix" "${bases[index_value]}"
        source_identity="$SOURCE_KIND"$'\n'"$SOURCE_CANONICAL"$'\n'"$SOURCE_REF"
        if [[ -z "$first_identity" ]]; then
            first_identity="$source_identity"
            RESOLVED_SOURCE_KIND="$SOURCE_KIND"
            RESOLVED_SOURCE_CANONICAL="$SOURCE_CANONICAL"
            RESOLVED_SOURCE_REF="$SOURCE_REF"
        elif [[ "$source_identity" != "$first_identity" ]]; then
            fail "Conflicting declarations for marketplace key '$key'. Supply explicit management provenance."
        fi
        LOCATOR_DECLARED_IN+=("${labels[index_value]}")
    done
    ((${#LOCATOR_DECLARED_IN[@]} > 0)) ||
        fail "No user or explicit project extraKnownMarketplaces declaration found for installed key '$key'."
    SOURCE_KIND="$RESOLVED_SOURCE_KIND"
    SOURCE_CANONICAL="$RESOLVED_SOURCE_CANONICAL"
    SOURCE_REF="$RESOLVED_SOURCE_REF"
    EVIDENCE_PLUGIN_ID="$plugin"
    EVIDENCE_READABLE_NAME="$key"
    LOCATOR_KIND=installed
    LOCATOR_COPILOT_HOME="$copilot_home"
    LOCATOR_MARKETPLACE_KEY="$key"
    EVIDENCE_FOUND=true
    return 0
}

resolve_directory_evidence() {
    local payload="$1" requested_plugin_id="$2" cursor manifest relative plugin_root source_base
    local count name source_path candidate matches matched_name manifest_name
    local -a manifests=(
        ".github/plugin/marketplace.json"
        "marketplace.json"
        ".plugin/marketplace.json"
        ".claude-plugin/marketplace.json"
    )
    EVIDENCE_FOUND=false
    cursor="$payload"
    while :; do
        for relative in "${manifests[@]}"; do
            manifest="$cursor/$relative"
            [[ -f "$manifest" ]] || continue
            json_optional_string_into plugin_root "$manifest" "$(path_join metadata pluginRoot)"
            source_base="$cursor"
            if [[ -n "$plugin_root" ]]; then
                ! is_absolute "$plugin_root" || fail "Marketplace metadata.pluginRoot must be relative in '$manifest'."
                [[ "/$plugin_root/" != */../* ]] || fail "Marketplace metadata.pluginRoot may not escape '$cursor'."
                source_base="$(canonical_path "$cursor/$plugin_root")"
                path_is_within "$source_base" "$cursor" ||
                    fail "Marketplace metadata.pluginRoot escapes '$cursor'."
            fi
            count="$(json_length_path "$manifest" plugins)" ||
                fail "Marketplace manifest '$manifest' has no plugins array."
            matches=0
            matched_name=""
            for ((index_value = 0; index_value < count; index_value++)); do
                json_optional_string_into name "$manifest" "$(path_join "$(path_join plugins "$index_value")" name)"
                [[ -z "$requested_plugin_id" || "$name" == "$requested_plugin_id" ]] || continue
                json_optional_string_into source_path "$manifest" "$(path_join "$(path_join plugins "$index_value")" source)"
                [[ -n "$source_path" ]] || continue
                ! is_absolute "$source_path" || fail "Marketplace plugin source must be relative and remain beneath '$cursor'."
                [[ "/$source_path/" != */../* ]] || fail "Marketplace plugin source must be relative and remain beneath '$cursor'."
                candidate="$(canonical_path "$source_base/$source_path")"
                path_is_within "$candidate" "$cursor" ||
                    fail "Marketplace plugin source escapes '$cursor'."
                if [[ -e "$candidate" ]] && paths_equal "$candidate" "$payload"; then
                    matches=$((matches + 1))
                    matched_name="$name"
                fi
            done
            ((matches == 1)) ||
                fail "Marketplace manifest '$manifest' does not contain exactly one plugin entry resolving to '$payload'."
            json_optional_string_into manifest_name "$manifest" name marketplace
            SOURCE_KIND=directory
            SOURCE_CANONICAL="directory:$(canonical_path "$cursor" true)"
            SOURCE_REF=""
            EVIDENCE_PLUGIN_ID="$matched_name"
            EVIDENCE_READABLE_NAME="$manifest_name"
            LOCATOR_KIND=directory
            LOCATOR_MARKETPLACE_ROOT="$(canonical_path "$cursor" true)"
            EVIDENCE_FOUND=true
            return 0
        done
        [[ "$cursor" != / ]] || break
        cursor="$(canonical_path "$cursor/..")"
    done
    return 0
}

NS_MARKETPLACE_ID=""
NS_FINGERPRINT=""
NS_GENERATION=""

validate_namespace_receipt() {
    local receipt_path="$1" durable_home="$2" actual cell_root marketplaces_root canonical_receipt
    local lexical_marketplaces lexical_cell
    local schema version receipt_id generation state source_prefix fingerprint slug receipt_marketplace_id
    is_absolute "$receipt_path" || fail "The namespace receipt pointer must be absolute."
    lexical_marketplaces="$durable_home/marketplaces"
    [[ ! -L "$lexical_marketplaces" ]] ||
        fail "The marketplaces root may not be a symbolic link or reparse point."
    marketplaces_root="$(canonical_path "$lexical_marketplaces")"
    paths_equal "$(dirname -- "$marketplaces_root")" "$durable_home" ||
        fail "The marketplaces root escapes the durable installation home."
    lexical_cell="$(dirname -- "$receipt_path")"
    [[ ! -L "$lexical_cell" ]] ||
        fail "The marketplace cell root may not be a symbolic link or reparse point."
    cell_root="$(canonical_path "$lexical_cell")"
    paths_equal "$(dirname -- "$cell_root")" "$marketplaces_root" ||
        fail "Namespace receipt '$receipt_path' is outside the durable marketplaces root."
    [[ ! -L "$receipt_path" ]] ||
        fail "namespace.json may not be a symbolic link or reparse point."
    actual="$(canonical_path "$receipt_path" true)"
    paths_equal "$(dirname -- "$actual")" "$cell_root" ||
        fail "namespace.json escapes its canonical marketplace cell."
    receipt_id="$(basename -- "$cell_root")"
    [[ ! -L "$cell_root/namespace.json" ]] ||
        fail "namespace.json may not be a symbolic link or reparse point."
    canonical_receipt="$(canonical_path "$cell_root/namespace.json")"
    paths_equal "$actual" "$canonical_receipt" ||
        fail "namespace.json is not at its exact canonical receipt location '$canonical_receipt'."
    json_optional_string_into schema "$actual" schema
    version="$(json_optional_path "$actual" version)"
    assert_json_type "$actual" schema string "namespace.json schema"
    assert_json_type "$actual" version number "namespace.json version"
    [[ "$schema" == copilot-extensions.marketplace-namespace && "$version" == 1 ]] ||
        fail "Namespace receipt '$actual' has an unsupported schema or version."
    json_optional_string_into receipt_marketplace_id "$actual" marketplaceId
    [[ "$receipt_marketplace_id" == "$receipt_id" ]] ||
        fail "Namespace receipt '$actual' does not match its cell directory."
    [[ "$receipt_id" =~ ^(.+)--[0-9a-f]{16}$ ]] ||
        fail "Invalid source-derived marketplace id '$receipt_id'."
    slug="${BASH_REMATCH[1]}"
    generation="$(json_optional_path "$actual" generation)"
    json_optional_string_into state "$actual" state
    assert_json_type "$actual" generation number "namespace.json generation"
    assert_json_type "$actual" state string "namespace.json state"
    assert_receipt_generation "$generation" "namespace.json generation"
    assert_receipt_state "$state" "namespace.json state"
    source_prefix=source
    normalize_source "$actual" "$source_prefix" "" true
    derive_identity "$slug"
    [[ "$MARKETPLACE_ID" == "$receipt_id" ]] ||
        fail "Namespace receipt '$actual' id does not match its normalized source."
    json_optional_string_into fingerprint "$actual" "$(path_join source fingerprint)"
    [[ "$fingerprint" == "$SOURCE_FINGERPRINT" ]] ||
        fail "Namespace receipt '$actual' fingerprint does not match its normalized source."
    NS_MARKETPLACE_ID="$receipt_id"
    NS_FINGERPRINT="$SOURCE_FINGERPRINT"
    NS_GENERATION="$generation"
}

resolve_relative_root() {
    local plugin_root="$1" relative="$2" name="$3" resolved segments
    [[ -n "${relative//[[:space:]]/}" ]] || fail "roots.$name must be a non-empty relative path."
    ! is_absolute "$relative" || fail "roots.$name must be a non-empty relative path."
    segments="/${relative//\\//}/"
    [[ "$segments" != */../* && "$segments" != */./* ]] ||
        fail "roots.$name may not escape or use dot segments."
    resolved="$(canonical_path "$plugin_root/$relative")"
    path_is_within "$resolved" "$plugin_root" || fail "roots.$name escapes pluginRoot."
    printf '%s' "$resolved"
}

CONTEXT_JSON=""
CTX_MARKETPLACE_ID=""
CTX_PLUGIN_ID=""
CTX_PAYLOAD_ROOT=""
CTX_CELL_ROOT=""
CTX_PLUGIN_ROOT=""
CTX_VERSIONS_ROOT=""
CTX_SNAPSHOTS_ROOT=""
CTX_NAMESPACE_RECEIPT=""
CTX_INSTALL_RECEIPT=""
CTX_NAMESPACE_GENERATION=""
CTX_INSTALL_GENERATION=""
CTX_INSTALL_STATE=""
CTX_SOURCE_KIND=""
CTX_SOURCE_CANONICAL=""
CTX_SOURCE_REF=""
CTX_SOURCE_FINGERPRINT=""

validate_context_receipt() {
    local receipt_path="$1" durable_home="$2" expected_marketplace="$3" expected_plugin="$4"
    local expected_payload="$5" expected_cell="$6"
    local actual schema version marketplace_id plugin_id cell_root plugin_root canonical_receipt
    local marketplaces_root plugins_root lexical_marketplaces lexical_cell lexical_plugins lexical_plugin
    local receipt_plugin_root generation state namespace_path payload_root payload_version payload_origin
    local roots_json="" name value resolved delimiter="" inherited_payload namespace_receipt
    local versions_root="" snapshots_root=""
    local payload_origin_receipt payload_origin_receipt_type
    is_absolute "$receipt_path" || fail "The installation-context receipt pointer must be absolute."
    [[ -z "$expected_payload" ]] || is_absolute "$expected_payload" ||
        fail "expected payload root must be absolute."
    [[ -z "$expected_cell" ]] || is_absolute "$expected_cell" ||
        fail "expected cell root must be absolute."
    [[ ! -L "$receipt_path" ]] ||
        fail "install.json may not be a symbolic link or reparse point."
    actual="$(canonical_path "$receipt_path" true)"
    json_optional_string_into schema "$actual" schema
    version="$(json_optional_path "$actual" version)"
    assert_json_type "$actual" schema string "install.json schema"
    assert_json_type "$actual" version number "install.json version"
    [[ "$schema" == copilot-extensions.plugin-installation && "$version" == 1 ]] ||
        fail "install.json has an unsupported schema or version."
    json_optional_string_into marketplace_id "$actual" marketplaceId
    json_optional_string_into plugin_id "$actual" pluginId
    [[ -n "$marketplace_id" && -n "$plugin_id" ]] || fail "install.json identity is incomplete."
    [[ "$marketplace_id" =~ ^[a-z0-9]+(-[a-z0-9]+)*--[0-9a-f]{16}$ ]] ||
        fail "Invalid source-derived marketplace id '$marketplace_id'."
    assert_plugin_id "$plugin_id"
    lexical_marketplaces="$durable_home/marketplaces"
    [[ ! -L "$lexical_marketplaces" ]] ||
        fail "The marketplaces root may not be a symbolic link or reparse point."
    marketplaces_root="$(canonical_path "$lexical_marketplaces")"
    paths_equal "$(dirname -- "$marketplaces_root")" "$durable_home" ||
        fail "The marketplaces root escapes the durable installation home."
    lexical_cell="$marketplaces_root/$marketplace_id"
    [[ ! -L "$lexical_cell" ]] ||
        fail "The marketplace cell root may not be a symbolic link or reparse point."
    cell_root="$(canonical_path "$lexical_cell")"
    paths_equal "$(dirname -- "$cell_root")" "$marketplaces_root" ||
        fail "The marketplace cell root escapes the marketplaces root."
    lexical_plugins="$cell_root/plugins"
    [[ ! -L "$lexical_plugins" ]] ||
        fail "The cell plugins root may not be a symbolic link or reparse point."
    plugins_root="$(canonical_path "$lexical_plugins")"
    paths_equal "$(dirname -- "$plugins_root")" "$cell_root" ||
        fail "The cell plugins root escapes the marketplace cell."
    lexical_plugin="$plugins_root/$plugin_id"
    [[ ! -L "$lexical_plugin" ]] ||
        fail "The plugin root may not be a symbolic link or reparse point."
    plugin_root="$(canonical_path "$lexical_plugin")"
    paths_equal "$(dirname -- "$plugin_root")" "$plugins_root" ||
        fail "The plugin root escapes the cell plugins root."
    [[ ! -L "$plugin_root/install.json" ]] ||
        fail "install.json may not be a symbolic link or reparse point."
    canonical_receipt="$(canonical_path "$plugin_root/install.json")"
    paths_equal "$actual" "$canonical_receipt" ||
        fail "install.json is not at its exact canonical receipt location '$canonical_receipt'."
    json_optional_string_into receipt_plugin_root "$actual" pluginRoot
    paths_equal "$receipt_plugin_root" "$plugin_root" ||
        fail "install.json pluginRoot does not match its canonical cell/plugin location."
    [[ -z "$expected_marketplace" || "$marketplace_id" == "$expected_marketplace" ]] ||
        fail "Expected marketplace '$expected_marketplace', receipt names '$marketplace_id'."
    [[ -z "$expected_plugin" || "$plugin_id" == "$expected_plugin" ]] ||
        fail "Expected plugin '$expected_plugin', receipt names '$plugin_id'."
    [[ -z "$expected_cell" ]] || paths_equal "$cell_root" "$expected_cell" ||
        fail "Expected cell '$expected_cell', receipt belongs to '$cell_root'."
    generation="$(json_optional_path "$actual" generation)"
    json_optional_string_into state "$actual" state
    assert_json_type "$actual" generation number "install.json generation"
    assert_json_type "$actual" state string "install.json state"
    assert_receipt_generation "$generation" "install.json generation"
    assert_receipt_state "$state" "install.json state"
    [[ ! -L "$cell_root/namespace.json" ]] ||
        fail "namespace.json may not be a symbolic link or reparse point."
    namespace_path="$(canonical_path "$cell_root/namespace.json")"
    json_optional_string_into namespace_receipt "$actual" namespaceReceipt
    paths_equal "$namespace_receipt" "$namespace_path" ||
        fail "install.json namespaceReceipt is not the exact namespace receipt in the same cell."
    validate_namespace_receipt "$cell_root/namespace.json" "$durable_home"
    [[ "$NS_MARKETPLACE_ID" == "$marketplace_id" ]] ||
        fail "namespace.json marketplaceId does not match install.json."
    json_optional_string_into payload_root "$actual" "$(path_join payload root)"
    is_absolute "$payload_root" || fail "payload.root must be absolute."
    json_optional_string_into payload_version "$actual" "$(path_join payload version)"
    [[ -n "${payload_version//[[:space:]]/}" ]] || fail "payload.version must be a non-empty string."
    json_optional_string_into payload_origin "$actual" "$(path_join payload origin)"
    case "$payload_origin" in installed|directory|staged|explicit) ;; *)
        fail "payload.origin must be installed, directory, staged, or explicit." ;;
    esac
    payload_origin_receipt_type="$(json_optional_type_path "$actual" "$(path_join payload originReceipt)")"
    if [[ -n "$payload_origin_receipt_type" && "$payload_origin_receipt_type" != null ]]; then
        [[ "$payload_origin_receipt_type" == string ]] ||
            fail "payload.originReceipt must be a string."
        json_optional_string_into payload_origin_receipt "$actual" "$(path_join payload originReceipt)"
        is_absolute "$payload_origin_receipt" ||
            fail "payload.originReceipt must be absolute."
    fi
    payload_root="$(canonical_path "$payload_root")"
    [[ -z "$expected_payload" ]] || paths_equal "$payload_root" "$expected_payload" ||
        fail "Expected payload '$expected_payload', receipt names '$payload_root'."
    inherited_payload="${COPILOT_PLUGIN_ROOT:-}"
    if [[ -n "$inherited_payload" ]]; then
        is_absolute "$inherited_payload" || fail "COPILOT_PLUGIN_ROOT must be absolute."
        paths_equal "$payload_root" "$inherited_payload" ||
            fail "COPILOT_PLUGIN_ROOT conflicts with the validated payload root."
    fi
    for name in versions snapshots state run logs cache launchers; do
        json_optional_string_into value "$actual" "$(path_join roots "$name")"
        resolved="$(resolve_relative_root "$plugin_root" "$value" "$name")"
        if [[ "$name" == versions ]]; then
            versions_root="$resolved"
        fi
        if [[ "$name" == snapshots ]]; then
            snapshots_root="$resolved"
        fi
        roots_json+="$delimiter$(json_quote "${name}Root"):$(
            json_quote "$resolved"
        )"
        delimiter=,
    done
    CONTEXT_JSON="{
        \"action\":\"validate\",
        \"marketplaceId\":$(json_quote "$marketplace_id"),
        \"marketplaceSlot\":$(json_quote "$marketplace_id"),
        \"sourceFingerprint\":$(json_quote "$NS_FINGERPRINT"),
        \"source\":{
            \"kind\":$(json_quote "$SOURCE_KIND"),
            \"canonical\":$(json_quote "$SOURCE_CANONICAL"),
            \"ref\":$(json_quote "$SOURCE_REF")
        },
        \"pluginId\":$(json_quote "$plugin_id"),
        \"payloadRoot\":$(json_quote "$payload_root"),
        \"cellRoot\":$(json_quote "$cell_root"),
        \"pluginRoot\":$(json_quote "$plugin_root"),
        $roots_json,
        \"reposRoot\":$(json_quote "$(canonical_path "$cell_root/repos")"),
        \"namespaceReceipt\":$(json_quote "$namespace_path"),
        \"installReceipt\":$(json_quote "$actual"),
        \"namespaceGeneration\":$NS_GENERATION,
        \"generation\":$generation,
        \"state\":$(json_quote "$state")
    }"
    CTX_MARKETPLACE_ID="$marketplace_id"
    CTX_PLUGIN_ID="$plugin_id"
    CTX_PAYLOAD_ROOT="$payload_root"
    CTX_CELL_ROOT="$cell_root"
    CTX_PLUGIN_ROOT="$plugin_root"
    CTX_VERSIONS_ROOT="$versions_root"
    CTX_SNAPSHOTS_ROOT="$snapshots_root"
    CTX_NAMESPACE_RECEIPT="$namespace_path"
    CTX_INSTALL_RECEIPT="$actual"
    CTX_NAMESPACE_GENERATION="$NS_GENERATION"
    CTX_INSTALL_GENERATION="$generation"
    CTX_INSTALL_STATE="$state"
    CTX_SOURCE_KIND="$SOURCE_KIND"
    CTX_SOURCE_CANONICAL="$SOURCE_CANONICAL"
    CTX_SOURCE_REF="$SOURCE_REF"
    CTX_SOURCE_FINGERPRINT="$NS_FINGERPRINT"
}

locator_matches_namespace() {
    local namespace_file="$1" locator_kind="$2" count kind key copilot_home marketplace_root
    if [[ "$(json_optional_type_path "$namespace_file" locators)" == array ]]; then
        count="$(json_length_path "$namespace_file" locators)"
    else
        count=0
    fi
    for ((locator_index = 0; locator_index < count; locator_index++)); do
        json_optional_string_into kind "$namespace_file" "$(path_join "$(path_join locators "$locator_index")" kind)"
        [[ "$kind" == "$locator_kind" ]] || continue
        if [[ "$kind" == installed ]]; then
            json_optional_string_into key "$namespace_file" "$(path_join "$(path_join locators "$locator_index")" marketplaceKey)"
            [[ "$key" == "$LOCATOR_MARKETPLACE_KEY" ]] ||
                continue
            json_optional_string_into copilot_home "$namespace_file" "$(path_join "$(path_join locators "$locator_index")" copilotHome)"
            paths_equal \
                "$copilot_home" \
                "$LOCATOR_COPILOT_HOME" && return 0
        elif [[ "$kind" == directory ]]; then
            json_optional_string_into marketplace_root "$namespace_file" "$(path_join "$(path_join locators "$locator_index")" marketplaceRoot)"
            paths_equal \
                "$marketplace_root" \
                "$LOCATOR_MARKETPLACE_ROOT" && return 0
        fi
    done
    return 1
}

EXISTING_JSON="[]"

find_existing_source() {
    local durable_home="$1" desired_fingerprint="$2" desired_id="$3" marketplaces
    local cell receipt same_id locator_match delimiter="" entries="" receipt_id receipt_fingerprint
    local saved_kind="$SOURCE_KIND" saved_canonical="$SOURCE_CANONICAL" saved_ref="$SOURCE_REF"
    local saved_record="$SOURCE_RECORD" saved_sha="$SOURCE_SHA256" saved_fingerprint="$SOURCE_FINGERPRINT"
    local saved_marketplace_id="$MARKETPLACE_ID"
    marketplaces="$durable_home/marketplaces"
    [[ -d "$marketplaces" ]] || { EXISTING_JSON="[]"; return; }
    shopt -s nullglob
    for cell in "$marketplaces"/*; do
        [[ -d "$cell" && -f "$cell/namespace.json" ]] || continue
        receipt="$cell/namespace.json"
        validate_namespace_receipt "$receipt" "$durable_home"
        receipt_id="$NS_MARKETPLACE_ID"
        receipt_fingerprint="$NS_FINGERPRINT"
        if [[ "$(basename -- "$cell")" == "$desired_id" && "$receipt_fingerprint" != "$desired_fingerprint" ]]; then
            fail "Marketplace id '$desired_id' is already occupied by a different full source fingerprint."
        fi
        [[ "$receipt_fingerprint" == "$desired_fingerprint" ]] || continue
        same_id=false
        [[ "$receipt_id" == "$desired_id" ]] && same_id=true
        locator_match=false
        if [[ -z "$LOCATOR_KIND" ]]; then
            locator_match=true
        elif locator_matches_namespace "$receipt" "$LOCATOR_KIND"; then
            locator_match=true
        fi
        entries+="$delimiter{
            \"marketplaceId\":$(json_quote "$receipt_id"),
            \"namespaceReceipt\":$(json_quote "$(canonical_path "$receipt" true)"),
            \"sameId\":$same_id,
            \"locatorMatch\":$locator_match
        }"
        delimiter=,
    done
    shopt -u nullglob
    EXISTING_JSON="[$entries]"
    SOURCE_KIND="$saved_kind"
    SOURCE_CANONICAL="$saved_canonical"
    SOURCE_REF="$saved_ref"
    SOURCE_RECORD="$saved_record"
    SOURCE_SHA256="$saved_sha"
    SOURCE_FINGERPRINT="$saved_fingerprint"
    MARKETPLACE_ID="$saved_marketplace_id"
}

assert_expected_generation() {
    local actual="$1" expected="$2" receipt_name="$3"
    [[ "$expected" =~ ^[0-9]+$ ]] ||
        fail "Expected $receipt_name generation must be a non-negative integer."
    [[ "$actual" == "$expected" ]] ||
        fail "$receipt_name generation changed: expected $expected, found $actual; restart installation-context resolution."
}

emit_receipt_locator() {
    local namespace="$1" index="$2" base kind declared_count declared_index delimiter="" value
    base="$(path_join locators "$index")"
    json_optional_string_into kind "$namespace" "$(path_join "$base" kind)"
    if [[ "$kind" == installed ]]; then
        json_optional_string_into value "$namespace" "$(path_join "$base" copilotHome)"
        printf '{"kind":"installed","copilotHome":%s,' "$(json_quote "$value")"
        json_optional_string_into value "$namespace" "$(path_join "$base" marketplaceKey)"
        printf '"marketplaceKey":%s,"declaredIn":[' "$(json_quote "$value")"
        if [[ "$(json_optional_type_path "$namespace" "$(path_join "$base" declaredIn)")" == array ]]; then
            declared_count="$(json_length_path "$namespace" "$(path_join "$base" declaredIn)")"
        else
            declared_count=0
        fi
        for ((declared_index = 0; declared_index < declared_count; declared_index++)); do
            json_optional_string_into value "$namespace" \
                "$(path_join "$(path_join "$base" declaredIn)" "$declared_index")"
            printf '%s%s' "$delimiter" "$(json_quote "$value")"
            delimiter=,
        done
        printf ']}'
    elif [[ "$kind" == directory ]]; then
        json_optional_string_into value "$namespace" "$(path_join "$base" marketplaceRoot)"
        printf '{"kind":"directory","marketplaceRoot":%s}' "$(json_quote "$value")"
    else
        fail "Unsupported marketplace locator kind '$kind' in namespace.json."
    fi
}

namespace_locators_json() {
    local namespace="${1-}" append_locator="$2" count=0 start=0 index delimiter="" total
    if [[ -n "$namespace" ]]; then
        if [[ "$(json_optional_type_path "$namespace" locators)" == array ]]; then
            count="$(json_length_path "$namespace" locators)"
        else
            fail "namespace.json locators must be an array."
        fi
    fi
    total=$((count + append_locator))
    ((total <= 16)) || start=$((total - 16))
    printf '['
    for ((index = start; index < count; index++)); do
        printf '%s' "$delimiter"
        emit_receipt_locator "$namespace" "$index"
        delimiter=,
    done
    if ((append_locator == 1)); then
        printf '%s' "$delimiter"
        emit_locator
    fi
    printf ']'
}

stamp_context() {
    local cell_root plugin_root namespace install genesis_lock install_lock now
    local existing_namespace="" existing_install="" namespace_generation=0 install_generation=0
    local namespace_state_existing="" install_state_existing="" namespace_changed=false install_changed=false
    local append_locator=0 locators_json created_at namespace_json install_json roots_json
    local current_payload_root="" current_payload_version="" current_payload_origin=""
    local current_origin_receipt="" current_origin_type="" origin_receipt_json=""
    local name root_value delimiter="" expected_root current_root

    [[ -n "$PAYLOAD_VERSION" ]] || fail "stamp requires --payload-version."
    case "$PAYLOAD_ORIGIN" in installed|directory|staged|explicit) ;; *)
        fail "payload origin must be installed, directory, staged, or explicit." ;;
    esac
    assert_receipt_state "$NAMESPACE_STATE" "namespace.json state"
    assert_receipt_state "$INSTALL_STATE" "install.json state"
    normalize_expected_generation_into EXPECTED_NAMESPACE_GENERATION \
        "$EXPECTED_NAMESPACE_GENERATION" namespace.json
    normalize_expected_generation_into EXPECTED_INSTALL_GENERATION \
        "$EXPECTED_INSTALL_GENERATION" install.json
    if [[ -n "$PAYLOAD_ORIGIN_RECEIPT" ]]; then
        is_absolute "$PAYLOAD_ORIGIN_RECEIPT" || fail "payload origin receipt must be absolute."
        PAYLOAD_ORIGIN_RECEIPT="$(canonical_path "$PAYLOAD_ORIGIN_RECEIPT" true)"
        origin_receipt_json=",\"originReceipt\":$(json_quote "$PAYLOAD_ORIGIN_RECEIPT")"
    fi

    cell_root="$(canonical_path "$DURABLE_HOME/marketplaces/$MARKETPLACE_ID")"
    plugin_root="$(canonical_path "$cell_root/plugins/$EVIDENCE_PLUGIN_ID")"
    namespace="$cell_root/namespace.json"
    install="$plugin_root/install.json"
    genesis_lock="$DURABLE_HOME/marketplaces/.locks/$MARKETPLACE_ID.genesis"
    install_lock="$cell_root/.locks/$EVIDENCE_PLUGIN_ID.install.lock"

    acquire_lock "$genesis_lock" genesis "$MARKETPLACE_ID"
    if [[ -f "$namespace" ]]; then
        validate_namespace_receipt "$namespace" "$DURABLE_HOME"
        existing_namespace="$namespace"
        namespace_generation="$NS_GENERATION"
        json_optional_string_into namespace_state_existing "$namespace" state
    fi
    assert_expected_generation "$namespace_generation" "$EXPECTED_NAMESPACE_GENERATION" "namespace.json"
    if [[ -n "$LOCATOR_KIND" && -n "$existing_namespace" ]] &&
        ! locator_matches_namespace "$namespace" "$LOCATOR_KIND"; then
        append_locator=1
    elif [[ -n "$LOCATOR_KIND" && -z "$existing_namespace" ]]; then
        append_locator=1
    fi
    locators_json="$(namespace_locators_json "$existing_namespace" "$append_locator")"
    if [[ -z "$existing_namespace" || "$namespace_state_existing" != "$NAMESPACE_STATE" || "$append_locator" == 1 ]]; then
        now="$(utc_now)"
        created_at="$now"
        if [[ -n "$existing_namespace" ]]; then
            json_optional_string_into created_at "$namespace" createdAt "$now"
        fi
        [[ "$namespace_generation" != 9223372036854775807 ]] ||
            fail "namespace.json generation cannot be incremented; explicit repair is required."
        namespace_generation=$((namespace_generation + 1))
        namespace_json="{
  \"schema\":\"copilot-extensions.marketplace-namespace\",
  \"version\":1,
  \"marketplaceId\":$(json_quote "$MARKETPLACE_ID"),
  \"source\":{
    \"kind\":$(json_quote "$SOURCE_KIND"),
    \"canonical\":$(json_quote "$SOURCE_CANONICAL"),
    \"ref\":$(json_quote "$SOURCE_REF"),
    \"fingerprint\":$(json_quote "$SOURCE_FINGERPRINT")
  },
  \"locators\":$locators_json,
  \"generation\":$namespace_generation,
  \"state\":$(json_quote "$NAMESPACE_STATE"),
  \"createdAt\":$(json_quote "$created_at"),
  \"updatedAt\":$(json_quote "$now")
}"
        atomic_write_json "$namespace" "$namespace_json"
        namespace_changed=true
    fi
    release_lock

    acquire_lock "$install_lock" install "$MARKETPLACE_ID" "$EVIDENCE_PLUGIN_ID"
    if [[ -f "$install" ]]; then
        COPILOT_PLUGIN_ROOT="" validate_context_receipt \
            "$install" "$DURABLE_HOME" "$MARKETPLACE_ID" "$EVIDENCE_PLUGIN_ID" "" "$cell_root"
        existing_install="$install"
        install_generation="$(json_optional_path "$install" generation)"
        json_optional_string_into install_state_existing "$install" state
        json_optional_string_into current_payload_root "$install" "$(path_join payload root)"
        current_payload_root="$(canonical_path "$current_payload_root")"
        json_optional_string_into current_payload_version "$install" "$(path_join payload version)"
        json_optional_string_into current_payload_origin "$install" "$(path_join payload origin)"
        current_origin_type="$(json_optional_type_path "$install" "$(path_join payload originReceipt)")"
        if [[ "$current_origin_type" == string ]]; then
            json_optional_string_into current_origin_receipt "$install" "$(path_join payload originReceipt)"
        fi
    fi
    assert_expected_generation "$install_generation" "$EXPECTED_INSTALL_GENERATION" "install.json"
    if [[ -z "$existing_install" ]] ||
        ! paths_equal "$current_payload_root" "$PAYLOAD_ROOT" ||
        [[ "$current_payload_version" != "$PAYLOAD_VERSION" ||
           "$current_payload_origin" != "$PAYLOAD_ORIGIN" ||
           "$current_origin_receipt" != "$PAYLOAD_ORIGIN_RECEIPT" ||
           "$install_state_existing" != "$INSTALL_STATE" ]]; then
        now="$(utc_now)"
        created_at="$now"
        if [[ -n "$existing_install" ]]; then
            json_optional_string_into created_at "$install" createdAt "$now"
        fi
        [[ "$install_generation" != 9223372036854775807 ]] ||
            fail "install.json generation cannot be incremented; explicit repair is required."
        install_generation=$((install_generation + 1))
        if [[ -n "$existing_install" ]]; then
            for name in versions snapshots state run logs cache launchers; do
                json_optional_string_into root_value "$install" "$(path_join roots "$name")"
                roots_json+="$delimiter$(json_quote "$name"):$(json_quote "$root_value")"
                delimiter=,
            done
        else
            for name in versions snapshots state run logs cache launchers; do
                roots_json+="$delimiter$(json_quote "$name"):$(json_quote "$name")"
                delimiter=,
            done
        fi
        install_json="{
  \"schema\":\"copilot-extensions.plugin-installation\",
  \"version\":1,
  \"marketplaceId\":$(json_quote "$MARKETPLACE_ID"),
  \"pluginId\":$(json_quote "$EVIDENCE_PLUGIN_ID"),
  \"pluginRoot\":$(json_quote "$plugin_root"),
  \"namespaceReceipt\":$(json_quote "$(canonical_path "$namespace")"),
  \"payload\":{
    \"root\":$(json_quote "$PAYLOAD_ROOT"),
    \"version\":$(json_quote "$PAYLOAD_VERSION"),
    \"origin\":$(json_quote "$PAYLOAD_ORIGIN")$origin_receipt_json
  },
  \"roots\":{$roots_json},
  \"generation\":$install_generation,
  \"state\":$(json_quote "$INSTALL_STATE"),
  \"createdAt\":$(json_quote "$created_at"),
  \"updatedAt\":$(json_quote "$now")
}"
        atomic_write_json "$install" "$install_json"
        install_changed=true
    fi
    release_lock

    validate_context_receipt "$install" "$DURABLE_HOME" "$MARKETPLACE_ID" \
        "$EVIDENCE_PLUGIN_ID" "$PAYLOAD_ROOT" "$cell_root"
    CONTEXT_JSON="${CONTEXT_JSON/\"action\":\"validate\"/\"action\":\"stamp\"}"
    CONTEXT_JSON="${CONTEXT_JSON%\}},\"namespaceChanged\":$namespace_changed,\"installChanged\":$install_changed,\"operative\":false}"
    printf '%s\n' "$CONTEXT_JSON"
}

assert_snapshot_id() {
    local value="$1" basename
    [[ "$value" =~ ^[A-Za-z0-9]([A-Za-z0-9._+-]*[A-Za-z0-9])?$ && "$value" != . && "$value" != .. ]] ||
        fail "Invalid filesystem-safe snapshot id '$value'."
    basename="${value%%.*}"
    basename="${basename^^}"
    case "$basename" in
        CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])
            fail "Invalid filesystem-safe snapshot id '$value'." ;;
    esac
}

assert_runtime_version() {
    local value="$1" basename
    (( $(utf8_length "$value") <= 128 )) ||
        fail "Runtime version exceeds the portable 128-character limit."
    [[ "$value" =~ ^[A-Za-z0-9]([A-Za-z0-9._+-]*[A-Za-z0-9])?$ && "$value" != . && "$value" != .. ]] ||
        fail "Invalid filesystem-safe runtime version '$value'."
    basename="${value%%.*}"
    basename="${basename^^}"
    case "$basename" in
        CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])
            fail "Invalid filesystem-safe runtime version '$value'." ;;
    esac
}

PAYLOAD_IDENTITY_ROOT=""
PAYLOAD_IDENTITY_VERSION=""
PAYLOAD_IDENTITY_ORIGIN=""
PAYLOAD_IDENTITY_ORIGIN_RECEIPT=""
PAYLOAD_IDENTITY_JSON=""

load_payload_identity() {
    local install="$1" origin_type origin_json=null
    json_optional_string_into PAYLOAD_IDENTITY_ROOT "$install" "$(path_join payload root)"
    is_absolute "$PAYLOAD_IDENTITY_ROOT" || fail "payload.root must be absolute."
    PAYLOAD_IDENTITY_ROOT="$(canonical_path "$PAYLOAD_IDENTITY_ROOT")"
    json_optional_string_into PAYLOAD_IDENTITY_VERSION "$install" "$(path_join payload version)"
    [[ -n "${PAYLOAD_IDENTITY_VERSION//[[:space:]]/}" ]] ||
        fail "payload.version must be a non-empty string."
    json_optional_string_into PAYLOAD_IDENTITY_ORIGIN "$install" "$(path_join payload origin)"
    case "$PAYLOAD_IDENTITY_ORIGIN" in installed|directory|staged|explicit) ;; *)
        fail "payload.origin must be installed, directory, staged, or explicit." ;;
    esac
    PAYLOAD_IDENTITY_ORIGIN_RECEIPT=""
    origin_type="$(json_optional_type_path "$install" "$(path_join payload originReceipt)")"
    if [[ -n "$origin_type" && "$origin_type" != null ]]; then
        [[ "$origin_type" == string ]] || fail "payload.originReceipt must be a string."
        json_optional_string_into PAYLOAD_IDENTITY_ORIGIN_RECEIPT "$install" "$(path_join payload originReceipt)"
        is_absolute "$PAYLOAD_IDENTITY_ORIGIN_RECEIPT" ||
            fail "payload.originReceipt must be absolute."
        PAYLOAD_IDENTITY_ORIGIN_RECEIPT="$(canonical_path "$PAYLOAD_IDENTITY_ORIGIN_RECEIPT")"
        origin_json="$(json_quote "$PAYLOAD_IDENTITY_ORIGIN_RECEIPT")"
    fi
    PAYLOAD_IDENTITY_JSON="{
        \"root\":$(json_quote "$PAYLOAD_IDENTITY_ROOT"),
        \"version\":$(json_quote "$PAYLOAD_IDENTITY_VERSION"),
        \"origin\":$(json_quote "$PAYLOAD_IDENTITY_ORIGIN"),
        \"originReceipt\":$origin_json
    }"
}

SNAPSHOT_ROOT=""
SNAPSHOT_PROVENANCE=""
SNAPSHOT_JSON=""
SNAPSHOT_MARKETPLACE_ID=""
SNAPSHOT_PLUGIN_ID=""
SNAPSHOT_SOURCE_FINGERPRINT=""
SNAPSHOT_NAMESPACE_RECEIPT=""
SNAPSHOT_INSTALL_RECEIPT=""
SNAPSHOT_NAMESPACE_GENERATION=""
SNAPSHOT_INSTALL_GENERATION=""

resolve_snapshot_paths() {
    local snapshots_root="$1" snapshot_id="$2" lexical_root lexical_provenance
    local entries=() entry materialized=false
    assert_snapshot_id "$snapshot_id"
    lexical_root="$snapshots_root/$snapshot_id"
    [[ ! -L "$lexical_root" ]] ||
        fail "Snapshot root may not be a symbolic link or reparse point."
    [[ -d "$lexical_root" ]] ||
        fail "Snapshot root must be an existing materialized directory."
    SNAPSHOT_ROOT="$(canonical_path "$lexical_root" true)"
    paths_equal "$(dirname -- "$SNAPSHOT_ROOT")" "$snapshots_root" ||
        fail "Snapshot root must be one direct child of snapshotsRoot."
    [[ "$(basename -- "$SNAPSHOT_ROOT")" == "$snapshot_id" ]] ||
        fail "Snapshot root does not retain the requested snapshot id."
    shopt -s nullglob dotglob
    entries=("$SNAPSHOT_ROOT"/*)
    shopt -u nullglob dotglob
    for entry in "${entries[@]}"; do
        if [[ "$(basename -- "$entry")" != snapshot-provenance.json ]]; then
            materialized=true
            break
        fi
    done
    [[ "$materialized" == true ]] ||
        fail "Snapshot root must contain materialized payload content."
    lexical_provenance="$SNAPSHOT_ROOT/snapshot-provenance.json"
    [[ ! -L "$lexical_provenance" ]] ||
        fail "Snapshot provenance may not be a symbolic link or reparse point."
    SNAPSHOT_PROVENANCE="$(canonical_path "$lexical_provenance")"
    path_is_within "$SNAPSHOT_PROVENANCE" "$snapshots_root" ||
        fail "Snapshot provenance path escapes snapshotsRoot."
}

validate_snapshot_provenance() {
    local context="$1" durable_home="$2" expected_marketplace="$3" expected_plugin="$4"
    local snapshot_id="$5" require_current_receipts="${6:-true}"
    local provenance schema version marketplace_id plugin_id source_prefix
    local fingerprint snapshot_recorded_id snapshot_recorded_root namespace_path install_path
    local namespace_generation install_generation namespace_state created_at created_epoch
    local payload_root payload_version payload_origin payload_origin_type payload_origin_receipt=""
    local payload_origin_receipt_json=null current_source_kind current_source_canonical
    local current_source_ref current_source_fingerprint readable_name

    [[ -n "$context" ]] || fail "snapshot-validate requires --context."
    [[ -n "$expected_marketplace" ]] ||
        fail "snapshot-validate requires --expected-marketplace-id."
    [[ -n "$expected_plugin" ]] ||
        fail "snapshot-validate requires --expected-plugin-id."
    [[ -n "$snapshot_id" ]] || fail "snapshot-validate requires --snapshot-id."
    assert_snapshot_id "$snapshot_id"
    COPILOT_PLUGIN_ROOT="" validate_context_receipt \
        "$context" "$durable_home" "$expected_marketplace" "$expected_plugin" "" ""
    resolve_snapshot_paths "$CTX_SNAPSHOTS_ROOT" "$snapshot_id"
    provenance="$(canonical_path "$SNAPSHOT_PROVENANCE" true)"
    paths_equal "$provenance" "$SNAPSHOT_PROVENANCE" ||
        fail "Snapshot provenance is not at its exact canonical location '$SNAPSHOT_PROVENANCE'."
    [[ "$(json_type_path "$provenance" "")" == object ]] ||
        fail "Snapshot provenance must be a JSON object."
    json_optional_string_into schema "$provenance" schema
    version="$(json_optional_path "$provenance" version)"
    assert_json_type "$provenance" schema string "snapshot provenance schema"
    assert_json_type "$provenance" version number "snapshot provenance version"
    [[ "$schema" == copilot-extensions.snapshot-provenance && "$version" == 1 ]] ||
        fail "Snapshot provenance has an unsupported schema or version."
    json_optional_string_into marketplace_id "$provenance" marketplaceId
    json_optional_string_into plugin_id "$provenance" pluginId
    [[ "$marketplace_id" == "$expected_marketplace" ]] ||
        fail "Expected marketplace '$expected_marketplace', snapshot provenance names '$marketplace_id'."
    [[ "$plugin_id" == "$expected_plugin" ]] ||
        fail "Expected plugin '$expected_plugin', snapshot provenance names '$plugin_id'."

    current_source_kind="$CTX_SOURCE_KIND"
    current_source_canonical="$CTX_SOURCE_CANONICAL"
    current_source_ref="$CTX_SOURCE_REF"
    current_source_fingerprint="$CTX_SOURCE_FINGERPRINT"
    source_prefix=source
    [[ "$(json_type_path "$provenance" "$source_prefix")" == object ]] ||
        fail "Snapshot provenance source is missing."
    normalize_source "$provenance" "$source_prefix" "" true
    readable_name="${marketplace_id%--*}"
    derive_identity "$readable_name"
    json_optional_string_into fingerprint "$provenance" "$(path_join source fingerprint)"
    [[ "$MARKETPLACE_ID" == "$marketplace_id" ]] ||
        fail "Snapshot provenance marketplaceId does not match its normalized source."
    [[ "$SOURCE_FINGERPRINT" == "$fingerprint" ]] ||
        fail "Snapshot provenance fingerprint does not match its normalized source."
    [[ "$fingerprint" == "$current_source_fingerprint" &&
       "$SOURCE_KIND" == "$current_source_kind" &&
       "$SOURCE_CANONICAL" == "$current_source_canonical" &&
       "$SOURCE_REF" == "$current_source_ref" ]] ||
        fail "Snapshot provenance source does not match the canonical namespace receipt."

    [[ "$(json_type_path "$provenance" snapshot)" == object ]] ||
        fail "Snapshot provenance snapshot identity is missing."
    json_optional_string_into snapshot_recorded_id "$provenance" "$(path_join snapshot id)"
    [[ "$snapshot_recorded_id" == "$snapshot_id" ]] ||
        fail "Snapshot provenance id does not match its canonical snapshot directory."
    json_optional_string_into snapshot_recorded_root "$provenance" "$(path_join snapshot root)"
    is_absolute "$snapshot_recorded_root" ||
        fail "Snapshot provenance snapshot.root must be absolute."
    paths_equal "$snapshot_recorded_root" "$SNAPSHOT_ROOT" ||
        fail "Snapshot provenance snapshot.root is not its exact canonical location."

    [[ "$(json_type_path "$provenance" namespaceReceipt)" == object &&
       "$(json_type_path "$provenance" installReceipt)" == object ]] ||
        fail "Snapshot provenance receipt references are missing."
    json_optional_string_into namespace_path "$provenance" "$(path_join namespaceReceipt path)"
    json_optional_string_into install_path "$provenance" "$(path_join installReceipt path)"
    is_absolute "$namespace_path" && is_absolute "$install_path" ||
        fail "Snapshot provenance receipt paths must be absolute."
    paths_equal "$namespace_path" "$CTX_NAMESPACE_RECEIPT" ||
        fail "Snapshot provenance namespace receipt does not match the current context."
    paths_equal "$install_path" "$CTX_INSTALL_RECEIPT" ||
        fail "Snapshot provenance install receipt does not match the current context."
    namespace_generation="$(json_optional_path "$provenance" "$(path_join namespaceReceipt generation)")"
    install_generation="$(json_optional_path "$provenance" "$(path_join installReceipt generation)")"
    assert_json_type "$provenance" "$(path_join namespaceReceipt generation)" number \
        "snapshot provenance namespace generation"
    assert_json_type "$provenance" "$(path_join installReceipt generation)" number \
        "snapshot provenance install generation"
    assert_receipt_generation "$namespace_generation" "snapshot provenance namespace generation"
    assert_receipt_generation "$install_generation" "snapshot provenance install generation"
    if [[ "$require_current_receipts" == true ]]; then
        [[ "$namespace_generation" == "$CTX_NAMESPACE_GENERATION" ]] ||
            fail "Snapshot provenance namespace generation is stale; restart snapshot production."
        [[ "$install_generation" == "$CTX_INSTALL_GENERATION" ]] ||
            fail "Snapshot provenance install generation is stale; restart snapshot production."
    elif ((CTX_NAMESPACE_GENERATION < namespace_generation ||
           CTX_INSTALL_GENERATION < install_generation)); then
        fail "Current receipt generation predates the owned runtime slot."
    fi
    json_optional_string_into namespace_state "$CTX_NAMESPACE_RECEIPT" state
    if [[ "$require_current_receipts" == true ]]; then
        [[ "$namespace_state" == active && "$CTX_INSTALL_STATE" == active ]] ||
            fail "Snapshot provenance requires active namespace and install receipts."
    fi

    [[ "$(json_type_path "$provenance" payload)" == object ]] ||
        fail "Snapshot provenance payload identity is missing."
    json_optional_string_into payload_root "$provenance" "$(path_join payload root)"
    is_absolute "$payload_root" || fail "Snapshot provenance payload.root must be absolute."
    payload_root="$(canonical_path "$payload_root")"
    json_optional_string_into payload_version "$provenance" "$(path_join payload version)"
    [[ -n "${payload_version//[[:space:]]/}" ]] ||
        fail "Snapshot provenance payload.version must be a non-empty string."
    json_optional_string_into payload_origin "$provenance" "$(path_join payload origin)"
    case "$payload_origin" in installed|directory|staged|explicit) ;; *)
        fail "Snapshot provenance payload.origin is invalid." ;;
    esac
    payload_origin_type="$(json_optional_type_path "$provenance" "$(path_join payload originReceipt)")"
    [[ -n "$payload_origin_type" ]] ||
        fail "Snapshot provenance payload.originReceipt must be present."
    if [[ "$payload_origin_type" != null ]]; then
        [[ "$payload_origin_type" == string ]] ||
            fail "Snapshot provenance payload.originReceipt must be a string or null."
        json_optional_string_into payload_origin_receipt "$provenance" "$(path_join payload originReceipt)"
        is_absolute "$payload_origin_receipt" ||
            fail "Snapshot provenance payload.originReceipt must be absolute."
        payload_origin_receipt="$(canonical_path "$payload_origin_receipt")"
        payload_origin_receipt_json="$(json_quote "$payload_origin_receipt")"
    fi
    if [[ "$require_current_receipts" == true ]]; then
        load_payload_identity "$CTX_INSTALL_RECEIPT"
        [[ "$payload_root" == "$PAYLOAD_IDENTITY_ROOT" &&
           "$payload_version" == "$PAYLOAD_IDENTITY_VERSION" &&
           "$payload_origin" == "$PAYLOAD_IDENTITY_ORIGIN" &&
           "$payload_origin_receipt" == "$PAYLOAD_IDENTITY_ORIGIN_RECEIPT" ]] ||
            fail "Snapshot provenance payload does not match the pinned install receipt."
    fi
    json_optional_string_into created_at "$provenance" createdAt
    created_epoch="$(parse_utc_epoch "$created_at")" ||
        fail "Snapshot provenance createdAt must be RFC3339 UTC."
    [[ -n "$created_epoch" ]] ||
        fail "Snapshot provenance createdAt must be RFC3339 UTC."

    SNAPSHOT_JSON="{
        \"action\":\"snapshot-validate\",
        \"status\":\"ready\",
        \"reason\":\"snapshot-provenance-valid\",
        \"provenance\":$(json_quote "$provenance"),
        \"snapshotRoot\":$(json_quote "$SNAPSHOT_ROOT"),
        \"snapshotId\":$(json_quote "$snapshot_id"),
        \"marketplaceId\":$(json_quote "$marketplace_id"),
        \"pluginId\":$(json_quote "$plugin_id"),
        \"sourceFingerprint\":$(json_quote "$fingerprint"),
        \"namespaceReceipt\":$(json_quote "$(canonical_path "$namespace_path")"),
        \"installReceipt\":$(json_quote "$(canonical_path "$install_path")"),
        \"namespaceGeneration\":$namespace_generation,
        \"installGeneration\":$install_generation,
        \"payload\":{
            \"root\":$(json_quote "$payload_root"),
            \"version\":$(json_quote "$payload_version"),
            \"origin\":$(json_quote "$payload_origin"),
            \"originReceipt\":$payload_origin_receipt_json
        },
        \"operative\":false
    }"
    SNAPSHOT_MARKETPLACE_ID="$marketplace_id"
    SNAPSHOT_PLUGIN_ID="$plugin_id"
    SNAPSHOT_SOURCE_FINGERPRINT="$fingerprint"
    SNAPSHOT_NAMESPACE_RECEIPT="$(canonical_path "$namespace_path")"
    SNAPSHOT_INSTALL_RECEIPT="$(canonical_path "$install_path")"
    SNAPSHOT_NAMESPACE_GENERATION="$namespace_generation"
    SNAPSHOT_INSTALL_GENERATION="$install_generation"
}

stamp_snapshot_provenance() {
    local genesis_lock install_lock namespace_state snapshot_changed=false desired now
    [[ -n "$CONTEXT" ]] || fail "snapshot-stamp requires --context."
    [[ -n "$EXPECTED_MARKETPLACE_ID" ]] ||
        fail "snapshot-stamp requires --expected-marketplace-id."
    [[ -n "$EXPECTED_PLUGIN_ID" ]] ||
        fail "snapshot-stamp requires --expected-plugin-id."
    [[ -n "$SNAPSHOT_ID" ]] || fail "snapshot-stamp requires --snapshot-id."
    assert_snapshot_id "$SNAPSHOT_ID"
    normalize_expected_generation_into EXPECTED_NAMESPACE_GENERATION \
        "$EXPECTED_NAMESPACE_GENERATION" namespace.json
    normalize_expected_generation_into EXPECTED_INSTALL_GENERATION \
        "$EXPECTED_INSTALL_GENERATION" install.json
    COPILOT_PLUGIN_ROOT="" validate_context_receipt \
        "$CONTEXT" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" "$EXPECTED_PLUGIN_ID" "" ""
    genesis_lock="$DURABLE_HOME/marketplaces/.locks/$EXPECTED_MARKETPLACE_ID.genesis"
    install_lock="$CTX_CELL_ROOT/.locks/$EXPECTED_PLUGIN_ID.install.lock"
    acquire_lock "$genesis_lock" genesis "$EXPECTED_MARKETPLACE_ID"
    acquire_lock "$install_lock" install "$EXPECTED_MARKETPLACE_ID" "$EXPECTED_PLUGIN_ID"
    COPILOT_PLUGIN_ROOT="" validate_context_receipt \
        "$CTX_INSTALL_RECEIPT" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" "$EXPECTED_PLUGIN_ID" "" "$CTX_CELL_ROOT"
    assert_expected_generation "$CTX_NAMESPACE_GENERATION" "$EXPECTED_NAMESPACE_GENERATION" "namespace.json"
    assert_expected_generation "$CTX_INSTALL_GENERATION" "$EXPECTED_INSTALL_GENERATION" "install.json"
    json_optional_string_into namespace_state "$CTX_NAMESPACE_RECEIPT" state
    [[ "$namespace_state" == active && "$CTX_INSTALL_STATE" == active ]] ||
        fail "Snapshot provenance requires active namespace and install receipts."
    resolve_snapshot_paths "$CTX_SNAPSHOTS_ROOT" "$SNAPSHOT_ID"
    if [[ -e "$SNAPSHOT_PROVENANCE" || -L "$SNAPSHOT_PROVENANCE" ]]; then
        validate_snapshot_provenance \
            "$CTX_INSTALL_RECEIPT" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" \
            "$EXPECTED_PLUGIN_ID" "$SNAPSHOT_ID"
    else
        load_payload_identity "$CTX_INSTALL_RECEIPT"
        now="$(utc_now)"
        desired="{
  \"schema\":\"copilot-extensions.snapshot-provenance\",
  \"version\":1,
  \"marketplaceId\":$(json_quote "$EXPECTED_MARKETPLACE_ID"),
  \"pluginId\":$(json_quote "$EXPECTED_PLUGIN_ID"),
  \"source\":{
    \"kind\":$(json_quote "$CTX_SOURCE_KIND"),
    \"canonical\":$(json_quote "$CTX_SOURCE_CANONICAL"),
    \"ref\":$(json_quote "$CTX_SOURCE_REF"),
    \"fingerprint\":$(json_quote "$CTX_SOURCE_FINGERPRINT")
  },
  \"snapshot\":{
    \"id\":$(json_quote "$SNAPSHOT_ID"),
    \"root\":$(json_quote "$SNAPSHOT_ROOT")
  },
  \"payload\":$PAYLOAD_IDENTITY_JSON,
  \"namespaceReceipt\":{
    \"path\":$(json_quote "$CTX_NAMESPACE_RECEIPT"),
    \"generation\":$CTX_NAMESPACE_GENERATION
  },
  \"installReceipt\":{
    \"path\":$(json_quote "$CTX_INSTALL_RECEIPT"),
    \"generation\":$CTX_INSTALL_GENERATION
  },
  \"createdAt\":$(json_quote "$now")
}"
        atomic_write_json "$SNAPSHOT_PROVENANCE" "$desired"
        snapshot_changed=true
        validate_snapshot_provenance \
            "$CTX_INSTALL_RECEIPT" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" \
            "$EXPECTED_PLUGIN_ID" "$SNAPSHOT_ID"
    fi
    release_lock
    release_lock
    SNAPSHOT_JSON="${SNAPSHOT_JSON/\"action\":\"snapshot-validate\"/\"action\":\"snapshot-stamp\"}"
    if [[ "$snapshot_changed" == true ]]; then
        SNAPSHOT_JSON="${SNAPSHOT_JSON/\"reason\":\"snapshot-provenance-valid\"/\"reason\":\"snapshot-provenance-published\"}"
    else
        SNAPSHOT_JSON="${SNAPSHOT_JSON/\"reason\":\"snapshot-provenance-valid\"/\"reason\":\"snapshot-provenance-current\"}"
    fi
    SNAPSHOT_JSON="${SNAPSHOT_JSON%\}},\"snapshotChanged\":$snapshot_changed,\"pluginRoot\":$(json_quote "$CTX_PLUGIN_ROOT")}"
    printf '%s\n' "$SNAPSHOT_JSON"
}

assert_json_object_length() {
    local file="$1" path="$2" expected="$3" label="$4"
    [[ "$(json_type_path "$file" "$path")" == object ]] ||
        fail "$label must be a JSON object."
    [[ "$(json_length_path "$file" "$path")" == "$expected" ]] ||
        fail "$label contains unknown or missing fields."
}

RUNTIME_VERSIONS_RELATIVE=""
RUNTIME_VERSIONS_ROOT=""
RUNTIME_SLOT_ROOT=""
RUNTIME_OWNERSHIP_PATH=""
SLOT_JSON=""

resolve_runtime_slot_paths() {
    local runtime_version="$1" require_existing="${2:-false}"
    local install roots relative cursor part slot_root
    local parts=()

    assert_runtime_version "$runtime_version"
    install="$CTX_INSTALL_RECEIPT"
    roots="$(path_join roots versions)"
    json_optional_string_into relative "$install" "$roots"
    RUNTIME_VERSIONS_RELATIVE="$relative"
    cursor="$CTX_PLUGIN_ROOT"
    IFS='/' read -r -a parts <<<"$relative"
    for part in "${parts[@]}"; do
        [[ -n "$part" ]] || continue
        cursor="$cursor/$part"
        [[ ! -L "$cursor" ]] ||
            fail "Versions root may not traverse a symbolic link or reparse point."
        if [[ -e "$cursor" && ! -d "$cursor" ]]; then
            fail "Versions root path components must be ordinary directories."
        fi
    done
    if [[ "$require_existing" == true && ! -d "$cursor" ]]; then
        fail "Versions root must be an existing directory."
    fi
    RUNTIME_VERSIONS_ROOT="$(canonical_path "$cursor")"
    paths_equal "$RUNTIME_VERSIONS_ROOT" "$CTX_VERSIONS_ROOT" ||
        fail "Versions root does not match the validated install receipt."
    ! paths_equal "$RUNTIME_VERSIONS_ROOT" "$CTX_PLUGIN_ROOT" &&
        path_is_within "$RUNTIME_VERSIONS_ROOT" "$CTX_PLUGIN_ROOT" ||
        fail "Versions root must remain beneath the canonical plugin root."

    slot_root="$RUNTIME_VERSIONS_ROOT/$runtime_version"
    [[ ! -L "$slot_root" ]] ||
        fail "Runtime slot may not be a symbolic link or reparse point."
    if [[ -e "$slot_root" && ! -d "$slot_root" ]]; then
        fail "Runtime slot must be an ordinary directory."
    fi
    if [[ "$require_existing" == true && ! -d "$slot_root" ]]; then
        fail "Runtime slot must be an existing directory."
    fi
    RUNTIME_SLOT_ROOT="$(canonical_path "$slot_root")"
    paths_equal "$(dirname -- "$RUNTIME_SLOT_ROOT")" "$RUNTIME_VERSIONS_ROOT" ||
        fail "Runtime slot must be one direct child of versionsRoot."
    [[ "$(basename -- "$RUNTIME_SLOT_ROOT")" == "$runtime_version" ]] ||
        fail "Runtime slot does not retain the requested runtime version."
    RUNTIME_OWNERSHIP_PATH="$RUNTIME_SLOT_ROOT/.runtime-slot-ownership.json"
    [[ ! -L "$RUNTIME_OWNERSHIP_PATH" ]] ||
        fail "Runtime slot ownership may not be a symbolic link or reparse point."
}

ensure_versions_root_chain() {
    local cursor="$CTX_PLUGIN_ROOT" part
    local parts=()
    IFS='/' read -r -a parts <<<"$RUNTIME_VERSIONS_RELATIVE"
    for part in "${parts[@]}"; do
        [[ -n "$part" ]] || continue
        cursor="$cursor/$part"
        [[ ! -L "$cursor" ]] ||
            fail "Versions root may not traverse a symbolic link or reparse point."
        if [[ ! -e "$cursor" ]]; then
            if ! mkdir -- "$cursor" 2>/dev/null; then
                [[ ! -L "$cursor" && -d "$cursor" ]] ||
                    fail "Cannot create versions root component '$cursor'."
            fi
        fi
        [[ ! -L "$cursor" && -d "$cursor" ]] ||
            fail "Versions root path components must be ordinary directories."
    done
}

publish_json_no_replace() {
    local path="$1" content="$2" staging_parent="$3" temporary
    if ! temporary="$(mktemp "$staging_parent/.runtime-slot-marker.XXXXXX")"; then
        fail "Cannot stage runtime slot ownership beneath '$staging_parent'."
    fi
    TEMP_FILES+=("$temporary")
    printf '%s\n' "$content" >"$temporary"
    assert_all_locks_owned
    if ! ln -- "$temporary" "$path" 2>/dev/null; then
        if [[ -e "$path" || -L "$path" ]]; then
            fail "Runtime slot ownership appeared during publication; refusing replacement."
        fi
        fail "Cannot publish runtime slot ownership '$path' without replacement."
    fi
    rm -f -- "$temporary"
}

validate_runtime_slot_ownership() {
    local context="$1" durable_home="$2" expected_marketplace="$3" expected_plugin="$4"
    local snapshot_id="$5" runtime_version="$6" ownership actual schema version
    local marketplace_id plugin_id source_fingerprint runtime_recorded_version
    local runtime_root snapshot_recorded_id snapshot_root snapshot_provenance
    local snapshot_recorded_sha256 snapshot_actual_sha256
    local namespace_path namespace_generation install_path install_generation created_at
    local namespace_state slot_empty=true entry
    local entries=()

    [[ -n "$context" ]] || fail "slot-validate requires --context."
    [[ -n "$expected_marketplace" ]] ||
        fail "slot-validate requires --expected-marketplace-id."
    [[ -n "$expected_plugin" ]] ||
        fail "slot-validate requires --expected-plugin-id."
    [[ -n "$snapshot_id" ]] || fail "slot-validate requires --snapshot-id."
    [[ -n "$runtime_version" ]] || fail "slot-validate requires --runtime-version."
    assert_marketplace_id "$expected_marketplace"
    assert_plugin_id "$expected_plugin"
    assert_snapshot_id "$snapshot_id"
    assert_runtime_version "$runtime_version"

    COPILOT_PLUGIN_ROOT="" validate_context_receipt \
        "$context" "$durable_home" "$expected_marketplace" "$expected_plugin" "" ""
    validate_snapshot_provenance \
        "$context" "$durable_home" "$expected_marketplace" "$expected_plugin" \
        "$snapshot_id" false
    resolve_runtime_slot_paths "$runtime_version" true
    ownership="$RUNTIME_OWNERSHIP_PATH"
    [[ -e "$ownership" ]] || fail "Runtime slot ownership must exist."
    actual="$(canonical_path "$ownership" true)"
    paths_equal "$actual" "$ownership" ||
        fail "Runtime slot ownership is not at its exact canonical location '$ownership'."
    [[ -f "$actual" && ! -L "$actual" ]] ||
        fail "Runtime slot ownership must be an ordinary file."

    assert_json_object_length "$actual" "" 10 "Runtime slot ownership"
    assert_json_object_length "$actual" runtime 2 "Runtime slot ownership runtime identity"
    assert_json_object_length "$actual" snapshot 4 "Runtime slot ownership snapshot identity"
    assert_json_object_length "$actual" namespaceReceipt 2 "Runtime slot ownership namespace receipt"
    assert_json_object_length "$actual" installReceipt 2 "Runtime slot ownership install receipt"

    json_optional_string_into schema "$actual" schema
    version="$(json_optional_path "$actual" version)"
    assert_json_type "$actual" schema string "runtime slot ownership schema"
    assert_json_type "$actual" version number "runtime slot ownership version"
    [[ "$schema" == copilot-extensions.runtime-slot-ownership && "$version" == 1 ]] ||
        fail "Runtime slot ownership has an unsupported schema or version."
    json_optional_string_into marketplace_id "$actual" marketplaceId
    json_optional_string_into plugin_id "$actual" pluginId
    json_optional_string_into source_fingerprint "$actual" sourceFingerprint
    json_optional_string_into runtime_recorded_version "$actual" "$(path_join runtime version)"
    json_optional_string_into runtime_root "$actual" "$(path_join runtime root)"
    json_optional_string_into snapshot_recorded_id "$actual" "$(path_join snapshot id)"
    json_optional_string_into snapshot_root "$actual" "$(path_join snapshot root)"
    json_optional_string_into snapshot_provenance "$actual" "$(path_join snapshot provenance)"
    json_optional_string_into snapshot_recorded_sha256 "$actual" \
        "$(path_join snapshot provenanceSha256)"
    json_optional_string_into namespace_path "$actual" "$(path_join namespaceReceipt path)"
    namespace_generation="$(json_optional_path "$actual" "$(path_join namespaceReceipt generation)")"
    json_optional_string_into install_path "$actual" "$(path_join installReceipt path)"
    install_generation="$(json_optional_path "$actual" "$(path_join installReceipt generation)")"
    json_optional_string_into created_at "$actual" createdAt
    assert_json_type "$actual" "$(path_join namespaceReceipt generation)" number \
        "runtime slot ownership namespace generation"
    assert_json_type "$actual" "$(path_join installReceipt generation)" number \
        "runtime slot ownership install generation"
    assert_receipt_generation "$namespace_generation" \
        "runtime slot ownership namespace generation"
    assert_receipt_generation "$install_generation" \
        "runtime slot ownership install generation"
    parse_utc_epoch "$created_at" >/dev/null ||
        fail "runtime slot ownership createdAt must be RFC3339 UTC."

    is_absolute "$runtime_root" &&
        is_absolute "$snapshot_root" &&
        is_absolute "$snapshot_provenance" &&
        is_absolute "$namespace_path" &&
        is_absolute "$install_path" ||
        fail "Runtime slot ownership paths must be absolute."
    [[ "$marketplace_id" == "$SNAPSHOT_MARKETPLACE_ID" &&
       "$plugin_id" == "$SNAPSHOT_PLUGIN_ID" &&
       "$source_fingerprint" == "$SNAPSHOT_SOURCE_FINGERPRINT" &&
       "$runtime_recorded_version" == "$runtime_version" &&
       "$snapshot_recorded_id" == "$snapshot_id" &&
       "$namespace_generation" == "$SNAPSHOT_NAMESPACE_GENERATION" &&
       "$install_generation" == "$SNAPSHOT_INSTALL_GENERATION" ]] ||
        fail "Runtime slot ownership does not match the validated snapshot and installation receipts."
    snapshot_actual_sha256="$(digest_file "$SNAPSHOT_PROVENANCE")"
    [[ "$snapshot_recorded_sha256" == "$snapshot_actual_sha256" ]] ||
        fail "Runtime slot ownership does not match the validated snapshot and installation receipts."
    paths_equal "$runtime_root" "$RUNTIME_SLOT_ROOT" &&
        paths_equal "$snapshot_root" "$SNAPSHOT_ROOT" &&
        paths_equal "$snapshot_provenance" "$SNAPSHOT_PROVENANCE" &&
        paths_equal "$namespace_path" "$SNAPSHOT_NAMESPACE_RECEIPT" &&
        paths_equal "$install_path" "$SNAPSHOT_INSTALL_RECEIPT" ||
        fail "Runtime slot ownership does not match the validated snapshot and installation receipts."

    json_optional_string_into namespace_state "$CTX_NAMESPACE_RECEIPT" state
    shopt -s nullglob dotglob
    entries=("$RUNTIME_SLOT_ROOT"/*)
    shopt -u nullglob dotglob
    for entry in "${entries[@]}"; do
        if [[ "$(basename -- "$entry")" != .runtime-slot-ownership.json ]]; then
            slot_empty=false
            break
        fi
    done
    SLOT_JSON="{
        \"action\":\"slot-validate\",
        \"status\":\"ready\",
        \"reason\":\"runtime-slot-ownership-valid\",
        \"slotRoot\":$(json_quote "$RUNTIME_SLOT_ROOT"),
        \"runtimeVersion\":$(json_quote "$runtime_version"),
        \"ownership\":$(json_quote "$actual"),
        \"snapshotId\":$(json_quote "$snapshot_id"),
        \"snapshotProvenance\":$(json_quote "$SNAPSHOT_PROVENANCE"),
        \"marketplaceId\":$(json_quote "$marketplace_id"),
        \"pluginId\":$(json_quote "$plugin_id"),
        \"sourceFingerprint\":$(json_quote "$source_fingerprint"),
        \"namespaceReceipt\":$(json_quote "$SNAPSHOT_NAMESPACE_RECEIPT"),
        \"installReceipt\":$(json_quote "$SNAPSHOT_INSTALL_RECEIPT"),
        \"namespaceGeneration\":$namespace_generation,
        \"installGeneration\":$install_generation,
        \"namespaceState\":$(json_quote "$namespace_state"),
        \"installState\":$(json_quote "$CTX_INSTALL_STATE"),
        \"slotEmpty\":$slot_empty,
        \"activated\":false,
        \"operative\":false
    }"
}

provision_runtime_slot() {
    local genesis_lock install_lock desired now provenance_sha slot_changed=false
    [[ -n "$CONTEXT" ]] || fail "slot-provision requires --context."
    [[ -n "$EXPECTED_MARKETPLACE_ID" ]] ||
        fail "slot-provision requires --expected-marketplace-id."
    [[ -n "$EXPECTED_PLUGIN_ID" ]] ||
        fail "slot-provision requires --expected-plugin-id."
    [[ -n "$SNAPSHOT_ID" ]] || fail "slot-provision requires --snapshot-id."
    [[ -n "$RUNTIME_VERSION" ]] || fail "slot-provision requires --runtime-version."
    assert_marketplace_id "$EXPECTED_MARKETPLACE_ID"
    assert_plugin_id "$EXPECTED_PLUGIN_ID"
    assert_snapshot_id "$SNAPSHOT_ID"
    assert_runtime_version "$RUNTIME_VERSION"

    COPILOT_PLUGIN_ROOT="" validate_context_receipt \
        "$CONTEXT" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" "$EXPECTED_PLUGIN_ID" "" ""
    genesis_lock="$DURABLE_HOME/marketplaces/.locks/$EXPECTED_MARKETPLACE_ID.genesis"
    install_lock="$CTX_CELL_ROOT/.locks/$EXPECTED_PLUGIN_ID.install.lock"
    acquire_lock "$genesis_lock" genesis "$EXPECTED_MARKETPLACE_ID" "" \
        "$RUNTIME_SLOT_LOCK_TIMEOUT_SECONDS"
    acquire_lock "$install_lock" install "$EXPECTED_MARKETPLACE_ID" "$EXPECTED_PLUGIN_ID" \
        "$RUNTIME_SLOT_LOCK_TIMEOUT_SECONDS"
    COPILOT_PLUGIN_ROOT="" validate_context_receipt \
        "$CTX_INSTALL_RECEIPT" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" \
        "$EXPECTED_PLUGIN_ID" "" "$CTX_CELL_ROOT"
    resolve_runtime_slot_paths "$RUNTIME_VERSION" false
    if [[ -e "$RUNTIME_SLOT_ROOT" || -L "$RUNTIME_SLOT_ROOT" ]]; then
        validate_runtime_slot_ownership \
            "$CTX_INSTALL_RECEIPT" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" \
            "$EXPECTED_PLUGIN_ID" "$SNAPSHOT_ID" "$RUNTIME_VERSION"
        release_lock
        release_lock
        SLOT_JSON="${SLOT_JSON/\"action\":\"slot-validate\"/\"action\":\"slot-provision\"}"
        SLOT_JSON="${SLOT_JSON/\"reason\":\"runtime-slot-ownership-valid\"/\"reason\":\"runtime-slot-ownership-current\"}"
        SLOT_JSON="${SLOT_JSON%\}},\"slotChanged\":false}"
        printf '%s\n' "$SLOT_JSON"
        return
    fi

    validate_snapshot_provenance \
        "$CTX_INSTALL_RECEIPT" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" \
        "$EXPECTED_PLUGIN_ID" "$SNAPSHOT_ID" true
    ensure_versions_root_chain
    resolve_runtime_slot_paths "$RUNTIME_VERSION" false
    if ! mkdir -- "$RUNTIME_SLOT_ROOT" 2>/dev/null; then
        if [[ -e "$RUNTIME_SLOT_ROOT" || -L "$RUNTIME_SLOT_ROOT" ]]; then
            fail "Runtime slot appeared during publication; refusing replacement."
        fi
        fail "Cannot reserve runtime slot '$RUNTIME_SLOT_ROOT'."
    fi
    TEMP_DIRS+=("$RUNTIME_SLOT_ROOT")
    now="$(utc_now)"
    provenance_sha="$(digest_file "$SNAPSHOT_PROVENANCE")"
    desired="{
  \"schema\":\"copilot-extensions.runtime-slot-ownership\",
  \"version\":1,
  \"marketplaceId\":$(json_quote "$EXPECTED_MARKETPLACE_ID"),
  \"pluginId\":$(json_quote "$EXPECTED_PLUGIN_ID"),
  \"sourceFingerprint\":$(json_quote "$SNAPSHOT_SOURCE_FINGERPRINT"),
  \"runtime\":{
    \"version\":$(json_quote "$RUNTIME_VERSION"),
    \"root\":$(json_quote "$RUNTIME_SLOT_ROOT")
  },
  \"snapshot\":{
    \"id\":$(json_quote "$SNAPSHOT_ID"),
    \"root\":$(json_quote "$SNAPSHOT_ROOT"),
    \"provenance\":$(json_quote "$SNAPSHOT_PROVENANCE"),
    \"provenanceSha256\":$(json_quote "$provenance_sha")
  },
  \"namespaceReceipt\":{
    \"path\":$(json_quote "$SNAPSHOT_NAMESPACE_RECEIPT"),
    \"generation\":$SNAPSHOT_NAMESPACE_GENERATION
  },
  \"installReceipt\":{
    \"path\":$(json_quote "$SNAPSHOT_INSTALL_RECEIPT"),
    \"generation\":$SNAPSHOT_INSTALL_GENERATION
  },
  \"createdAt\":$(json_quote "$now")
}"
    publish_json_no_replace \
        "$RUNTIME_OWNERSHIP_PATH" "$desired" "$RUNTIME_SLOT_ROOT"
    slot_changed=true
    validate_runtime_slot_ownership \
        "$CTX_INSTALL_RECEIPT" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" \
        "$EXPECTED_PLUGIN_ID" "$SNAPSHOT_ID" "$RUNTIME_VERSION"
    release_lock
    release_lock
    SLOT_JSON="${SLOT_JSON/\"action\":\"slot-validate\"/\"action\":\"slot-provision\"}"
    SLOT_JSON="${SLOT_JSON/\"reason\":\"runtime-slot-ownership-valid\"/\"reason\":\"runtime-slot-ownership-published\"}"
    SLOT_JSON="${SLOT_JSON%\}},\"slotChanged\":$slot_changed}"
    printf '%s\n' "$SLOT_JSON"
}

emit_source_identity() {
    printf '{'
    printf '"kind":%s,' "$(json_quote "$SOURCE_KIND")"
    printf '"canonical":%s,' "$(json_quote "$SOURCE_CANONICAL")"
    printf '"ref":%s,' "$(json_quote "$SOURCE_REF")"
    printf '"record":%s,' "$(json_quote "$SOURCE_RECORD")"
    printf '"sha256":%s,' "$(json_quote "$SOURCE_SHA256")"
    printf '"fingerprint":%s,' "$(json_quote "$SOURCE_FINGERPRINT")"
    printf '"marketplaceId":%s' "$(json_quote "$MARKETPLACE_ID")"
    printf '}\n'
}

emit_locator() {
    local delimiter="" value
    if [[ "$LOCATOR_KIND" == installed ]]; then
        printf '{"kind":"installed","copilotHome":%s,"marketplaceKey":%s,"declaredIn":[' \
            "$(json_quote "$LOCATOR_COPILOT_HOME")" "$(json_quote "$LOCATOR_MARKETPLACE_KEY")"
        for value in "${LOCATOR_DECLARED_IN[@]}"; do
            printf '%s%s' "$delimiter" "$(json_quote "$value")"
            delimiter=,
        done
        printf ']}'
    elif [[ "$LOCATOR_KIND" == directory ]]; then
        printf '{"kind":"directory","marketplaceRoot":%s}' "$(json_quote "$LOCATOR_MARKETPLACE_ROOT")"
    else
        printf 'null'
    fi
}

emit_resolved_context() {
    local payload="$1" durable_home="$2" cell_root plugin_root
    cell_root="$(canonical_path "$durable_home/marketplaces/$MARKETPLACE_ID")"
    plugin_root="$(canonical_path "$cell_root/plugins/$EVIDENCE_PLUGIN_ID")"
    printf '{'
    printf '"action":"resolve",'
    printf '"source":{"kind":%s,"canonical":%s,"ref":%s,"record":%s},' \
        "$(json_quote "$SOURCE_KIND")" "$(json_quote "$SOURCE_CANONICAL")" \
        "$(json_quote "$SOURCE_REF")" "$(json_quote "$SOURCE_RECORD")"
    printf '"sourceFingerprint":%s,' "$(json_quote "$SOURCE_FINGERPRINT")"
    printf '"marketplaceId":%s,"marketplaceSlot":%s,' \
        "$(json_quote "$MARKETPLACE_ID")" "$(json_quote "$MARKETPLACE_ID")"
    printf '"pluginId":%s,"payloadRoot":%s,' \
        "$(json_quote "$EVIDENCE_PLUGIN_ID")" "$(json_quote "$payload")"
    printf '"cellRoot":%s,"pluginRoot":%s,' \
        "$(json_quote "$cell_root")" "$(json_quote "$plugin_root")"
    printf '"versionsRoot":%s,' "$(json_quote "$(canonical_path "$plugin_root/versions")")"
    printf '"snapshotsRoot":%s,' "$(json_quote "$(canonical_path "$plugin_root/snapshots")")"
    printf '"stateRoot":%s,' "$(json_quote "$(canonical_path "$plugin_root/state")")"
    printf '"runRoot":%s,' "$(json_quote "$(canonical_path "$plugin_root/run")")"
    printf '"logsRoot":%s,' "$(json_quote "$(canonical_path "$plugin_root/logs")")"
    printf '"cacheRoot":%s,' "$(json_quote "$(canonical_path "$plugin_root/cache")")"
    printf '"launchersRoot":%s,' "$(json_quote "$(canonical_path "$plugin_root/launchers")")"
    printf '"reposRoot":%s,' "$(json_quote "$(canonical_path "$cell_root/repos")")"
    printf '"namespaceReceipt":%s,' "$(json_quote "$(canonical_path "$cell_root/namespace.json")")"
    printf '"installReceipt":%s,' "$(json_quote "$(canonical_path "$plugin_root/install.json")")"
    printf '"locator":'
    emit_locator
    printf ',"existingCells":%s,"rebindRequired":false,"operative":false}\n' "$EXISTING_JSON"
}

capture_output() {
    local target="$1"
    shift
    local output status
    set +e
    output="$("$@" 2>&1)"
    status=$?
    set -e
    printf -v "$target" '%s' "$output"
    return "$status"
}

snapshot_value() {
    local snapshot="$1" key="$2" prefix line
    prefix="$key"$'\t'
    while IFS= read -r line; do
        if [[ "$line" == "$prefix"* ]]; then
            printf '%s' "${line#"$prefix"}"
            return 0
        fi
    done <<<"$snapshot"
    return 1
}

snapshot_hex() {
    LC_ALL=C od -An -v -tx1 | tr -d ' \n'
}

snapshot_hex_into() {
    local target="$1" snapshot="$2" key="$3" encoded escapes="" decoded=""
    encoded="$(snapshot_value "$snapshot" "${key}Hex")" || return 1
    [[ "$encoded" =~ ^([0-9a-f]{2})*$ ]] ||
        fail "Invalid encoded snapshot value for '$key'."
    while [[ -n "$encoded" ]]; do
        escapes+="\\x${encoded:0:2}"
        encoded="${encoded:2}"
    done
    printf -v decoded '%b' "$escapes"
    printf -v "$target" '%s' "$decoded"
}

normalized_short_host() {
    local host
    host="$(hostname 2>/dev/null || uname -n 2>/dev/null || printf '')"
    host="${host%%.*}"
    printf '%s' "${host,,}"
}

pid_is_live() {
    local pid="$1"
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
    if kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    [[ -d "/proc/$pid" ]]
}

parse_utc_epoch() {
    local value="$1" epoch
    [[ "$value" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] ||
        return 1
    epoch="$(date -u -d "$value" +%s 2>/dev/null)" || return 1
    [[ "$(date -u -d "@$epoch" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" == "$value" ]] ||
        return 1
    printf '%s' "$epoch"
}

json_bool_literal_or_null() {
    case "$1" in
        true | false) printf '%s' "$1" ;;
        *) printf 'null' ;;
    esac
}

json_number_literal_or_null() {
    if [[ -n "$1" ]]; then
        printf '%s' "$1"
    else
        printf 'null'
    fi
}

json_string_or_null() {
    if [[ -n "$1" ]]; then
        json_quote "$1"
    else
        printf 'null'
    fi
}

CURRENT_PLATFORM=""
CURRENT_PROFILE_HOME=""
CURRENT_WSL_DISTRO=""
CURRENT_WSL_DISTRO_TYPE=null
CURRENT_HOST=""

resolve_current_environment() {
    local uid passwd_entry home_path
    [[ -n "$CURRENT_PROFILE_HOME" ]] && return 0
    CURRENT_PLATFORM=posix
    CURRENT_HOST="$(normalized_short_host)"
    uid="$(id -u 2>/dev/null)" || fail "Cannot determine the current account identity."
    if command -v getent >/dev/null 2>&1; then
        passwd_entry="$(getent passwd "$uid" 2>/dev/null || true)"
    fi
    if [[ -z "$passwd_entry" && -r /etc/passwd ]]; then
        passwd_entry="$(LC_ALL=C awk -F: -v uid="$uid" '$3 == uid { print; exit }' /etc/passwd)"
    fi
    [[ -n "$passwd_entry" ]] ||
        fail "Cannot determine the current account home from the passwd database."
    home_path="$(printf '%s' "$passwd_entry" | LC_ALL=C cut -d: -f6)"
    [[ -n "$home_path" ]] ||
        fail "Cannot determine the current account home from the passwd database."
    CURRENT_PROFILE_HOME="$(canonical_path "$home_path" true)"
    CURRENT_WSL_DISTRO="${WSL_DISTRO_NAME:-}"
    if [[ -n "$CURRENT_WSL_DISTRO" ]]; then
        CURRENT_WSL_DISTRO_TYPE=string
    fi
}

READ_ENV_PLATFORM=""
READ_ENV_HOME_REAL_PATH=""
READ_ENV_WSL_DISTRO=""
READ_ENV_WSL_DISTRO_TYPE=""

read_environment_tuple() {
    local file="$1" prefix="$2" label="$3" wsl_type
    [[ "$(json_type_path "$file" "$prefix")" == object ]] ||
        fail "$label must be a JSON object."
    json_optional_string_into READ_ENV_PLATFORM "$file" "$(path_join "$prefix" platform)"
    case "$READ_ENV_PLATFORM" in
        windows | posix) ;;
        *) fail "$label.platform must be windows or posix." ;;
    esac
    json_optional_string_into READ_ENV_HOME_REAL_PATH "$file" "$(path_join "$prefix" homeRealPath)"
    [[ -n "$READ_ENV_HOME_REAL_PATH" ]] ||
        fail "$label.homeRealPath must be a non-empty string."
    if [[ "$READ_ENV_PLATFORM" == windows ]]; then
        [[ "$READ_ENV_HOME_REAL_PATH" =~ ^[A-Za-z]:[\\/]|^\\\\[^\\/]+[\\/][^\\/]+([\\/]|$) ]] ||
            fail "$label.homeRealPath must be absolute."
    else
        is_absolute "$READ_ENV_HOME_REAL_PATH" ||
            fail "$label.homeRealPath must be absolute."
    fi
    wsl_type="$(json_optional_type_path "$file" "$(path_join "$prefix" wslDistro)")"
    READ_ENV_WSL_DISTRO_TYPE="$wsl_type"
    case "$wsl_type" in
        null) READ_ENV_WSL_DISTRO="" ;;
        string) json_optional_string_into READ_ENV_WSL_DISTRO "$file" "$(path_join "$prefix" wslDistro)" ;;
        *) fail "$label.wslDistro must be a string or null." ;;
    esac
    if [[ "$READ_ENV_PLATFORM" == windows && "$wsl_type" != null ]]; then
        fail "$label.wslDistro must be null on Windows."
    fi
}

LEGACY_PROBE_DECLARED=false
LEGACY_PROBE_RESULT=unknown
LEGACY_PROBE_CHECKED_AT=""

legacy_probe_snapshot() {
    local file="$1" prefix="${2-}" declared_path result_path checked_path checked_type checked_epoch
    declared_path="$(path_join "$prefix" declared)"
    result_path="$(path_join "$prefix" result)"
    checked_path="$(path_join "$prefix" checkedAt)"
    [[ "$(json_type_path "$file" "$prefix")" == object ]] ||
        fail "Legacy probe input must be a JSON object."
    [[ -n "$(json_optional_type_path "$file" "$declared_path")" ]] ||
        fail "Legacy probe input requires declared."
    [[ "$(json_type_path "$file" "$declared_path")" == boolean ]] ||
        fail "Legacy probe input declared must be a boolean."
    [[ -n "$(json_optional_type_path "$file" "$result_path")" ]] ||
        fail "Legacy probe input requires result."
    [[ "$(json_type_path "$file" "$result_path")" == string ]] ||
        fail "Legacy probe input result must be a string."
    [[ -n "$(json_optional_type_path "$file" "$checked_path")" ]] ||
        fail "Legacy probe input requires checkedAt."
    LEGACY_PROBE_DECLARED="$(json_optional_path "$file" "$declared_path")"
    json_optional_string_into LEGACY_PROBE_RESULT "$file" "$result_path"
    case "$LEGACY_PROBE_RESULT" in
        absent | present | unknown) ;;
        *) fail "Legacy probe input result must be absent, present, or unknown." ;;
    esac
    if [[ "$LEGACY_PROBE_DECLARED" != true && "$LEGACY_PROBE_RESULT" != unknown ]]; then
        fail "Legacy probe input result must be unknown when declared is false."
    fi
    checked_type="$(json_optional_type_path "$file" "$checked_path")"
    case "$checked_type" in
        null) LEGACY_PROBE_CHECKED_AT="" ;;
        string)
            json_optional_string_into LEGACY_PROBE_CHECKED_AT "$file" "$checked_path"
            checked_epoch="$(parse_utc_epoch "$LEGACY_PROBE_CHECKED_AT")" ||
                fail "Legacy probe input checkedAt must be RFC3339 UTC."
            [[ -n "$checked_epoch" ]] || fail "Legacy probe input checkedAt must be RFC3339 UTC."
            ;;
        *) fail "Legacy probe input checkedAt must be a string or null." ;;
    esac
}

policy_snapshot() {
    local file="$1" marketplace_id="$2" plugin_id="$3"
    local schema version installation_mode_type marketplaces_type path keys marketplace_key plugins_type
    local enabled_type plugin_path marketplace_path enabled reason scope plugin_key
    local global_enabled="" marketplace_enabled="" plugin_enabled=""
    [[ "$(json_type_path "$file" "")" == object ]] ||
        fail "Policy file must be a JSON object."
    [[ "$(json_type_path "$file" schema)" == string ]] ||
        fail "Policy schema must be a string."
    json_optional_string_into schema "$file" schema
    [[ "$schema" == copilot-extensions.installation-mode ]] ||
        fail "Policy schema must be copilot-extensions.installation-mode."
    [[ "$(json_type_path "$file" version)" == number ]] ||
        fail "Policy version must be a number."
    version="$(json_optional_path "$file" version)"
    [[ "$version" =~ ^[0-9]+$ ]] ||
        fail "Policy version must be a positive integer."
    if ((version > 1)); then
        printf 'state\tunsupported\nscope\tdefault\nenabled\tnull\nreason\tpolicy-version-unsupported\n'
        return 0
    fi
    ((version == 1)) || fail "Policy version must be 1."
    installation_mode_type="$(json_optional_type_path "$file" installationMode)"
    case "$installation_mode_type" in
        "") printf 'state\tvalid\nscope\tdefault\nenabled\tfalse\nreason\tpolicy-default-false\n'; return 0 ;;
        object) ;;
        *) fail "installationMode must be a JSON object." ;;
    esac

    enabled_type="$(json_optional_type_path "$file" "$(path_join installationMode enabled)")"
    case "$enabled_type" in
        "") ;;
        boolean) global_enabled="$(json_optional_path "$file" "$(path_join installationMode enabled)")" ;;
        *) fail "installationMode.enabled must be a boolean." ;;
    esac

    marketplaces_type="$(json_optional_type_path "$file" "$(path_join installationMode marketplaces)")"
    case "$marketplaces_type" in
        "") ;;
        object)
            keys="$(json_object_keys "$file" "$(path_join installationMode marketplaces)")"
            while IFS= read -r marketplace_key; do
                [[ -n "$marketplace_key" ]] || continue
                [[ "$marketplace_key" =~ ^[a-z0-9]+(-[a-z0-9]+)*--[0-9a-f]{16}$ ]] ||
                    fail "Policy marketplace ids must be exact source-derived ids."
                marketplace_path="$(path_join "$(path_join installationMode marketplaces)" "$marketplace_key")"
                [[ "$(json_type_path "$file" "$marketplace_path")" == object ]] ||
                    fail "Policy marketplace entries must be JSON objects."
                enabled_type="$(json_optional_type_path "$file" "$(path_join "$marketplace_path" enabled)")"
                case "$enabled_type" in
                    "") ;;
                    boolean)
                        if [[ "$marketplace_key" == "$marketplace_id" ]]; then
                            marketplace_enabled="$(json_optional_path "$file" "$(path_join "$marketplace_path" enabled)")"
                        fi
                        ;;
                    *) fail "Policy marketplace enabled values must be booleans." ;;
                esac
                plugins_type="$(json_optional_type_path "$file" "$(path_join "$marketplace_path" plugins)")"
                case "$plugins_type" in
                    "") ;;
                    object)
                        path="$(path_join "$marketplace_path" plugins)"
                        while IFS= read -r plugin_key; do
                            [[ -n "$plugin_key" ]] || continue
                            assert_plugin_id "$plugin_key"
                            plugin_path="$(path_join "$path" "$plugin_key")"
                            [[ "$(json_type_path "$file" "$plugin_path")" == object ]] ||
                                fail "Policy plugin entries must be JSON objects."
                            enabled_type="$(json_optional_type_path "$file" "$(path_join "$plugin_path" enabled)")"
                            case "$enabled_type" in
                                "") ;;
                                boolean)
                                    if [[ "$marketplace_key" == "$marketplace_id" && "$plugin_key" == "$plugin_id" ]]; then
                                        plugin_enabled="$(json_optional_path "$file" "$(path_join "$plugin_path" enabled)")"
                                    fi
                                    ;;
                                *) fail "Policy plugin enabled values must be booleans." ;;
                            esac
                        done < <(json_object_keys "$file" "$path")
                        ;;
                    *) fail "Policy marketplace plugin maps must be JSON objects." ;;
                esac
            done <<<"$keys"
            ;;
        *) fail "installationMode.marketplaces must be a JSON object." ;;
    esac

    if [[ -n "$plugin_enabled" ]]; then
        scope=plugin
        enabled="$plugin_enabled"
    elif [[ -n "$marketplace_enabled" ]]; then
        scope=marketplace
        enabled="$marketplace_enabled"
    elif [[ -n "$global_enabled" ]]; then
        scope=global
        enabled="$global_enabled"
    else
        scope=default
        enabled=false
    fi
    if [[ "$enabled" == true ]]; then
        reason="policy-${scope}-true"
    else
        reason="policy-${scope}-false"
    fi
    printf 'state\tvalid\nscope\t%s\nenabled\t%s\nreason\t%s\n' "$scope" "$enabled" "$reason"
}

maintenance_sidecar_snapshot() {
    local file="$1" owner host pid reason entered_at expected_until entered_epoch expected_epoch now_epoch state
    [[ "$(json_type_path "$file" "")" == object ]] ||
        fail "Maintenance sidecar must be a JSON object."
    [[ "$(json_type_path "$file" owner)" == string ]] || fail "Maintenance owner must be a string."
    [[ "$(json_type_path "$file" host)" == string ]] || fail "Maintenance host must be a string."
    [[ "$(json_type_path "$file" pid)" == number ]] || fail "Maintenance pid must be a number."
    [[ "$(json_type_path "$file" reason)" == string ]] || fail "Maintenance reason must be a string."
    [[ "$(json_type_path "$file" enteredAt)" == string ]] || fail "Maintenance enteredAt must be a string."
    [[ "$(json_type_path "$file" expectedUntil)" == string ]] || fail "Maintenance expectedUntil must be a string."
    json_optional_string_into owner "$file" owner
    json_optional_string_into host "$file" host
    pid="$(json_optional_path "$file" pid)"
    json_optional_string_into reason "$file" reason
    json_optional_string_into entered_at "$file" enteredAt
    json_optional_string_into expected_until "$file" expectedUntil
    [[ -n "$owner" && -n "$host" && -n "$reason" ]] ||
        fail "Maintenance sidecar fields must be non-empty."
    host="${host%%.*}"
    host="${host,,}"
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] ||
        fail "Maintenance pid must be a positive integer."
    entered_epoch="$(parse_utc_epoch "$entered_at")" ||
        fail "Maintenance enteredAt must be RFC3339 UTC."
    expected_epoch="$(parse_utc_epoch "$expected_until")" ||
        fail "Maintenance expectedUntil must be RFC3339 UTC."
    now_epoch="$(date -u +%s)"
    state=stale
    if [[ "$host" == "$CURRENT_HOST" ]] && pid_is_live "$pid" &&
        ((entered_epoch <= now_epoch)) && ((now_epoch <= expected_epoch)); then
        state=active
    fi
    printf 'state\t%s\nowner\t%s\nhost\t%s\npid\t%s\nreason\t%s\nenteredAt\t%s\nexpectedUntil\t%s\n' \
        "$state" "$owner" "$host" "$pid" "$reason" "$entered_at" "$expected_until"
}

activation_snapshot() {
    local file="$1" durable_home="$2" marketplace_id="$3" plugin_id="$4" plugin_root="$5" legacy_root="$6"
    local schema version mode state context generation namespace_generation install_generation
    local cell_root current_install_generation
    [[ "$(json_type_path "$file" "")" == object ]] ||
        fail "Activation receipt must be a JSON object."
    [[ "$(json_type_path "$file" schema)" == string ]] ||
        fail "Activation schema must be a string."
    [[ "$(json_type_path "$file" version)" == number ]] ||
        fail "Activation version must be a number."
    json_optional_string_into schema "$file" schema
    version="$(json_optional_path "$file" version)"
    [[ "$schema" == copilot-extensions.installation-activation && "$version" == 1 ]] ||
        fail "Activation schema or version is unsupported."
    json_optional_string_into READ_ENV_PLATFORM "$file" marketplaceId
    [[ "$READ_ENV_PLATFORM" == "$marketplace_id" ]] ||
        fail "Activation marketplaceId does not match the resolved marketplace."
    json_optional_string_into READ_ENV_PLATFORM "$file" pluginId
    [[ "$READ_ENV_PLATFORM" == "$plugin_id" ]] ||
        fail "Activation pluginId does not match the resolved plugin."
    [[ "$(json_type_path "$file" mode)" == string ]] || fail "Activation mode must be a string."
    [[ "$(json_type_path "$file" state)" == string ]] || fail "Activation state must be a string."
    json_optional_string_into mode "$file" mode
    json_optional_string_into state "$file" state
    case "$mode/$state" in
        namespaced/active | legacy/deactivated) ;;
        *) fail "Activation mode/state is invalid." ;;
    esac
    read_environment_tuple "$file" environment "activation environment"
    if [[ "$READ_ENV_PLATFORM" != "$CURRENT_PLATFORM" ||
        "$READ_ENV_HOME_REAL_PATH" != "$CURRENT_PROFILE_HOME" ||
        "$READ_ENV_WSL_DISTRO_TYPE" != "$CURRENT_WSL_DISTRO_TYPE" ||
        "$READ_ENV_WSL_DISTRO" != "$CURRENT_WSL_DISTRO" ]]; then
        printf 'status\tforeign-environment\nmode\t%s\nstate\t%s\ncontextHex\t\nactivationGeneration\t\ninstallGeneration\t\nruntimeRootHex\t%s\n' \
            "$mode" "$state" \
            "$(printf '%s' "$([[ "$mode" == namespaced ]] && printf '%s' "$plugin_root" || printf '%s' "$legacy_root")" | snapshot_hex)"
        return 0
    fi
    [[ "$(json_type_path "$file" context)" == string ]] || fail "Activation context must be a string."
    json_optional_string_into context "$file" context
    is_absolute "$context" || fail "Activation context must be absolute."
    cell_root="$(canonical_path "$durable_home/marketplaces/$marketplace_id")"
    paths_equal "$context" "$plugin_root/install.json" ||
        fail "Activation context is not the canonical install receipt."
    [[ "$(json_type_path "$file" namespaceGeneration)" == number ]] ||
        fail "Activation namespaceGeneration must be a number."
    [[ "$(json_type_path "$file" installGeneration)" == number ]] ||
        fail "Activation installGeneration must be a number."
    [[ "$(json_type_path "$file" generation)" == number ]] ||
        fail "Activation generation must be a number."
    namespace_generation="$(json_optional_path "$file" namespaceGeneration)"
    install_generation="$(json_optional_path "$file" installGeneration)"
    generation="$(json_optional_path "$file" generation)"
    assert_receipt_generation "$namespace_generation" "activation namespaceGeneration"
    assert_receipt_generation "$install_generation" "activation installGeneration"
    assert_receipt_generation "$generation" "activation generation"
    [[ "$(json_type_path "$file" legacy)" == object ]] ||
        fail "Activation legacy evidence must be a JSON object."
    [[ "$(json_type_path "$file" "$(path_join legacy disposition)")" == string ]] ||
        fail "Activation legacy disposition must be a string."
    json_optional_string_into READ_ENV_PLATFORM "$file" "$(path_join legacy disposition)"
    case "$READ_ENV_PLATFORM" in
        absent | quiesced | retained-inert | restored) ;;
        *) fail "Activation legacy disposition is invalid." ;;
    esac
    legacy_probe_snapshot "$file" "$(path_join legacy probe)"
    [[ "$(json_type_path "$file" createdAt)" == string ]] ||
        fail "Activation createdAt must be a string."
    [[ "$(json_type_path "$file" updatedAt)" == string ]] ||
        fail "Activation updatedAt must be a string."
    json_optional_string_into READ_ENV_PLATFORM "$file" createdAt
    parse_utc_epoch "$READ_ENV_PLATFORM" >/dev/null ||
        fail "Activation createdAt must be RFC3339 UTC."
    json_optional_string_into READ_ENV_HOME_REAL_PATH "$file" updatedAt
    parse_utc_epoch "$READ_ENV_HOME_REAL_PATH" >/dev/null ||
        fail "Activation updatedAt must be RFC3339 UTC."
    [[ "$READ_ENV_HOME_REAL_PATH" < "$READ_ENV_PLATFORM" ]] &&
        fail "Activation updatedAt precedes createdAt."
    validate_context_receipt "$context" "$durable_home" "$marketplace_id" "$plugin_id" "" "$cell_root"
    current_install_generation="$(json_optional_path "$context" generation)"
    if [[ "$namespace_generation" != "$NS_GENERATION" || "$install_generation" != "$current_install_generation" ]]; then
        printf 'status\trevalidation-required\nmode\t%s\nstate\t%s\ncontextHex\t%s\nactivationGeneration\t%s\ninstallGeneration\t%s\nruntimeRootHex\t%s\n' \
            "$mode" "$state" "$(printf '%s' "$context" | snapshot_hex)" \
            "$generation" "$current_install_generation" \
            "$(printf '%s' "$([[ "$mode" == namespaced ]] && printf '%s' "$plugin_root" || printf '%s' "$legacy_root")" | snapshot_hex)"
        return 0
    fi
    printf 'status\tvalid\nmode\t%s\nstate\t%s\ncontextHex\t%s\nactivationGeneration\t%s\ninstallGeneration\t%s\nruntimeRootHex\t%s\n' \
        "$mode" "$state" "$(printf '%s' "$context" | snapshot_hex)" \
        "$generation" "$current_install_generation" \
        "$(printf '%s' "$([[ "$mode" == namespaced ]] && printf '%s' "$plugin_root" || printf '%s' "$legacy_root")" | snapshot_hex)"
}

activation_cas() {
    local context="$CONTEXT" cell_root plugin_root activation genesis_lock install_lock
    local actual_namespace_generation actual_install_generation actual_activation_generation=0
    local activation_info="" activation_status created_at now next_generation
    local checked_at_json wsl_distro_json activation_json

    [[ "$CONTEXT_SUPPLIED" == true && -n "$context" ]] ||
        fail "activation-cas requires --context."
    [[ -n "$EXPECTED_MARKETPLACE_ID" ]] ||
        fail "activation-cas requires --expected-marketplace-id."
    [[ -n "$EXPECTED_PLUGIN_ID" ]] ||
        fail "activation-cas requires --expected-plugin-id."
    assert_marketplace_id "$EXPECTED_MARKETPLACE_ID"
    assert_plugin_id "$EXPECTED_PLUGIN_ID"
    normalize_expected_generation_into EXPECTED_NAMESPACE_GENERATION \
        "$EXPECTED_NAMESPACE_GENERATION" namespace
    normalize_expected_generation_into EXPECTED_INSTALL_GENERATION \
        "$EXPECTED_INSTALL_GENERATION" install
    normalize_expected_generation_into EXPECTED_ACTIVATION_GENERATION \
        "$EXPECTED_ACTIVATION_GENERATION" activation
    case "$ACTIVATION_MODE/$ACTIVATION_STATE" in
        namespaced/active | legacy/deactivated) ;;
        *) fail "Activation mode/state pair is invalid." ;;
    esac
    case "$LEGACY_DISPOSITION" in
        absent | quiesced | retained-inert | restored) ;;
        *) fail "Activation legacy disposition is invalid." ;;
    esac
    [[ "$LEGACY_PROBE_JSON_SUPPLIED" == true || "$LEGACY_PROBE_FILE_SUPPLIED" == true ]] ||
        fail "activation-cas requires --legacy-probe-json or --legacy-probe-file."
    legacy_probe_snapshot "$LEGACY_PROBE_FILE"
    resolve_current_environment

    is_absolute "$context" || fail "Activation context must be absolute."
    context="$(canonical_path "$context" true)"
    cell_root="$(canonical_path "$DURABLE_HOME/marketplaces/$EXPECTED_MARKETPLACE_ID")"
    plugin_root="$(canonical_path "$cell_root/plugins/$EXPECTED_PLUGIN_ID")"
    paths_equal "$context" "$plugin_root/install.json" ||
        fail "Activation context is not the canonical install receipt."
    if [[ -n "$LEGACY_ROOT" ]]; then
        is_absolute "$LEGACY_ROOT" || fail "--legacy-root must be absolute."
        LEGACY_ROOT="$(canonical_path "$LEGACY_ROOT")"
    else
        LEGACY_ROOT="$(canonical_path "$CURRENT_PROFILE_HOME/.$EXPECTED_PLUGIN_ID")"
    fi
    activation="$plugin_root/installation-activation.json"
    genesis_lock="$DURABLE_HOME/marketplaces/.locks/$EXPECTED_MARKETPLACE_ID.genesis"
    install_lock="$cell_root/.locks/$EXPECTED_PLUGIN_ID.install.lock"

    acquire_lock "$genesis_lock" genesis "$EXPECTED_MARKETPLACE_ID"
    acquire_lock "$install_lock" install "$EXPECTED_MARKETPLACE_ID" "$EXPECTED_PLUGIN_ID"
    COPILOT_PLUGIN_ROOT="" validate_context_receipt \
        "$context" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" "$EXPECTED_PLUGIN_ID" "" "$cell_root"
    actual_namespace_generation="$NS_GENERATION"
    actual_install_generation="$(json_optional_path "$context" generation)"
    if [[ -e "$activation" || -L "$activation" ]]; then
        [[ -f "$activation" && ! -L "$activation" ]] ||
            fail "Existing activation receipt is invalid."
        activation_info="$(activation_snapshot \
            "$activation" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" \
            "$EXPECTED_PLUGIN_ID" "$plugin_root" "$LEGACY_ROOT")"
        activation_status="$(snapshot_value "$activation_info" status)"
        case "$activation_status" in
            valid | revalidation-required)
                actual_activation_generation="$(snapshot_value "$activation_info" activationGeneration)"
                ;;
            foreign-environment)
                fail "Existing activation receipt belongs to a foreign environment."
                ;;
            *)
                fail "Existing activation receipt is invalid."
                ;;
        esac
    fi

    if [[ "$actual_namespace_generation" != "$EXPECTED_NAMESPACE_GENERATION" ||
        "$actual_install_generation" != "$EXPECTED_INSTALL_GENERATION" ||
        "$actual_activation_generation" != "$EXPECTED_ACTIVATION_GENERATION" ]]; then
        release_lock
        release_lock
        printf '{'
        printf '"action":"activation-cas","status":"revalidation-required","reason":"generation-changed",'
        printf '"activation":%s,' "$(json_string_or_null "$([[ -e "$activation" ]] && printf '%s' "$(canonical_path "$activation")")")"
        printf '"activationChanged":false,'
        printf '"activationGeneration":%s,' "$actual_activation_generation"
        printf '"namespaceGeneration":%s,' "$actual_namespace_generation"
        printf '"installGeneration":%s,' "$actual_install_generation"
        printf '"expectedActivationGeneration":%s,' "$EXPECTED_ACTIVATION_GENERATION"
        printf '"expectedNamespaceGeneration":%s,' "$EXPECTED_NAMESPACE_GENERATION"
        printf '"expectedInstallGeneration":%s,' "$EXPECTED_INSTALL_GENERATION"
        printf '"operative":false}\n'
        return 0
    fi

    local namespace_state install_state
    json_optional_string_into namespace_state "$cell_root/namespace.json" state
    json_optional_string_into install_state "$context" state
    [[ "$namespace_state" == active && "$install_state" == active ]] ||
        fail "Activation requires active namespace and install receipts."

    [[ "$actual_activation_generation" != 9223372036854775807 ]] ||
        fail "installation-activation.json generation cannot be incremented; explicit repair is required."
    next_generation=$((actual_activation_generation + 1))
    now="$(utc_now)"
    created_at="$now"
    if [[ -f "$activation" ]]; then
        json_optional_string_into created_at "$activation" createdAt "$now"
    fi
    if [[ -n "$LEGACY_PROBE_CHECKED_AT" ]]; then
        checked_at_json="$(json_quote "$LEGACY_PROBE_CHECKED_AT")"
    else
        checked_at_json=null
    fi
    if [[ "$CURRENT_WSL_DISTRO_TYPE" == string ]]; then
        wsl_distro_json="$(json_quote "$CURRENT_WSL_DISTRO")"
    else
        wsl_distro_json=null
    fi
    activation_json="{
  \"schema\":\"copilot-extensions.installation-activation\",
  \"version\":1,
  \"marketplaceId\":$(json_quote "$EXPECTED_MARKETPLACE_ID"),
  \"pluginId\":$(json_quote "$EXPECTED_PLUGIN_ID"),
  \"mode\":$(json_quote "$ACTIVATION_MODE"),
  \"state\":$(json_quote "$ACTIVATION_STATE"),
  \"environment\":{
    \"platform\":$(json_quote "$CURRENT_PLATFORM"),
    \"homeRealPath\":$(json_quote "$CURRENT_PROFILE_HOME"),
    \"wslDistro\":$wsl_distro_json
  },
  \"context\":$(json_quote "$context"),
  \"namespaceGeneration\":$actual_namespace_generation,
  \"installGeneration\":$actual_install_generation,
  \"generation\":$next_generation,
  \"legacy\":{
    \"disposition\":$(json_quote "$LEGACY_DISPOSITION"),
    \"probe\":{
      \"declared\":$LEGACY_PROBE_DECLARED,
      \"result\":$(json_quote "$LEGACY_PROBE_RESULT"),
      \"checkedAt\":$checked_at_json
    }
  },
  \"createdAt\":$(json_quote "$created_at"),
  \"updatedAt\":$(json_quote "$now")
}"
    atomic_write_json "$activation" "$activation_json"
    activation_info="$(activation_snapshot \
        "$activation" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" \
        "$EXPECTED_PLUGIN_ID" "$plugin_root" "$LEGACY_ROOT")"
    [[ "$(snapshot_value "$activation_info" status)" == valid ]] ||
        fail "Published activation receipt did not validate as current."
    release_lock
    release_lock
    printf '{'
    printf '"action":"activation-cas","status":"ready","reason":"activation-published",'
    printf '"activation":%s,"activationChanged":true,' "$(json_quote "$(canonical_path "$activation")")"
    printf '"activationGeneration":%s,' "$next_generation"
    printf '"namespaceGeneration":%s,' "$actual_namespace_generation"
    printf '"installGeneration":%s,' "$actual_install_generation"
    printf '"environment":{"platform":%s,"homeRealPath":%s,"wslDistro":%s},' \
        "$(json_quote "$CURRENT_PLATFORM")" "$(json_quote "$CURRENT_PROFILE_HOME")" "$wsl_distro_json"
    printf '"mode":%s,"state":%s,"context":%s,"operative":false}\n' \
        "$(json_quote "$ACTIVATION_MODE")" "$(json_quote "$ACTIVATION_STATE")" "$(json_quote "$context")"
}

tombstone_snapshot() {
    local file="$1" durable_home="$2" current_marketplace_id="$3" current_plugin_id="$4"
    local schema version owner_marketplace_id owner_plugin_id activation_path activation_generation disposition activation_info activation_status activation_mode activation_state
    [[ "$(json_type_path "$file" "")" == object ]] ||
        fail "Tombstone must be a JSON object."
    [[ "$(json_type_path "$file" schema)" == string ]] || fail "Tombstone schema must be a string."
    [[ "$(json_type_path "$file" version)" == number ]] || fail "Tombstone version must be a number."
    json_optional_string_into schema "$file" schema
    version="$(json_optional_path "$file" version)"
    [[ "$schema" == copilot-extensions.legacy-installation-ownership && "$version" == 1 ]] ||
        fail "Tombstone schema or version is unsupported."
    [[ "$(json_type_path "$file" marketplaceId)" == string ]] ||
        fail "Tombstone marketplaceId must be a string."
    [[ "$(json_type_path "$file" pluginId)" == string ]] ||
        fail "Tombstone pluginId must be a string."
    json_optional_string_into owner_marketplace_id "$file" marketplaceId
    json_optional_string_into owner_plugin_id "$file" pluginId
    [[ "$owner_marketplace_id" =~ ^[a-z0-9]+(-[a-z0-9]+)*--[0-9a-f]{16}$ ]] ||
        fail "Tombstone marketplaceId must be an exact source-derived id."
    assert_plugin_id "$owner_plugin_id"
    if [[ -n "$current_plugin_id" && "$owner_plugin_id" != "$current_plugin_id" ]]; then
        fail "Tombstone pluginId does not match the resolved plugin."
    fi
    read_environment_tuple "$file" environment "tombstone environment"
    if [[ "$READ_ENV_PLATFORM" != "$CURRENT_PLATFORM" ||
        "$READ_ENV_HOME_REAL_PATH" != "$CURRENT_PROFILE_HOME" ||
        "$READ_ENV_WSL_DISTRO_TYPE" != "$CURRENT_WSL_DISTRO_TYPE" ||
        "$READ_ENV_WSL_DISTRO" != "$CURRENT_WSL_DISTRO" ]]; then
        printf 'status\tforeign-environment\ndisposition\torphaned-transfer\nownerMarketplaceId\t%s\nactivation\t\n' \
            "$owner_marketplace_id"
        return 0
    fi
    [[ "$(json_type_path "$file" activation)" == object ]] ||
        fail "Tombstone activation must be a JSON object."
    [[ "$(json_type_path "$file" "$(path_join activation path)")" == string ]] ||
        fail "Tombstone activation.path must be a string."
    [[ "$(json_type_path "$file" "$(path_join activation generation)")" == number ]] ||
        fail "Tombstone activation.generation must be a number."
    json_optional_string_into activation_path "$file" "$(path_join activation path)"
    activation_generation="$(json_optional_path "$file" "$(path_join activation generation)")"
    is_absolute "$activation_path" || fail "Tombstone activation.path must be absolute."
    assert_receipt_generation "$activation_generation" "tombstone activation generation"
    [[ "$(json_type_path "$file" transferredAt)" == string ]] ||
        fail "Tombstone transferredAt must be a string."
    json_optional_string_into READ_ENV_PLATFORM "$file" transferredAt
    parse_utc_epoch "$READ_ENV_PLATFORM" >/dev/null ||
        fail "Tombstone transferredAt must be RFC3339 UTC."
    paths_equal "$activation_path" "$durable_home/marketplaces/$owner_marketplace_id/plugins/$owner_plugin_id/installation-activation.json" ||
        fail "Tombstone activation.path is not canonical for its owner."
    if [[ ! -f "$activation_path" ]]; then
        printf 'status\torphaned-transfer\ndisposition\torphaned-transfer\nownerMarketplaceId\t%s\nactivation\t%s\n' \
            "$owner_marketplace_id" "$activation_path"
        return 0
    fi
    if ! capture_output activation_info activation_snapshot \
        "$activation_path" "$durable_home" "$owner_marketplace_id" "$owner_plugin_id" \
        "$(canonical_path "$durable_home/marketplaces/$owner_marketplace_id/plugins/$owner_plugin_id")" \
        "$(canonical_path "$(dirname -- "$file")")"; then
        printf 'status\torphaned-transfer\ndisposition\torphaned-transfer\nownerMarketplaceId\t%s\nactivation\t%s\n' \
            "$owner_marketplace_id" "$activation_path"
        return 0
    fi
    activation_status="$(snapshot_value "$activation_info" status || true)"
    activation_mode="$(snapshot_value "$activation_info" mode || true)"
    activation_state="$(snapshot_value "$activation_info" state || true)"
    if [[ "$activation_status" != valid || "$activation_mode" != namespaced || "$activation_state" != active ]]; then
        printf 'status\torphaned-transfer\ndisposition\torphaned-transfer\nownerMarketplaceId\t%s\nactivation\t%s\n' \
            "$owner_marketplace_id" "$activation_path"
        return 0
    fi
    if [[ "$(snapshot_value "$activation_info" activationGeneration || true)" != "$activation_generation" ]]; then
        printf 'status\torphaned-transfer\ndisposition\torphaned-transfer\nownerMarketplaceId\t%s\nactivation\t%s\n' \
            "$owner_marketplace_id" "$activation_path"
        return 0
    fi
    if [[ "$owner_marketplace_id" == "$current_marketplace_id" && "$owner_plugin_id" == "$current_plugin_id" ]]; then
        disposition=owned-by-current-cell
    else
        disposition=owned-by-other-cell
    fi
    printf 'status\tvalid\ndisposition\t%s\nownerMarketplaceId\t%s\nactivation\t%s\n' \
        "$disposition" "$owner_marketplace_id" "$activation_path"
}

status_identity_snapshot() {
    if [[ -n "$SOURCE_FILE" ]]; then
        [[ -n "$PLUGIN_ID" ]] || fail "Explicit source resolution requires --plugin-id."
        normalize_source "$SOURCE_FILE" ""
        EVIDENCE_PLUGIN_ID="$PLUGIN_ID"
        EVIDENCE_READABLE_NAME="${MARKETPLACE_KEY:-marketplace}"
    else
        resolve_installed_evidence "$PAYLOAD_ROOT" "$COPILOT_HOME" "$PROJECT_ROOT"
        if [[ "$EVIDENCE_FOUND" != true ]]; then
            resolve_directory_evidence "$PAYLOAD_ROOT" "$PLUGIN_ID"
        fi
        if [[ "$EVIDENCE_FOUND" != true ]]; then
            fail "Cannot establish marketplace provenance for payload '$PAYLOAD_ROOT'. Supply an explicit source descriptor for management/development mode."
        fi
        [[ -z "$PLUGIN_ID" || "$PLUGIN_ID" == "$EVIDENCE_PLUGIN_ID" ]] ||
            fail "Expected plugin '$PLUGIN_ID', payload evidence identifies '$EVIDENCE_PLUGIN_ID'."
    fi
    assert_plugin_id "$EVIDENCE_PLUGIN_ID"
    derive_identity "$EVIDENCE_READABLE_NAME"
    find_existing_source "$DURABLE_HOME" "$SOURCE_FINGERPRINT" "$MARKETPLACE_ID"
    if [[ "$EXISTING_JSON" =~ \"sameId\":false || "$EXISTING_JSON" =~ \"locatorMatch\":false ]]; then
        fail "Source '$SOURCE_FINGERPRINT' already owns another cell/locator; explicit rebind or new-cell intent is required."
    fi
    printf 'marketplaceId\t%s\npluginId\t%s\n' "$MARKETPLACE_ID" "$EVIDENCE_PLUGIN_ID"
}

POLICY_RESOLVED_PATH=""
POLICY_AUTHORITATIVE=false
POLICY_STATE=missing
POLICY_SCOPE=default
POLICY_ENABLED=false
POLICY_REASON=policy-default-false

resolve_policy_result() {
    local marketplace_id="$1" plugin_id="$2" snapshot policy_entry
    resolve_current_environment
    policy_entry="$CURRENT_PROFILE_HOME/.copilot-extensions/installation-mode.json"
    POLICY_AUTHORITATIVE=true
    if [[ -n "$POLICY_PATH" ]]; then
        policy_entry="$POLICY_PATH"
        POLICY_AUTHORITATIVE=false
    fi
    POLICY_RESOLVED_PATH="$(canonical_path "$policy_entry")"
    POLICY_STATE=missing
    POLICY_SCOPE=default
    POLICY_ENABLED=false
    POLICY_REASON=policy-default-false
    if [[ ! -e "$policy_entry" && ! -L "$policy_entry" ]]; then
        if [[ "$POLICY_AUTHORITATIVE" != true ]]; then
            POLICY_REASON=policy-injected-non-authoritative
        fi
        return 0
    fi
    if [[ -L "$policy_entry" || ! -f "$policy_entry" ]]; then
        POLICY_STATE=invalid
        POLICY_ENABLED=
        POLICY_REASON=policy-invalid
        return 0
    fi
    if ! capture_output snapshot policy_snapshot "$POLICY_RESOLVED_PATH" "$marketplace_id" "$plugin_id"; then
        POLICY_STATE=invalid
        POLICY_SCOPE=default
        POLICY_ENABLED=
        POLICY_REASON=policy-invalid
        return 0
    fi
    POLICY_STATE="$(snapshot_value "$snapshot" state || printf valid)"
    POLICY_SCOPE="$(snapshot_value "$snapshot" scope || printf default)"
    POLICY_ENABLED="$(snapshot_value "$snapshot" enabled || printf false)"
    POLICY_REASON="$(snapshot_value "$snapshot" reason || printf policy-default-false)"
    if [[ "$POLICY_AUTHORITATIVE" != true && "$POLICY_STATE" == valid ]]; then
        POLICY_REASON=policy-injected-non-authoritative
    fi
}

MAINTENANCE_STATE=inactive
MAINTENANCE_SCOPE=none
MAINTENANCE_MARKER=""
MAINTENANCE_SIDECAR=""
MAINTENANCE_OWNER=""
MAINTENANCE_HOST=""
MAINTENANCE_PID=""
MAINTENANCE_REASON_TEXT=""
MAINTENANCE_ENTERED_AT=""
MAINTENANCE_EXPECTED_UNTIL=""

apply_maintenance_marker() {
    local scope="$1" marker="$2" sidecar="$3" snapshot
    MAINTENANCE_SCOPE="$scope"
    MAINTENANCE_MARKER="$marker"
    MAINTENANCE_SIDECAR="$sidecar"
    MAINTENANCE_OWNER=""
    MAINTENANCE_HOST=""
    MAINTENANCE_PID=""
    MAINTENANCE_REASON_TEXT=""
    MAINTENANCE_ENTERED_AT=""
    MAINTENANCE_EXPECTED_UNTIL=""
    if [[ -L "$marker" || -L "$sidecar" || ! -f "$sidecar" ]]; then
        MAINTENANCE_STATE=stale
        return 0
    fi
    if ! capture_output snapshot maintenance_sidecar_snapshot "$sidecar"; then
        MAINTENANCE_STATE=stale
        return 0
    fi
    MAINTENANCE_STATE="$(snapshot_value "$snapshot" state || printf stale)"
    MAINTENANCE_OWNER="$(snapshot_value "$snapshot" owner || true)"
    MAINTENANCE_HOST="$(snapshot_value "$snapshot" host || true)"
    MAINTENANCE_PID="$(snapshot_value "$snapshot" pid || true)"
    MAINTENANCE_REASON_TEXT="$(snapshot_value "$snapshot" reason || true)"
    MAINTENANCE_ENTERED_AT="$(snapshot_value "$snapshot" enteredAt || true)"
    MAINTENANCE_EXPECTED_UNTIL="$(snapshot_value "$snapshot" expectedUntil || true)"
}

resolve_maintenance_result() {
    local plugin_root="${1-}" user_marker user_sidecar plugin_marker plugin_sidecar
    resolve_current_environment
    MAINTENANCE_STATE=inactive
    MAINTENANCE_SCOPE=none
    MAINTENANCE_MARKER=""
    MAINTENANCE_SIDECAR=""
    MAINTENANCE_OWNER=""
    MAINTENANCE_HOST=""
    MAINTENANCE_PID=""
    MAINTENANCE_REASON_TEXT=""
    MAINTENANCE_ENTERED_AT=""
    MAINTENANCE_EXPECTED_UNTIL=""
    user_marker="$CURRENT_PROFILE_HOME/.copilot-extensions/maintenance"
    user_sidecar="$CURRENT_PROFILE_HOME/.copilot-extensions/maintenance.json"
    if [[ -e "$user_marker" || -L "$user_marker" ]]; then
        apply_maintenance_marker user "$user_marker" "$user_sidecar"
        return 0
    fi
    if [[ -n "$plugin_root" ]]; then
        plugin_marker="$plugin_root/maintenance"
        plugin_sidecar="$plugin_root/maintenance.json"
        if [[ -e "$plugin_marker" || -L "$plugin_marker" ]]; then
            apply_maintenance_marker plugin "$plugin_marker" "$plugin_sidecar"
        fi
    fi
}

ACTIVATION_STATUS=missing
ACTIVATION_MODE=""
ACTIVATION_RUNTIME_STATE=""
ACTIVATION_PATH=""
ACTIVATION_CONTEXT=""
ACTIVATION_GENERATION=""
ACTIVATION_INSTALL_GENERATION=""
ACTIVATION_RUNTIME_ROOT=""

resolve_activation_result() {
    local durable_home="$1" marketplace_id="$2" plugin_id="$3" plugin_root="$4" legacy_root="$5" snapshot activation_entry
    ACTIVATION_STATUS=missing
    ACTIVATION_MODE=""
    ACTIVATION_RUNTIME_STATE=""
    ACTIVATION_PATH=""
    ACTIVATION_CONTEXT=""
    ACTIVATION_GENERATION=""
    ACTIVATION_INSTALL_GENERATION=""
    ACTIVATION_RUNTIME_ROOT=""
    [[ -n "$plugin_root" ]] || return 0
    activation_entry="$plugin_root/installation-activation.json"
    ACTIVATION_PATH="$(canonical_path "$activation_entry")"
    if [[ ! -e "$activation_entry" && ! -L "$activation_entry" ]]; then
        ACTIVATION_PATH=""
        return 0
    fi
    if [[ -L "$activation_entry" || ! -f "$activation_entry" ]]; then
        ACTIVATION_STATUS=invalid
        return 0
    fi
    if ! capture_output snapshot activation_snapshot "$ACTIVATION_PATH" "$durable_home" "$marketplace_id" "$plugin_id" "$plugin_root" "$legacy_root"; then
        ACTIVATION_STATUS=invalid
        return 0
    fi
    ACTIVATION_STATUS="$(snapshot_value "$snapshot" status || printf invalid)"
    ACTIVATION_MODE="$(snapshot_value "$snapshot" mode || true)"
    ACTIVATION_RUNTIME_STATE="$(snapshot_value "$snapshot" state || true)"
    snapshot_hex_into ACTIVATION_CONTEXT "$snapshot" context || true
    ACTIVATION_GENERATION="$(snapshot_value "$snapshot" activationGeneration || true)"
    ACTIVATION_INSTALL_GENERATION="$(snapshot_value "$snapshot" installGeneration || true)"
    snapshot_hex_into ACTIVATION_RUNTIME_ROOT "$snapshot" runtimeRoot || true
}

LEGACY_DISPOSITION=active
LEGACY_TOMBSTONE_PATH=""
LEGACY_OWNER_MARKETPLACE_ID=""
TOMBSTONE_STATUS=missing

resolve_tombstone_result() {
    local durable_home="$1" current_marketplace_id="$2" current_plugin_id="$3" legacy_root="$4" snapshot tombstone_entry
    LEGACY_DISPOSITION=active
    LEGACY_TOMBSTONE_PATH=""
    LEGACY_OWNER_MARKETPLACE_ID=""
    TOMBSTONE_STATUS=missing
    tombstone_entry="$legacy_root/.installation-ownership.json"
    LEGACY_TOMBSTONE_PATH="$(canonical_path "$tombstone_entry")"
    if [[ ! -e "$tombstone_entry" && ! -L "$tombstone_entry" ]]; then
        LEGACY_TOMBSTONE_PATH=""
        return 0
    fi
    if [[ -L "$tombstone_entry" || ! -f "$tombstone_entry" ]]; then
        TOMBSTONE_STATUS=orphaned-transfer
        LEGACY_DISPOSITION=orphaned-transfer
        return 0
    fi
    if ! capture_output snapshot tombstone_snapshot "$LEGACY_TOMBSTONE_PATH" "$durable_home" "$current_marketplace_id" "$current_plugin_id"; then
        TOMBSTONE_STATUS=orphaned-transfer
        LEGACY_DISPOSITION=orphaned-transfer
        return 0
    fi
    TOMBSTONE_STATUS="$(snapshot_value "$snapshot" status || printf orphaned-transfer)"
    LEGACY_DISPOSITION="$(snapshot_value "$snapshot" disposition || printf orphaned-transfer)"
    LEGACY_OWNER_MARKETPLACE_ID="$(snapshot_value "$snapshot" ownerMarketplaceId || true)"
    if [[ "$TOMBSTONE_STATUS" == foreign-environment ]]; then
        LEGACY_DISPOSITION=orphaned-transfer
    fi
}

PROVENANCE_BLOCKED=false
INVALID_CONTEXT=false
RESOLVED_MARKETPLACE_ID=""
RESOLVED_PLUGIN_ID=""
RESOLVED_PLUGIN_ROOT=""
RESOLVED_CELL_ROOT=""

resolve_status_identity() {
    local snapshot message
    PROVENANCE_BLOCKED=false
    INVALID_CONTEXT=false
    RESOLVED_MARKETPLACE_ID=""
    RESOLVED_PLUGIN_ID=""
    RESOLVED_PLUGIN_ROOT=""
    RESOLVED_CELL_ROOT=""
    if [[ -n "$CONTEXT" ]]; then
        is_absolute "$CONTEXT" ||
            fail "The installation-context receipt pointer must be absolute."
        CONTEXT="$(canonical_path "$CONTEXT")"
        if capture_output snapshot validate_context_receipt \
            "$CONTEXT" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" "${PLUGIN_ID:-$EXPECTED_PLUGIN_ID}" \
            "${EXPECTED_PAYLOAD_ROOT:-${PAYLOAD_ROOT:-}}" "$EXPECTED_CELL_ROOT"; then
            RESOLVED_MARKETPLACE_ID="$(json_optional_path "$CONTEXT" marketplaceId)"
            RESOLVED_PLUGIN_ID="$(json_optional_path "$CONTEXT" pluginId)"
        else
            INVALID_CONTEXT=true
            RESOLVED_PLUGIN_ID="${PLUGIN_ID:-$EXPECTED_PLUGIN_ID}"
            if [[ "$(basename -- "$CONTEXT")" == install.json &&
                "$(basename -- "$(dirname -- "$(dirname -- "$CONTEXT")")")" == plugins ]]; then
                RESOLVED_PLUGIN_ID="$(basename -- "$(dirname -- "$CONTEXT")")"
                RESOLVED_CELL_ROOT="$(dirname -- "$(dirname -- "$(dirname -- "$CONTEXT")")")"
                RESOLVED_MARKETPLACE_ID="$(basename -- "$RESOLVED_CELL_ROOT")"
            fi
        fi
    else
        if ! capture_output snapshot status_identity_snapshot; then
            message="${snapshot#installation-context: }"
            if [[ -n "$SOURCE_FILE" ]]; then
                if [[ "$message" == *"already owns another cell/locator; explicit rebind or new-cell intent is required." ||
                    "$message" == *"is already occupied by a different full source fingerprint." ]]; then
                    PROVENANCE_BLOCKED=true
                    return 0
                fi
                fail "$message"
            fi
            case "$message" in
                "Expected plugin '"* ) fail "$message" ;;
                *)
                    PROVENANCE_BLOCKED=true
                    RESOLVED_PLUGIN_ID="${PLUGIN_ID:-$EXPECTED_PLUGIN_ID}"
                    if [[ -z "$RESOLVED_PLUGIN_ID" && -n "$PAYLOAD_ROOT" ]]; then
                        RESOLVED_PLUGIN_ID="$(basename -- "$PAYLOAD_ROOT")"
                    fi
                    return 0
                    ;;
            esac
        fi
        RESOLVED_MARKETPLACE_ID="$(snapshot_value "$snapshot" marketplaceId || true)"
        RESOLVED_PLUGIN_ID="$(snapshot_value "$snapshot" pluginId || true)"
    fi
    if [[ -n "$RESOLVED_MARKETPLACE_ID" && -n "$RESOLVED_PLUGIN_ID" ]]; then
        RESOLVED_CELL_ROOT="$(canonical_path "$DURABLE_HOME/marketplaces/$RESOLVED_MARKETPLACE_ID")"
        RESOLVED_PLUGIN_ROOT="$(canonical_path "$RESOLVED_CELL_ROOT/plugins/$RESOLVED_PLUGIN_ID")"
    fi
}

emit_environment_json() {
    printf '{"platform":%s,"homeRealPath":%s,"wslDistro":%s}' \
        "$(json_quote "$CURRENT_PLATFORM")" \
        "$(json_quote "$CURRENT_PROFILE_HOME")" \
        "$(json_string_or_null "$CURRENT_WSL_DISTRO")"
}

emit_maintenance_json() {
    printf '{'
    printf '"state":%s,' "$(json_quote "$MAINTENANCE_STATE")"
    printf '"scope":%s,' "$(json_quote "$MAINTENANCE_SCOPE")"
    printf '"marker":%s,' "$(json_string_or_null "$MAINTENANCE_MARKER")"
    printf '"sidecar":%s' "$(json_string_or_null "$MAINTENANCE_SIDECAR")"
    printf '}'
}

emit_policy_json() {
    printf '{'
    printf '"path":%s,' "$(json_quote "$POLICY_RESOLVED_PATH")"
    printf '"authoritative":%s,' "$([[ "$POLICY_AUTHORITATIVE" == true ]] && printf true || printf false)"
    printf '"state":%s,' "$(json_quote "$POLICY_STATE")"
    printf '"scope":%s,' "$(json_quote "$POLICY_SCOPE")"
    printf '"enabled":%s,' "$(json_bool_literal_or_null "$POLICY_ENABLED")"
    printf '"reason":%s' "$(json_quote "$POLICY_REASON")"
    printf '}'
}

emit_legacy_json() {
    printf '{'
    printf '"root":%s,' "$(json_quote "$LEGACY_ROOT")"
    printf '"probe":{"declared":%s,"result":%s,"checkedAt":%s},' \
        "$([[ "$LEGACY_PROBE_DECLARED" == true ]] && printf true || printf false)" \
        "$(json_quote "$LEGACY_PROBE_RESULT")" \
        "$(json_string_or_null "$LEGACY_PROBE_CHECKED_AT")"
    printf '"tombstone":%s,' "$(json_string_or_null "$LEGACY_TOMBSTONE_PATH")"
    printf '"disposition":%s,' "$(json_quote "$LEGACY_DISPOSITION")"
    printf '"ownerMarketplaceId":%s' "$(json_string_or_null "$LEGACY_OWNER_MARKETPLACE_ID")"
    printf '}'
}

emit_status_result() {
    local desired_mode="$1" actual_mode="$2" status="$3" runtime_root="$4" context_path="$5" activation_path="$6" activation_generation="$7" install_generation="$8" reason="$9" allow_mutation="${10-}" probe_reason="${11-}"
    printf '{'
    printf '"schema":"copilot-extensions.installation-resolution",'
    printf '"version":1,'
    printf '"marketplaceId":%s,' "$(json_string_or_null "$RESOLVED_MARKETPLACE_ID")"
    printf '"pluginId":%s,' "$(json_string_or_null "$RESOLVED_PLUGIN_ID")"
    printf '"environment":'
    emit_environment_json
    printf ','
    printf '"desiredMode":%s,' "$(json_string_or_null "$desired_mode")"
    printf '"actualMode":%s,' "$(json_string_or_null "$actual_mode")"
    printf '"status":%s,' "$(json_quote "$status")"
    printf '"maintenance":'
    emit_maintenance_json
    printf ','
    printf '"runtimeRoot":%s,' "$(json_string_or_null "$runtime_root")"
    printf '"context":%s,' "$(json_string_or_null "$context_path")"
    printf '"activation":%s,' "$(json_string_or_null "$activation_path")"
    printf '"activationGeneration":%s,' "$(json_number_literal_or_null "$activation_generation")"
    printf '"installGeneration":%s,' "$(json_number_literal_or_null "$install_generation")"
    printf '"reason":%s,' "$(json_quote "$reason")"
    printf '"policy":'
    emit_policy_json
    printf ','
    printf '"legacy":'
    emit_legacy_json
    if [[ -n "$allow_mutation" ]]; then
        printf ',"allowMutation":%s' "$([[ "$allow_mutation" == true ]] && printf true || printf false)"
        printf ',"probeReason":%s' "$(json_quote "$probe_reason")"
    fi
    printf '}'
}

run_status_action() {
    local desired_mode actual_mode runtime_root context_path activation_path activation_generation install_generation
    local status reason base_status base_reason active_namespaced
    local allow_mutation probe_reason exit_code
    resolve_current_environment
    legacy_probe_snapshot "${LEGACY_PROBE_FILE:-@LEGACY_PROBE_JSON}"
    resolve_status_identity
    resolve_policy_result "$RESOLVED_MARKETPLACE_ID" "$RESOLVED_PLUGIN_ID"
    resolve_maintenance_result "$RESOLVED_PLUGIN_ROOT"
    resolve_activation_result "$DURABLE_HOME" "$RESOLVED_MARKETPLACE_ID" "$RESOLVED_PLUGIN_ID" "$RESOLVED_PLUGIN_ROOT" "$LEGACY_ROOT"
    resolve_tombstone_result "$DURABLE_HOME" "$RESOLVED_MARKETPLACE_ID" "$RESOLVED_PLUGIN_ID" "$LEGACY_ROOT"

    desired_mode=""
    actual_mode=legacy
    runtime_root="$LEGACY_ROOT"
    context_path=""
    activation_path="$ACTIVATION_PATH"
    activation_generation="$ACTIVATION_GENERATION"
    install_generation="$ACTIVATION_INSTALL_GENERATION"
    active_namespaced=false
    if [[ "$ACTIVATION_STATUS" == valid || "$ACTIVATION_STATUS" == revalidation-required ]]; then
        actual_mode="$ACTIVATION_MODE"
        runtime_root="$ACTIVATION_RUNTIME_ROOT"
        context_path="$ACTIVATION_CONTEXT"
        if [[ "$ACTIVATION_MODE" == namespaced && "$ACTIVATION_RUNTIME_STATE" == active ]]; then
            active_namespaced=true
        fi
    elif [[ "$ACTIVATION_STATUS" == invalid ]]; then
        actual_mode=""
        runtime_root=""
        context_path=""
    elif [[ "$ACTIVATION_STATUS" == foreign-environment ]]; then
        actual_mode=""
        runtime_root=""
        context_path=""
    fi

    if [[ "$POLICY_STATE" == valid || "$POLICY_STATE" == missing ]]; then
        if [[ "$POLICY_AUTHORITATIVE" == true ]]; then
            if [[ "$POLICY_ENABLED" == true ]]; then
                desired_mode=namespaced
            else
                desired_mode=legacy
            fi
            base_reason="$POLICY_REASON"
        else
            if [[ "$active_namespaced" == true ]]; then
                desired_mode=namespaced
                base_reason=namespaced-active
            elif [[ "$POLICY_ENABLED" == true ]]; then
                desired_mode=legacy
                base_reason=policy-injected-non-authoritative
            else
                desired_mode=legacy
                base_reason="$POLICY_REASON"
            fi
        fi
    fi
    if [[ "$POLICY_AUTHORITATIVE" != true && "$active_namespaced" == true ]]; then
        desired_mode=namespaced
    fi

    if [[ "$active_namespaced" == true ]]; then
        if [[ "$desired_mode" == legacy && "$POLICY_AUTHORITATIVE" == true ]]; then
            base_status=deactivation-required
            base_reason=deactivation-required
        else
            base_status=ready
            base_reason=namespaced-active
        fi
    elif [[ "$desired_mode" == namespaced ]]; then
        if [[ "$ACTIVATION_STATUS" == missing &&
            "$LEGACY_PROBE_DECLARED" == true && "$LEGACY_PROBE_RESULT" == absent ]]; then
            base_status=ready
            base_reason=activation-required
        else
            base_status=migration-required
            base_reason=migration-required
        fi
    elif [[ -n "$desired_mode" ]]; then
        base_status=ready
        base_reason="$base_reason"
    else
        base_status=ready
        base_reason=policy-default-false
    fi

    status="$base_status"
    reason="$base_reason"
    if [[ "$POLICY_STATE" == invalid ]]; then
        status=invalid
        reason=policy-invalid
    elif [[ "$POLICY_STATE" == unsupported ]]; then
        status=invalid
        reason=policy-version-unsupported
    elif [[ "$INVALID_CONTEXT" == true ]]; then
        status=invalid
        reason=context-invalid
    elif [[ "$ACTIVATION_STATUS" == invalid ]]; then
        status=invalid
        reason=activation-invalid
    elif [[ "$MAINTENANCE_STATE" == active ]]; then
        status=maintenance-blocked
        reason=maintenance-active
    elif [[ "$MAINTENANCE_STATE" == stale ]]; then
        status=maintenance-blocked
        reason=maintenance-stale
    elif [[ "$ACTIVATION_STATUS" == foreign-environment || "$TOMBSTONE_STATUS" == foreign-environment ]]; then
        status=foreign-environment
        reason=foreign-environment
    elif [[ "$TOMBSTONE_STATUS" == orphaned-transfer ]]; then
        status=orphaned-transfer
        reason=orphaned-transfer
    elif [[ "$ACTIVATION_STATUS" == revalidation-required ]]; then
        status=revalidation-required
        reason=revalidation-required
    elif [[ "$PROVENANCE_BLOCKED" == true ]]; then
        status=provenance-blocked
        reason=provenance-blocked
    fi
    if [[ "$PROVENANCE_BLOCKED" == true || "$INVALID_CONTEXT" == true ]]; then
        desired_mode=""
        actual_mode=""
        runtime_root=""
        context_path=""
        activation_path=""
        activation_generation=""
        install_generation=""
    fi

    if [[ "$ACTION" == status ]]; then
        emit_status_result "$desired_mode" "$actual_mode" "$status" "$runtime_root" \
            "$context_path" "$activation_path" "$activation_generation" "$install_generation" "$reason"
        printf '\n'
        return 0
    fi

    allow_mutation=false
    probe_reason="$reason"
    exit_code=3
    if [[ "$reason" == namespaced-active ]]; then
        probe_reason=namespaced-active
    elif [[ "$LEGACY_DISPOSITION" == owned-by-current-cell || "$LEGACY_DISPOSITION" == owned-by-other-cell ]]; then
        probe_reason=legacy-owned-by-other-cell
    elif [[ "$status" == ready && "$reason" == activation-required ]]; then
        probe_reason=namespaced-requested
    elif [[ "$status" == migration-required ]]; then
        allow_mutation=true
        probe_reason=migration-required
        exit_code=0
    elif [[ "$status" == ready && "$actual_mode" == legacy ]]; then
        allow_mutation=true
        probe_reason=legacy-active
        exit_code=0
    fi

    emit_status_result "$desired_mode" "$actual_mode" "$status" "$runtime_root" \
        "$context_path" "$activation_path" "$activation_generation" "$install_generation" "$reason" \
        "$allow_mutation" "$probe_reason"
    printf '\n'
    return "$exit_code"
}

ACTION="${1:-}"
[[ "$ACTION" == source-id || "$ACTION" == resolve || "$ACTION" == validate || "$ACTION" == stamp || "$ACTION" == activation-cas || "$ACTION" == snapshot-stamp || "$ACTION" == snapshot-validate || "$ACTION" == slot-provision || "$ACTION" == slot-validate || "$ACTION" == status || "$ACTION" == probe-legacy ]] ||
    fail "Usage: installation-context.sh {source-id|resolve|validate|stamp|activation-cas|snapshot-stamp|snapshot-validate|slot-provision|slot-validate|status|probe-legacy} [options]"
shift

SOURCE_JSON=""
SOURCE_FILE=""
MARKETPLACE_KEY=""
PLUGIN_ID=""
PAYLOAD_ROOT=""
COPILOT_HOME="${HOME}/.copilot"
PROJECT_ROOT=""
DURABLE_HOME="${HOME}/.copilot-extensions"
CONTEXT=""
CONTEXT_SUPPLIED=false
EXPECTED_MARKETPLACE_ID=""
EXPECTED_PLUGIN_ID=""
EXPECTED_PAYLOAD_ROOT=""
EXPECTED_CELL_ROOT=""
PAYLOAD_VERSION=""
PAYLOAD_ORIGIN=""
PAYLOAD_ORIGIN_RECEIPT=""
EXPECTED_NAMESPACE_GENERATION=""
EXPECTED_INSTALL_GENERATION=""
EXPECTED_ACTIVATION_GENERATION=""
SNAPSHOT_ID=""
RUNTIME_VERSION=""
NAMESPACE_STATE="active"
INSTALL_STATE="active"
ACTIVATION_MODE=""
ACTIVATION_STATE=""
LEGACY_DISPOSITION=""
LEGACY_ROOT=""
LEGACY_PROBE_JSON='{"declared":false,"result":"unknown","checkedAt":null}'
LEGACY_PROBE_FILE=""
LEGACY_PROBE_JSON_SUPPLIED=false
LEGACY_PROBE_FILE_SUPPLIED=false
POLICY_PATH=""

while (($#)); do
    case "$1" in
        --source-json) need_value "$@"; SOURCE_JSON="$2"; shift 2 ;;
        --source-file) need_value "$@"; SOURCE_FILE="$2"; shift 2 ;;
        --marketplace-key) need_value "$@"; MARKETPLACE_KEY="$2"; shift 2 ;;
        --plugin-id) need_value "$@"; PLUGIN_ID="$2"; shift 2 ;;
        --payload-root) need_value "$@"; PAYLOAD_ROOT="$2"; shift 2 ;;
        --copilot-home) need_value "$@"; COPILOT_HOME="$2"; shift 2 ;;
        --project-root) need_value "$@"; PROJECT_ROOT="$2"; shift 2 ;;
        --durable-home) need_value "$@"; DURABLE_HOME="$2"; shift 2 ;;
        --context) need_value "$@"; CONTEXT="$2"; CONTEXT_SUPPLIED=true; shift 2 ;;
        --expected-marketplace-id) need_value "$@"; EXPECTED_MARKETPLACE_ID="$2"; shift 2 ;;
        --expected-plugin-id) need_value "$@"; EXPECTED_PLUGIN_ID="$2"; shift 2 ;;
        --expected-payload-root) need_value "$@"; EXPECTED_PAYLOAD_ROOT="$2"; shift 2 ;;
        --expected-cell-root) need_value "$@"; EXPECTED_CELL_ROOT="$2"; shift 2 ;;
        --payload-version) need_value "$@"; PAYLOAD_VERSION="$2"; shift 2 ;;
        --payload-origin) need_value "$@"; PAYLOAD_ORIGIN="$2"; shift 2 ;;
        --payload-origin-receipt) need_value "$@"; PAYLOAD_ORIGIN_RECEIPT="$2"; shift 2 ;;
        --expected-namespace-generation) need_value "$@"; EXPECTED_NAMESPACE_GENERATION="$2"; shift 2 ;;
        --expected-install-generation) need_value "$@"; EXPECTED_INSTALL_GENERATION="$2"; shift 2 ;;
        --expected-activation-generation) need_value "$@"; EXPECTED_ACTIVATION_GENERATION="$2"; shift 2 ;;
        --snapshot-id) need_value "$@"; SNAPSHOT_ID="$2"; shift 2 ;;
        --runtime-version) need_value "$@"; RUNTIME_VERSION="$2"; shift 2 ;;
        --namespace-state) need_value "$@"; NAMESPACE_STATE="$2"; shift 2 ;;
        --install-state) need_value "$@"; INSTALL_STATE="$2"; shift 2 ;;
        --activation-mode) need_value "$@"; ACTIVATION_MODE="$2"; shift 2 ;;
        --activation-state) need_value "$@"; ACTIVATION_STATE="$2"; shift 2 ;;
        --legacy-disposition) need_value "$@"; LEGACY_DISPOSITION="$2"; shift 2 ;;
        --legacy-root) need_value "$@"; LEGACY_ROOT="$2"; shift 2 ;;
        --legacy-probe-json) need_value "$@"; LEGACY_PROBE_JSON="$2"; LEGACY_PROBE_JSON_SUPPLIED=true; shift 2 ;;
        --legacy-probe-file) need_value "$@"; LEGACY_PROBE_FILE="$2"; LEGACY_PROBE_FILE_SUPPLIED=true; shift 2 ;;
        --policy-path) need_value "$@"; POLICY_PATH="$2"; shift 2 ;;
        *) fail "Unknown option '$1'." ;;
    esac
done

[[ -z "$SOURCE_JSON" || -z "$SOURCE_FILE" ]] ||
    fail "Specify only one of --source-json and --source-file."
[[ "$LEGACY_PROBE_JSON_SUPPLIED" != true || "$LEGACY_PROBE_FILE_SUPPLIED" != true ]] ||
    fail "Specify only one of --legacy-probe-json and --legacy-probe-file."
if [[ -n "$SOURCE_JSON" ]]; then
    if [[ "$ACTION" == status || "$ACTION" == probe-legacy ]]; then
        SOURCE_FILE="@SOURCE_JSON"
    else
        SOURCE_FILE="$(mktemp "${TMPDIR:-/tmp}/installation-context-source.XXXXXX")"
        TEMP_FILES+=("$SOURCE_FILE")
        printf '%s' "$SOURCE_JSON" >"$SOURCE_FILE"
    fi
fi
if [[ -n "$LEGACY_PROBE_FILE" ]]; then
    LEGACY_PROBE_FILE="$(canonical_path "$LEGACY_PROBE_FILE" true)"
elif [[ "$ACTION" == status || "$ACTION" == probe-legacy || "$ACTION" == activation-cas ]]; then
    LEGACY_PROBE_FILE="@LEGACY_PROBE_JSON"
fi

if ! is_absolute "$COPILOT_HOME" || ! is_absolute "$DURABLE_HOME"; then
    fail "--copilot-home and --durable-home must be absolute."
fi
COPILOT_HOME="$(canonical_path "$COPILOT_HOME")"
DURABLE_HOME="$(canonical_path "$DURABLE_HOME")"
if [[ -n "$PROJECT_ROOT" ]]; then
    PROJECT_ROOT="$(canonical_path "$PROJECT_ROOT" true)"
fi
if [[ -n "$POLICY_PATH" ]]; then
    is_absolute "$POLICY_PATH" || fail "--policy-path must be absolute."
    POLICY_PATH="$(canonical_path "$POLICY_PATH")"
fi

if [[ "$ACTION" == source-id ]]; then
    [[ -n "$SOURCE_FILE" ]] || fail "source-id requires --source-json or --source-file."
    normalize_source "$SOURCE_FILE" ""
    derive_identity "${MARKETPLACE_KEY:-marketplace}"
    emit_source_identity
    exit 0
fi

if [[ "$ACTION" != activation-cas &&
      "$ACTION" != snapshot-stamp &&
      "$ACTION" != snapshot-validate &&
      "$ACTION" != slot-provision &&
      "$ACTION" != slot-validate ]]; then
    CONTEXT="${CONTEXT:-${COPILOT_EXTENSIONS_CONTEXT:-}}"
fi
if [[ "$ACTION" == validate ]]; then
    [[ -n "$CONTEXT" ]] || fail "validate requires --context or COPILOT_EXTENSIONS_CONTEXT."
    validate_context_receipt \
        "$CONTEXT" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" "$EXPECTED_PLUGIN_ID" \
        "${EXPECTED_PAYLOAD_ROOT:-$PAYLOAD_ROOT}" "$EXPECTED_CELL_ROOT"
    printf '%s\n' "$CONTEXT_JSON"
    exit 0
fi

if [[ "$ACTION" == snapshot-validate ]]; then
    validate_snapshot_provenance \
        "$CONTEXT" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" "$EXPECTED_PLUGIN_ID" \
        "$SNAPSHOT_ID"
    printf '%s\n' "$SNAPSHOT_JSON"
    exit 0
fi

if [[ "$ACTION" == snapshot-stamp ]]; then
    stamp_snapshot_provenance
    exit 0
fi

if [[ "$ACTION" == slot-validate ]]; then
    validate_runtime_slot_ownership \
        "$CONTEXT" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" "$EXPECTED_PLUGIN_ID" \
        "$SNAPSHOT_ID" "$RUNTIME_VERSION"
    printf '%s\n' "$SLOT_JSON"
    exit 0
fi

if [[ "$ACTION" == slot-provision ]]; then
    provision_runtime_slot
    exit 0
fi

if [[ "$ACTION" == status || "$ACTION" == probe-legacy ]]; then
    [[ -n "$LEGACY_ROOT" ]] || fail "$ACTION requires --legacy-root."
    is_absolute "$LEGACY_ROOT" || fail "--legacy-root must be absolute."
    LEGACY_ROOT="$(canonical_path "$LEGACY_ROOT")"
    if [[ -z "$CONTEXT" ]]; then
        PAYLOAD_ROOT="${PAYLOAD_ROOT:-${COPILOT_PLUGIN_ROOT:-}}"
        [[ -n "$PAYLOAD_ROOT" ]] || fail "$ACTION requires --payload-root, --context, or COPILOT_PLUGIN_ROOT."
        is_absolute "$PAYLOAD_ROOT" || fail "The payload root must be absolute."
        PAYLOAD_ROOT="$(canonical_path "$PAYLOAD_ROOT" true)"
        [[ -d "$PAYLOAD_ROOT" ]] || fail "The payload root must be an existing directory: $PAYLOAD_ROOT"
        if [[ -n "${COPILOT_PLUGIN_ROOT:-}" ]]; then
            is_absolute "$COPILOT_PLUGIN_ROOT" || fail "COPILOT_PLUGIN_ROOT must be absolute."
            paths_equal "$PAYLOAD_ROOT" "$COPILOT_PLUGIN_ROOT" ||
                fail "COPILOT_PLUGIN_ROOT conflicts with --payload-root."
        fi
    fi
    run_status_action
    exit $?
fi

if [[ "$ACTION" == activation-cas ]]; then
    activation_cas
    exit 0
fi

if [[ "$ACTION" == resolve && -n "$CONTEXT" ]]; then
    payload_expectation="${EXPECTED_PAYLOAD_ROOT:-${PAYLOAD_ROOT:-${COPILOT_PLUGIN_ROOT:-}}}"
    plugin_expectation="${PLUGIN_ID:-$EXPECTED_PLUGIN_ID}"
    [[ -n "$plugin_expectation" ]] ||
        fail "resolve with an explicit context requires an expected plugin id."
    [[ -n "$payload_expectation" || -n "$EXPECTED_MARKETPLACE_ID" || -n "$EXPECTED_CELL_ROOT" ]] ||
        fail "resolve with an explicit context requires an expected payload, marketplace, or cell identity."
    validate_context_receipt \
        "$CONTEXT" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" "$plugin_expectation" \
        "$payload_expectation" "$EXPECTED_CELL_ROOT"
    CONTEXT_JSON="${CONTEXT_JSON/\"action\":\"validate\"/\"action\":\"resolve\"}"
    printf '%s\n' "$CONTEXT_JSON"
    exit 0
fi

PAYLOAD_ROOT="${PAYLOAD_ROOT:-${COPILOT_PLUGIN_ROOT:-}}"
[[ -n "$PAYLOAD_ROOT" ]] || fail "$ACTION requires --payload-root or COPILOT_PLUGIN_ROOT."
is_absolute "$PAYLOAD_ROOT" || fail "The payload root must be absolute."
PAYLOAD_ROOT="$(canonical_path "$PAYLOAD_ROOT" true)"
[[ -d "$PAYLOAD_ROOT" ]] || fail "The payload root must be an existing directory: $PAYLOAD_ROOT"
if [[ -n "${COPILOT_PLUGIN_ROOT:-}" ]]; then
    is_absolute "$COPILOT_PLUGIN_ROOT" || fail "COPILOT_PLUGIN_ROOT must be absolute."
    paths_equal "$PAYLOAD_ROOT" "$COPILOT_PLUGIN_ROOT" ||
        fail "COPILOT_PLUGIN_ROOT conflicts with --payload-root."
fi

if [[ -n "$SOURCE_FILE" ]]; then
    [[ -n "$PLUGIN_ID" ]] || fail "Explicit source resolution requires --plugin-id."
    normalize_source "$SOURCE_FILE" ""
    EVIDENCE_PLUGIN_ID="$PLUGIN_ID"
    EVIDENCE_READABLE_NAME="${MARKETPLACE_KEY:-marketplace}"
else
    resolve_installed_evidence "$PAYLOAD_ROOT" "$COPILOT_HOME" "$PROJECT_ROOT"
    if [[ "$EVIDENCE_FOUND" != true ]]; then
        resolve_directory_evidence "$PAYLOAD_ROOT" "$PLUGIN_ID"
    fi
    if [[ "$EVIDENCE_FOUND" != true ]]; then
        fail "Cannot establish marketplace provenance for payload '$PAYLOAD_ROOT'. Supply an explicit source descriptor for management/development mode."
    fi
    [[ -z "$PLUGIN_ID" || "$PLUGIN_ID" == "$EVIDENCE_PLUGIN_ID" ]] ||
        fail "Expected plugin '$PLUGIN_ID', payload evidence identifies '$EVIDENCE_PLUGIN_ID'."
fi

assert_plugin_id "$EVIDENCE_PLUGIN_ID"
derive_identity "$EVIDENCE_READABLE_NAME"
find_existing_source "$DURABLE_HOME" "$SOURCE_FINGERPRINT" "$MARKETPLACE_ID"
if [[ "$EXISTING_JSON" =~ \"sameId\":false || "$EXISTING_JSON" =~ \"locatorMatch\":false ]]; then
    fail "Source '$SOURCE_FINGERPRINT' already owns another cell/locator; explicit rebind or new-cell intent is required."
fi
if [[ "$ACTION" == stamp ]]; then
    [[ -n "$EXPECTED_NAMESPACE_GENERATION" ]] ||
        fail "stamp requires --expected-namespace-generation."
    [[ -n "$EXPECTED_INSTALL_GENERATION" ]] ||
        fail "stamp requires --expected-install-generation."
    stamp_context
else
    emit_resolved_context "$PAYLOAD_ROOT" "$DURABLE_HOME"
fi
