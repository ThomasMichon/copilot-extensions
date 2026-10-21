function Invoke-ManagerMuxCallback {
    param([string]$PayloadRoot, [string[]]$CallbackArguments)
    $slot = Split-Path -Parent $PayloadRoot
    $versions = Split-Path -Parent $slot
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    if ((Split-Path -Leaf $PayloadRoot) -eq 'payload' -and (Split-Path -Leaf $versions) -eq 'versions') {
        $python = Join-Path $slot 'Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $python)) { $python = Join-Path $slot 'bin/python' }
        $bootstrap = Join-Path $slot '.runtime-bootstrap/runtime_launcher.py'
        if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $bootstrap) -or
            -not (Test-Path -LiteralPath (Join-Path $slot '.install-complete.json'))) {
            throw 'mux callback requires a constructed Manager runtime'
        }
        $startInfo.FileName = $python
        $commandArguments = @('-I', '-S', '-B', '-X', 'utf8', $bootstrap)
        foreach ($name in @('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', '__PYVENV_LAUNCHER__')) {
            $null = $startInfo.Environment.Remove($name)
        }
        $startInfo.Environment['WORKTREE_MANAGER_ROOT'] = Split-Path -Parent $versions
    } else {
        $startInfo.FileName = (Get-Command uv -CommandType Application -ErrorAction Stop).Source
        $commandArguments = @('run', '--quiet', '--project', $PayloadRoot)
    }
    $commandArguments += @('-m', 'worktree_manager.mux_callback') + $CallbackArguments
    foreach ($argument in $commandArguments) { $startInfo.ArgumentList.Add([string]$argument) }
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
