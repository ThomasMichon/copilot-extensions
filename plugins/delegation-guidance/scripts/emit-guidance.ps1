$ErrorActionPreference = 'Stop'
$MaxContextBytes = 2048

try {
    $PluginRoot = if ($env:COPILOT_PLUGIN_ROOT) {
        $env:COPILOT_PLUGIN_ROOT
    } else {
        Split-Path -Parent $PSScriptRoot
    }
    $Manifest = Join-Path $PluginRoot 'plugin.json'
    $Skill = Join-Path (Join-Path (Join-Path $PluginRoot 'skills') 'delegating-work') 'SKILL.md'
    if (-not (Test-Path -LiteralPath $Manifest -PathType Leaf) -or
        -not (Test-Path -LiteralPath $Skill -PathType Leaf)) {
        throw 'plugin payload incomplete'
    }
    $Version = (Get-Content -Raw -LiteralPath $Manifest | ConvertFrom-Json).version
    if ($Version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:-dev[0-9]+)?$') {
        throw 'invalid plugin version'
    }
    $Context = @(
        "[owner: delegation-guidance@$Version]"
        'Treat the main agent as coordinator and task-master. Before broad code/file research, comparisons, evaluations, or separable bulk edits, estimate context cost and separability. Hard rule: when a comparison or evaluation has three or more independent implementation/subsystem tracks and each needs multiple source bodies, launch one bounded evidence agent per track before reading those bodies; a reviewer is not an evidence-track substitute. Delegate other broad work early when independent source bodies or tool payloads would materially consume coordinator context; keep small bounded lookups, small comparisons, and one genuinely continuous trace direct. Give each delegate one bounded, non-overlapping scope, required evidence, compact cited output, and exclusive edit ownership. Do not duplicate delegated investigation or ingest its full sources without a concrete verification reason. Keep decomposition, synthesis, integration, final decisions, cohesive implementation, and completion judgment with the coordinator. Prefer domain sub-agents for domain MCP/service calls so verbose catalogs and payloads remain in their context; compact shared research and orchestration tools may stay with the coordinator. If you were invoked as a sub-agent, execute only your assigned scope directly and do not create child agents unless your prompt explicitly authorizes it. Run each required independent review role once per unchanged artifact; repeat only after a defect or material change. Before delegating, use the `delegating-work` skill to load model routing: select a demonstrated model for the purpose and execution surface; candidates require explicit trials.'
    ) -join "`n"
    $AggregateContext = @(
        "[owner: delegation-guidance@$Version]"
        'Hard rule: if a comparison/evaluation has 3+ independent tracks and each needs multiple source bodies, launch one bounded evidence agent per track before reading them; a reviewer is not a track substitute. Keep small lookups and comparisons direct. Delegate other broad separable research, domain-tool work, and disjoint edits early. Give delegates bounded non-overlapping scopes and compact evidence; the coordinator retains synthesis, integration, decisions, cohesive implementation, and completion. Sub-agents do not spawn children unless explicitly authorized. Before delegating, use the `delegating-work` skill to load model routing: select a demonstrated model for the purpose/surface; candidates require explicit trials.'
    ) -join "`n"
    $OutputContext = if ($args -contains '--aggregate') {
        $AggregateContext
    } else {
        $Context
    }
    $OutputBudget = if ($args -contains '--aggregate') {
        1024
    } else {
        $MaxContextBytes
    }
    if ([Text.Encoding]::UTF8.GetByteCount($OutputContext) -ge $OutputBudget) {
        throw 'guidance exceeds context budget'
    }
    [Console]::Out.Write(
        (@{ additionalContext = $OutputContext } | ConvertTo-Json -Compress)
    )
} catch {
    [Console]::Error.WriteLine('[delegation-guidance] no guidance context emitted')
    [Console]::Out.Write('{}')
}
exit 0
