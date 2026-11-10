function Get-ManagedWorktreesRuntimeRoot {
    $runtimeRoot = Join-Path $env:USERPROFILE '.agent-worktrees'
    if ($env:COPILOT_EXTENSIONS_CONTEXT) {
        $pointer = $env:COPILOT_EXTENSIONS_CONTEXT
        if ($pointer -notmatch '^(?:[A-Za-z]:[\\/]|[\\/]{2}[^\\/]+[\\/][^\\/]+[\\/])' -or
            [IO.Path]::GetFileName($pointer) -cne 'install.json') { throw 'Invalid context pointer' }
        $runtimeRoot = Split-Path -Parent ([IO.Path]::GetFullPath($pointer))
        if ((Split-Path -Leaf $runtimeRoot) -cne 'agent-worktrees' -or
            (Split-Path -Leaf (Split-Path -Parent $runtimeRoot)) -cne 'plugins') {
            throw 'Invalid context layout'
        }
        $ancestor = $pointer
        while ($ancestor) {
            if ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Linked context path'
            }
            $ancestor = Split-Path -Parent $ancestor
        }
    }
    return $runtimeRoot
}

function Get-ManagedWorktreesPython {
    $python = $env:AGENT_WORKTREES_RESIDENT_PRE_TOOL_PYTHON
    if ($python -and $python -notlike '*\WindowsApps\*' -and
        [IO.Path]::IsPathRooted($python) -and [IO.File]::Exists($python)) { return $python }
    $runtimeRoot = Get-ManagedWorktreesRuntimeRoot
    $python = $null
    $resolver = Join-Path $PSScriptRoot 'resolve-runtime.ps1'
    if ([IO.File]::Exists($resolver)) {
        $previousRoot = $env:AGENT_RT_ROOT
        try {
            $env:AGENT_RT_ROOT = $runtimeRoot
            . $resolver -TraceMode read-only
            $python = $AwPy
        } finally {
            if ($null -eq $previousRoot) { Remove-Item Env:AGENT_RT_ROOT -ErrorAction SilentlyContinue }
            else { $env:AGENT_RT_ROOT = $previousRoot }
        }
    }
    if (-not $python -and -not $env:COPILOT_EXTENSIONS_CONTEXT) {
        $python = Get-Command python -CommandType Application -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*\WindowsApps\*' } |
        Select-Object -First 1 -ExpandProperty Source
    }
    return $python
}

function ConvertFrom-ManagedWorktreesJson {
    param([string]$Text)
    if (-not $Text.TrimStart().StartsWith('{')) { throw 'Expected a JSON object' }
    if (-not $script:managedWorktreesUtilityAssembly) {
        $script:managedWorktreesUtilityAssembly = [Reflection.Assembly]::Load(
            'Microsoft.PowerShell.Commands.Utility, Version=3.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35')
    }
    if ($PSVersionTable.PSVersion.Major -ge 6) {
        $jsonError = $null
        $decoded = [Microsoft.PowerShell.Commands.JsonObject]::ConvertFromJson($Text, $true, [ref]$jsonError)
        if ($null -ne $jsonError) { throw $jsonError.Exception }
        return ,$decoded
    }
    if (-not $script:managedWorktreesJson) {
        [void][Reflection.Assembly]::Load('System.Web.Extensions, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35')
        # Retain the shell's metadata semantics without projecting keys onto PSObject.
        $resolverType = $script:managedWorktreesUtilityAssembly.GetType(
            'Microsoft.PowerShell.Commands.JsonObjectTypeResolver', $true)
        $resolver = [Activator]::CreateInstance($resolverType, $true)
        $script:managedWorktreesJson = [System.Web.Script.Serialization.JavaScriptSerializer]::new($resolver)
        $script:managedWorktreesJson.MaxJsonLength = [int]::MaxValue
        $script:managedWorktreesJson.RecursionLimit = 100
    }
    return ,($script:managedWorktreesJson.DeserializeObject($Text))
}

function ConvertTo-PreparedWorktreesJson {
    param([object]$InputObject, [int]$Depth = 2)
    if (-not $script:managedWorktreesUtilityAssembly) {
        $null = ConvertFrom-ManagedWorktreesJson '{}'
    }
    if ($PSVersionTable.PSVersion.Major -ge 6) {
        $contextType = $script:managedWorktreesUtilityAssembly.GetType(
            'Microsoft.PowerShell.Commands.JsonObject+ConvertToJsonContext')
        if ($null -eq $contextType) {
            # The public serialization context is absent on older PowerShell releases.
            return ConvertTo-Json -InputObject $InputObject -Depth $Depth -Compress
        }
        $context = [Microsoft.PowerShell.Commands.JsonObject+ConvertToJsonContext]::new($Depth, $false, $true)
        return [Microsoft.PowerShell.Commands.JsonObject]::ConvertToJson($InputObject, [ref]$context)
    }
    return $script:managedWorktreesJson.Serialize($InputObject)
}

function Get-PreparedWorktreesPath {
    param([object]$Path)
    if ($Path -isnot [string] -or $Path.Length -gt 4096 -or
        $Path -notmatch '^[A-Za-z]:[\\/]' -or $Path.Contains([char]0)) {
        throw 'Unsupported prepared membership path'
    }
    $full = [IO.Path]::GetFullPath($Path)
    if ($full.Length -gt 3) { $full = $full.TrimEnd('\') }
    $ancestor = $full
    for ($depth = 0; $ancestor; $depth++) {
        if ($depth -ge 128) { throw 'Prepared membership path exceeds its bound' }
        try {
            if ([IO.File]::GetAttributes($ancestor) -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Prepared membership requires canonical unlinked paths'
            }
        } catch [IO.FileNotFoundException], [IO.DirectoryNotFoundException] {
        }
        $ancestor = [IO.Path]::GetDirectoryName($ancestor)
    }
    return $full.ToLowerInvariant()
}

function Test-PreparedWorktreesStamp {
    param([string]$Path, [object]$Stamp)
    $canonical = Get-PreparedWorktreesPath $Path
    try {
        $attributes = [IO.File]::GetAttributes($Path)
    } catch [IO.FileNotFoundException], [IO.DirectoryNotFoundException] {
        return $null -eq $Stamp
    }
    if ($Stamp -isnot [Collections.IList] -or $Stamp.Count -lt 2 -or
        $Stamp[0] -isnot [string] -or $Stamp[1] -isnot [string] -or
        -not [string]::Equals($Stamp[1], $canonical, [StringComparison]::Ordinal)) { return $false }
    if ($attributes -band [IO.FileAttributes]::Directory) {
        if ([string]::Equals($Stamp[0], 'directory', [StringComparison]::Ordinal)) {
            return $Stamp.Count -eq 2
        }
        if (-not [string]::Equals($Stamp[0], 'listing', [StringComparison]::Ordinal) -or
            $Stamp.Count -ne 3) { return $false }
        $mtime = [IO.Directory]::GetLastWriteTimeUtc($Path)
    } else {
        if (-not [string]::Equals($Stamp[0], 'file', [StringComparison]::Ordinal) -or $Stamp.Count -ne 4 -or
            $Stamp[2] -isnot [string] -or
            -not [string]::Equals($Stamp[2], ([IO.FileInfo]::new($Path)).Length.ToString(
                [Globalization.CultureInfo]::InvariantCulture), [StringComparison]::Ordinal)) {
            return $false
        }
        $mtime = [IO.File]::GetLastWriteTimeUtc($Path)
    }
    # Python's Windows stat uses the same 100 ns filesystem timestamp.
    $nanoseconds = ($mtime.Ticks - [long]621355968000000000) * [long]100
    return $Stamp[-1] -is [string] -and [string]::Equals(
        $Stamp[-1], $nanoseconds.ToString([Globalization.CultureInfo]::InvariantCulture),
        [StringComparison]::Ordinal)
}

function Test-PreparedWorktreesContent {
    param([string]$Path, [object]$Digest)
    $stream = $hash = $null
    try {
        if ($Digest -isnot [string] -or $Digest -cnotmatch '^[0-9a-f]{64}$') { return $false }
        $null = Get-PreparedWorktreesPath $Path
        if ([IO.File]::GetAttributes($Path) -band [IO.FileAttributes]::Directory) { return $false }
        $stream = [IO.FileStream]::new(
            $Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
        )
        $openedLength = $stream.Length
        if ($openedLength -gt 1048576) { return $false }
        $bytes = [byte[]]::new([int]$openedLength + 1)
        $count = 0
        while ($count -lt $bytes.Length) {
            $read = $stream.Read($bytes, $count, $bytes.Length - $count)
            if ($read -eq 0) { break }
            $count += $read
        }
        if ($count -eq $bytes.Length) { return $false }
        $null = Get-PreparedWorktreesPath $Path
        $hash = [Security.Cryptography.SHA256]::Create()
        $actual = [BitConverter]::ToString($hash.ComputeHash($bytes, 0, $count)).Replace('-', '').ToLowerInvariant()
        return [string]::Equals($actual, $Digest, [StringComparison]::Ordinal)
    } finally {
        if ($null -ne $hash) { $hash.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Test-PreparedManagedWorktreesSession {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Raw)
    $stream = $null
    try {
        if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
            $Raw.Length -gt 1048576) { return $false }
        $event = ConvertFrom-ManagedWorktreesJson $Raw
        if ($event -isnot [Collections.IDictionary]) { return $false }
        if ($event.ContainsKey('sessionId')) {
            $session = $event['sessionId']
        } else { $session = $env:COPILOT_AGENT_SESSION_ID }
        if ($event.ContainsKey('cwd')) {
            $cwd = $event['cwd']
        } elseif ($event.ContainsKey('workingDirectory')) {
            $cwd = $event['workingDirectory']
        } else { $cwd = $env:COPILOT_PROJECT_DIR }
        if ($session -isnot [string] -or
            $session -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') { return $false }
        $workspace = Get-PreparedWorktreesPath $cwd
        if (-not [IO.Directory]::Exists($workspace)) { return $false }
        $userHome = Get-PreparedWorktreesPath $env:USERPROFILE
        $registryHome = if ($env:AGENT_HOME -and $env:AGENT_HOME.Trim()) {
            $env:AGENT_HOME.Trim()
        } else { $env:USERPROFILE }
        $context = if ($env:COPILOT_EXTENSIONS_CONTEXT) { $env:COPILOT_EXTENSIONS_CONTEXT } else { '' }
        if ($context) {
            $pointer = Get-PreparedWorktreesPath $context
            if ([IO.Path]::GetFileName($pointer) -cne 'install.json') { return $false }
            $registry = [IO.Path]::GetDirectoryName($pointer)
            if ([IO.Path]::GetFileName($registry) -cne 'agent-worktrees' -or
                [IO.Path]::GetFileName([IO.Path]::GetDirectoryName($registry)) -cne 'plugins') {
                return $false
            }
        } else {
            $registry = Get-PreparedWorktreesPath ([IO.Path]::Combine($registryHome, '.agent-worktrees'))
        }
        $marker = [IO.Path]::Combine(
            $userHome, '.copilot', 'session-state', $session, 'agent-worktrees', 'managed-session.json'
        )
        $null = Get-PreparedWorktreesPath $marker
        if ([IO.File]::GetAttributes($marker) -band [IO.FileAttributes]::Directory) { return $false }
        $stream = [IO.FileStream]::new(
            $marker, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
        )
        $openedLength = $stream.Length
        if ($openedLength -gt 1048576) { return $false }
        $bytes = [byte[]]::new([int]$openedLength + 1)
        $count = 0
        while ($count -lt $bytes.Length) {
            $read = $stream.Read($bytes, $count, $bytes.Length - $count)
            if ($read -eq 0) { break }
            $count += $read
        }
        if ($count -eq $bytes.Length) { return $false }
        $text = ([Text.UTF8Encoding]::new($false, $true)).GetString($bytes, 0, $count)
        $value = ConvertFrom-ManagedWorktreesJson $text.TrimStart([char]0xfeff)
        if ($value -isnot [Collections.IDictionary] -or
            ($value['version'] -isnot [int] -and $value['version'] -isnot [long]) -or
            $value['version'] -ne 1) { return $false }
        foreach ($name in @('scope', 'identity', 'sources', 'inputs')) {
            if ($value[$name] -isnot [Collections.IDictionary]) { return $false }
        }
        $scope = $value['scope']
        $expectedScope = @{
            session = $session; workspace = $workspace; registry = $registry
            context = $context; platform = 'windows'
        }
        if ($scope.Count -ne $expectedScope.Count) { return $false }
        foreach ($name in $expectedScope.Keys) {
            if ($scope[$name] -isnot [string] -or
                -not [string]::Equals($scope[$name], $expectedScope[$name], [StringComparison]::Ordinal)) {
                return $false
            }
        }
        $identity = $value['identity']
        foreach ($name in @('root', 'git_dir', 'common_dir')) {
            $null = Get-PreparedWorktreesPath $identity[$name]
        }
        $root = $identity['root']
        if (-not [string]::Equals($workspace, $root, [StringComparison]::Ordinal) -and
            -not $workspace.StartsWith($root + '\', [StringComparison]::Ordinal)) {
            return $false
        }
        $inputs = $value['inputs']
        $sources = $value['sources']
        if ($inputs.Count -gt 256) { return $false }
        $expectedSources = [Collections.Generic.Dictionary[string, bool]]::new([StringComparer]::Ordinal)
        $expectedSources.Add([IO.Path]::Combine($registry, 'projects.yaml'), $false)
        $expectedSources.Add([IO.Path]::Combine($registry, 'repos.yaml'), $false)
        if ($context) {
            $installation = $value['installation']
            if ($installation -isnot [Collections.IDictionary] -or $installation.Count -ne 10 -or
                ($installation['version'] -isnot [int] -and $installation['version'] -isnot [long]) -or
                $installation['version'] -ne 1 -or $installation['sourceKind'] -isnot [string] -or
                $installation['sourceKind'] -cnotin @('github', 'git', 'opaque')) { return $false }
            $selected = @{}
            foreach ($name in @('namespaceReceipt', 'installReceipt', 'payloadRoot', 'cellRoot', 'pluginRoot')) {
                $selected[$name] = Get-PreparedWorktreesPath $installation[$name]
                if (-not $expectedSources.ContainsKey($selected[$name])) {
                    $expectedSources.Add($selected[$name], $false)
                }
            }
            $declared = $installation['declaredPaths']
            if ($declared -isnot [Collections.IDictionary] -or $declared.Count -ne 3) { return $false }
            foreach ($name in @('payloadRoot', 'pluginRoot', 'namespaceReceipt')) {
                if (-not [string]::Equals(
                    (Get-PreparedWorktreesPath $declared[$name]), $selected[$name],
                    [StringComparison]::Ordinal)) { return $false }
            }
            $cell = [IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($registry))
            if (-not [string]::Equals($selected['pluginRoot'], $registry, [StringComparison]::Ordinal) -or
                -not [string]::Equals($selected['installReceipt'], $pointer, [StringComparison]::Ordinal) -or
                -not [string]::Equals($selected['cellRoot'], $cell, [StringComparison]::Ordinal) -or
                -not [string]::Equals(
                    $selected['namespaceReceipt'], [IO.Path]::Combine($cell, 'namespace.json'),
                    [StringComparison]::Ordinal)) { return $false }
            $payload = if ($env:AGENT_WORKTREES_PAYLOAD_ROOT) {
                $env:AGENT_WORKTREES_PAYLOAD_ROOT
            } elseif ($env:COPILOT_PLUGIN_ROOT) { $env:COPILOT_PLUGIN_ROOT }
            else { [IO.Path]::GetDirectoryName($PSScriptRoot) }
            if (-not [string]::Equals(
                $selected['payloadRoot'], (Get-PreparedWorktreesPath $payload),
                [StringComparison]::Ordinal)) { return $false }
            if ($env:COPILOT_PLUGIN_ROOT -and -not [string]::Equals(
                $selected['payloadRoot'], (Get-PreparedWorktreesPath $env:COPILOT_PLUGIN_ROOT),
                [StringComparison]::Ordinal)) { return $false }
            $expectedSources.Add([IO.Path]::Combine($selected['payloadRoot'], 'plugin.json'), $false)
            $files = $installation['files']
            if ($files -isnot [Collections.IDictionary] -or $files.Count -ne 3) { return $false }
            $expectedFiles = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
            foreach ($path in @(
                $selected['namespaceReceipt'], $selected['installReceipt'],
                [IO.Path]::Combine($selected['payloadRoot'], 'plugin.json')
            )) { $null = $expectedFiles.Add($path) }
            foreach ($path in $files.Keys) {
                if (-not $expectedFiles.Remove((Get-PreparedWorktreesPath $path)) -or
                    -not (Test-PreparedWorktreesContent $path $files[$path])) { return $false }
            }
            if ($expectedFiles.Count) { return $false }
            $roots = $installation['roots']
            if ($roots -isnot [Collections.IDictionary] -or $roots.Count -ne 7) { return $false }
            foreach ($name in @('versions', 'snapshots', 'state', 'run', 'logs', 'cache', 'launchers')) {
                $entry = $roots[$name]
                if ($entry -isnot [Collections.IDictionary] -or $entry.Count -ne 2) { return $false }
                $lexical = Get-PreparedWorktreesPath $entry['path']
                $resolved = Get-PreparedWorktreesPath $entry['resolved']
                if (-not [string]::Equals($lexical, $resolved, [StringComparison]::Ordinal) -or
                    -not $lexical.StartsWith($registry + '\', [StringComparison]::Ordinal)) {
                    return $false
                }
            }
        }
        if ($sources.Count -ne $expectedSources.Count) { return $false }
        foreach ($path in $sources.Keys) {
            $canonical = Get-PreparedWorktreesPath $path
            if (-not $expectedSources.ContainsKey($canonical) -or $expectedSources[$canonical] -or
                -not $inputs.ContainsKey($path) -or
                -not (Test-PreparedWorktreesStamp $path $sources[$path])) { return $false }
            if ($context -and $null -eq $sources[$path] -and
                $canonical -cnotin @(
                    [IO.Path]::Combine($registry, 'projects.yaml'),
                    [IO.Path]::Combine($registry, 'repos.yaml')
                )) { return $false }
            $expectedSources[$canonical] = $true
            $source = ConvertTo-PreparedWorktreesJson -InputObject $sources[$path]
            $inputStamp = ConvertTo-PreparedWorktreesJson -InputObject $inputs[$path]
            if (-not [string]::Equals($source, $inputStamp, [StringComparison]::Ordinal)) { return $false }
        }
        $hasGitMarker = $false
        foreach ($path in $inputs.Keys) {
            if ($path -isnot [string] -or
                -not (Test-PreparedWorktreesStamp $path $inputs[$path])) { return $false }
            if ([string]::Equals(
                (Get-PreparedWorktreesPath $path), [IO.Path]::Combine($root, '.git'),
                [StringComparison]::Ordinal)) {
                $hasGitMarker = $true
            }
        }
        return $hasGitMarker
    } catch {
        return $false
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Test-ManagedWorktreesSession {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Raw, [ref]$Prepared)
    if ($null -ne $Prepared) { $Prepared.Value = $false }
    if (Test-PreparedManagedWorktreesSession -Raw $Raw) {
        if ($null -ne $Prepared) { $Prepared.Value = $true }
        return $true
    }
    $helper = Join-Path $PSScriptRoot 'managed_session.py'
    if (-not [IO.File]::Exists($helper)) { return $false }
    $process = $null
    try {
        if (-not $Raw.TrimStart().StartsWith('{')) { return $false }
        $event = ConvertFrom-ManagedWorktreesJson $Raw
        $query = @{}
        foreach ($name in @('sessionId', 'cwd', 'workingDirectory')) {
            foreach ($key in $event.get_Keys()) {
                if ($key -ceq $name) {
                    $query[$name] = $event[$key]
                    break
                }
            }
        }
        $frame = if ($PSVersionTable.PSVersion.Major -ge 6) {
            ConvertTo-PreparedWorktreesJson -InputObject $query -Depth 2
        } else { ConvertTo-Json -InputObject $query -Compress }
        $python = Get-ManagedWorktreesPython
        if (-not $python) { return $false }
        $info = [Diagnostics.ProcessStartInfo]::new()
        $info.FileName = $python
        $info.Arguments = '-I -S -B -X utf8 "' + $helper + '" --base64-input'
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardInput = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $utf8 = [Text.UTF8Encoding]::new($false)
        $info.StandardOutputEncoding = $utf8
        $info.StandardErrorEncoding = $utf8
        $process = [Diagnostics.Process]::Start($info)
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $bytes = $utf8.GetBytes([Convert]::ToBase64String($utf8.GetBytes($frame)) + "`n")
        $writing = $process.StandardInput.BaseStream.WriteAsync($bytes, 0, $bytes.Length)
        if (-not $writing.Wait(2000) -or
            -not $process.StandardInput.BaseStream.FlushAsync().Wait(2000)) { return $false }
        if (-not $process.WaitForExit(2000) -or
            -not [Threading.Tasks.Task]::WaitAll(@($stdout, $stderr), 1000) -or
            $process.ExitCode -ne 0 -or $stdout.Result.Length -gt 65536) { return $false }
        $result = ConvertFrom-ManagedWorktreesJson $stdout.Result
        return $result.admitted -is [bool] -and $result.admitted
    } catch {
        return $false
    } finally {
        if ($process) {
            try { $process.StandardInput.Close() } catch {}
            if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
            $process.Dispose()
        }
    }
}
