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
    if ($PSVersionTable.PSVersion.Major -ge 6) {
        return ConvertFrom-Json -InputObject $Text -AsHashtable -ErrorAction Stop
    }
    if (-not $script:managedWorktreesJson) {
        $null = ConvertFrom-Json -InputObject '{}' -ErrorAction Stop
        [void][Reflection.Assembly]::Load('System.Web.Extensions, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35')
        # Retain the shell's metadata semantics without projecting keys onto PSObject.
        $resolverType = [Microsoft.PowerShell.Commands.JsonObject].Assembly.GetType(
            'Microsoft.PowerShell.Commands.JsonObjectTypeResolver', $true)
        $resolver = [Activator]::CreateInstance($resolverType, $true)
        $script:managedWorktreesJson = [System.Web.Script.Serialization.JavaScriptSerializer]::new($resolver)
        $script:managedWorktreesJson.MaxJsonLength = [int]::MaxValue
        $script:managedWorktreesJson.RecursionLimit = 100
    }
    return ,($script:managedWorktreesJson.DeserializeObject($Text))
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
        $Stamp[1] -cne $canonical) { return $false }
    if ($attributes -band [IO.FileAttributes]::Directory) {
        if ($Stamp[0] -ceq 'directory') { return $Stamp.Count -eq 2 }
        if ($Stamp[0] -cne 'listing' -or $Stamp.Count -ne 3) { return $false }
        $mtime = [IO.Directory]::GetLastWriteTimeUtc($Path)
    } else {
        if ($Stamp[0] -cne 'file' -or $Stamp.Count -ne 4 -or
            $Stamp[2] -isnot [string] -or
            $Stamp[2] -cne ([IO.FileInfo]::new($Path)).Length.ToString(
                [Globalization.CultureInfo]::InvariantCulture)) { return $false }
        $mtime = [IO.File]::GetLastWriteTimeUtc($Path)
    }
    # Python's Windows stat uses the same 100 ns filesystem timestamp.
    $nanoseconds = ($mtime.Ticks - [long]621355968000000000) * [long]100
    return $Stamp[-1] -is [string] -and $Stamp[-1] -ceq $nanoseconds.ToString(
        [Globalization.CultureInfo]::InvariantCulture)
}

function Test-PreparedManagedWorktreesSession {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Raw)
    $stream = $null
    try {
        if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
            $env:COPILOT_EXTENSIONS_CONTEXT -or $Raw.Length -gt 1048576) { return $false }
        $event = ConvertFrom-ManagedWorktreesJson $Raw
        if ($event -isnot [Collections.IDictionary]) { return $false }
        $session = if ($event.ContainsKey('sessionId')) {
            $event['sessionId']
        } else { $env:COPILOT_AGENT_SESSION_ID }
        $cwd = if ($event.ContainsKey('cwd')) {
            $event['cwd']
        } elseif ($event.ContainsKey('workingDirectory')) {
            $event['workingDirectory']
        } else { $env:COPILOT_PROJECT_DIR }
        if ($session -isnot [string] -or
            $session -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') { return $false }
        $workspace = Get-PreparedWorktreesPath $cwd
        if (-not [IO.Directory]::Exists($workspace)) { return $false }
        $userHome = Get-PreparedWorktreesPath $env:USERPROFILE
        $registryHome = if ($env:AGENT_HOME -and $env:AGENT_HOME.Trim()) {
            $env:AGENT_HOME.Trim()
        } else { $env:USERPROFILE }
        $registry = Get-PreparedWorktreesPath ([IO.Path]::Combine($registryHome, '.agent-worktrees'))
        $marker = [IO.Path]::Combine(
            $userHome, '.copilot', 'session-state', $session, 'agent-worktrees', 'managed-session.json'
        )
        $null = Get-PreparedWorktreesPath $marker
        if ([IO.File]::GetAttributes($marker) -band [IO.FileAttributes]::Directory) { return $false }
        $stream = [IO.FileStream]::new(
            $marker, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
        )
        $bytes = [byte[]]::new(1048577)
        $count = 0
        while ($count -lt $bytes.Length) {
            $read = $stream.Read($bytes, $count, $bytes.Length - $count)
            if ($read -eq 0) { break }
            $count += $read
        }
        if ($count -gt 1048576) { return $false }
        $text = ([Text.UTF8Encoding]::new($false, $true)).GetString($bytes, 0, $count)
        $value = ConvertFrom-ManagedWorktreesJson $text.TrimStart([char]0xfeff)
        if ($value -isnot [Collections.IDictionary] -or
            ($value['version'] -isnot [int] -and $value['version'] -isnot [long]) -or
            $value['version'] -ne 1) { return $false }
        foreach ($name in @('scope', 'identity', 'sources', 'inputs')) {
            if ($value[$name] -isnot [Collections.IDictionary]) { return $false }
        }
        $scope = $value['scope']
        if ($scope.Count -ne 5 -or $scope['session'] -isnot [string] -or
            $scope['session'] -cne $session -or $scope['workspace'] -isnot [string] -or
            $scope['workspace'] -cne $workspace -or $scope['registry'] -isnot [string] -or
            $scope['registry'] -cne $registry -or $scope['context'] -isnot [string] -or
            $scope['context'] -cne '' -or $scope['platform'] -isnot [string] -or
            $scope['platform'] -cne 'windows') { return $false }
        $identity = $value['identity']
        foreach ($name in @('root', 'git_dir', 'common_dir')) {
            $null = Get-PreparedWorktreesPath $identity[$name]
        }
        $root = $identity['root']
        if ($workspace -cne $root -and -not $workspace.StartsWith($root + '\', [StringComparison]::Ordinal)) {
            return $false
        }
        $inputs = $value['inputs']
        $sources = $value['sources']
        if ($inputs.Count -gt 256 -or $sources.Count -ne 2) { return $false }
        $expectedSources = @{
            ([IO.Path]::Combine($registry, 'projects.yaml')) = $false
            ([IO.Path]::Combine($registry, 'repos.yaml')) = $false
        }
        foreach ($path in $sources.Keys) {
            $canonical = Get-PreparedWorktreesPath $path
            if (-not $expectedSources.ContainsKey($canonical) -or $expectedSources[$canonical] -or
                -not $inputs.ContainsKey($path) -or
                -not (Test-PreparedWorktreesStamp $path $sources[$path])) { return $false }
            $expectedSources[$canonical] = $true
            $source = ConvertTo-Json -InputObject $sources[$path] -Compress
            $inputStamp = ConvertTo-Json -InputObject $inputs[$path] -Compress
            if ($source -cne $inputStamp) { return $false }
        }
        $hasGitMarker = $false
        foreach ($path in $inputs.Keys) {
            if ($path -isnot [string] -or
                -not (Test-PreparedWorktreesStamp $path $inputs[$path])) { return $false }
            if ((Get-PreparedWorktreesPath $path) -ceq [IO.Path]::Combine($root, '.git')) {
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
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Raw)
    if (Test-PreparedManagedWorktreesSession -Raw $Raw) { return $true }
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
        $frame = ConvertTo-Json -InputObject $query -Compress
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
