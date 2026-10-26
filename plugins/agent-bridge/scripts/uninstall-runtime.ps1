function Invoke-FiniteRuntimeUninstall {
    param([string]$InstallRoot, [switch]$Check)
    $helper = Join-Path $PSScriptRoot 'installer_engine_uninstall.py'
    if (-not (Test-Path -LiteralPath $helper)) {
        $helper = Join-Path $PSScriptRoot '..\..\..\libs\installer-engine\installer_engine_uninstall.py'
    }
    $python = $null
    foreach ($name in @('python', 'python3', 'py')) {
        foreach ($candidate in @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue)) {
            if ((Get-Item -LiteralPath $candidate.Source -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
            if ($candidate.Source.StartsWith($InstallRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { continue }
            $prefix = if ($name -eq 'py') { @('-3') } else { @() }
            $output = Invoke-Hidden -FilePath $candidate.Source -ArgList ($prefix + @('-I', '-S', '-c', 'import sys; print(sys.executable)'))
            if ($LASTEXITCODE -eq 0 -and $output.Trim() -and -not $output.Trim().StartsWith(
                    $InstallRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
                $python = $output.Trim()
                break
            }
        }
        if ($python) { break }
    }
    if (-not $python) { throw 'Runtime uninstall requires an external metadata Python interpreter' }
    $mode = if ($Check) { '--check' } else { '--remove-runtime' }
    $output = Invoke-Hidden -FilePath $python -ArgList @(
        '-I', '-B', $helper, '--payload-root', (Split-Path -Parent $PSScriptRoot),
        '--plugin-id', 'agent-bridge', '--root', $InstallRoot,
        '--shell', (Get-Process -Id $PID).Path, $mode)
    if ($LASTEXITCODE -ne 0) { throw 'Finite runtime uninstall refused; see native diagnostic' }
    if ($output) { Write-Host $output.Trim() }
}
