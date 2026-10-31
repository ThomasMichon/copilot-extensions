# Native admission wrapper for the legacy bootstrap-check driver submission.
$ErrorActionPreference = 'Stop'
function Write-SessionStartJson {
    [Console]::Out.Write('{}')
}
try {
    try {
        $mode = & (Join-Path $PSScriptRoot 'payload-mode.ps1')
    } catch {
        [Console]::Error.WriteLine("Bootstrap admission refused: $($_.Exception.Message)")
        exit 126
    }
    if ($mode -ceq 'legacy') {
        # Console.Out bypasses PowerShell pipelines. Capture the child's real
        # stdout while leaving stderr inherited and its failure status intact.
        $start = New-Object System.Diagnostics.ProcessStartInfo
        $start.FileName = (Get-Process -Id $PID).Path
        $start.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
            (Join-Path $PSScriptRoot 'bootstrap-check.ps1') + '"'
        $location = Get-Location
        if ($location.Provider.Name -cne 'FileSystem') {
            throw 'Bootstrap requires a filesystem project location.'
        }
        $start.WorkingDirectory = $location.ProviderPath
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.RedirectStandardOutput = $true
        $child = [System.Diagnostics.Process]::Start($start)
        try {
            $drain = $child.StandardOutput.BaseStream.CopyToAsync([IO.Stream]::Null)
            $child.WaitForExit()
            [void]$drain.GetAwaiter().GetResult()
            $code = $child.ExitCode
        } finally {
            $child.Dispose()
        }
        exit $code
    }
    if ($mode -cne 'cell') { throw 'Native installation admission returned an unknown mode' }
    # Provision and activation remain on demand, not session start.
} finally { Write-SessionStartJson }
