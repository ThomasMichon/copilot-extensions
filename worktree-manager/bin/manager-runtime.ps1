param(
    [switch]$Companion,
    [switch]$CompanionPane,
    [ValidateSet('show', 'return')][string]$Action,
    [string]$Cwd
)

function Get-ManagerRuntimeSlot {
    param([string]$PayloadRoot)
    $slot = Split-Path -Parent $PayloadRoot
    $versions = Split-Path -Parent $slot
    if ((Split-Path -Leaf $PayloadRoot) -eq 'payload' -and (Split-Path -Leaf $versions) -eq 'versions') {
        return $slot
    }
    if ((Split-Path -Leaf $PayloadRoot) -ne '_runtime_assets') { return $null }
    $directory = [IO.DirectoryInfo]::new([IO.Path]::GetFullPath($PayloadRoot))
    while ($directory.Parent -and $directory.Parent.Name -ne 'versions') { $directory = $directory.Parent }
    if ($directory.Parent -and $directory.Parent.Name -eq 'versions') {
        $prefix = if (Test-Path -LiteralPath (Join-Path $directory.FullName 'application.json') -PathType Leaf) {
            Join-Path $directory.FullName '.venv'
        } else { $directory.FullName }
        if (Test-Path -LiteralPath (Join-Path $prefix 'pyvenv.cfg') -PathType Leaf) {
            return $directory.FullName
        }
    }
    return $null
}

function Get-ManagerRuntimeInvocation {
    param([string]$PayloadRoot, [string[]]$ModuleArguments, [switch]$RequireInstalled)
    $slot = Get-ManagerRuntimeSlot -PayloadRoot $PayloadRoot
    $environment = @{}
    $clearEnvironment = @()
    if ($slot) {
        $versions = Split-Path -Parent $slot
        $prefix = if (Test-Path -LiteralPath (Join-Path $slot 'application.json') -PathType Leaf) {
            Join-Path $slot '.venv'
        } else { $slot }
        $python = Join-Path $prefix 'Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $python)) { $python = Join-Path $prefix 'bin/python' }
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
        if ($RequireInstalled -or (Split-Path -Leaf $PayloadRoot) -eq '_runtime_assets') {
            throw 'Companion pane requires a constructed Manager runtime'
        }
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

if ($CompanionPane) {
    if (-not $Action -or -not $Cwd) { throw 'Companion pane requires Action and Cwd' }
    $invocation = Get-ManagerRuntimeInvocation -PayloadRoot (Split-Path -Parent $PSScriptRoot) `
        -RequireInstalled -ModuleArguments @('-m', 'worktree_manager', 'companion-pane', '--action', $Action, '--cwd', $Cwd)
    # Native launch uses PowerShell's Env: cache; .NET-only changes can
    # resurrect inherited values when the child environment is assembled.
    foreach ($name in @($invocation.ClearEnvironment) + @('GH_TOKEN', 'GITHUB_TOKEN', 'AGENT_WORKTREES_AHP_AUTH_TOKEN')) {
        Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue
    }
    foreach ($name in $invocation.Environment.Keys) {
        Set-Item -LiteralPath ('Env:' + $name) -Value $invocation.Environment[$name]
    }
    & $invocation.FileName @($invocation.Arguments)
    exit $LASTEXITCODE
}

if ($Companion) {
    $invocation = Get-ManagerRuntimeInvocation -PayloadRoot (Split-Path -Parent $PSScriptRoot) `
        -ModuleArguments @('-m', 'worktree_manager', 'companion')
    foreach ($name in $invocation.ClearEnvironment) {
        Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue
    }
    foreach ($name in $invocation.Environment.Keys) {
        Set-Item -LiteralPath ('Env:' + $name) -Value $invocation.Environment[$name]
    }
    # This is the popup's foreground TUI: inherit its terminal and every stream.
    & $invocation.FileName @($invocation.Arguments)
    exit $LASTEXITCODE
}
