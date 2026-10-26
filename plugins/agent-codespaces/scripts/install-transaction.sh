#!/usr/bin/env bash
# shellcheck shell=bash
codespaces_requires_install_transaction() {
    [[ "${1:-}" != status ]] && { [[ "${1:-}" != uninstall ]] || [[ "${2:-false}" != true ]]; }
}

enter_codespaces_install_transaction() {
    local timeout="${COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC:-300}" py reply
    [[ "$timeout" =~ ^[1-9][0-9]*$ ]] || timeout=300
    py="$(_bootstrap_python)" || return 1
    coproc CODESPACES_LOCK_OWNER { "$py" -I -B "$SCRIPT_DIR/install-transaction.py" "$INSTALL_DIR" "$timeout"; }
    CODESPACES_INSTALL_LOCK_PID="$CODESPACES_LOCK_OWNER_PID"
    CODESPACES_INSTALL_LOCK_READ="${CODESPACES_LOCK_OWNER[0]}"
    CODESPACES_INSTALL_LOCK_WRITE="${CODESPACES_LOCK_OWNER[1]}"
    if ! IFS= read -r -t "$((timeout + 5))" reply <&"$CODESPACES_INSTALL_LOCK_READ" ||
       [[ "$reply" != locked ]]; then
        exit_codespaces_install_transaction
        return 1
    fi
}

exit_codespaces_install_transaction() {
    if [[ -n "${CODESPACES_INSTALL_LOCK_PID:-}" ]]; then
        exec {CODESPACES_INSTALL_LOCK_WRITE}>&-
        exec {CODESPACES_INSTALL_LOCK_READ}<&-
        wait "$CODESPACES_INSTALL_LOCK_PID"
        CODESPACES_INSTALL_LOCK_PID=""
    fi
}
