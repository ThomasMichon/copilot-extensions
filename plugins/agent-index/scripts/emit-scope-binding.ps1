# emit-scope-binding -- agent-index sessionStart hook.
#
# Emits a succinct "what agent-index covers + prefer @agent-index for retrieval
# within these scopes" guidance fragment as {"additionalContext": "..."}, so
# every agent learns the configured index scopes at session start without the MCP
# tool schemas entering the main context (harness MCP policy: the tools live in
# the @agent-index sub-agent). Generic emitter (ships in the plugin); it reads the
# LOCAL scope config -- `<repo>/.agent-index/config.yaml` `corpus.sources` -- so
# the operator's scope values stay in the repo, not baked into the plugin.
#
# cwd-gated: fires only when the current git repo has an .agent-index/config.yaml
# with corpus.sources; otherwise emits {} so nothing leaks into unrelated repos.
# Dependency-light (no YAML module): a small line scanner, PowerShell 5.1+ / 7+.

$ErrorActionPreference = 'SilentlyContinue'

function Emit-Empty { Write-Output '{}'; exit 0 }

# Resolve the current repo root (cwd-gated). No git repo -> nothing to say.
$root = (& git rev-parse --show-toplevel 2>$null | Select-Object -First 1)
if (-not $root) { Emit-Empty }
$cfg = Join-Path $root '.agent-index/config.yaml'
if (-not (Test-Path -LiteralPath $cfg)) { Emit-Empty }

$lines = Get-Content -LiteralPath $cfg
if (-not $lines) { Emit-Empty }

# Minimal scanner for the corpus.sources list: within the top-level `corpus:`
# block, each `- name: X` item, with its following `repo:` / `trust_domain:`.
$inCorpus = $false
$sources = @()
$cur = $null
foreach ($raw in $lines) {
    $line = $raw -replace '\s+#.*$', ''      # strip trailing comments
    if ($line -match '^\s*#') { continue }
    if ($line -match '^[^\s].*:') {          # a new top-level key
        $inCorpus = ($line -match '^\s*corpus\s*:')
        continue
    }
    if (-not $inCorpus) { continue }
    if ($line -match '^\s*-\s*name\s*:\s*[''"]?([^''"]+?)[''"]?\s*$') {
        if ($cur) { $sources += , $cur }
        $cur = @{ name = $Matches[1].Trim(); repo = ''; trust = '' }
    }
    elseif ($cur -and $line -match '^\s*repo\s*:\s*[''"]?([^''"]+?)[''"]?\s*$') {
        $cur.repo = $Matches[1].Trim()
    }
    elseif ($cur -and $line -match '^\s*trust_domain\s*:\s*[''"]?([^''"]+?)[''"]?\s*$') {
        $cur.trust = $Matches[1].Trim()
    }
}
if ($cur) { $sources += , $cur }
if (-not $sources -or $sources.Count -eq 0) { Emit-Empty }

$rows = ($sources | ForEach-Object {
        $label = if ($_.repo) { $_.repo } else { $_.name }
        $td = if ($_.trust) { " [$($_.trust)]" } else { '' }
        "  - $label (source ``$($_.name)``)$td"
    }) -join "`n"

$md = @"
## agent-index retrieval is available for these scopes

The **agent-index** semantic + lexical index covers the following configured
scopes (delegate retrieval to the **``@agent-index``** sub-agent):

$rows

**Prefer ``@agent-index``** (``agent_index_search`` / ``agent_index_find_similar``)
over a broad ``grep``/``glob`` sweep when searching **within these scopes** by
meaning/behavior, when you want the most-relevant few results across a large
corpus, or to pivot 'more like this' from a hit. Pass the ``source``/``repo``
filter to scope to one corpus (and to respect trust-domain boundaries, which are
not yet enforced at query time). Fall back to direct ``grep``/``glob`` for
exact-string hunts, files outside these scopes, or if the index is unavailable.
"@

Write-Output (@{ additionalContext = $md } | ConvertTo-Json -Compress -Depth 3)
exit 0
