guest_is_wsl() {
    local release cgroup=""
    [[ -f /.dockerenv || -f /run/.containerenv ]] && return 1
    if [[ -r /proc/1/cgroup ]]; then cgroup="$(</proc/1/cgroup)"; fi
    [[ "$cgroup" =~ /docker|/kubepods|containerd|/lxc|libpod ]] && return 1
    [[ -r /proc/sys/kernel/osrelease ]] || return 1
    release="$(</proc/sys/kernel/osrelease)"
    [[ "${release,,}" == *microsoft* ]]
}

guest_machine_id() {
    local identity
    [[ -r /etc/machine-id ]] ||
        fail "guest-identity-unavailable: cannot read the operating-system machine identity."
    identity="$(</etc/machine-id)"
    identity="${identity#"${identity%%[![:space:]]*}"}"
    identity="${identity%"${identity##*[![:space:]]}"}"
    [[ "$identity" =~ ^[0-9a-fA-F]{32}$ && ! "$identity" =~ ^0{32}$ ]] ||
        fail "guest-identity-unavailable: the operating-system machine identity is invalid."
    GUEST_MACHINE_ID="${identity,,}"
}

guest_distribution_name() {
    local LC_ALL=C
    [[ -n "$1" && ! "$1" =~ [[:cntrl:]] ]] ||
        fail "guest-identity-invalid: distribution must be a non-empty single-line string."
}

guest_distribution_key() {
    local LC_ALL=C
    GUEST_DISTRIBUTION_KEY="${1,,}"
}

guest_read_environment() {
    local profile="$1" path owner mode size snapshot schema version distribution machine_id home key
    GUEST_REGISTERED_DISTRIBUTION=""
    path="$profile/.copilot-extensions/guest-environment.json"
    [[ -e "$path" || -L "$path" ]] || return 0
    [[ ! -L "$path" && ! -L "${path%/*}" ]] ||
        fail "guest-identity-invalid: guest registration may not be linked."
    [[ -f "$path" ]] ||
        fail "guest-identity-invalid: guest registration must be a bounded regular file."
    read -r owner mode size < <(stat -c '%u %a %s' -- "$path")
    [[ "$owner" =~ ^[0-9]+$ && "$mode" =~ ^[0-7]+$ && "$size" =~ ^[0-9]+$ ]] ||
        fail "guest-identity-invalid: cannot inspect guest registration ownership."
    ((size <= 65536)) ||
        fail "guest-identity-invalid: guest registration must be a bounded regular file."
    [[ "$owner" == "$(id -u)" ]] && (( (8#$mode & 0022) == 0 )) ||
        fail "guest-identity-invalid: guest registration must be owned and writable only by this user."
    capture_json_for_validation snapshot "$path" "Guest registration"
    assert_json_object_length "$snapshot" "" 5 "Guest registration"
    while IFS= read -r key; do
        case "$key" in schema|version|distribution|machineId|homeRealPath) ;;
            *) fail "guest-identity-invalid: unsupported or malformed guest registration." ;;
        esac
    done < <(json_object_keys "$snapshot" "")
    json_optional_string_into schema "$snapshot" schema
    version="$(json_optional_path "$snapshot" version)"
    [[ "$schema" == copilot-extensions.guest-environment &&
       "$(json_type_path "$snapshot" version)" == number && "$version" == 1 ]] ||
        fail "guest-identity-invalid: unsupported or malformed guest registration."
    json_optional_string_into distribution "$snapshot" distribution
    guest_distribution_name "$distribution"
    json_optional_string_into machine_id "$snapshot" machineId
    json_optional_string_into home "$snapshot" homeRealPath
    guest_machine_id
    [[ "$home" == "$profile" && "$machine_id" == "$GUEST_MACHINE_ID" ]] ||
        fail "guest-identity-foreign: registration does not identify this operating-system guest and profile."
    GUEST_REGISTERED_DISTRIBUTION="$distribution"
}

guest_resolve_distribution() {
    local profile="$1" observed="${WSL_DISTRO_NAME:-}" observed_key registered_key
    CURRENT_WSL_DISTRO=""
    CURRENT_WSL_DISTRO_TYPE=null
    guest_is_wsl || return 0
    [[ -z "$observed" ]] || guest_distribution_name "$observed"
    guest_read_environment "$profile"
    if [[ -n "$GUEST_REGISTERED_DISTRIBUTION" ]]; then
        if [[ -n "$observed" ]]; then
            guest_distribution_key "$observed"; observed_key="$GUEST_DISTRIBUTION_KEY"
            guest_distribution_key "$GUEST_REGISTERED_DISTRIBUTION"; registered_key="$GUEST_DISTRIBUTION_KEY"
            [[ "$observed_key" == "$registered_key" ]] ||
                fail "guest-identity-conflict: observed distribution differs from the guest registration."
        fi
        CURRENT_WSL_DISTRO="$GUEST_REGISTERED_DISTRIBUTION"
    elif [[ -n "$observed" ]]; then
        CURRENT_WSL_DISTRO="$observed"
    else
        fail "guest-identity-unregistered: this WSL entry has no distribution identity. Run guest-register --distribution <name> through that named direct WSL entry; no runtime ownership metadata has been changed."
    fi
    CURRENT_WSL_DISTRO_TYPE=string
}

guest_register_environment() {
    local requested_key observed_key path created=false registration
    guest_distribution_name "$DISTRIBUTION"
    guest_is_wsl ||
        fail "guest-identity-conflict: guest-register requires a real WSL guest, not a host or container."
    [[ -n "${WSL_DISTRO_NAME:-}" ]] ||
        fail "guest-identity-unverified: run guest-register through the matching named direct WSL entry."
    guest_distribution_name "$WSL_DISTRO_NAME"
    guest_distribution_key "$DISTRIBUTION"; requested_key="$GUEST_DISTRIBUTION_KEY"
    guest_distribution_key "$WSL_DISTRO_NAME"; observed_key="$GUEST_DISTRIBUTION_KEY"
    [[ "$requested_key" == "$observed_key" ]] ||
        fail "guest-identity-unverified: run guest-register through the matching named direct WSL entry."
    resolve_current_environment
    path="$CURRENT_PROFILE_HOME/.copilot-extensions/guest-environment.json"
    [[ ! -L "${path%/*}" ]] ||
        fail "guest-identity-invalid: guest registration directory may not be linked."
    acquire_lock "${path%/*}/.guest-environment.lock" guest-environment os-profile
    guest_resolve_distribution "$CURRENT_PROFILE_HOME"
    guest_read_environment "$CURRENT_PROFILE_HOME"
    if [[ -z "$GUEST_REGISTERED_DISTRIBUTION" ]]; then
        guest_machine_id
        registration="{\"schema\":\"copilot-extensions.guest-environment\",\"version\":1,\"distribution\":$(json_quote "$CURRENT_WSL_DISTRO"),\"machineId\":$(json_quote "$GUEST_MACHINE_ID"),\"homeRealPath\":$(json_quote "$CURRENT_PROFILE_HOME")}"
        atomic_write_json "$path" "$registration"
        created=true
    fi
    release_lock
    printf '{"action":"guest-register","status":"ready","created":%s,"registration":%s,"environment":' \
        "$created" "$(json_quote "$path")"
    emit_environment_json
    printf '}\n'
}

guest_environment_status() {
    local path registration=null status=ready registration_state=not-applicable requested_key actual_key
    [[ "$DISTRIBUTION_SUPPLIED" != true ]] || guest_distribution_name "$DISTRIBUTION"
    resolve_current_environment
    path="$CURRENT_PROFILE_HOME/.copilot-extensions/guest-environment.json"
    if [[ -n "$CURRENT_WSL_DISTRO" ]]; then
        registration_state=unregistered
        if [[ -f "$path" ]]; then registration="$(json_quote "$path")"; registration_state=registered; fi
        if [[ "$DISTRIBUTION_SUPPLIED" == true ]]; then
            guest_distribution_key "$DISTRIBUTION"; requested_key="$GUEST_DISTRIBUTION_KEY"
            guest_distribution_key "$CURRENT_WSL_DISTRO"; actual_key="$GUEST_DISTRIBUTION_KEY"
            [[ "$requested_key" == "$actual_key" ]] ||
                fail "guest-identity-conflict: requested distribution differs from the current guest."
        fi
    elif [[ "$DISTRIBUTION_SUPPLIED" == true ]]; then status=unavailable
    fi
    printf '{"action":"guest-status","status":%s,"environment":' "$(json_quote "$status")"
    emit_environment_json
    printf ',"registrationState":%s,"registration":%s,"hostDefault":{"status":"unavailable","distribution":null,"expectedDistribution":%s,"matches":null}}\n' \
        "$(json_quote "$registration_state")" "$registration" "$(json_string_or_null "$DISTRIBUTION")"
    [[ "$status" == ready ]]
}
