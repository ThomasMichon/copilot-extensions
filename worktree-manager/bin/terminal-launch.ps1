function Invoke-WorktreeTerminalLaunch {
    param(
        [Parameter(Mandatory)][string]$Executable,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$Arguments
    )

    # Nested dispatchers share the outer terminal's one-shot return channel.
    if ($env:CE_TERMINAL_LAUNCH -or [Console]::IsInputRedirected) {
        & $Executable @Arguments
        return
    }

    $directory = Join-Path ([IO.Path]::GetTempPath()) (
        'ce-terminal-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss') + '-' +
        [Guid]::NewGuid().ToString('N')
    )
    $nonce = [Guid]::NewGuid().ToString('N')
    $savedEnvironment = @{}
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
        foreach ($name in @('WORKTREE_MANAGER_ENGINE_ARGV', 'WORKTREE_MANAGER_ROOT', 'WORKTREE_NO_MUX')) {
            $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
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
        foreach ($name in $savedEnvironment.Keys) {
            if ($null -eq $savedEnvironment[$name]) {
                Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue
            } else {
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
