# Side-effect-only sessionStart wrapper: invokes write_session_guidance.py,
# UNLESS a warm per-repository guidance cache lets this wrapper serve the
# session-scoped file directly without spawning python at all (process-count
# reduction; see copilot-agent-runtime#22031's sibling investigation).
# write_session_guidance.py remains the sole authority for the cache's format
# and every safety invariant (session-id shape, symlink/reparse defense,
# atomic replace, byte budget); this fast path mirrors those checks narrowly
# and falls through to the unchanged python path at the first sign of
# anything unexpected -- it never weakens what python would have done.
$ErrorActionPreference = 'SilentlyContinue'

$root = if ($env:COPILOT_PLUGIN_ROOT) {
    $env:COPILOT_PLUGIN_ROOT
} elseif ($env:PLUGIN_ROOT) {
    $env:PLUGIN_ROOT
} elseif ($env:CLAUDE_PLUGIN_ROOT) {
    $env:CLAUDE_PLUGIN_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
$script = Join-Path (Join-Path $root 'scripts') 'write_session_guidance.py'

$MaxInputBytes = 65536
$GuidanceMaxBytes = 4096
$CacheFormatVersion = 1
$GuidanceHeader = "# AI attribution session guidance`n`n"
$SessionIdPattern = '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'

function Invoke-PythonFallback {
    param([string]$StdinText)
    $python = $null
    foreach ($candidate in @('python3', 'python', 'py')) {
        $found = Get-Command $candidate -CommandType Application -All -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notmatch '\\WindowsApps\\' } |
            Select-Object -First 1
        if ($found) {
            $python = $found
            break
        }
    }
    if (-not $python -or -not (Test-Path -LiteralPath $script -PathType Leaf)) {
        [Console]::Out.Write('{}')
        return
    }
    $env:PYTHONPATH = ''
    try {
        if ($null -ne $StdinText) {
            $StdinText | & $python.Source $script
        } else {
            & $python.Source $script
        }
        if ($LASTEXITCODE -ne 0) {
            [Console]::Out.Write('{}')
        }
    } catch {
        [Console]::Out.Write('{}')
    }
}

# Read the whole payload up front (bounded, mirroring the python writer's own
# bound): a cache hit never invokes python, and a miss/fallback must re-supply
# the exact bytes already drained from the real stdin stream.
$stdinText = $null
try {
    $stdinText = [Console]::In.ReadToEnd()
} catch {
    $stdinText = $null
}
if (
    $null -eq $stdinText -or
    [System.Text.Encoding]::UTF8.GetByteCount($stdinText) -gt $MaxInputBytes
) {
    Invoke-PythonFallback -StdinText $stdinText
    exit 0
}

$fastPathOk = $false
try {
    $payload = $stdinText | ConvertFrom-Json -ErrorAction Stop
    $sessionId = $payload.sessionId
    $cwd = $payload.cwd
    if (
        $sessionId -is [string] -and $sessionId -match $SessionIdPattern -and
        $cwd -is [string] -and $cwd.Length -gt 0 -and
        -not [bool]$env:AI_ATTRIBUTION_FORCE_REFRESH
    ) {
        $resolvedHome = if ($env:USERPROFILE) { $env:USERPROFILE } elseif ($env:HOME) { $env:HOME } else { $null }
        if ($resolvedHome) {
            # Repo cache key: same derivation as write_session_guidance.py's
            # _repo_cache_key -- git toplevel when resolvable, else raw cwd,
            # SHA-256 of the UTF-8 bytes, first 16 hex chars.
            $repoRoot = $cwd
            try {
                $top = & git -C $cwd rev-parse --show-toplevel 2>$null
                if ($LASTEXITCODE -eq 0 -and $top) {
                    $repoRoot = ($top | Select-Object -First 1).Trim()
                }
            } catch {}
            $sha256 = [System.Security.Cryptography.SHA256]::Create()
            try {
                $bytes = [System.Text.Encoding]::UTF8.GetBytes($repoRoot)
                $hashBytes = $sha256.ComputeHash($bytes)
            } finally {
                $sha256.Dispose()
            }
            $key = -join ($hashBytes | ForEach-Object { $_.ToString('x2') })
            $key = $key.Substring(0, 16)

            $cacheDir = Join-Path (Join-Path $resolvedHome '.copilot') 'ai-attribution-cache'
            $cachePath = Join-Path $cacheDir "$key.json"
            if (Test-Path -LiteralPath $cachePath -PathType Leaf) {
                $cacheItem = Get-Item -LiteralPath $cachePath -Force -ErrorAction Stop
                if (-not $cacheItem.Attributes.HasFlag([System.IO.FileAttributes]::ReparsePoint)) {
                    $data = Get-Content -LiteralPath $cachePath -Raw -Encoding UTF8 -ErrorAction Stop |
                        ConvertFrom-Json -ErrorAction Stop
                    $ttl = 0.0
                    if ($env:AI_ATTRIBUTION_CACHE_TTL_SECONDS) {
                        try { $ttl = [double]$env:AI_ATTRIBUTION_CACHE_TTL_SECONDS } catch { $ttl = 0.0 }
                    }
                    if ($ttl -le 0) { $ttl = 3600.0 }
                    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
                    if (
                        $data.version -eq $CacheFormatVersion -and
                        $null -ne $data.computed_at -and
                        $data.guidance -is [string] -and
                        ($now - [double]$data.computed_at) -le $ttl
                    ) {
                        $guidance = $data.guidance
                        $content = $GuidanceHeader + $guidance + "`n"
                        if (
                            [System.Text.Encoding]::UTF8.GetByteCount($content) -gt $GuidanceMaxBytes -and
                            $guidance
                        ) {
                            $content = $GuidanceHeader + (
                                "Current AI attribution guidance was omitted because it exceeded " +
                                "the bounded file budget. Treat guidance as unavailable for this " +
                                "session-start invocation.`n"
                            )
                        }

                        # Session-scoped target: create each level and refuse
                        # (falling through to the unchanged python path) at
                        # the first sign of a symlink/reparse point.
                        $copilotRoot = Join-Path $resolvedHome '.copilot'
                        $stateRoot = Join-Path $copilotRoot 'session-state'
                        $sessionRoot = Join-Path $stateRoot $sessionId
                        $instructionsDir = Join-Path $sessionRoot 'instructions'
                        $targetDir = Join-Path $instructionsDir 'ai-attribution'
                        $safe = $true
                        foreach ($dir in @($copilotRoot, $stateRoot, $sessionRoot, $instructionsDir, $targetDir)) {
                            New-Item -ItemType Directory -Force -Path $dir -ErrorAction Stop | Out-Null
                            $dirItem = Get-Item -LiteralPath $dir -Force -ErrorAction Stop
                            if ($dirItem.Attributes.HasFlag([System.IO.FileAttributes]::ReparsePoint)) {
                                $safe = $false
                                break
                            }
                        }
                        if ($safe) {
                            $target = Join-Path $targetDir 'session-guidance.instructions.md'
                            $temp = Join-Path $targetDir (
                                '.session-guidance.' + [System.IO.Path]::GetRandomFileName() + '.tmp'
                            )
                            [System.IO.File]::WriteAllText(
                                $temp, $content, [System.Text.UTF8Encoding]::new($false)
                            )
                            Move-Item -LiteralPath $temp -Destination $target -Force -ErrorAction Stop
                            $fastPathOk = $true
                        }
                    }
                }
            }
        }
    }
} catch {
    $fastPathOk = $false
}

if ($fastPathOk) {
    [Console]::Out.Write('{}')
} else {
    Invoke-PythonFallback -StdinText $stdinText
}
exit 0
