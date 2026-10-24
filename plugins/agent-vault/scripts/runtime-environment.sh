apply_runtime_env() {
    local runtime_root="${1:?runtime root required}" context="${2:-}"
    local installation_id="${3:-}" service_suffix="${4:-}"
    export AGENT_VAULT_HOME="$runtime_root"
    export AGENT_VAULT_VALIDATED_CONTEXT="$context"
    if [[ -n "$context" ]]; then
        export COPILOT_EXTENSIONS_CONTEXT="$context"
    else
        unset COPILOT_EXTENSIONS_CONTEXT
    fi
    if [[ -n "$installation_id" ]]; then
        export AGENT_VAULT_INSTALLATION_ID="$installation_id"
        export AGENT_VAULT_RUN_DIR="$runtime_root/run"
        export AGENT_VAULT_CORE_RUN_DIR="$runtime_root/core"
        export AGENT_VAULT_CACHE_DIR="$runtime_root/cache"
        export AGENT_VAULT_SOCKET="$runtime_root/run/agent-vault.sock"
        export AGENT_VAULT_PIPE="\\\\.\\pipe\\agent-vault-$service_suffix"
        export AGENT_VAULT_PID="$runtime_root/run/agent-vault-service.pid"
        export AGENT_VAULT_LOG="$runtime_root/logs/agent-vault-service.log"
        export AGENT_VAULT_PORT=0
        export AGENT_VAULT_SYSTEMD_UNIT="agent-vault-$service_suffix.service"
        export AGENT_VAULT_TASK_NAME="AgentVault-$service_suffix"
    else
        unset AGENT_VAULT_INSTALLATION_ID
        unset AGENT_VAULT_RUN_DIR AGENT_VAULT_CORE_RUN_DIR AGENT_VAULT_CACHE_DIR
        unset AGENT_VAULT_SOCKET AGENT_VAULT_PIPE AGENT_VAULT_PID AGENT_VAULT_LOG
        unset AGENT_VAULT_PORT AGENT_VAULT_SYSTEMD_UNIT AGENT_VAULT_TASK_NAME
    fi
}
