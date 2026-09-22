#!/usr/bin/env bash
# shellcheck shell=bash
# Canonical vendored installer-engine helpers for runtime plugins.
#
# This file is the canonical source of truth for the shared installer-engine
# helpers that runtime plugins vendor into their own `scripts/` directories.
# Plugins do NOT source it across plugin boundaries at install or runtime;
# `tools/sync-installer-engine.py` copies it byte-for-byte into each adopter.

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
    local out rc delay
    out="$("$uv_cmd" pip install "$@" 2>&1)"; rc=$?
    for delay in 3 6 10; do
        if [[ $rc -eq 0 ]] || ! test_is_sre_module_mismatch "$out"; then
            break
        fi
        _warn "uv build hit a transient SRE module mismatch (shared Python cache race, #6785) -- retrying in ${delay}s"
        sleep "$delay"
        out="$("$uv_cmd" pip install "$@" 2>&1)"; rc=$?
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
    return "${rc:-1}"
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
    invoke_uv_venv_resilient "$uv_cmd" "$venv_dir" --python "$python_version" --allow-existing >/dev/null || \
        invoke_uv_venv_resilient "$uv_cmd" "$venv_dir" --allow-existing >/dev/null
}

write_deploy_manifest() {
    local service="$1" plugin="$2" install_path="$3" plugin_path="$4" venv_path="$5"
    local additional_json="${6:-}"
    local manifest="$install_path/deploy-manifest.json"
    local kind ver commit branch dirty content_hash tmp
    kind="$(_source_kind "$plugin_path")"
    ver="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$plugin_path/pyproject.toml" 2>/dev/null | head -n1)"
    [[ -n "$ver" ]] || ver="0.0.0"
    commit="null"; branch="null"; dirty="false"
    if [[ "$kind" == "local" ]]; then
        local repo_root _c _b _d
        repo_root="$(cd "$plugin_path/.." && pwd)"
        read -r _c _b _d <<< "$(_git_info "$repo_root")"
        commit="\"$_c\""; branch="\"$_b\""; dirty="$_d"
    fi
    content_hash=""
    if declare -F _payload_hash >/dev/null 2>&1; then
        content_hash="$(_payload_hash)"
    fi
    tmp="$manifest.tmp"
    cat > "$tmp" <<EOF
{
  "schema_version": 3,
  "service": "$service",
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$kind",
    "path": "$plugin_path",
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
    mv -f "$tmp" "$manifest"
    _ok "Deploy manifest written (source: $kind)"
}

write_simple_binstub() {
    local command_name="$1" module_name="$2" runtime_root="$3" local_bin="$4" install_bin_dir="$5" snapshot_installer_rel="$6" no_self_provision_env="$7"
    local resolver_ps1_source="${8:-}" resolver_sh_source="${9:-}"
    mkdir -p "$local_bin" "$install_bin_dir"
    [[ -n "$resolver_ps1_source" && -f "$resolver_ps1_source" ]] && cp -f "$resolver_ps1_source" "$install_bin_dir/resolve-runtime.ps1"
    [[ -n "$resolver_sh_source" && -f "$resolver_sh_source" ]] && cp -f "$resolver_sh_source" "$install_bin_dir/resolve-runtime.sh"
    local stub="$local_bin/$command_name"
    cat > "$stub" <<EOF
#!/usr/bin/env bash
export PYTHONUTF8=1
_name="$command_name"
_root="$runtime_root"
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
        if [[ "\$_live" == 0 && "\$(readlink "\$_lock_link" 2>/dev/null || true)" == "\$_owner" ]]; then
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
_install="\$_snapshot/$snapshot_installer_rel"
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
exit "\${_rc:-1}"
EOF
    chmod +x "$stub"
    _ok "Binstub: $stub (self-provisioning)"
}
