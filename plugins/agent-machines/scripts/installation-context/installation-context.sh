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
HELD_LOCK_DIR=""
HELD_LOCK_TOKEN=""

cleanup() {
    local path
    if [[ -n "$HELD_LOCK_DIR" && -d "$HELD_LOCK_DIR" ]] &&
        lock_owner_matches "$HELD_LOCK_DIR/owner.json"; then
        rm -f -- "$HELD_LOCK_DIR/owner.json" 2>/dev/null || true
        rmdir -- "$HELD_LOCK_DIR" 2>/dev/null || true
    fi
    for path in "${TEMP_FILES[@]}"; do
        rm -f -- "$path" 2>/dev/null || true
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
    local mode="$1" file="$2" path="$3"
    LC_ALL=C awk -f "$JSON_QUERY" -v "mode=$mode" -v "query_path=$path" "$file"
}

json_optional_into() {
    local target="$1" file="$2" path="$3" default_value="${4-}" encoded status escapes="" decoded=""
    set +e
    encoded="$(json_query hex "$file" "$path")"
    status=$?
    set -e
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
        2) fail "Invalid JSON in '$file'." ;;
        4) fail "Expected a scalar JSON value in '$file'." ;;
        5) fail "JSON property must use exact case in '$file'." ;;
        *) fail "Cannot read JSON value in '$file'." ;;
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
    set +e
    result="$(json_query type "$file" "$path")"
    status=$?
    set -e
    case "$status" in
        0) printf '%s' "$result" ;;
        3) ;;
        2) fail "Invalid JSON in '$file'." ;;
        5) fail "JSON property must use exact case in '$file'." ;;
        *) fail "Cannot read JSON type in '$file'." ;;
    esac
}

json_optional_type_path() {
    json_type_path "$1" "$2"
}

json_length_path() {
    local file="$1" path="$2" result status
    set +e
    result="$(json_query len "$file" "$path")"
    status=$?
    set -e
    case "$status" in
        0) printf '%s' "$result" ;;
        2) fail "Invalid JSON in '$file'." ;;
        3) fail "Missing JSON array or object in '$file'." ;;
        4) fail "Expected a JSON array or object in '$file'." ;;
        5) fail "JSON property must use exact case in '$file'." ;;
        *) fail "Cannot read JSON length in '$file'." ;;
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
    if [[ -n "$HELD_LOCK_DIR" && "$skip_lock" != true ]]; then
        assert_lock_owned
    fi
    mv -f -- "$temporary" "$path"
}

lock_owner_matches() {
    local owner="$1" token
    [[ -f "$owner" ]] || return 1
    json_optional_string_into token "$owner" token
    [[ "$token" == "$HELD_LOCK_TOKEN" ]]
}

assert_lock_owned() {
    [[ -n "$HELD_LOCK_DIR" && -d "$HELD_LOCK_DIR" ]] ||
        fail "Installation lock is not held."
    lock_owner_matches "$HELD_LOCK_DIR/owner.json" ||
        fail "Installation lock '$HELD_LOCK_DIR' ownership changed during mutation."
}

acquire_lock() {
    local path="$1" kind="$2" marketplace_id="$3" plugin_id="${4-}"
    local attempt owner owner_snapshot owner_host owner_pid host token owner_schema owner_version
    local owner_kind owner_marketplace owner_plugin owner_token
    host="$(hostname)"
    token="$(random_token)"
    mkdir -p -- "$(dirname -- "$path")"
    for ((attempt = 0; attempt < 100; attempt++)); do
        if mkdir -- "$path" 2>/dev/null; then
            HELD_LOCK_DIR="$path"
            HELD_LOCK_TOKEN="$token"
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
            if ((attempt == 99)); then
                fail "Installation lock '$path' has no owner receipt; explicit repair is required."
            fi
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
        assert_json_type "$owner_snapshot" version number "installation lock version"
        assert_json_type "$owner_snapshot" pid number "installation lock pid"
        [[ "$owner_pid" =~ ^[1-9][0-9]*$ ]] ||
            fail "Installation lock owner receipt '$owner' is invalid."
        if [[ "$owner_host" == "$host" ]]; then
            if ! kill -0 "$owner_pid" 2>/dev/null; then
                fail "Installation lock '$path' has a stale owner (host=$owner_host, pid=$owner_pid); explicit repair is required."
            fi
            sleep 0.01
            continue
        fi
        fail "Installation lock '$path' is busy (host=$owner_host, pid=$owner_pid)."
    done
    fail "Installation lock '$path' remained busy."
}

release_lock() {
    assert_lock_owned
    rm -f -- "$HELD_LOCK_DIR/owner.json"
    rmdir -- "$HELD_LOCK_DIR" ||
        fail "Cannot release installation lock '$HELD_LOCK_DIR'."
    HELD_LOCK_DIR=""
    HELD_LOCK_TOKEN=""
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

assert_positive_integer() {
    [[ "$1" =~ ^[1-9][0-9]*$ ]] || fail "$2 must be an integer of at least 1."
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
    local schema version receipt_id generation state source_prefix fingerprint slug receipt_marketplace_id
    actual="$(canonical_path "$receipt_path" true)"
    cell_root="$(canonical_path "$(dirname -- "$actual")")"
    marketplaces_root="$(canonical_path "$durable_home/marketplaces")"
    path_is_within "$cell_root" "$marketplaces_root" ||
        fail "Namespace receipt '$actual' is outside the durable marketplaces root."
    receipt_id="$(basename -- "$cell_root")"
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
    assert_positive_integer "$generation" "namespace.json generation"
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

validate_context_receipt() {
    local receipt_path="$1" durable_home="$2" expected_marketplace="$3" expected_plugin="$4"
    local expected_payload="$5" expected_cell="$6"
    local actual schema version marketplace_id plugin_id cell_root plugin_root canonical_receipt
    local receipt_plugin_root generation state namespace_path payload_root payload_version payload_origin
    local roots_json="" name value resolved delimiter="" inherited_payload namespace_receipt
    local payload_origin_receipt payload_origin_receipt_type
    is_absolute "$receipt_path" || fail "The installation-context receipt pointer must be absolute."
    [[ -z "$expected_payload" ]] || is_absolute "$expected_payload" ||
        fail "expected payload root must be absolute."
    [[ -z "$expected_cell" ]] || is_absolute "$expected_cell" ||
        fail "expected cell root must be absolute."
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
    cell_root="$(canonical_path "$durable_home/marketplaces/$marketplace_id")"
    plugin_root="$(canonical_path "$cell_root/plugins/$plugin_id")"
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
    assert_positive_integer "$generation" "install.json generation"
    assert_receipt_state "$state" "install.json state"
    namespace_path="$(canonical_path "$cell_root/namespace.json")"
    json_optional_string_into namespace_receipt "$actual" namespaceReceipt
    paths_equal "$namespace_receipt" "$namespace_path" ||
        fail "install.json namespaceReceipt is not the exact namespace receipt in the same cell."
    validate_namespace_receipt "$namespace_path" "$durable_home"
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
    [[ "$EXPECTED_NAMESPACE_GENERATION" =~ ^[0-9]+$ ]] ||
        fail "Expected namespace.json generation must be a non-negative integer."
    [[ "$EXPECTED_INSTALL_GENERATION" =~ ^[0-9]+$ ]] ||
        fail "Expected install.json generation must be a non-negative integer."
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

ACTION="${1:-}"
[[ "$ACTION" == source-id || "$ACTION" == resolve || "$ACTION" == validate || "$ACTION" == stamp ]] ||
    fail "Usage: installation-context.sh {source-id|resolve|validate|stamp} [options]"
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
EXPECTED_MARKETPLACE_ID=""
EXPECTED_PLUGIN_ID=""
EXPECTED_PAYLOAD_ROOT=""
EXPECTED_CELL_ROOT=""
PAYLOAD_VERSION=""
PAYLOAD_ORIGIN=""
PAYLOAD_ORIGIN_RECEIPT=""
EXPECTED_NAMESPACE_GENERATION=""
EXPECTED_INSTALL_GENERATION=""
NAMESPACE_STATE="active"
INSTALL_STATE="active"

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
        --context) need_value "$@"; CONTEXT="$2"; shift 2 ;;
        --expected-marketplace-id) need_value "$@"; EXPECTED_MARKETPLACE_ID="$2"; shift 2 ;;
        --expected-plugin-id) need_value "$@"; EXPECTED_PLUGIN_ID="$2"; shift 2 ;;
        --expected-payload-root) need_value "$@"; EXPECTED_PAYLOAD_ROOT="$2"; shift 2 ;;
        --expected-cell-root) need_value "$@"; EXPECTED_CELL_ROOT="$2"; shift 2 ;;
        --payload-version) need_value "$@"; PAYLOAD_VERSION="$2"; shift 2 ;;
        --payload-origin) need_value "$@"; PAYLOAD_ORIGIN="$2"; shift 2 ;;
        --payload-origin-receipt) need_value "$@"; PAYLOAD_ORIGIN_RECEIPT="$2"; shift 2 ;;
        --expected-namespace-generation) need_value "$@"; EXPECTED_NAMESPACE_GENERATION="$2"; shift 2 ;;
        --expected-install-generation) need_value "$@"; EXPECTED_INSTALL_GENERATION="$2"; shift 2 ;;
        --namespace-state) need_value "$@"; NAMESPACE_STATE="$2"; shift 2 ;;
        --install-state) need_value "$@"; INSTALL_STATE="$2"; shift 2 ;;
        *) fail "Unknown option '$1'." ;;
    esac
done

[[ -z "$SOURCE_JSON" || -z "$SOURCE_FILE" ]] ||
    fail "Specify only one of --source-json and --source-file."
if [[ -n "$SOURCE_JSON" ]]; then
    SOURCE_FILE="$(mktemp "${TMPDIR:-/tmp}/installation-context-source.XXXXXX")"
    TEMP_FILES+=("$SOURCE_FILE")
    printf '%s' "$SOURCE_JSON" >"$SOURCE_FILE"
fi

if ! is_absolute "$COPILOT_HOME" || ! is_absolute "$DURABLE_HOME"; then
    fail "--copilot-home and --durable-home must be absolute."
fi
COPILOT_HOME="$(canonical_path "$COPILOT_HOME")"
DURABLE_HOME="$(canonical_path "$DURABLE_HOME")"
if [[ -n "$PROJECT_ROOT" ]]; then
    PROJECT_ROOT="$(canonical_path "$PROJECT_ROOT" true)"
fi

if [[ "$ACTION" == source-id ]]; then
    [[ -n "$SOURCE_FILE" ]] || fail "source-id requires --source-json or --source-file."
    normalize_source "$SOURCE_FILE" ""
    derive_identity "${MARKETPLACE_KEY:-marketplace}"
    emit_source_identity
    exit 0
fi

CONTEXT="${CONTEXT:-${COPILOT_EXTENSIONS_CONTEXT:-}}"
if [[ "$ACTION" == validate ]]; then
    [[ -n "$CONTEXT" ]] || fail "validate requires --context or COPILOT_EXTENSIONS_CONTEXT."
    validate_context_receipt \
        "$CONTEXT" "$DURABLE_HOME" "$EXPECTED_MARKETPLACE_ID" "$EXPECTED_PLUGIN_ID" \
        "${EXPECTED_PAYLOAD_ROOT:-$PAYLOAD_ROOT}" "$EXPECTED_CELL_ROOT"
    printf '%s\n' "$CONTEXT_JSON"
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
