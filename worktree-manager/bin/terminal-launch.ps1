function Get-WorktreeTerminalCapability {
    param([string]$Executable, [string[]]$QueryArguments, [string]$Package)
    if (-not $QueryArguments.Count -or $Package -notin @('agent_worktrees', 'worktree_manager')) {
        throw 'Selected runtime terminal query is unavailable.'
    }
    $helper = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\scripts\daemon_instances.ps1'))
    if (-not [IO.File]::Exists($helper)) { throw 'Native terminal query owner is unavailable.' }
    . $helper
    $environment = @{}
    foreach ($entry in [Environment]::GetEnvironmentVariables().GetEnumerator()) {
        if ([string]$entry.Key -ine 'CE_TERMINAL_LAUNCH') {
            $environment[[string]$entry.Key] = [string]$entry.Value
        }
    }
    $environment['CE_TERMINAL_CAPABILITY_QUERY'] = '1'
    $environment['UV_NO_SYNC'] = '1'
    $environment['UV_OFFLINE'] = '1'
    $environment['WORKTREE_NO_UPDATE'] = '1'
    $environment['AGENT_WORKTREES_NO_SELFPROVISION'] = '1'
    $application = @(Get-Command $Executable -CommandType Application -ErrorAction Stop)[0].Path
    $result = Invoke-DaemonInstanceRequest -Transient @{command='run-query'; parameters=@{
        executable=$application; arguments=@($QueryArguments)
        cwd=[IO.Directory]::GetCurrentDirectory(); environment=$environment
        timeout_ms=10000; limit=4096
    }}
    if ($result.returncode -ne 0 -or $result.stderr) {
        throw 'Selected runtime terminal capability query failed; no launch was admitted.'
    }
    $text = $result.stdout.Trim()
    $legacy = if ($Package -ceq 'worktree_manager') {
        '\Aworktree-manager [0-9]+\.[0-9]+\.[0-9]+(?:-dev[0-9]+)?\z'
    } else {
        '\Aagent-worktrees [0-9?]+\.[0-9?]+\.[0-9?]+(?:-dev[0-9]+)?  ' +
        'commit (?:[0-9a-f]{1,10}|unknown)  branch [^\r\n]+  built [^\r\n]+' +
        '(?:\r?\ndeployed [^\r\n]+)?\z'
    }
    if ($text -cmatch $legacy) { return $false }
    $response = $text | ConvertFrom-Json -ErrorAction Stop
    $propertyCount = if ($Package -ceq 'worktree_manager') { 3 } else { 2 }
    if ($response.package -cne $Package -or $response.capability -cne 'ce-terminal-launch-v1' -or
        @($response.PSObject.Properties).Count -ne $propertyCount) {
        throw 'Selected runtime returned an invalid terminal capability.'
    }
    if ($Package -ceq 'worktree_manager') {
        $binding = $response.runtime
        $command = $binding.command
        if ($null -eq $binding -or @($binding.PSObject.Properties).Count -ne 2 -or
            @($binding.PSObject.Properties.Name | Where-Object { $_ -cnotin @('command', 'root') }).Count -or
            $command -isnot [array] -or $command.Count -notin @(7, 9) -or
            @($command | Where-Object { $_ -isnot [string] -or -not $_ -or $_.Contains([char]0) }).Count -or
            -not [IO.Path]::IsPathRooted($command[0]) -or -not [IO.File]::Exists($command[0]) -or
            ($command[1..4] -join ',') -cne '-I,-B,-X,utf8' -or
            ($command[-2..-1] -join ',') -cne '-m,worktree_manager' -or
            ($null -ne $binding.root -and
                ($binding.root -isnot [string] -or $binding.root.Contains([char]0) -or
                    -not [IO.Path]::IsPathRooted($binding.root)))) {
            throw 'Selected runtime returned an invalid runtime binding.'
        }
        if ($command.Count -eq 9) {
            $bootstrap = $command[6]
            $directory = [IO.Path]::GetDirectoryName($bootstrap)
            $slot = [IO.Path]::GetDirectoryName($directory)
            if ($command[5] -cne '-S' -or -not [IO.Path]::IsPathRooted($bootstrap) -or
                -not [IO.File]::Exists($bootstrap) -or
                [IO.Path]::GetFileName($bootstrap) -cne 'runtime_launcher.py' -or
                [IO.Path]::GetFileName($directory) -cne '.runtime-bootstrap' -or
                [IO.Path]::GetFileName([IO.Path]::GetDirectoryName($slot)) -cne 'versions' -or
                -not [IO.File]::Exists((Join-Path $slot '.install-complete.json'))) {
                throw 'Selected runtime is no longer available.'
            }
        }
    }
    return $true
}

function Invoke-WorktreeTerminalLaunch {
    param(
        [Parameter(Mandatory)][string]$Executable,
        [Parameter(Mandatory)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Arguments,
        [string[]]$QueryArguments = @(),
        [string]$Package = ''
    )

    # Native failures remain exit results, matching the previous child shell.
    $PSNativeCommandUseErrorActionPreference = $false

    $inherited = $env:CE_TERMINAL_LAUNCH
    Remove-Item Env:CE_TERMINAL_LAUNCH -ErrorAction SilentlyContinue
    if ([Console]::IsInputRedirected -and -not $inherited) {
        & $Executable @Arguments
        return
    }
    try {
        if ($inherited) {
            $item = Get-Item -LiteralPath $inherited -ErrorAction Stop
            if (-not [IO.Path]::IsPathRooted($inherited) -or $item.Name -cne 'request.json' -or
                $item.PSIsContainer -or $item.Length -gt 4096 -or
                ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw 'Invalid inherited terminal launch request.'
            }
            $identity = [IO.File]::ReadAllText($inherited) | ConvertFrom-Json
            if (($identity.version -isnot [int] -and $identity.version -isnot [long]) -or
                $identity.version -cne 1 -or $identity.nonce -isnot [string] -or
                $identity.nonce -cnotmatch '\A[0-9a-f]{32}\z' -or
                ($identity.host_pid -isnot [int] -and $identity.host_pid -isnot [long]) -or
                $identity.host_pid -le 0) { throw 'Invalid inherited terminal launch identity.' }
        }
        $supported = Get-WorktreeTerminalCapability $Executable $QueryArguments $Package
        if ($inherited -or -not $supported) {
            try {
                if ($supported) { $env:CE_TERMINAL_LAUNCH = $inherited }
                & $Executable @Arguments
            } finally { Remove-Item Env:CE_TERMINAL_LAUNCH -ErrorAction SilentlyContinue }
            return
        }
    } catch {
        [Console]::Error.WriteLine("Terminal query admission failed: $($_.Exception.Message)")
        $global:LASTEXITCODE = 1
        return
    }

    $directory = Join-Path ([IO.Path]::GetTempPath()) (
        'ce-terminal-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss') + '-' +
        [Guid]::NewGuid().ToString('N')
    )
    $nonce = [Guid]::NewGuid().ToString('N')
    $savedEnvironment = @{}
    $restoreEnvironment = $false
    try {
        [IO.Directory]::CreateDirectory($directory) | Out-Null
        $request = Join-Path $directory 'request.json'
        [IO.File]::WriteAllText($request, (@{
            version = 1; nonce = $nonce; host_pid = $PID
        } | ConvertTo-Json -Compress))
        $env:CE_TERMINAL_LAUNCH = $request
        & $Executable @Arguments
        $prepareExit = $LASTEXITCODE
        Remove-Item Env:CE_TERMINAL_LAUNCH -ErrorAction SilentlyContinue
        $resultPath = Join-Path $directory 'result.json'
        if ($prepareExit -ne 0 -or -not (Test-Path -LiteralPath $resultPath)) {
            $global:LASTEXITCODE = $prepareExit
            return
        }
        $stream = [IO.FileStream]::new(
            $resultPath, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            [IO.FileShare]::None, 4096, [IO.FileOptions]::DeleteOnClose
        )
        try {
            if ($stream.Length -gt 65536) { throw 'Prepared terminal launch is too large.' }
            $reader = [IO.StreamReader]::new($stream)
            $result = $reader.ReadToEnd() | ConvertFrom-Json
        } finally {
            if ($reader) { $reader.Dispose() } else { $stream.Dispose() }
        }
        if ($result.version -cne 1 -or $result.nonce -cne $nonce -or
            $result.script -isnot [string] -or
            -not [IO.Path]::IsPathRooted($result.script) -or
            -not (Test-Path -LiteralPath $result.script -PathType Leaf) -or
            $result.arguments -isnot [array] -or
            @($result.arguments | Where-Object { $_ -isnot [string] -or $_.Contains([char]0) }).Count -or
            $result.completion -isnot [array] -or
            @($result.completion | Where-Object { $_ -isnot [string] -or $_.Contains([char]0) }).Count -or
            ($result.completion.Count -gt 0 -and
                -not [IO.Path]::IsPathRooted($result.completion[0])) -or
            $result.cwd -isnot [string] -or
            -not [IO.Path]::IsPathRooted($result.cwd) -or
            -not (Test-Path -LiteralPath $result.cwd -PathType Container)) {
            throw 'Invalid prepared terminal launch.'
        }
        [IO.File]::Delete($request)
        [IO.Directory]::Delete($directory)
        foreach ($item in Get-ChildItem Env:) {
            $savedEnvironment[$item.Name] = $item.Value
        }
        $restoreEnvironment = $true
        foreach ($name in @('WORKTREE_MANAGER_ENGINE_ARGV', 'WORKTREE_MANAGER_ROOT', 'WORKTREE_NO_MUX')) {
            $value = $result.environment.$name
            if ($null -ne $value -and $value -isnot [string]) {
                throw 'Invalid prepared terminal environment.'
            }
            if ($null -eq $value) {
                Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue
            } else {
                Set-Item -LiteralPath ('Env:' + $name) -Value $value
            }
        }
        $savedLocation = Get-Location
        $savedNativeDirectory = [IO.Directory]::GetCurrentDirectory()
        try {
            Set-Location -LiteralPath $result.cwd
            [IO.Directory]::SetCurrentDirectory($result.cwd)
            # Invoke in this PowerShell host, not in another Python waiter.
            $launchArguments = @($result.arguments)
            & $result.script @launchArguments
            $launchExit = $LASTEXITCODE
        } finally {
            Set-Location -LiteralPath $savedLocation.Path
            [IO.Directory]::SetCurrentDirectory($savedNativeDirectory)
        }
        if ($result.completion.Count) {
            $completion = @($result.completion)
            try {
                $completionArguments = @($completion | Select-Object -Skip 1) + @([string]$launchExit)
                & $completion[0] @completionArguments
                if ($LASTEXITCODE -ne 0) { throw 'Completion observer returned nonzero.' }
            } catch {
                [Console]::Error.WriteLine('Could not retain terminal launch outcome; do not retry automatically.')
            }
        }
        $global:LASTEXITCODE = $launchExit
    } catch {
        [Console]::Error.WriteLine("Terminal launch handoff failed: $($_.Exception.Message)")
        $global:LASTEXITCODE = 1
    } finally {
        Remove-Item Env:CE_TERMINAL_LAUNCH -ErrorAction SilentlyContinue
        if ($restoreEnvironment) {
            foreach ($item in Get-ChildItem Env:) {
                if (-not $savedEnvironment.ContainsKey($item.Name)) {
                    Remove-Item -LiteralPath ('Env:' + $item.Name) -ErrorAction Stop
                }
            }
            foreach ($name in $savedEnvironment.Keys) {
                Set-Item -LiteralPath ('Env:' + $name) -Value $savedEnvironment[$name]
            }
        }
        foreach ($name in @('result.json', 'request.json')) {
            $path = Join-Path $directory $name
            if (Test-Path -LiteralPath $path) { [IO.File]::Delete($path) }
        }
        if (Test-Path -LiteralPath $directory) { [IO.Directory]::Delete($directory) }
    }
}
