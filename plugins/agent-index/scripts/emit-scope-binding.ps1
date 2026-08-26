# emit-scope-binding -- agent-index sessionStart hook.
#
# Emits a succinct "what agent-index covers + how to search it" guidance fragment
# as {"additionalContext": "..."}, so EVERY agent learns, at session start, that a
# semantic index is available and how to query it -- by calling the `agent-index`
# CLI DIRECTLY. agent-index is a uniform retrieval capability every agent may use
# (like built-in search), so it is deliberately NOT wrapped in a sub-agent or an
# MCP tool; the how-to-search instructions ride in this hook's additionalContext
# instead. Generic emitter (ships in the plugin); it reads the LOCAL scope config
# -- `<repo>/.agent-index/config.yaml` `corpus.sources` -- so the operator's scope
# values stay in the repo, not baked into the plugin.
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
## agent-index retrieval — use the session command catalog

A semantic + lexical index of this harness is available to **every agent**. Take
``commands[id=agent-index].argv`` from the injected command catalog and append
the arguments below (no sub-agent, no MCP tool, no PATH lookup). It covers:

$rows

**How to search:**
- ``<catalog argv[0]> search "<natural-language or code query>" [--source <name>] [--language <lang>] [--repo <repo>] [--limit N] --json`` — ranked hits; each has ``chunk_id``, ``source``, ``file_path``, ``line_start``/``line_end``, ``content``.
- ``<catalog argv[0]> similar <chunk_id> [--source <name>] [--limit N]`` — pivot 'more like this' from a hit.
- ``<catalog argv[0]> clusters [--source <name>] [--exact-dupes-only] [--limit N]`` — near-duplicate groups.
- ``<catalog argv[0]> status`` — index health + per-source coverage; probe once if results look sparse.

**Prefer the catalog command's ``search`` subcommand** over a broad ``grep``/``glob`` sweep when
searching **within these scopes** by meaning/behavior, for the most-relevant few
results across a large corpus, or to pivot from a hit. Pass ``--source`` to scope
to one corpus (and to respect trust-domain boundaries, not yet enforced at query
time). Fall back to ``grep``/``glob`` for exact-string hunts, files outside these
scopes, or if the index is unavailable. Read-only: never reindex from an agent --
that is the operator flow (``<catalog argv[0]> index``).
"@

Write-Output (@{ additionalContext = $md } | ConvertTo-Json -Compress -Depth 3)
exit 0
