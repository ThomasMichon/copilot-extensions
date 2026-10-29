function Enter-NativeInstallerDriver {
    param(
        [Parameter(Mandatory)][string]$Payload,
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$Plugin,
        [Parameter(Mandatory)][string]$Action,
        [string[]]$Options = @(),
        [string]$RetainedRuntime,
        [string]$RetainedRuntimeSha256
    )
    $probe = @'
import json,sys
from pathlib import Path
assert sys.version_info >= (3,11)
python = Path(sys._base_executable).resolve(strict=True)
if sys.argv[1].lower() in {'uninstall', 'broker-remove'}:
    root = Path(sys.argv[2]).absolute()
    assert '..' not in root.parts
    assert all(not python.is_relative_to(boundary) for boundary in (root, root.resolve()))
print(json.dumps(str(python)))
'@
    $python = $null
    $candidates = @()
    foreach ($name in @('python3', 'python')) {
        $candidates += @(Get-Command $name -All -CommandType Application -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notmatch '[\\/]WindowsApps[\\/]' } |
            ForEach-Object { $_.Source })
    }
    foreach ($launcher in @(Get-Command py -All -CommandType Application -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notmatch '[\\/]WindowsApps[\\/]' })) {
        try {
            $nativeError = ''
            $listed = Invoke-Hidden -FilePath $launcher.Source -ArgList @('-0p') -ErrorOutput ([ref]$nativeError)
            if ($LASTEXITCODE -eq 0) {
                foreach ($match in [regex]::Matches("$listed", '(?m)([A-Za-z]:[\\/].*python(?:[0-9.]*)?\.exe)"?\s*$')) {
                    $candidates += $match.Groups[1].Value
                }
            }
        } catch { }
    }
    foreach ($candidate in @($candidates | Select-Object -Unique)) {
        try {
            $nativeError = ''
            $selected = Invoke-Hidden -FilePath $candidate -ArgList @(
                '-I', '-S', '-B', '-c', $probe, $Action, $Root
            ) -ErrorOutput ([ref]$nativeError)
            if ($LASTEXITCODE -eq 0) {
                $python = ($selected | Out-String) | ConvertFrom-Json
                if ($python -and [IO.File]::Exists($python)) { break }
                $python = $null
            }
        } catch { $python = $null }
    }
    if (-not $python) { throw 'Native install and uninstall require an existing Python 3.11+ bootstrap interpreter; removal requires it outside the runtime' }
    $arguments = @('-I', '-S', '-B', '-X', 'utf8',
        (Join-Path $Payload 'scripts\installer_driver.py'),
        '--payload', $Payload, '--root', $Root,
        '--shell', (Get-Process -Id $PID).Path, '--plugin', $Plugin,
        '--action', $Action.ToLowerInvariant())
    foreach ($option in $Options) { $arguments += @('--option', $option) }
    if ($RetainedRuntime) { $arguments += @('--retained-runtime', $RetainedRuntime) }
    if ($RetainedRuntimeSha256) { $arguments += @('--retained-runtime-sha256', $RetainedRuntimeSha256) }
    $participant = $env:COPILOT_EXTENSIONS_NATIVE_INSTALLER -eq $Plugin
    if ($participant) { $arguments += '--check-native' }
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        Invoke-Hidden -FilePath $python -ArgList $arguments -StreamOutput
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    return [pscustomobject]@{
        Python = $python; Arguments = $arguments; Participant = $participant; ExitCode = $code
    }
}

function Invoke-Hidden {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgList = @(),
        [ref]$ErrorOutput,
        [string]$InputText,
        [switch]$StreamOutput
    )
    if ($StreamOutput -and ($PSBoundParameters.ContainsKey('ErrorOutput') -or
        $PSBoundParameters.ContainsKey('InputText'))) {
        throw 'Streaming native output cannot be combined with captured streams.'
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    if ($psi.PSObject.Properties['ArgumentList']) {
        foreach ($a in $ArgList) { [void]$psi.ArgumentList.Add($a) }
    } else {
        # Windows PowerShell 5.1 uses .NET Framework's quoted command line.
        $psi.Arguments = ($ArgList | ForEach-Object {
            if ($_ -and $_ -notmatch '[\s"]') {
                $_
            } else {
                '"' + [regex]::Replace([regex]::Replace($_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
            }
        }) -join ' '
    }
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $PSBoundParameters.ContainsKey('InputText')
    $proc = [System.Diagnostics.Process]::Start($psi)
    try {
        if ($StreamOutput) {
            $stdoutStream = [Console]::OpenStandardOutput()
            $stderrStream = [Console]::OpenStandardError()
            $stdout = $proc.StandardOutput.BaseStream.CopyToAsync($stdoutStream)
            $stderr = $proc.StandardError.BaseStream.CopyToAsync($stderrStream)
        } else {
            $stdout = $proc.StandardOutput.ReadToEndAsync()
            $stderr = $proc.StandardError.ReadToEndAsync()
        }
        if ($psi.RedirectStandardInput) {
            $encoding = New-Object System.Text.UTF8Encoding($false)
            $bytes = $encoding.GetBytes($InputText)
            $proc.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
            $proc.StandardInput.BaseStream.Flush()
            $proc.StandardInput.Close()
        }
        $proc.WaitForExit()
        [Threading.Tasks.Task]::WaitAll($stdout, $stderr)
        $global:LASTEXITCODE = $proc.ExitCode
        if ($StreamOutput) {
            $stdoutStream.Flush()
            $stderrStream.Flush()
            return
        }
        if ($ErrorOutput) {
            $ErrorOutput.Value = $stderr.Result
        } elseif ($stderr.Result) {
            if ($proc.ExitCode -ne 0) { [Console]::Error.Write($stderr.Result) }
            else { Write-Verbose $stderr.Result }
        }
        return $stdout.Result
    } finally {
        $proc.Dispose()
    }
}
