# Shared with the installer: preserve its existing POSIX completion fingerprint.
bridge_payload_hash() (
    cd "$1" || return 1
    {
        [[ -f pyproject.toml ]] && sha256sum pyproject.toml
        for sub in src libs; do
            [[ -d "$sub" ]] || continue
            find "$sub" -type f \
                ! -path '*/__pycache__/*' ! -path '*/.venv/*' ! -path '*/venv/*' \
                ! -path '*/.pytest_cache/*' ! -path '*/.mypy_cache/*' \
                ! -path '*/build/*' ! -path '*/dist/*' ! -path '*.egg-info/*' \
                ! -name '*.pyc' -print0 |
                sort -z | xargs -0 -r sha256sum
        done
    } | sort | sha256sum | awk '{print $1}'
)
