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
        'This session has context-handoff enabled. When you own the active objective, it can span multiple agent sessions. Work thoroughly across context windows: do not narrow investigation, planning, implementation, validation, or landing merely to fit one session. A context boundary is a relay point, not a stopping condition. At a natural stopping point, compose and store the baton safely before context gets tight. Distinguish the trigger: if context pressure is the reason and work remains, call `trigger_handoff` directly after saving; do not ask first. If you are instead ending the turn with proposed follow-ups, replace that list with one short offer to continue via handoff, and ask before calling `trigger_handoff` unless autopilot or prior explicit authorization applies. `trigger_handoff` only signals pickup and never performs process management. When efforts and handoffs coexist, let one session own one slice of the larger effort and hand the next slice forward. Consuming or producing a handoff is setup or progress, never completion. Near token pressure, preserve the objective, remaining work, decisions, and in-flight state in a precise baton, then keep going in the successor when pickup occurs. Bounded delegates remain within their assigned scope. The session owning the objective stops only when its completion gate is met, an explicit scope or required confirmation stops progress, or a real blocker needs input. Use the `context-handoff` skill for handoff mechanics.'
    ) -join "`n"
    $AggregateContext = @(
        "[owner: context-handoff@$Version]"
        'An owned objective may span sessions: a context boundary or handoff is progress, never completion. Near token pressure, compose/store the baton and call `trigger_handoff` directly if work remains. Only the turn-end follow-up path asks before `trigger_handoff`, unless autopilot or prior authorization applies. `trigger_handoff` only signals pickup. When efforts and handoffs coexist, let one session own one slice and hand the next slice forward. Preserve the objective, remaining work, decisions, and in-flight state in the handoff. Use the `context-handoff` skill for mechanics.'
    ) -join "`n"
    if ([Text.Encoding]::UTF8.GetByteCount($Context) -ge $MaxContextBytes) {
        throw 'guidance exceeds context budget'
    }
    if ($args -contains '--aggregate') {
        [Console]::Out.Write(
            (@{ additionalContext = $AggregateContext } | ConvertTo-Json -Compress)
        )
        exit 0
    }
    if ($args -contains '--own-only') {
        [Console]::Out.Write(
            (@{ additionalContext = $Context } | ConvertTo-Json -Compress)
        )
        exit 0
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
