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
        'This session has context-handoff enabled whether or not this session began from a handoff; it is available from turn one. When you own the active objective, it can span multiple agent sessions: do not narrow investigation, planning, implementation, validation, or landing to fit one window. First select continuity: contextManagementTools enabled or all four native context tools offered without explicit false selects native-first. Confirmed unavailable tools permit custom recovery; unknown metadata requires diagnostics, not invented tools. For native context-only rollover, use get_context_remaining, the current owner''s revision-bound session_artifacts checkpoint, bounded session_history, and terminal new_context. Preserve the parent gate, current slice, unresolved requests, decisions, live obligations, and exactly one next action. Verify persistence/snapshot binding, then rollover; read the bound checkpoint and refresh durable guidance. No custom pickup, raw clear, guessed artifact files, or policy bypass; inspect partial-clear errors before retrying. For custom new-owner/process transfer only: quiesce+sync first if pressure-driven, then compose and store the baton safely; call trigger_handoff if work remains, do not ask first. If ending the turn with proposed follow-ups, ask before trigger unless autopilot/pre-authorized. trigger_handoff always stores/seeds; never performs process management. Live signaling needs mode: auto (default: manual-only). Let one session own one slice of the larger effort. Consuming or producing a handoff is setup or progress, never completion. The session owning the objective stops only at its completion gate, explicit scope/confirmation gate, or real blocker; never for lateness or a stopping point alone. Use the `context-handoff` skill for mechanics.'
    ) -join "`n"
    $AggregateContext = @(
        "[owner: context-handoff@$Version]"
        'An objective may span sessions; a handoff is progress, never completion. Native flag/tool selection uses a verified current-owner checkpoint and terminal new_context, not custom pickup; preserve the parent gate, obligations, and one next action, then refresh guidance. Near token pressure use the `context-handoff` skill. For custom transfer only: quiesce/sync/store, then trigger_handoff if work remains. Turn-end follow-ups ask before trigger unless pre-authorized. Live signaling needs mode: auto (default: manual-only). One session owns one slice; never stop for lateness alone.'
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
