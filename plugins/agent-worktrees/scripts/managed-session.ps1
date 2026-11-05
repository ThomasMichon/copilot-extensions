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

function Test-ManagedWorktreesSession {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Raw)
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
