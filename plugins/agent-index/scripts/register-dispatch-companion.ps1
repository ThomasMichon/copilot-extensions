$ErrorActionPreference = 'SilentlyContinue'
try {
    $pluginRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    $registrar = Join-Path $pluginRoot 'references\agent-dispatch\registrar'
    if (-not (Test-Path -LiteralPath $registrar -PathType Container)) { exit 0 }
    $directory = if ($env:AGENT_DISPATCH_REGISTRAR_DROPINS_DIR) {
        $env:AGENT_DISPATCH_REGISTRAR_DROPINS_DIR
    } else {
        Join-Path $env:USERPROFILE '.agent-dispatch\registrar.d'
    }
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $payload = [ordered]@{
        schema_version = 1
        plugin = 'agent-index@copilot-extensions'
        plugin_root = $pluginRoot
        registrar = 'references/agent-dispatch/registrar'
    } | ConvertTo-Json
    $target = Join-Path $directory 'agent-index-copilot-extensions.json'
    $existing = if (Test-Path -LiteralPath $target -PathType Leaf) {
        [IO.File]::ReadAllText($target)
    } else {
        $null
    }
    if ($existing -ne $payload) {
        $temporary = "$target.$PID.tmp"
        $backup = "$target.$PID.bak"
        [IO.File]::WriteAllText(
            $temporary,
            $payload,
            [Text.UTF8Encoding]::new($false)
        )
        try {
            if (Test-Path -LiteralPath $target) {
                [IO.File]::Replace($temporary, $target, $backup, $true)
            } else {
                try {
                    [IO.File]::Move($temporary, $target)
                } catch [IO.IOException] {
                    if (-not (Test-Path -LiteralPath $target)) { throw }
                    [IO.File]::Replace($temporary, $target, $backup, $true)
                }
            }
        } finally {
            Remove-Item -LiteralPath $temporary, $backup -Force `
                -ErrorAction SilentlyContinue
        }
    }
} catch {}
exit 0
