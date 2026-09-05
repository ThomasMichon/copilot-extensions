# Emit the ai-attribution ambient policy for the session-start repository.

$ErrorActionPreference = 'SilentlyContinue'
$script:PluginVersion = '0.1.0-dev10'
$script:MaxPayloadBytes = 65536
$script:MaxConfigBytes = 65536
$script:MaxConfigLines = 200
$script:MaxCustomDirsLength = 65536
$script:MaxCustomDirsEntries = 128
$script:MaxJsonDepth = 64
$script:Disclosure = 'third-party'
$script:OwnedAccounts = @()
$script:ContributionGuides = @()
$script:RepoRoot = ''
$script:IsWindowsPlatform = $env:OS -eq 'Windows_NT'

function Write-Diagnostic([string] $Message) {
    [Console]::Error.WriteLine("[ai-attribution] $Message")
}

function Emit-Empty {
    [Console]::Out.Write('{}')
    exit 0
}

function Test-Host([string] $Value) {
    if ($Value.Length -lt 1 -or $Value.Length -gt 253 -or $Value -cnotmatch '^[A-Za-z0-9.-]+$') {
        return $false
    }
    foreach ($Label in $Value.Split('.')) {
        if ($Label.Length -lt 1 -or $Label.Length -gt 63 -or
            $Label -cnotmatch '^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$') {
            return $false
        }
    }
    return $true
}

function Test-Owner([string] $Value) {
    return $Value -cmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
}

function Test-Account([string] $Value) {
    $Parts = $Value.Split('/')
    return $Parts.Count -eq 2 -and (Test-Host $Parts[0]) -and (Test-Owner $Parts[1])
}

function Test-AbsolutePath([string] $Value) {
    if ($script:IsWindowsPlatform) {
        return $Value -cmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+)'
    }
    return $Value.StartsWith('/')
}

function Test-PathContainsReparsePoint([string] $Path) {
    try {
        $Full = [IO.Path]::GetFullPath($Path)
        $Root = [IO.Path]::GetPathRoot($Full)
        $Current = $Root
        $Remainder = $Full.Substring($Root.Length)
        foreach ($Segment in ($Remainder -split '[\\/]')) {
            if (-not $Segment) { continue }
            $Current = Join-Path $Current $Segment
            if (Test-Path -LiteralPath $Current) {
                $Item = Get-Item -LiteralPath $Current -Force -ErrorAction Stop
                if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    return $true
                }
            }
        }
        return $false
    } catch {
        return $true
    }
}

function Test-ContributionGuide([string] $Value) {
    if ($Value.Length -gt 160 -or $Value -cnotmatch '^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$') {
        return $false
    }
    $CurrentPath = $script:RepoRoot
    foreach ($Segment in $Value.Split('/')) {
        if ($Segment -eq '.' -or $Segment -eq '..') { return $false }
        $CurrentPath = Join-Path $CurrentPath $Segment
        if (Test-PathContainsReparsePoint $CurrentPath) { return $false }
    }
    $GuidePath = Join-Path $script:RepoRoot $Value
    return Test-Path -LiteralPath $GuidePath -PathType Leaf
}

function Read-PolicyConfig([string] $Path, [string] $Authority) {
    if (-not (Test-Path -LiteralPath $Path) -and -not (Test-PathContainsReparsePoint $Path)) { return }
    if ((Test-PathContainsReparsePoint $Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Write-Diagnostic 'could not safely read config; safe defaults remain active'
        return
    }

    try {
        $File = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        if ($File.Length -gt $script:MaxConfigBytes) {
            Write-Diagnostic 'config exceeds the 65536-byte limit; safe defaults remain active'
            return
        }
        $Stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        try {
            $Buffer = New-Object byte[] ($script:MaxPayloadBytes + 1)
            $Count = 0
            while ($Count -lt $Buffer.Length) {
                $Read = $Stream.Read($Buffer, $Count, $Buffer.Length - $Count)
                if ($Read -eq 0) { break }
                $Count += $Read
            }
        } finally {
            $Stream.Dispose()
        }
        if ($Count -gt $script:MaxConfigBytes) {
            Write-Diagnostic 'config exceeds the 65536-byte limit; safe defaults remain active'
            return
        }
        $Utf8 = New-Object Text.UTF8Encoding($false, $true)
        try {
            $Content = $Utf8.GetString($Buffer, 0, $Count)
        } catch {
            Write-Diagnostic 'config is not valid UTF-8; safe defaults remain active'
            return
        }
        if ($Content.Contains([char]0)) {
            Write-Diagnostic 'config contains NUL; safe defaults remain active'
            return
        }
        $Lines = $Content -split '\r\n|\n|\r'
        $LineCount = ([regex]::Matches($Content, '\r\n|\n|\r')).Count
        if ($Content.Length -gt 0 -and
            -not ($Content.EndsWith("`r") -or $Content.EndsWith("`n"))) {
            $LineCount += 1
        }
    } catch {
        Write-Diagnostic 'could not safely read config; safe defaults remain active'
        return
    }
    if ($LineCount -gt $script:MaxConfigLines) {
        Write-Diagnostic 'config exceeds the 200-line limit; safe defaults remain active'
        return
    }

    foreach ($Raw in $Lines) {
        $Line = $Raw.Trim()
        if (-not $Line -or $Line.StartsWith('#')) { continue }
        $Equals = $Line.IndexOf('=')
        if ($Equals -lt 0) {
            Write-Diagnostic 'ignored malformed line (expected key=value)'
            continue
        }
        $Key = $Line.Substring(0, $Equals).Trim()
        $Value = $Line.Substring($Equals + 1).Trim()
        if (-not $Key -or -not $Value) {
            Write-Diagnostic 'ignored malformed line (key and value are required)'
            continue
        }

        switch -CaseSensitive ($Key) {
            'disclosure' {
                if ($Authority -ceq 'repo') {
                    Write-Diagnostic "ignored non-repo-delegable key 'disclosure'"
                } elseif ($Value -ceq 'always') {
                    $script:Disclosure = 'always'
                } elseif ($Value -ceq 'third-party') {
                    if ($script:Disclosure -ceq 'always') {
                        Write-Diagnostic 'ignored disclosure=third-party because earlier policy requires always'
                    }
                } else {
                    Write-Diagnostic 'ignored invalid disclosure value'
                }
            }
            'owned_account' {
                if ($Authority -ceq 'repo') {
                    Write-Diagnostic "ignored non-repo-delegable key 'owned_account'"
                } elseif (Test-Account $Value) {
                    $script:OwnedAccounts += $Value
                } else {
                    Write-Diagnostic 'ignored invalid owned_account value'
                }
            }
            'contribution_guide' {
                if ($Authority -cne 'repo') {
                    Write-Diagnostic "ignored repo-only key 'contribution_guide'"
                } elseif (-not (Test-ContributionGuide $Value)) {
                    Write-Diagnostic 'ignored invalid contribution_guide path'
                } elseif ($script:ContributionGuides.Count -ge 4) {
                    Write-Diagnostic 'ignored contribution_guide beyond the four-entry limit'
                } else {
                    $script:ContributionGuides += $Value
                }
            }
            default {
                Write-Diagnostic 'ignored unknown config key'
            }
        }
    }
}

function Get-RemoteAccount([string] $RepositoryRoot) {
    $Url = (& git -C $RepositoryRoot remote get-url origin 2>$null | Select-Object -First 1)
    if (-not $Url) {
        $First = (& git -C $RepositoryRoot remote 2>$null | Select-Object -First 1)
        if ($First) {
            $Url = (& git -C $RepositoryRoot remote get-url $First 2>$null | Select-Object -First 1)
        }
    }
    if (-not $Url) { return '' }

    $HostName = ''
    $Path = ''
    if ($Url -match '^[A-Za-z][A-Za-z0-9+.-]*://(?:[^/@]+@)?([^/]+)/(.*)$') {
        $HostName = $Matches[1]
        $Path = $Matches[2]
    } elseif ($Url -match '^[^@]+@([^:]+):(.*)$') {
        $HostName = $Matches[1]
        $Path = $Matches[2]
    } else {
        return ''
    }
    $Path = $Path.TrimStart('/')
    if (-not $Path.Contains('/')) { return '' }
    $Owner = $Path.Split('/')[0]
    if (-not (Test-Host $HostName) -or -not (Test-Owner $Owner)) {
        Write-Diagnostic 'remote host or owner is invalid; ownership remains unresolved'
        return ''
    }
    return $HostName.ToLowerInvariant() + '/' + $Owner
}

function Test-OwnedAccount([string] $Candidate) {
    foreach ($Account in $script:OwnedAccounts) {
        if ($Candidate.Equals($Account, [StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function ConvertTo-JsonString([string] $Value) {
    $Builder = New-Object Text.StringBuilder
    foreach ($Character in $Value.ToCharArray()) {
        $Code = [int][char]$Character
        if ($Code -eq 34) {
            [void]$Builder.Append('\"')
        } elseif ($Code -eq 92) {
            [void]$Builder.Append('\\')
        } elseif ($Code -eq 8) {
            [void]$Builder.Append('\b')
        } elseif ($Code -eq 9) {
            [void]$Builder.Append('\t')
        } elseif ($Code -eq 10) {
            [void]$Builder.Append('\n')
        } elseif ($Code -eq 12) {
            [void]$Builder.Append('\f')
        } elseif ($Code -eq 13) {
            [void]$Builder.Append('\r')
        } elseif ($Code -lt 32) {
            [void]$Builder.Append(('\u{0:x4}' -f $Code))
        } else {
            [void]$Builder.Append($Character)
        }
    }
    return $Builder.ToString()
}

function Resolve-ConfigDirectory([string] $Configured) {
    $Value = $Configured.Trim()
    if ($Value -eq '~') {
        $Value = $HOME
    } elseif ($Value.StartsWith('~/') -or $Value.StartsWith('~\')) {
        $Value = Join-Path $HOME $Value.Substring(2)
    }
    if (-not (Test-AbsolutePath $Value)) { return '' }
    try {
        $Full = [IO.Path]::GetFullPath($Value)
        if (Test-PathContainsReparsePoint $Full) { return '' }
        $Resolved = (Resolve-Path -LiteralPath $Full -ErrorAction Stop).ProviderPath
        if (-not (Test-Path -LiteralPath $Resolved -PathType Container)) { return '' }
        return [IO.Path]::GetFullPath($Resolved)
    } catch {
        return ''
    }
}

function Test-PayloadLexicalSafety([string] $PayloadText) {
    $Depth = 0
    $CwdCount = 0
    for ($Index = 0; $Index -lt $PayloadText.Length; $Index++) {
        $Character = $PayloadText[$Index]
        if ($Character -eq [char]0) { return $false }
        if ($Character -eq '"') {
            $StringDepth = $Depth
            $Builder = New-Object Text.StringBuilder
            $Closed = $false
            for ($Index += 1; $Index -lt $PayloadText.Length; $Index++) {
                $Character = $PayloadText[$Index]
                if ($Character -eq [char]0 -or [int]$Character -lt 32) {
                    return $false
                }
                if ($Character -eq '"') {
                    $Closed = $true
                    break
                }
                if ($Character -ne '\') {
                    [void]$Builder.Append($Character)
                    continue
                }
                $Index += 1
                if ($Index -ge $PayloadText.Length) { return $false }
                $Escape = $PayloadText[$Index]
                if ($Escape -eq 'u') {
                    if ($Index + 4 -ge $PayloadText.Length) { return $false }
                    $Hex = $PayloadText.Substring($Index + 1, 4)
                    if ($Hex -cnotmatch '^[0-9A-Fa-f]{4}$') { return $false }
                    $Code = [Convert]::ToInt32($Hex, 16)
                    if ($Code -eq 0) { return $false }
                    [void]$Builder.Append([char]$Code)
                    $Index += 4
                } else {
                    [void]$Builder.Append($Escape)
                }
            }
            if (-not $Closed) { return $false }
            $Lookahead = $Index + 1
            while ($Lookahead -lt $PayloadText.Length -and
                [char]::IsWhiteSpace($PayloadText[$Lookahead])) {
                $Lookahead += 1
            }
            if ($StringDepth -eq 1 -and
                $Lookahead -lt $PayloadText.Length -and
                $PayloadText[$Lookahead] -eq ':' -and
                $Builder.ToString() -ceq 'cwd') {
                $CwdCount += 1
                if ($CwdCount -gt 1) { return $false }
            }
            continue
        }
        if ($Character -eq '{' -or $Character -eq '[') {
            $Depth += 1
            if ($Depth -gt $script:MaxJsonDepth) { return $false }
        } elseif ($Character -eq '}' -or $Character -eq ']') {
            $Depth -= 1
            if ($Depth -lt 0) { return $false }
        }
    }
    return $true
}

function Test-PathAtOrBelow([string] $Candidate, [string] $Root) {
    $Comparison = if ($script:IsWindowsPlatform) {
        [StringComparison]::OrdinalIgnoreCase
    } else {
        [StringComparison]::Ordinal
    }
    if ($Candidate.Equals($Root, $Comparison)) { return $true }
    $Prefix = $Root.TrimEnd([char[]]@('\', '/')) + [IO.Path]::DirectorySeparatorChar
    return $Candidate.StartsWith($Prefix, $Comparison)
}

function Read-CustomInstructionConfigs {
    $RawDirectories = [string]$env:COPILOT_CUSTOM_INSTRUCTIONS_DIRS
    if ($RawDirectories.Length -gt $script:MaxCustomDirsLength) {
        Write-Diagnostic 'ignored custom instruction directories beyond the 65536-character limit'
        return
    }
    $SeparatorPattern = '[,' + [Regex]::Escape([string][IO.Path]::PathSeparator) + ']'
    $ConfiguredDirectories = @($RawDirectories -split $SeparatorPattern)
    if ($ConfiguredDirectories.Count -gt $script:MaxCustomDirsEntries) {
        Write-Diagnostic 'ignored custom instruction directories beyond the 128-entry limit'
        return
    }
    foreach ($ConfiguredDir in $ConfiguredDirectories) {
        if (-not $ConfiguredDir.Trim()) { continue }
        $ResolvedDir = Resolve-ConfigDirectory $ConfiguredDir
        if (-not $ResolvedDir) {
            Write-Diagnostic 'ignored unresolved or reparse-point custom instruction directory'
        } elseif (Test-PathAtOrBelow $ResolvedDir $script:RepoRoot) {
            Write-Diagnostic 'ignored custom instruction directory at or beneath the session-start repository'
        } else {
            Read-PolicyConfig (Join-Path $ResolvedDir 'ai-attribution.conf') 'operator'
        }
    }
}

function Read-OperatorConfig(
    [string] $ConfiguredDirectory,
    [string] $RelativePath
) {
    $ResolvedDirectory = Resolve-ConfigDirectory $ConfiguredDirectory
    if (-not $ResolvedDirectory) { return }
    if (Test-PathAtOrBelow $ResolvedDirectory $script:RepoRoot) {
        Write-Diagnostic 'ignored operator config path at or beneath the session-start repository'
        return
    }
    Read-PolicyConfig (Join-Path $ResolvedDirectory $RelativePath) 'operator'
}

function Invoke-Policy {
    try {
        $Stream = [Console]::OpenStandardInput()
        $Buffer = New-Object byte[] ($script:MaxConfigBytes + 1)
        $Count = 0
        while ($Count -lt $Buffer.Length) {
            $Read = $Stream.Read($Buffer, $Count, $Buffer.Length - $Count)
            if ($Read -eq 0) { break }
            $Count += $Read
        }
        if ($Count -gt $script:MaxPayloadBytes) { throw 'oversized payload' }
        $Utf8 = New-Object Text.UTF8Encoding($false, $true)
        $PayloadText = $Utf8.GetString($Buffer, 0, $Count)
        if ($PayloadText.Length -gt $script:MaxPayloadBytes -or
            -not (Test-PayloadLexicalSafety $PayloadText)) {
            throw 'oversized or nul payload'
        }
        if (-not $PayloadText.Trim()) { throw 'missing payload' }
        $Payload = $PayloadText | ConvertFrom-Json -ErrorAction Stop
        if ($Payload -isnot [pscustomobject] -or
            -not ($Payload.PSObject.Properties.Name -ccontains 'cwd') -or
            $Payload.cwd -isnot [string] -or
            -not $Payload.cwd) {
            throw 'missing cwd'
        }
        if ($Payload.cwd.Contains([char]0) -or
            $Payload.cwd.Contains("`r") -or
            $Payload.cwd.Contains("`n")) {
            throw 'invalid cwd control'
        }
        if (-not (Test-AbsolutePath $Payload.cwd)) {
            throw 'relative cwd'
        }
        $PayloadCwd = [IO.Path]::GetFullPath(
            (Resolve-Path -LiteralPath $Payload.cwd -ErrorAction Stop).ProviderPath
        )
        if (-not (Test-Path -LiteralPath $PayloadCwd -PathType Container)) {
            throw 'cwd is not a directory'
        }
    } catch {
        Write-Diagnostic 'missing or malformed sessionStart payload; no policy context emitted'
        Emit-Empty
    }

    $RawRepoRoot = (& git -C $PayloadCwd rev-parse --show-toplevel 2>$null | Select-Object -First 1)
    if (-not $RawRepoRoot) { Emit-Empty }
    try {
        $script:RepoRoot = [IO.Path]::GetFullPath(
            (Resolve-Path -LiteralPath $RawRepoRoot -ErrorAction Stop).ProviderPath
        )
    } catch {
        Emit-Empty
    }

    Read-OperatorConfig (Join-Path $HOME '.copilot') 'ai-attribution.conf'
    if ($env:XDG_CONFIG_HOME) {
        $ConfigHome = $env:XDG_CONFIG_HOME
    } elseif ($script:IsWindowsPlatform -and $env:APPDATA) {
        $ConfigHome = $env:APPDATA
    } else {
        $ConfigHome = Join-Path $HOME '.config'
    }
    Read-OperatorConfig (Join-Path $ConfigHome 'ai-attribution') 'config.conf'

    if ($env:COPILOT_CUSTOM_INSTRUCTIONS_DIRS) {
        Read-CustomInstructionConfigs
    }

    Read-PolicyConfig (Join-Path $script:RepoRoot '.github/ai-attribution.conf') 'repo'

    if ($args -contains '--aggregate') {
        $Kernel = "[owner: ai-attribution@$script:PluginVersion] " +
            'Before publishing, classify audience and repository ownership. ' +
            'Disclose AI assistance prominently for third-party contributions ' +
            'and whenever operator policy requires; ' +
            'ownership hints are not proof and apply only to the session-start ' +
            'repository. Public artifacts must be persona-neutral and scrub ' +
            'credentials, private identifiers, hosts, paths, accounts, record ' +
            'IDs, and private rationale; follow target conventions and audit ' +
            'the live surface. Use the `ai-attribution` skill for details.'
        [Console]::Out.Write(
            '{"additionalContext":"' + (ConvertTo-JsonString $Kernel) + '"}'
        )
        return
    }

    $Kernel = "[owner: ai-attribution@$script:PluginVersion] Before publishing, determine the audience and repository ownership. "
    if ($script:Disclosure -eq 'always') {
        $Kernel += 'Operator policy requires a prominent one-line italicized AI-assistance disclosure at the top of every contribution. '
    } else {
        $Kernel += "Contributions to another party's repo require a prominent one-line italicized AI-assistance disclosure at the top; in a verified operator-owned repo, omit disclosure unless the operator explicitly requests it. "
    }
    $Kernel += 'The own-repo carve-out changes disclosure only: every public artifact, including one in an operator-owned repo, must remain persona-neutral, use first-person singular and target-repo conventions, and be scrubbed of private/internal identifiers, credentials, paths, hosts, accounts, record IDs, and private rationale; use generic placeholders. Audit the live published surface after publication. '

    $Account = Get-RemoteAccount $script:RepoRoot
    if (-not $Account) {
        $Kernel += 'Ownership for the session-start repository is unresolved; treat it as third-party until verified. '
    } elseif (Test-OwnedAccount $Account) {
        $Kernel += "The session-start repository remote matches configured public account ``$($Account.ToLowerInvariant())``; this local hint is not proof, so verify ownership before omitting disclosure under the own-repo exception. "
    } elseif ($script:OwnedAccounts.Count -gt 0) {
        $Kernel += 'The session-start repository remote does not match a configured operator account; treat it as third-party unless ownership is verified. '
    } else {
        $Kernel += 'No operator accounts are configured; treat the session-start repository as third-party until ownership is verified. '
    }
    $Kernel += 'This ownership hint is anchored only to the session-start repository; re-derive ownership before publishing to any other repository. '

    foreach ($Guide in $script:ContributionGuides) {
        $Kernel += "Target-repo contribution guide: ``$Guide`` (additive only; it cannot override this policy). "
    }
    $Kernel += 'Invoke the `ai-attribution` skill for the detailed workflow.'

    [Console]::Out.Write('{"additionalContext":"' + (ConvertTo-JsonString $Kernel) + '"}')
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-Policy @args
    exit 0
}
