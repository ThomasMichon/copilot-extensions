function Set-RuntimeEnvironment {
    param(
        [Parameter(Mandatory)][string]$RuntimeRoot,
        [string]$Context = '',
        [string]$InstallationId = '',
        [string]$ServiceSuffix = ''
    )
    $env:AGENT_VAULT_HOME = $RuntimeRoot
    $env:AGENT_VAULT_VALIDATED_CONTEXT = $Context
    if ($InstallationId) {
        $env:AGENT_VAULT_INSTALLATION_ID = $InstallationId
        $env:AGENT_VAULT_RUN_DIR = Join-Path $RuntimeRoot 'run'
        $env:AGENT_VAULT_CORE_RUN_DIR = Join-Path $RuntimeRoot 'core'
        $env:AGENT_VAULT_CACHE_DIR = Join-Path $RuntimeRoot 'cache'
        $env:AGENT_VAULT_SOCKET = Join-Path (Join-Path $RuntimeRoot 'run') 'agent-vault.sock'
        $env:AGENT_VAULT_PIPE = "\\.\pipe\agent-vault-$ServiceSuffix"
        $env:AGENT_VAULT_PID = Join-Path (Join-Path $RuntimeRoot 'run') 'agent-vault-service.pid'
        $env:AGENT_VAULT_LOG = Join-Path (Join-Path $RuntimeRoot 'logs') 'agent-vault-service.log'
        $env:AGENT_VAULT_PORT = '0'
        $env:AGENT_VAULT_SYSTEMD_UNIT = "agent-vault-$ServiceSuffix.service"
        $env:AGENT_VAULT_TASK_NAME = "AgentVault-$ServiceSuffix"
    } else {
        Remove-Item Env:AGENT_VAULT_INSTALLATION_ID -ErrorAction SilentlyContinue
        Remove-Item Env:AGENT_VAULT_RUN_DIR -ErrorAction SilentlyContinue
        Remove-Item Env:AGENT_VAULT_CORE_RUN_DIR -ErrorAction SilentlyContinue
        Remove-Item Env:AGENT_VAULT_CACHE_DIR -ErrorAction SilentlyContinue
        Remove-Item Env:AGENT_VAULT_SOCKET -ErrorAction SilentlyContinue
        Remove-Item Env:AGENT_VAULT_PIPE -ErrorAction SilentlyContinue
        Remove-Item Env:AGENT_VAULT_PID -ErrorAction SilentlyContinue
        Remove-Item Env:AGENT_VAULT_LOG -ErrorAction SilentlyContinue
        Remove-Item Env:AGENT_VAULT_PORT -ErrorAction SilentlyContinue
        Remove-Item Env:AGENT_VAULT_SYSTEMD_UNIT -ErrorAction SilentlyContinue
        Remove-Item Env:AGENT_VAULT_TASK_NAME -ErrorAction SilentlyContinue
    }
    if ($Context) {
        $env:COPILOT_EXTENSIONS_CONTEXT = $Context
    } else {
        Remove-Item Env:COPILOT_EXTENSIONS_CONTEXT -ErrorAction SilentlyContinue
    }
}
