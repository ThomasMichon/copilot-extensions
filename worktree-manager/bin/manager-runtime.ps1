param([switch]$Companion)

function Get-ManagerRuntimeInvocation {
    param([string]$PayloadRoot, [string[]]$ModuleArguments)
    $slot = Split-Path -Parent $PayloadRoot
    $versions = Split-Path -Parent $slot
    $environment = @{}
    $clearEnvironment = @()
    if ((Split-Path -Leaf $PayloadRoot) -eq 'payload' -and (Split-Path -Leaf $versions) -eq 'versions') {
        $python = Join-Path $slot 'Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $python)) { $python = Join-Path $slot 'bin/python' }
        $bootstrap = Join-Path $slot '.runtime-bootstrap/runtime_launcher.py'
        if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $bootstrap) -or
            -not (Test-Path -LiteralPath (Join-Path $slot '.install-complete.json'))) {
            throw 'Manager invocation requires a constructed runtime'
        }
        $fileName = $python
        $commandArguments = @('-I', '-S', '-B', '-X', 'utf8', $bootstrap)
        $clearEnvironment = @('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', '__PYVENV_LAUNCHER__')
        $environment['WORKTREE_MANAGER_ROOT'] = Split-Path -Parent $versions
    } else {
        $fileName = (Get-Command uv -CommandType Application -ErrorAction Stop).Source
        $commandArguments = @('run', '--quiet', '--project', $PayloadRoot)
    }
    [pscustomobject]@{
        FileName = $fileName
        Arguments = @($commandArguments) + $ModuleArguments
        Environment = $environment
        ClearEnvironment = $clearEnvironment
    }
}

function Invoke-ManagerMuxCallback {
    param([string]$PayloadRoot, [string[]]$CallbackArguments)
    $invocation = Get-ManagerRuntimeInvocation -PayloadRoot $PayloadRoot `
        -ModuleArguments (@('-m', 'worktree_manager.mux_callback') + $CallbackArguments)
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $invocation.FileName
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($name in $invocation.ClearEnvironment) { $null = $startInfo.Environment.Remove($name) }
    foreach ($name in $invocation.Environment.Keys) { $startInfo.Environment[$name] = $invocation.Environment[$name] }
    foreach ($argument in $invocation.Arguments) { $startInfo.ArgumentList.Add([string]$argument) }
    $process = [Diagnostics.Process]::Start($startInfo)
    try {
        $process.StandardInput.Close()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        [Threading.Tasks.Task]::WaitAll($stdout, $stderr)
        if ($process.ExitCode -ne 0) { throw "Manager callback dispatch failed: $($stderr.Result)" }
    } finally {
        $process.Dispose()
    }
}

if ($Companion) {
    $invocation = Get-ManagerRuntimeInvocation -PayloadRoot (Split-Path -Parent $PSScriptRoot) `
        -ModuleArguments @('-m', 'worktree_manager', 'companion')
    foreach ($name in $invocation.ClearEnvironment) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
    foreach ($name in $invocation.Environment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $invocation.Environment[$name], 'Process')
    }
    # This is the popup's foreground TUI: inherit its terminal and every stream.
    & $invocation.FileName @($invocation.Arguments)
    exit $LASTEXITCODE
}
