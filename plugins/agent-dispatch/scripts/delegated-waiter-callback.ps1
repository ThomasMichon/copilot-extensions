$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
if ($args.Count -ne 1 -or $args[0] -cnotmatch '^hex:[0-9a-f]+$') {
    [Console]::Error.WriteLine('agent-dispatch: invalid delegated callback arguments')
    exit 1
}
$envelope = $args[0]
$env:AGENT_RT_ROOT = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'resolve-runtime.ps1') -TraceMode read-only
if (-not $AgentRtPy) {
    [Console]::Error.WriteLine('agent-dispatch: delegated callback runtime unavailable')
    exit 1
}
$start = New-Object Diagnostics.ProcessStartInfo
$start.FileName = $AgentRtPy
$start.Arguments = '-I -X utf8 -m agent_dispatch _delegated-waiter-callback ' + $envelope
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$child = [Diagnostics.Process]::Start($start)
try {
    $output = $child.StandardOutput.BaseStream.CopyToAsync([Console]::OpenStandardOutput())
    $errors = $child.StandardError.BaseStream.CopyToAsync([Console]::OpenStandardError())
    $inputCopy = [Console]::OpenStandardInput().CopyToAsync($child.StandardInput.BaseStream)
    $inputCopy.GetAwaiter().GetResult()
    $child.StandardInput.Close()
    $child.WaitForExit()
    $output.GetAwaiter().GetResult()
    $errors.GetAwaiter().GetResult()
    $code = $child.ExitCode
} finally {
    $child.Dispose()
}
exit $code
