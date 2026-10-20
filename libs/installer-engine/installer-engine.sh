#!/usr/bin/env bash
# shellcheck shell=bash
# Canonical vendored installer-engine helpers for runtime plugins.
#
# This file is the canonical source of truth for the shared installer-engine
# helpers that runtime plugins use in one of two valid dev-time forms:
# (1) a canonical-reference adopter sources this file directly from
# `libs/installer-engine/` while working in `dev`, and promotion materializes
# a real plugin-local `scripts/installer-engine.sh` copy back into the shipped
# payload; or (2) a byte-vendored adopter keeps that plugin-local copy on
# `dev`, kept byte-identical by `tools/sync-installer-engine.py`. In the
# shipped artifact, plugins never source this file across plugin boundaries.

INSTALLER_ENGINE_DEPENDENCY_POLICY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/installer_dependency_policy.py"

remove_install_runtime_content() {
    # The caller holds the transaction lock; retain its inode and staged owners.
    local root="${1:-}" entry name home
    while [[ "$root" != "/" ]]; do
        case "$root" in
            */) root="${root%/}" ;;
            */.) root="${root%.}" ;;
            *) break ;;
        esac
    done
    if [[ -z "$root" || ! -d "$root" || -L "$root" ]]; then
        printf '%s\n' 'Runtime purge requires a real install directory, not a link' >&2
        return 1
    fi
    root="$(CDPATH= cd -P -- "$root" && printf '%s.' "$PWD")" || return 1
    root="${root%.}"
    if [[ "$root" == "/" ]]; then
        printf '%s\n' 'Refusing to purge a filesystem root' >&2
        return 1
    fi
    if [[ -n "${HOME:-}" && -d "$HOME" ]]; then
        home="$(CDPATH= cd -P -- "$HOME" && printf '%s.' "$PWD")" || return 1
        home="${home%.}"
        if [[ "$root" == "$home" ]]; then
            printf '%s\n' 'Refusing to purge a user home' >&2
            return 1
        fi
    fi
    if [[ ! -r "$root" || ! -x "$root" ]]; then
        printf '%s\n' 'Runtime purge requires a readable and searchable install directory' >&2
        return 1
    fi
    for entry in "$root"/* "$root"/.[!.]* "$root"/..?*; do
        [[ -e "$entry" || -L "$entry" ]] || continue
        name="${entry##*/}"
        case "$name" in
            .install.lock|.install.lock.pid|.install.lock.pid.reap|.install-stage) continue ;;
        esac
        if ! rm -rf -- "$entry"; then
            printf 'Runtime purge failed while removing: %s\n' "$entry" >&2
            return 1
        fi
    done
}

invoke_native_capture() {
    local output rc
    output="$("$@" 2>&1)"; rc=$?
    printf '%s' "$output"
    return "$rc"
}

test_is_sre_module_mismatch() {
    grep -q 'SRE module mismatch' <<<"${1:-}"
}

test_is_venv_corruption() {
    grep -qE 'failed to locate pyvenv\.cfg|exit code:? ?106' <<<"${1:-}"
}

invoke_uv_pip_install_resilient() {
    local uv_cmd="$1"; shift
    local payload_dir="${INSTALLER_ENGINE_PAYLOAD_DIR_TO_SCRUB:-}"
    local before_attempt="${INSTALLER_ENGINE_BEFORE_ATTEMPT:-}"
    local before_argument="${INSTALLER_ENGINE_BEFORE_ATTEMPT_ARGUMENT:-}"
    if [[ -n "$before_attempt" ]] && ! declare -F "$before_attempt" >/dev/null; then
        printf '%s\n' 'Installer before-attempt callback is not a shell function' >&2
        return 1
    fi
    local policy_python="" previous="" arg
    for arg in "$@"; do
        if [[ "$previous" == "--python" && -x "$arg" ]]; then
            policy_python="$arg"
            break
        fi
        previous="$arg"
    done
    if [[ -z "$policy_python" ]]; then
        policy_python="$(command -v python3 || command -v python || true)"
    fi
    if [[ -z "$policy_python" || ! -f "$INSTALLER_ENGINE_DEPENDENCY_POLICY" ]]; then
        printf '%s\n' 'dependency-seasoning: bootstrap Python or canonical policy helper is unavailable' >&2
        return 1
    fi
    local out rc delay
    if [[ -n "$before_attempt" ]]; then "$before_attempt" "$before_argument" || return $?; fi
    out="$("$policy_python" -I "$INSTALLER_ENGINE_DEPENDENCY_POLICY" --uv "$uv_cmd" -- "$@" 2>&1)"; rc=$?
    for delay in 3 6 10; do
        if [[ $rc -eq 0 ]] || ! test_is_sre_module_mismatch "$out"; then
            break
        fi
        _warn "uv build hit a transient SRE module mismatch (shared Python cache race, #6785) -- retrying in ${delay}s"
        sleep "$delay"
        if [[ -n "$before_attempt" ]]; then "$before_attempt" "$before_argument" || return $?; fi
        out="$("$policy_python" -I "$INSTALLER_ENGINE_DEPENDENCY_POLICY" --uv "$uv_cmd" -- "$@" 2>&1)"; rc=$?
    done
    if [[ $rc -eq 0 && -n "$payload_dir" ]]; then
        rm -rf "$payload_dir/build" "$payload_dir"/*.egg-info 2>/dev/null || true
    fi
    printf '%s\n' "$out"
    return "$rc"
}

invoke_uv_venv_resilient() {
    local uv_cmd="$1" venv_dir="$2"; shift 2
    local out rc delay
    out="$("$uv_cmd" venv "$venv_dir" "$@" 2>&1)"; rc=$?
    for delay in 3 6 10; do
        if [[ $rc -eq 0 && -f "$venv_dir/pyvenv.cfg" ]]; then
            printf '%s\n' "$out"
            return 0
        fi
        if [[ $rc -eq 0 ]]; then
            _warn "uv venv reported success but pyvenv.cfg is missing at $venv_dir/pyvenv.cfg (shared interpreter race, #6852) -- retrying in ${delay}s"
        elif test_is_sre_module_mismatch "$out"; then
            _warn "uv venv hit a transient SRE module mismatch (shared Python cache race, #6785) -- retrying in ${delay}s"
        elif test_is_venv_corruption "$out"; then
            _warn "uv venv hit a transient pyvenv.cfg corruption (shared Python cache race, #6852) -- retrying in ${delay}s"
        else
            break
        fi
        sleep "$delay"
        out="$("$uv_cmd" venv "$venv_dir" "$@" 2>&1)"; rc=$?
    done
    printf '%s\n' "$out"
    if [[ $rc -eq 0 && -f "$venv_dir/pyvenv.cfg" ]]; then
        return 0
    fi
    if [[ $rc -eq 0 ]]; then
        return 1
    fi
    return "$rc"
}

ensure_uv() {
    local install_root="$1"
    local tool_dir_name="${2:-tool}"
    local acquire_if_missing="${3:-1}"
    local bootstrap_version="${4:-0.12.6}"
    local tool_dir="$install_root/$tool_dir_name"
    local uv_cmd=""
    if command -v uv >/dev/null 2>&1; then
        uv_cmd="$(command -v uv)"
        if "$uv_cmd" --version >/dev/null 2>&1; then
            printf '%s\n' "$uv_cmd"
            return 0
        fi
    fi
    if [[ -x "$tool_dir/uv" ]]; then
        if "$tool_dir/uv" --version >/dev/null 2>&1; then
            export PATH="$tool_dir:$PATH"
            printf '%s\n' "$tool_dir/uv"
            return 0
        fi
        rm -f "$tool_dir/uv" "$tool_dir/uvx" 2>/dev/null || true
    fi
    [[ "$acquire_if_missing" = "1" ]] || return 1
    _step "uv not found -- vendoring pinned uv $bootstrap_version into $tool_dir" >&2
    mkdir -p "$tool_dir"

    local os arch libc asset expected_sha url archive staging got=""
    case "$(uname -s)" in
        Darwin) os="darwin" ;;
        Linux) os="linux" ;;
        *)
            _fail "uv bootstrap does not support this POSIX host: $(uname -s)" >&2
            return 1
            ;;
    esac
    case "$(uname -m)" in
        x86_64|amd64) arch="x86_64" ;;
        arm64|aarch64) arch="aarch64" ;;
        *)
            _fail "uv bootstrap does not support this POSIX architecture: $(uname -m)" >&2
            return 1
            ;;
    esac
    if [[ "$os" == "darwin" ]]; then
        case "$arch" in
            x86_64)
                asset="uv-x86_64-apple-darwin.tar.gz"
                expected_sha="2a26ea71bbeff1c7e12c2cc40245c96a041deff276bc921e7038e304d5d3e04c"
                ;;
            aarch64)
                asset="uv-aarch64-apple-darwin.tar.gz"
                expected_sha="14b459d51ea2e71eeba28c45a268c922bdf8607fc6455e3f40b4e082895d160d"
                ;;
        esac
    else
        libc="gnu"
        if command -v ldd >/dev/null 2>&1 && ldd --version 2>&1 | grep -qi 'musl'; then
            libc="musl"
        fi
        case "$arch/$libc" in
            x86_64/gnu)
                asset="uv-x86_64-unknown-linux-gnu.tar.gz"
                expected_sha="8681d8921e7d520fb368991dcf5f9c1905b80f5bf2a265a0ed085c8d8e342477"
                ;;
            x86_64/musl)
                asset="uv-x86_64-unknown-linux-musl.tar.gz"
                expected_sha="14e4172aace66a475062cebec7ca04f497d5619e95325dfcc9e4447b9c516846"
                ;;
            aarch64/gnu)
                asset="uv-aarch64-unknown-linux-gnu.tar.gz"
                expected_sha="d58030acd26159499ac82f32da12d1b3c12a3a1bfc414232d9082070c03e128d"
                ;;
            aarch64/musl)
                asset="uv-aarch64-unknown-linux-musl.tar.gz"
                expected_sha="3719891de9ab41c878a84331e55826d2a46421976a346a65326513a6795b089a"
                ;;
            *)
                _fail "uv bootstrap does not support this Linux target: $arch/$libc" >&2
                return 1
                ;;
        esac
    fi

    url="https://github.com/astral-sh/uv/releases/download/$bootstrap_version/$asset"
    archive="$tool_dir/$asset"
    staging="$install_root/.uv-stage-$$"
    rm -rf "$staging" 2>/dev/null || true
    mkdir -p "$staging"
    if command -v curl >/dev/null 2>&1; then curl -LsSf "$url" -o "$archive" 2>/dev/null && got=1; fi
    if [[ -z "$got" ]] && command -v wget >/dev/null 2>&1; then wget -qO "$archive" "$url" 2>/dev/null && got=1; fi
    if [[ -z "$got" ]] && command -v python3 >/dev/null 2>&1; then
        python3 - "$url" "$archive" <<'PY' 2>/dev/null && got=1
import sys, urllib.request
urllib.request.urlretrieve(sys.argv[1], sys.argv[2])
PY
    fi
    if [[ -z "$got" || ! -s "$archive" ]]; then
        rm -f "$archive"
        rm -rf "$staging" 2>/dev/null || true
        return 1
    fi

    local actual_sha=""
    if command -v sha256sum >/dev/null 2>&1; then
        actual_sha="$(sha256sum "$archive" | awk '{print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
        actual_sha="$(shasum -a 256 "$archive" | awk '{print $1}')"
    elif command -v openssl >/dev/null 2>&1; then
        actual_sha="$(openssl dgst -sha256 "$archive" | awk '{print $NF}')"
    fi
    if [[ "$actual_sha" != "$expected_sha" ]]; then
        rm -f "$archive"
        rm -rf "$staging" 2>/dev/null || true
        _fail "uv archive SHA-256 mismatch for $asset (expected $expected_sha, got ${actual_sha:-<missing>})" >&2
        return 1
    fi

    if ! tar -xzf "$archive" -C "$staging" >/dev/null 2>&1; then
        rm -f "$archive"
        rm -rf "$staging" 2>/dev/null || true
        _fail "Failed to extract vendored uv archive: $asset" >&2
        return 1
    fi
    local uv_source uvx_source
    uv_source="$(find "$staging" -type f -name uv | head -n1)"
    uvx_source="$(find "$staging" -type f -name uvx | head -n1)"
    if [[ -z "$uv_source" ]]; then
        rm -f "$archive"
        rm -rf "$staging" 2>/dev/null || true
        _fail "Vendored uv archive did not contain a uv binary: $asset" >&2
        return 1
    fi
    if [[ -n "$uvx_source" ]]; then
        mv -f "$uvx_source" "$tool_dir/uvx"
        chmod +x "$tool_dir/uvx" 2>/dev/null || true
    else
        rm -f "$tool_dir/uvx" 2>/dev/null || true
    fi
    mv -f "$uv_source" "$tool_dir/uv"
    chmod +x "$tool_dir/uv" 2>/dev/null || true
    rm -f "$archive"
    rm -rf "$staging" 2>/dev/null || true
    if "$tool_dir/uv" --version >/dev/null 2>&1; then
        export PATH="$tool_dir:$PATH"
        _ok "Vendored uv into $tool_dir" >&2
        printf '%s\n' "$tool_dir/uv"
        return 0
    fi
    rm -f "$tool_dir/uv" "$tool_dir/uvx" 2>/dev/null || true
    return 1
}

new_signed_venv() {
    # POSIX has no SAC/authenticode constraint; use the resilient uv creation path.
    local uv_cmd="$1" venv_dir="$2" python_version="$3"
    if [[ -x "$venv_dir/bin/python" && -f "$venv_dir/pyvenv.cfg" ]]; then
        return 0
    fi
    if [[ -x "$venv_dir/bin/python" && ! -f "$venv_dir/pyvenv.cfg" ]]; then
        _warn "Existing venv python present but pyvenv.cfg is missing at $venv_dir/pyvenv.cfg (shared interpreter race, #6852) -- rebuilding"
        rm -rf "$venv_dir"
    fi
    local venv_out
    if ! venv_out="$(invoke_uv_venv_resilient "$uv_cmd" "$venv_dir" --python "$python_version" --allow-existing 2>&1)"; then
        if ! venv_out="$(invoke_uv_venv_resilient "$uv_cmd" "$venv_dir" --allow-existing 2>&1)"; then
            [[ -n "$venv_out" ]] && printf '%s\n' "$venv_out" >&2
            return 1
        fi
    fi
}

source_kind_for_path() {
    case "$(printf '%s' "$1" | tr '\\' '/')" in
        */.copilot/installed-plugins/*) printf 'marketplace' ;;
        *) printf 'local' ;;
    esac
}

write_deploy_manifest() {
    local service="$1" plugin="$2" install_path="$3" plugin_path="$4" venv_path="$5"
    local additional_json="${6:-}" source_path_override="${7:-}" version_override="${8:-}"
    local manifest="$install_path/deploy-manifest.json"
    local kind ver commit branch dirty content_hash tmp provenance_path provenance_json_path
    if [[ -d "$manifest" ]]; then
        _fail "Deploy manifest destination must be a file: $manifest"
        return 1
    fi
    provenance_path="${source_path_override:-$plugin_path}"
    provenance_json_path="${provenance_path//\\//}"
    provenance_json_path="${provenance_json_path//\"/\\\"}"
    if [[ -n "$source_path_override" ]]; then
        kind="$(source_kind_for_path "$provenance_path")" || {
            _fail "Deploy manifest source classification failed"; return 1;
        }
    else
        kind="$(_source_kind "$provenance_path")" || {
            _fail "Deploy manifest source classification failed"; return 1;
        }
    fi
    ver="$version_override"
    if [[ -z "$ver" ]]; then
        ver="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$plugin_path/pyproject.toml" 2>/dev/null | head -n1 || true)"
    fi
    [[ -n "$ver" ]] || ver="0.0.0"
    commit="null"; branch="null"; dirty="false"
    if [[ "$kind" == "local" ]]; then
        local repo_root _c _b _d
        repo_root="$(dirname "$provenance_path")"
        if [[ -d "$repo_root" ]]; then
            repo_root="$(cd "$repo_root" && pwd)" || {
                _fail "Deploy manifest repository path cannot be resolved"; return 1;
            }
            local git_info
            git_info="$(_git_info "$repo_root")" || {
                _fail "Deploy manifest repository metadata failed"; return 1;
            }
            read -r _c _b _d <<< "$git_info" || {
                _fail "Deploy manifest repository metadata is unavailable"; return 1;
            }
            commit="\"$_c\""; branch="\"$_b\""; dirty="$_d"
        fi
    fi
    content_hash=""
    if declare -F _payload_hash >/dev/null 2>&1; then
        content_hash="$(_payload_hash)" || {
            _fail "Deploy manifest payload hash failed"; return 1;
        }
    fi
    if [[ -n "${_RUNTIME_PUBLICATION_EVIDENCE:-}" ]]; then
        _runtime_publication_stage "$manifest" tmp || return 1
    else
        tmp="$(mktemp "$manifest.tmp.XXXXXX")" || {
            _fail "Deploy manifest staging failed: $manifest"; return 1;
        }
    fi
    if ! cat > "$tmp" <<EOF
{
  "schema_version": 3,
  "service": "$service",
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$kind",
    "path": "$provenance_json_path",
    "repo": "copilot-extensions",
    "plugin": "$plugin",
    "version": "$ver",
    "commit": $commit,
    "branch": $branch,
    "dirty": $dirty$( [[ -n "$content_hash" ]] && printf ',\n    "content_hash": "%s"' "$content_hash" )
  },
  "venv": "$venv_path",
  "runtime": "python"$( [[ -n "$additional_json" ]] && printf ',\n%s' "$additional_json" )
}
EOF
    then
        rm -f "$tmp"
        _fail "Deploy manifest write failed: $manifest"
        return 1
    fi
    if [[ -n "${_RUNTIME_PUBLICATION_EVIDENCE:-}" ]]; then
        _runtime_publication_commit_file "$manifest" "$tmp" || {
            _fail "Deploy manifest publication failed; staging and evidence retained: $manifest"
            return 1
        }
        _ok "Deploy manifest written (source: $kind)"
        return 0
    fi
    mv -f "$tmp" "$manifest" || {
        rm -f "$tmp"
        _fail "Deploy manifest publication failed: $manifest"
        return 1
    }
    _ok "Deploy manifest written (source: $kind)"
}

write_simple_binstub() {
    local command_name="$1" module_name="$2" runtime_root="$3" local_bin="$4" install_bin_dir="$5" snapshot_installer_rel="$6" no_self_provision_env="$7"
    local resolver_ps1_source="${8:-}" resolver_sh_source="${9:-}"
    local runtime_root_literal snapshot_installer_literal
    printf -v runtime_root_literal '%q' "$runtime_root" || return 1
    printf -v snapshot_installer_literal '%q' "$snapshot_installer_rel" || return 1
    local resolver source destination temporary
    for source in "$resolver_ps1_source" "$resolver_sh_source"; do
        if [[ -n "$source" && ! -f "$source" ]]; then
            _fail "Binstub resolver source is unavailable: $source"
            return 1
        fi
    done
    mkdir -p "$local_bin" "$install_bin_dir" || {
        _fail "Binstub directories cannot be prepared: $local_bin"; return 1;
    }
    for destination in "${resolver_ps1_source:+$install_bin_dir/resolve-runtime.ps1}" "${resolver_sh_source:+$install_bin_dir/resolve-runtime.sh}" "$local_bin/$command_name"; do
        if [[ -d "$destination" ]]; then
            _fail "Binstub destination must be a file: $destination"
            return 1
        fi
    done
    for resolver in ps1 sh; do
        if [[ "$resolver" == ps1 ]]; then source="$resolver_ps1_source"; else source="$resolver_sh_source"; fi
        [[ -n "$source" ]] || continue
        destination="$install_bin_dir/resolve-runtime.$resolver"
        temporary="$(mktemp "$install_bin_dir/.resolve-runtime.$resolver.XXXXXX")" || {
            _fail "Binstub resolver staging failed: $destination"; return 1;
        }
        cp -f "$source" "$temporary" && [[ ! -d "$destination" ]] &&
            mv -f "$temporary" "$destination" &&
            [[ ! -e "$temporary" && ! -L "$temporary" && -f "$destination" ]] || {
            rm -f "$temporary"
            _fail "Binstub resolver publication failed: $destination"
            return 1
        }
    done
    local stub="$local_bin/$command_name"
    local stub_tmp
    stub_tmp="$(mktemp "$local_bin/.$command_name.XXXXXX")" || {
        _fail "Binstub staging failed: $stub"; return 1;
    }
    if ! cat > "$stub_tmp" <<EOF
#!/usr/bin/env bash
export PYTHONUTF8=1
_name="$command_name"
_root=$runtime_root_literal
_snapshot_installer_rel=$snapshot_installer_literal
_resolver="\$_root/bin/resolve-runtime.sh"
_resolve() {
    AGENT_RT_PY=""
    if [ -f "\$_resolver" ]; then
        AGENT_RT_ROOT="\$_root"
        . "\$_resolver"
    fi
}
_resolve
[ -n "\$AGENT_RT_PY" ] && exec "\$AGENT_RT_PY" -m "$module_name" "\$@"
if [ -n "\${$no_self_provision_env:-}" ]; then
    printf '[%s] runtime not provisioned (%s set).\n' "\$_name" "$no_self_provision_env" >&2
    exit 1
fi
mkdir -p "\$_root"
_lock="\$_root/.provision.lock"
_lock_link=""
_unlock_provision() {
    if [[ -n "\$_lock_link" ]]; then
        _owner="\$(readlink "\$_lock_link" 2>/dev/null || true)"
        [[ "\$_owner" == "\$\$" ]] && rm -f "\$_lock_link"
        _lock_link=""
    else
        flock -u 9 2>/dev/null || true
        exec 9>&-
    fi
}
if command -v flock >/dev/null 2>&1 && [[ "\${COPILOT_EXT_NO_FLOCK:-}" != "1" ]]; then
    exec 9>"\$_lock"
    flock 9
else
    _lock_link="\$_root/.provision.lock.pid"
    until ln -s "\$\$" "\$_lock_link" 2>/dev/null; do
        _owner="\$(readlink "\$_lock_link" 2>/dev/null || true)"
        case "\$_owner" in
            *[!0-9]*|"") _live=0 ;;
            *) if kill -0 "\$_owner" 2>/dev/null; then _live=1; else _live=0; fi ;;
        esac
        if [[ -z "\$_owner" ]]; then
            # An empty owner means the link vanished after the failed ln -s
            # (or is not a symlink): never unlink then -- a live lock could be
            # created between the read and the unlink. Just retry.
            sleep 0.1
        elif [[ "\$_live" == 0 && "\$(readlink "\$_lock_link" 2>/dev/null || true)" == "\$_owner" ]]; then
            rm -f "\$_lock_link"
        else
            sleep 1
        fi
    done
fi
trap '_unlock_provision' EXIT INT TERM
_resolve
[ -n "\$AGENT_RT_PY" ] && { _unlock_provision; trap - EXIT INT TERM; exec "\$AGENT_RT_PY" -m "$module_name" "\$@"; }
_snapshot="\$(cat "\$_root/payload-dir" 2>/dev/null || true)"
_install="\$_snapshot/\$_snapshot_installer_rel"
if [ ! -f "\$_install" ]; then
    printf '[%s] cannot self-provision: owning snapshot installer unavailable: %s\n' "\$_name" "\$_install" >&2
    exit 127
fi
printf '[%s] runtime not provisioned -- provisioning on first use (may take ~30-120s: acquires uv + builds a venv). Do not kill; extend your timeout.\n' "\$_name" >&2
printf '::agent-provisioning:: plugin=%s eta_seconds=120 reason=first-use\n' "\$_name" >&2
bash "\$_install" provision --install-dir "\$_root" >&2
_rc=\$?
_resolve
_unlock_provision
trap - EXIT INT TERM
if [ "\$_rc" -eq 0 ] && [ -n "\$AGENT_RT_PY" ]; then
    exec "\$AGENT_RT_PY" -m "$module_name" "\$@"
fi
printf '[%s] provisioning completed without a resolvable runtime.\n' "\$_name" >&2
if [ "\${_rc:-1}" -eq 0 ]; then
    exit 1
fi
exit "\${_rc:-1}"
EOF
    then
        rm -f "$stub_tmp"
        _fail "Binstub write failed: $stub"
        return 1
    fi
    chmod +x "$stub_tmp" && [[ ! -d "$stub" ]] && mv -f "$stub_tmp" "$stub" &&
        [[ ! -e "$stub_tmp" && ! -L "$stub_tmp" && -f "$stub" ]] || {
        rm -f "$stub_tmp"
        _fail "Binstub publication failed: $stub"
        return 1
    }
    _ok "Binstub: $stub (self-provisioning)"
}

# The caller holds the installation publication lock through commit or restore.
_runtime_publication_state() {
    local path="$1" before after digest
    [[ ! -L "$path" ]] || return 1
    if [[ ! -e "$path" ]]; then printf '%s' absent; return 0; fi
    [[ -f "$path" && -O "$path" ]] || return 1
    before="$(stat -c '%d:%i:%f:%u:%g:%h:%s:%y' "$path" 2>/dev/null)" ||
        before="$(stat -f '%d:%i:%p:%u:%g:%l:%z:%m' "$path" 2>/dev/null)" || return 1
    if command -v sha256sum >/dev/null 2>&1; then
        digest="$(sha256sum < "$path")" || return 1
    elif command -v shasum >/dev/null 2>&1; then
        digest="$(shasum -a 256 < "$path")" || return 1
    else
        digest="$(openssl dgst -sha256 -r < "$path")" || return 1
    fi
    digest="${digest%% *}"
    [[ "$digest" =~ ^[0-9a-f]{64}$ && ! -L "$path" && -f "$path" ]] || return 1
    after="$(stat -c '%d:%i:%f:%u:%g:%h:%s:%y' "$path" 2>/dev/null)" ||
        after="$(stat -f '%d:%i:%p:%u:%g:%l:%z:%m' "$path" 2>/dev/null)" || return 1
    [[ "$before" == "$after" ]] || return 1
    printf '%s:%s' "$before" "$digest"
}

_runtime_publication_index() {
    local i
    for ((i=0; i<${#_RUNTIME_PUBLICATION_PATHS[@]}; i++)); do
        if [[ "${_RUNTIME_PUBLICATION_PATHS[i]}" == "$1" ]]; then
            printf '%s' "$i"; return 0
        fi
    done
    return 1
}

_runtime_publication_journal() {
    local i native_receipt
    if [[ -e "$_RUNTIME_PUBLICATION_EVIDENCE/journal" || -L "$_RUNTIME_PUBLICATION_EVIDENCE/journal" ]]; then
        [[ -n "${_RUNTIME_PUBLICATION_JOURNAL_STATE:-}" &&
            "$(_runtime_publication_state "$_RUNTIME_PUBLICATION_EVIDENCE/journal")" == "$_RUNTIME_PUBLICATION_JOURNAL_STATE" ]] || return 1
    fi
    {
        printf '# path\\tbaseline-state\\towned-states\\tbackup\\tintent\\tintent-state\n'
        for ((i=0; i<${#_RUNTIME_PUBLICATION_PATHS[@]}; i++)); do
            printf '%q\t%q\t%q\t%q\t%q\t%q\n' "${_RUNTIME_PUBLICATION_PATHS[i]}" \
                "${_RUNTIME_PUBLICATION_BASELINE[i]}" "${_RUNTIME_PUBLICATION_OWNED[i]}" \
                "${_RUNTIME_PUBLICATION_BACKUPS[i]}" "${_RUNTIME_PUBLICATION_INTENTS[i]}" \
                "${_RUNTIME_PUBLICATION_INTENT_STATES[i]}"
        done
        for ((i=0; i<${#_RUNTIME_PUBLICATION_TEMPS[@]}; i++)); do
            printf '# stage\t%q\t%q\n' "${_RUNTIME_PUBLICATION_TEMPS[i]}" "${_RUNTIME_PUBLICATION_TEMP_IDS[i]}"
        done
        printf '# native-context\t%q\n' "${_RUNTIME_PUBLICATION_NATIVE_CONTEXT:-}"
        for native_receipt in "${_RUNTIME_PUBLICATION_NATIVE_RECEIPTS[@]:-}"; do
            printf '# required-native-proof\t%q\n' "$native_receipt"
        done
    } > "$_RUNTIME_PUBLICATION_EVIDENCE/journal" || return 1
    _RUNTIME_PUBLICATION_JOURNAL_STATE="$(_runtime_publication_state "$_RUNTIME_PUBLICATION_EVIDENCE/journal")"
}

_runtime_publication_file_id() {
    [[ ! -L "$1" && -f "$1" && -O "$1" ]] || return 1
    stat -c '%d:%i' "$1" 2>/dev/null || stat -f '%d:%i' "$1" 2>/dev/null
}

_runtime_publication_dir_id() {
    [[ ! -L "$1" && -d "$1" && -O "$1" ]] || return 1
    stat -c '%d:%i' "$1" 2>/dev/null || stat -f '%d:%i' "$1" 2>/dev/null
}

_runtime_publication_begin() {
    _RUNTIME_PUBLICATION_PATHS=("$@")
    _RUNTIME_PUBLICATION_BACKUPS=()
    _RUNTIME_PUBLICATION_BASELINE=()
    _RUNTIME_PUBLICATION_BACKUP_STATES=()
    _RUNTIME_PUBLICATION_OWNED=()
    _RUNTIME_PUBLICATION_INTENTS=()
    _RUNTIME_PUBLICATION_INTENT_STATES=()
    _RUNTIME_PUBLICATION_TEMPS=()
    _RUNTIME_PUBLICATION_TEMP_IDS=()
    _RUNTIME_PUBLICATION_STAGE_DIRS=()
    _RUNTIME_PUBLICATION_STAGE_DIR_IDS=()
    _RUNTIME_PUBLICATION_STAGE_FILES=()
    _RUNTIME_PUBLICATION_EVIDENCE_FILES=()
    _RUNTIME_PUBLICATION_EVIDENCE_STATES=()
    _RUNTIME_PUBLICATION_JOURNAL_STATE=""
    _RUNTIME_PUBLICATION_NATIVE_CONTEXT=""
    _RUNTIME_PUBLICATION_NATIVE_RECEIPTS=()
    _RUNTIME_PUBLICATION_NATIVE_HASHES=()
    _RUNTIME_PUBLICATION_EVIDENCE="$(mktemp -d "$INSTALL_DIR/.publication.XXXXXXXX")" || return 1
    local path parent backup state i=0
    for path in "$@"; do
        if ! state="$(_runtime_publication_state "$path")"; then
            _fail "Publication destination must be an ordinary file: $path"
            _runtime_publication_discard
            return 1
        fi
        _RUNTIME_PUBLICATION_BASELINE+=("$state")
        _RUNTIME_PUBLICATION_OWNED+=("")
        _RUNTIME_PUBLICATION_INTENTS+=("")
        _RUNTIME_PUBLICATION_INTENT_STATES+=("")
        _RUNTIME_PUBLICATION_NATIVE_HASHES+=("")
        parent="$(dirname "$path")" || { _runtime_publication_discard; return 1; }
        mkdir -p "$parent" || { _runtime_publication_discard; return 1; }
        backup=""
        if [[ "$state" != absent ]]; then
            backup="$_RUNTIME_PUBLICATION_EVIDENCE/before.$i"
            cp -p "$path" "$backup" || { _runtime_publication_discard; return 1; }
            [[ "$(_runtime_publication_state "$path")" == "$state" ]] || return 1
            local backup_state
            backup_state="$(_runtime_publication_state "$backup")" || return 1
            [[ "${backup_state##*:}" == "${state##*:}" ]] || return 1
            _RUNTIME_PUBLICATION_BACKUP_STATES+=("$backup_state")
            _RUNTIME_PUBLICATION_EVIDENCE_FILES+=("$backup")
            _RUNTIME_PUBLICATION_EVIDENCE_STATES+=("$backup_state")
        else
            _RUNTIME_PUBLICATION_BACKUP_STATES+=("")
        fi
        _RUNTIME_PUBLICATION_BACKUPS+=("$backup")
        ((i+=1))
    done
    _runtime_publication_journal
}

_runtime_publication_expect_value() {
    [[ -n "${_RUNTIME_PUBLICATION_EVIDENCE:-}" ]] || return 0
    local i file
    i="$(_runtime_publication_index "$1")" || return 0
    _runtime_publication_check "$1" || return 1
    file="$(mktemp "$_RUNTIME_PUBLICATION_EVIDENCE/intent.$i.XXXXXXXX")" || return 1
    printf '%s' "$2" > "$file" || return 1
    _RUNTIME_PUBLICATION_INTENTS[i]="$file"
    _RUNTIME_PUBLICATION_INTENT_STATES[i]="$(_runtime_publication_state "$file")" || return 1
    _RUNTIME_PUBLICATION_EVIDENCE_FILES+=("$file")
    _RUNTIME_PUBLICATION_EVIDENCE_STATES+=("${_RUNTIME_PUBLICATION_INTENT_STATES[i]}")
    _RUNTIME_PUBLICATION_NATIVE_HASHES[i]+=" ${_RUNTIME_PUBLICATION_INTENT_STATES[i]##*:}"
    _runtime_publication_journal
}

_runtime_publication_check() {
    [[ -n "${_RUNTIME_PUBLICATION_EVIDENCE:-}" ]] || return 0
    local i state
    i="$(_runtime_publication_index "$1")" || return 0
    state="$(_runtime_publication_state "$1")" || state=unsafe
    if [[ "$state" != "${_RUNTIME_PUBLICATION_BASELINE[i]}" &&
          "|${_RUNTIME_PUBLICATION_OWNED[i]}|" != *"|$state|"* ]]; then
        if ! _runtime_publication_native_check "$1"; then
            _fail "Unowned publication state: $1; recovery evidence retained: $_RUNTIME_PUBLICATION_EVIDENCE"
            return 1
        fi
    fi
}

_runtime_publication_expect_file() {
    [[ -n "${_RUNTIME_PUBLICATION_EVIDENCE:-}" ]] || return 0
    local i file
    i="$(_runtime_publication_index "$1")" || return 0
    _runtime_publication_check "$1" || return 1
    file="$(mktemp "$_RUNTIME_PUBLICATION_EVIDENCE/intent.$i.XXXXXXXX")" || return 1
    cp -p "$2" "$file" || return 1
    _RUNTIME_PUBLICATION_INTENTS[i]="$file"
    _RUNTIME_PUBLICATION_INTENT_STATES[i]="$(_runtime_publication_state "$file")" || return 1
    _RUNTIME_PUBLICATION_EVIDENCE_FILES+=("$file")
    _RUNTIME_PUBLICATION_EVIDENCE_STATES+=("${_RUNTIME_PUBLICATION_INTENT_STATES[i]}")
    _RUNTIME_PUBLICATION_NATIVE_HASHES[i]+=" ${_RUNTIME_PUBLICATION_INTENT_STATES[i]##*:}"
    _runtime_publication_journal
}

_runtime_publication_accept() {
    [[ -n "${_RUNTIME_PUBLICATION_EVIDENCE:-}" ]] || return 0
    _runtime_publication_native_check "$1"
}

_runtime_publication_native_cli() {
    local python
    python="$(_bootstrap_python)" || return 1
    "$python" -I -B "$SCRIPT_DIR/versioned_runtime.py" --root "$INSTALL_DIR" "$@"
}

_runtime_publication_native_begin() {
    local output="$1" receipt_path i backup
    printf -v "$output" '%s' ""
    [[ -n "${_RUNTIME_PUBLICATION_EVIDENCE:-}" ]] || return 0
    if [[ -z "$_RUNTIME_PUBLICATION_NATIVE_CONTEXT" ]]; then
        local context_path="$_RUNTIME_PUBLICATION_EVIDENCE/native-context.json"
        local arguments=(publication-context --output "$context_path")
        for ((i=0; i<${#_RUNTIME_PUBLICATION_PATHS[@]}; i++)); do
            backup="${_RUNTIME_PUBLICATION_BACKUPS[i]}"
            if [[ -n "$backup" ]]; then
                [[ "$(_runtime_publication_state "$backup")" == "${_RUNTIME_PUBLICATION_BACKUP_STATES[i]}" ]] || return 1
            fi
            arguments+=(--binding "${_RUNTIME_PUBLICATION_PATHS[i]}" "$backup" "${_RUNTIME_PUBLICATION_BASELINE[i]##*:}")
        done
        _runtime_publication_native_cli "${arguments[@]}" || return 1
        for ((i=0; i<${#_RUNTIME_PUBLICATION_BACKUPS[@]}; i++)); do
            backup="${_RUNTIME_PUBLICATION_BACKUPS[i]}"
            [[ -z "$backup" || "$(_runtime_publication_state "$backup")" == "${_RUNTIME_PUBLICATION_BACKUP_STATES[i]}" ]] || return 1
        done
        _RUNTIME_PUBLICATION_NATIVE_CONTEXT="$context_path"
    fi
    receipt_path="$_RUNTIME_PUBLICATION_EVIDENCE/producer.${#_RUNTIME_PUBLICATION_NATIVE_RECEIPTS[@]}.jsonl"
    _RUNTIME_PUBLICATION_NATIVE_RECEIPTS+=("$receipt_path")
    _runtime_publication_journal || return 1
    printf -v "$output" '%s' "$receipt_path"
}

_runtime_publication_native_arguments() {
    local receipt i digest
    _RUNTIME_PUBLICATION_NATIVE_ARGUMENTS=(--context "$_RUNTIME_PUBLICATION_NATIVE_CONTEXT")
    for receipt in "${_RUNTIME_PUBLICATION_NATIVE_RECEIPTS[@]}"; do
        _RUNTIME_PUBLICATION_NATIVE_ARGUMENTS+=(--receipt "$receipt")
    done
    for ((i=0; i<${#_RUNTIME_PUBLICATION_PATHS[@]}; i++)); do
        for digest in ${_RUNTIME_PUBLICATION_NATIVE_HASHES[i]}; do
            _RUNTIME_PUBLICATION_NATIVE_ARGUMENTS+=(--expected "${_RUNTIME_PUBLICATION_PATHS[i]}" "$digest")
        done
    done
}

_runtime_publication_native_check() {
    [[ ${#_RUNTIME_PUBLICATION_NATIVE_RECEIPTS[@]} != 0 ]] || return 1
    local target
    _runtime_publication_native_arguments
    for target in "$@"; do _RUNTIME_PUBLICATION_NATIVE_ARGUMENTS+=(--path "$target"); done
    _runtime_publication_native_cli publication-check "${_RUNTIME_PUBLICATION_NATIVE_ARGUMENTS[@]}"
}

_runtime_publication_prepare() {
    [[ -n "${_RUNTIME_PUBLICATION_EVIDENCE:-}" ]] || return 0
    local i state
    i="$(_runtime_publication_index "$1")" || return 0
    _runtime_publication_check "$1" || return 1
    state="$(_runtime_publication_state "$2")" || return 1
    [[ "$state" != absent ]] || return 1
    _RUNTIME_PUBLICATION_OWNED[i]+="|$state"
    _runtime_publication_journal
}

_runtime_publication_stage() {
    local path="$1" output="$2" created mask mode parent name directory
    _runtime_publication_check "$path" || return 1
    parent="${path%/*}"; name="${path##*/}"
    directory="$(mktemp -d "$parent/.${name}.publication-stage.XXXXXXXX")" || return 1
    _RUNTIME_PUBLICATION_STAGE_DIRS+=("$directory")
    _RUNTIME_PUBLICATION_STAGE_DIR_IDS+=("$(_runtime_publication_dir_id "$directory")")
    created="$directory/$name"
    _RUNTIME_PUBLICATION_STAGE_FILES+=("$created")
    (umask 077; : > "$created") || return 1
    _RUNTIME_PUBLICATION_TEMPS+=("$created")
    _RUNTIME_PUBLICATION_TEMP_IDS+=("$(_runtime_publication_file_id "$created")")
    if [[ -f "$path" ]]; then
        cp -p "$path" "$created" || return 1
    else
        mask="$(umask)" || return 1
        printf -v mode '%04o' "$((0666 & ~(8#$mask)))"
        chmod "$mode" "$created" || return 1
    fi
    printf -v "$output" '%s' "$created"
}

_runtime_publication_commit_file() {
    local planned result=0 parent="${1%/*}"
    [[ "${1##*/}" == "${2##*/}" ]] || {
        _fail "Publication stage basename does not bind its exact target: $1"; return 1;
    }
    planned="$(_runtime_publication_state "$2")" || return 1
    _runtime_publication_prepare "$1" "$2" || return 1
    [[ "$(_runtime_publication_state "$2")" == "$planned" ]] || return 1
    _runtime_publication_check "$1" || return 1
    mv -f "$2" "$parent/" || result=$?
    if [[ "$(_runtime_publication_state "$1")" != "$planned" || -e "$2" || -L "$2" ]]; then
        _fail "Publication did not replace its exact named target; staging and evidence retained: $1"
        return 1
    fi
    if [[ -z "${_RUNTIME_PUBLICATION_EVIDENCE:-}" ]]; then
        _runtime_publication_drop_directory "${2%/*}"
    fi
    return "$result"
}

_runtime_publication_drop_directory() {
    local i
    for ((i=0; i<${#_RUNTIME_PUBLICATION_STAGE_DIRS[@]}; i++)); do
        if [[ "${_RUNTIME_PUBLICATION_STAGE_DIRS[i]}" == "$1" ]]; then
            if [[ "$(_runtime_publication_dir_id "$1")" != "${_RUNTIME_PUBLICATION_STAGE_DIR_IDS[i]}" ]] ||
                ! rmdir "$1"; then
                _warn "Private publication staging directory retained: $1"
            fi
            return 0
        fi
    done
}

_runtime_publication_drop_stage() {
    [[ "$(_runtime_publication_state "$1")" == "$2" ]] || return 1
    rm -f "$1" || return 1
    if [[ -z "${_RUNTIME_PUBLICATION_EVIDENCE:-}" ]]; then
        _runtime_publication_drop_directory "${1%/*}"
    fi
}

_runtime_publication_copy() {
    local source="$1" destination="$2" temporary mode mask
    _runtime_publication_stage "$destination" temporary || return 1
    cp -f "$source" "$temporary" || return 1
    if [[ ! -e "$destination" ]]; then
        mode="$(stat -c '%a' "$source" 2>/dev/null)" ||
            mode="$(stat -f '%Lp' "$source" 2>/dev/null)" || return 1
        [[ "$mode" =~ ^[0-7]+$ ]] || return 1
        mask="$(umask)" || return 1
        printf -v mode '%04o' "$((8#$mode & ~(8#$mask)))"
        chmod "$mode" "$temporary" || return 1
    fi
    _runtime_publication_commit_file "$destination" "$temporary"
}

_runtime_publication_write_value() {
    local temporary
    _runtime_publication_stage "$1" temporary || return 1
    printf '%s' "$2" > "$temporary" || return 1
    _runtime_publication_commit_file "$1" "$temporary"
}

_runtime_publication_activate() {
    local current_index good_index
    _runtime_publication_expect_value "$INSTALL_DIR/current-version" "$SRC_VERSION"$'\n' || return 1
    _runtime_publication_expect_value "$INSTALL_DIR/last-known-good" "$SRC_VERSION"$'\n' || return 1
    "$@" || return $?
    [[ "${ACTIVATION_SUPERSEDED:-0}" != 1 ]] || return 0
    if [[ -n "${_RUNTIME_PUBLICATION_EVIDENCE:-}" ]]; then
        current_index="$(_runtime_publication_index "$INSTALL_DIR/current-version")" || return 1
        good_index="$(_runtime_publication_index "$INSTALL_DIR/last-known-good")" || return 1
        if [[ "$(_runtime_publication_state "$INSTALL_DIR/current-version")" == "${_RUNTIME_PUBLICATION_BASELINE[current_index]}" &&
              "$(_runtime_publication_state "$INSTALL_DIR/last-known-good")" == "${_RUNTIME_PUBLICATION_BASELINE[good_index]}" ]]; then
            return 0
        fi
    fi
    _runtime_publication_accept "$INSTALL_DIR/current-version" || return 1
    _runtime_publication_accept "$INSTALL_DIR/last-known-good"
}

_runtime_publication_reclaim() {
    local backup i entry
    if [[ -n "${_RUNTIME_PUBLICATION_EVIDENCE:-}" ]]; then
        for ((i=0; i<${#_RUNTIME_PUBLICATION_EVIDENCE_FILES[@]}; i++)); do
            backup="${_RUNTIME_PUBLICATION_EVIDENCE_FILES[i]}"
            [[ "$(_runtime_publication_state "$backup")" == "${_RUNTIME_PUBLICATION_EVIDENCE_STATES[i]}" ]] || {
                _warn "Recovery evidence changed; retained: $_RUNTIME_PUBLICATION_EVIDENCE"; return 0;
            }
        done
        if [[ -n "${_RUNTIME_PUBLICATION_JOURNAL_STATE:-}" ]]; then
            [[ "$(_runtime_publication_state "$_RUNTIME_PUBLICATION_EVIDENCE/journal")" == "$_RUNTIME_PUBLICATION_JOURNAL_STATE" ]] || {
                _warn "Publication journal changed; retained: $_RUNTIME_PUBLICATION_EVIDENCE"; return 0;
            }
        fi
        for ((i=0; i<${#_RUNTIME_PUBLICATION_TEMPS[@]}; i++)); do
            backup="${_RUNTIME_PUBLICATION_TEMPS[i]}"
            [[ -e "$backup" || -L "$backup" ]] || continue
            [[ "$(_runtime_publication_file_id "$backup")" == "${_RUNTIME_PUBLICATION_TEMP_IDS[i]}" ]] || {
                _warn "Unowned publication staging file; retained: $backup"; return 0;
            }
        done
        for ((i=0; i<${#_RUNTIME_PUBLICATION_STAGE_DIRS[@]}; i++)); do
            backup="${_RUNTIME_PUBLICATION_STAGE_DIRS[i]}"
            [[ -e "$backup" || -L "$backup" ]] || continue
            if [[ "$(_runtime_publication_dir_id "$backup")" != "${_RUNTIME_PUBLICATION_STAGE_DIR_IDS[i]}" ]]; then
                _warn "Private publication staging directory retained: $backup"; return 0;
            fi
            for entry in "$backup"/* "$backup"/.[!.]* "$backup"/..?*; do
                [[ -e "$entry" || -L "$entry" ]] || continue
                if [[ "$entry" != "${_RUNTIME_PUBLICATION_STAGE_FILES[i]}" ]]; then
                    _warn "Unknown publication staging artifact retained: $entry"; return 0;
                fi
            done
        done
        for ((i=0; i<${#_RUNTIME_PUBLICATION_EVIDENCE_FILES[@]}; i++)); do
            backup="${_RUNTIME_PUBLICATION_EVIDENCE_FILES[i]}"
            if [[ "$(_runtime_publication_state "$backup")" != "${_RUNTIME_PUBLICATION_EVIDENCE_STATES[i]}" ]] ||
                ! rm -f "$backup"; then
                _warn "Could not reclaim owned recovery evidence; retained: $_RUNTIME_PUBLICATION_EVIDENCE"
                return 0
            fi
        done
        for ((i=0; i<${#_RUNTIME_PUBLICATION_TEMPS[@]}; i++)); do
            backup="${_RUNTIME_PUBLICATION_TEMPS[i]}"
            [[ -e "$backup" || -L "$backup" ]] || continue
            if [[ "$(_runtime_publication_file_id "$backup")" != "${_RUNTIME_PUBLICATION_TEMP_IDS[i]}" ]] ||
                ! rm -f "$backup"; then
                _warn "Could not reclaim owned staging file; retained: $backup"; return 0;
            fi
        done
        for ((i=0; i<${#_RUNTIME_PUBLICATION_STAGE_DIRS[@]}; i++)); do
            backup="${_RUNTIME_PUBLICATION_STAGE_DIRS[i]}"
            [[ -e "$backup" || -L "$backup" ]] || continue
            if [[ "$(_runtime_publication_dir_id "$backup")" != "${_RUNTIME_PUBLICATION_STAGE_DIR_IDS[i]}" ]] ||
                ! rmdir "$backup"; then
                _warn "Private publication staging directory retained: $backup"; return 0;
            fi
        done
        if [[ -n "${_RUNTIME_PUBLICATION_JOURNAL_STATE:-}" ]]; then
            if [[ "$(_runtime_publication_state "$_RUNTIME_PUBLICATION_EVIDENCE/journal")" != "$_RUNTIME_PUBLICATION_JOURNAL_STATE" ]] ||
                ! rm -f "$_RUNTIME_PUBLICATION_EVIDENCE/journal"; then
                _warn "Could not reclaim owned journal; retained: $_RUNTIME_PUBLICATION_EVIDENCE"; return 0;
            fi
        fi
        if ! rmdir "$_RUNTIME_PUBLICATION_EVIDENCE"; then
            _warn "Publication evidence directory retained: $_RUNTIME_PUBLICATION_EVIDENCE"; return 0;
        fi
        _RUNTIME_PUBLICATION_EVIDENCE=""
    fi
}

_runtime_publication_discard() {
    if [[ ${#_RUNTIME_PUBLICATION_NATIVE_RECEIPTS[@]} != 0 ]]; then
        if ! _runtime_publication_native_check; then
            _fail "Required publication proof is invalid; whole set and evidence retained: $_RUNTIME_PUBLICATION_EVIDENCE"
            return 1
        fi
        _runtime_publication_native_arguments
        _runtime_publication_native_cli publication-cleanup "${_RUNTIME_PUBLICATION_NATIVE_ARGUMENTS[@]}" || return 1
    fi
    _runtime_publication_reclaim || _warn "Publication cleanup incomplete; evidence retained: ${_RUNTIME_PUBLICATION_EVIDENCE:-}"
    _RUNTIME_PUBLICATION_EVIDENCE=""
    _RUNTIME_PUBLICATION_PATHS=()
    _RUNTIME_PUBLICATION_BACKUPS=()
    _RUNTIME_PUBLICATION_BASELINE=()
    _RUNTIME_PUBLICATION_BACKUP_STATES=()
    _RUNTIME_PUBLICATION_OWNED=()
    _RUNTIME_PUBLICATION_INTENTS=()
    _RUNTIME_PUBLICATION_INTENT_STATES=()
    _RUNTIME_PUBLICATION_TEMPS=()
    _RUNTIME_PUBLICATION_TEMP_IDS=()
    _RUNTIME_PUBLICATION_STAGE_DIRS=()
    _RUNTIME_PUBLICATION_STAGE_DIR_IDS=()
    _RUNTIME_PUBLICATION_STAGE_FILES=()
    _RUNTIME_PUBLICATION_EVIDENCE_FILES=()
    _RUNTIME_PUBLICATION_EVIDENCE_STATES=()
    _RUNTIME_PUBLICATION_JOURNAL_STATE=""
    _RUNTIME_PUBLICATION_NATIVE_CONTEXT=""
    _RUNTIME_PUBLICATION_NATIVE_RECEIPTS=()
    _RUNTIME_PUBLICATION_NATIVE_HASHES=()
}

_runtime_publication_restore() {
    local path backup i state stage staged_state backup_state stage_data backup_data
    local native_paths=() native_indexes=() output_receipt index
    if [[ ${#_RUNTIME_PUBLICATION_NATIVE_RECEIPTS[@]} != 0 ]] && ! _runtime_publication_native_check; then
        _fail "Required publication proof is invalid; whole set and evidence retained: $_RUNTIME_PUBLICATION_EVIDENCE"
        return 1
    fi
    # Check the whole set before mutating any destination, not just the selector.
    for ((i=0; i<${#_RUNTIME_PUBLICATION_PATHS[@]}; i++)); do
        path="${_RUNTIME_PUBLICATION_PATHS[i]}"
        state="$(_runtime_publication_state "$path")" || state=unsafe
        if [[ "$state" != "${_RUNTIME_PUBLICATION_BASELINE[i]}" &&
              "|${_RUNTIME_PUBLICATION_OWNED[i]}|" != *"|$state|"* ]]; then
            if ! _runtime_publication_native_check "$path"; then
                _fail "Unowned publication state: $path; recovery evidence retained: $_RUNTIME_PUBLICATION_EVIDENCE"
                return 1
            fi
            native_paths+=("$path"); native_indexes+=("$i")
        fi
        backup="${_RUNTIME_PUBLICATION_BACKUPS[i]}"
        if [[ -n "$backup" && "$(_runtime_publication_state "$backup")" != "${_RUNTIME_PUBLICATION_BACKUP_STATES[i]}" ]]; then
            _fail "Publication backup changed: $backup; recovery evidence retained: $_RUNTIME_PUBLICATION_EVIDENCE"
            return 1
        fi
    done
    if [[ ${#native_paths[@]} != 0 ]]; then
        _runtime_publication_native_arguments
        for path in "${native_paths[@]}"; do _RUNTIME_PUBLICATION_NATIVE_ARGUMENTS+=(--path "$path"); done
        _runtime_publication_native_begin output_receipt || return 1
        if ! _runtime_publication_native_cli publication-restore "${_RUNTIME_PUBLICATION_NATIVE_ARGUMENTS[@]}" --output-receipt "$output_receipt"; then
            _fail "Native recovery incomplete; whole set and evidence retained: $_RUNTIME_PUBLICATION_EVIDENCE"
            return 1
        fi
    fi
    for ((i=0; i<${#_RUNTIME_PUBLICATION_PATHS[@]}; i++)); do
        index=0
        for native_index in "${native_indexes[@]}"; do [[ "$native_index" != "$i" ]] || index=1; done
        [[ "$index" == 0 ]] || continue
        path="${_RUNTIME_PUBLICATION_PATHS[i]}"
        backup="${_RUNTIME_PUBLICATION_BACKUPS[i]}"
        state="$(_runtime_publication_state "$path")" || state=unsafe
        [[ "$state" != "${_RUNTIME_PUBLICATION_BASELINE[i]}" ]] || continue
        if [[ "|${_RUNTIME_PUBLICATION_OWNED[i]}|" != *"|$state|"* ]]; then
            _fail "Publication changed during recovery: $path; recovery evidence retained: $_RUNTIME_PUBLICATION_EVIDENCE"
            return 1
        fi
        if [[ -n "$backup" ]]; then
            backup_state="$(_runtime_publication_state "$backup")" || return 1
            [[ "$backup_state" == "${_RUNTIME_PUBLICATION_BACKUP_STATES[i]}" ]] || return 1
            _runtime_publication_stage "$path" stage || return 1
            cp -p "$backup" "$stage" || return 1
            staged_state="$(_runtime_publication_state "$stage")" || return 1
            stage_data="${staged_state#*:}"; stage_data="${stage_data#*:}"
            backup_data="${backup_state#*:}"; backup_data="${backup_data#*:}"
            if [[ "$stage_data" != "$backup_data" ||
                "$(_runtime_publication_state "$backup")" != "$backup_state" ]]; then
                _fail "Publication backup changed during staging; evidence retained: $_RUNTIME_PUBLICATION_EVIDENCE"
                return 1
            fi
            [[ "$(_runtime_publication_state "$path")" == "$state" ]] || {
                _fail "Publication changed before recovery replacement: $path; evidence retained: $_RUNTIME_PUBLICATION_EVIDENCE"
                return 1
            }
            if ! _runtime_publication_commit_file "$path" "$stage" &&
                [[ "$(_runtime_publication_state "$path")" != "$staged_state" ]]; then
                _fail "Publication restore failed: $path; recovery evidence retained: $_RUNTIME_PUBLICATION_EVIDENCE"
                return 1
            fi
        elif [[ -e "$path" ]] && ! rm -f "$path"; then
            _fail "Could not remove owned publication: $path; recovery evidence retained: $_RUNTIME_PUBLICATION_EVIDENCE"
            return 1
        fi
    done
    _runtime_publication_discard
}
