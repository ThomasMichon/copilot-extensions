$ErrorActionPreference = 'Stop'
$MaxContextBytes = 2048
$MaxCombinedContextBytes = 3072

try {
    $PluginRoot = if ($env:COPILOT_PLUGIN_ROOT) {
        $env:COPILOT_PLUGIN_ROOT
    } else {
        Split-Path -Parent $PSScriptRoot
    }
    $Manifest = Join-Path $PluginRoot 'plugin.json'
    $Skill = Join-Path (Join-Path (Join-Path $PluginRoot 'skills') 'context-handoff') 'SKILL.md'
    if (-not (Test-Path -LiteralPath $Manifest -PathType Leaf) -or
        -not (Test-Path -LiteralPath $Skill -PathType Leaf)) {
        throw 'plugin payload incomplete'
    }
    $Version = (Get-Content -Raw -LiteralPath $Manifest | ConvertFrom-Json).version
    if ($Version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:-dev[0-9]+)?$') {
        throw 'invalid plugin version'
    }
    $Context = @(
        "[owner: context-handoff@$Version]"
        'This session has context-handoff enabled. When you own the active objective, it can span multiple agent sessions. Work thoroughly across context windows: do not narrow investigation, planning, implementation, validation, or landing merely to fit one session. A context boundary is a relay point, not a stopping condition. If the active plan is unfinished, finish the planning needed to act, then begin execution immediately, subject to any required safety, review, approval, or confirmation gate; do not stop at a plan unless the user requested planning only. Consuming or producing a handoff is setup or progress, never completion. Near token pressure, preserve the objective, remaining work, decisions, and in-flight state in a precise baton, transfer it through the available handoff path, and keep going in the successor. Bounded delegates remain within their assigned scope, and a session superseded by cutover stops work and assists the successor rather than competing. The session owning the objective stops only when its completion gate is met, an explicit scope or required confirmation stops progress, or a real blocker needs input. Use the `context-handoff` skill for handoff mechanics.'
    ) -join "`n"
    if ([Text.Encoding]::UTF8.GetByteCount($Context) -ge $MaxContextBytes) {
        throw 'guidance exceeds context budget'
    }

    $Contexts = [System.Collections.Generic.List[string]]::new()
    $Contexts.Add($Context)
    $AgentWorktreesRoot = Join-Path (Split-Path -Parent $PluginRoot) 'agent-worktrees'
    $AgentWorktreesManifest = Join-Path $AgentWorktreesRoot 'plugin.json'
    $AgentWorktreesCommand = Join-Path (Join-Path (Join-Path $AgentWorktreesRoot 'bin') 'payload') 'agent-worktrees.ps1'
    $AgentWorktreesInstaller = Join-Path (Join-Path $AgentWorktreesRoot 'scripts') 'install.ps1'
    if (Test-Path -LiteralPath $AgentWorktreesManifest -PathType Leaf) {
        try {
            $ResolvedRoot = (Resolve-Path -LiteralPath $AgentWorktreesRoot).Path
            $ResolvedCommand = [IO.Path]::GetFullPath($AgentWorktreesCommand)
            $Availability = if (
                (Test-Path -LiteralPath $AgentWorktreesCommand -PathType Leaf) -and
                (Test-Path -LiteralPath $AgentWorktreesInstaller -PathType Leaf)
            ) { 'ready' } else { 'unavailable' }
            $AgentWorktreesPlugin = Get-Content -Raw -LiteralPath $AgentWorktreesManifest | ConvertFrom-Json
            if ($AgentWorktreesPlugin.name -ceq 'agent-worktrees' -and
                $ResolvedCommand.StartsWith(
                    $ResolvedRoot + [IO.Path]::DirectorySeparatorChar,
                    [StringComparison]::OrdinalIgnoreCase
                )) {
                $Catalog = [ordered]@{
                    schema = 'copilot-extensions.session-command-catalog'
                    version = 1
                    plugin = 'agent-worktrees'
                    payload = [ordered]@{ provenance = 'adjacent-compatibility' }
                    commands = @(
                        [ordered]@{
                            id = 'agent-worktrees'
                            argv = @($ResolvedCommand)
                            shell = 'direct'
                            purpose = 'Manage worktrees and project lifecycle'
                            availability = $Availability
                        }
                    )
                }
                $Fence = '```'
                $CatalogContext = @(
                    '## agent-worktrees session command catalog'
                    ''
                    'Invoke the exact `argv` below. Do not search `PATH` or substitute a same-named command from another payload.'
                    ''
                    "${Fence}json"
                    ($Catalog | ConvertTo-Json -Compress -Depth 6)
                    $Fence
                ) -join "`n"
                $Contexts.Add($CatalogContext)
            }
        } catch {
        }
    }
    $CombinedContext = $Contexts -join "`n`n"
    if ([Text.Encoding]::UTF8.GetByteCount($CombinedContext) -ge $MaxCombinedContextBytes) {
        $CombinedContext = $Context
    }
    [Console]::Out.Write(
        (@{ additionalContext = $CombinedContext } | ConvertTo-Json -Compress)
    )
} catch {
    [Console]::Error.WriteLine('[context-handoff] no guidance context emitted')
    [Console]::Out.Write('{}')
}
exit 0
