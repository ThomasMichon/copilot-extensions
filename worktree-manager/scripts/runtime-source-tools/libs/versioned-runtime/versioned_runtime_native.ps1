function Get-NativeWheelSelection {
    param(
        [Parameter(Mandatory)][string]$Python,
        [Parameter(Mandatory)][string]$Helper,
        [Parameter(Mandatory)][string]$Profile,
        [Parameter(Mandatory)][string]$Directory,
        [Parameter(Mandatory)][string]$Payload,
        [Parameter(Mandatory)][string]$Plugin,
        [object]$Expected
    )
    $nativeError = ''
    $result = Invoke-Hidden -FilePath $Python -ArgList @(
        '-I', '-B', $Helper, '--native-inputs', '--wheel-profile', $Profile,
        '--wheel-directory', $Directory, '--payload', $Payload, '--component', $Plugin
    ) -ErrorOutput ([ref]$nativeError)
    if ($LASTEXITCODE -ne 0) {
        throw "Native wheel selection failed (exit ${LASTEXITCODE}): $result$nativeError"
    }
    $selection = $result | ConvertFrom-Json
    if ($selection.runtime_version -cnotmatch '^[0-9][A-Za-z0-9._+-]*$' -or
        $selection.payload_hash -cnotmatch '^[0-9a-f]{64}$') {
        throw 'Native wheel selection returned an invalid generation or content identity'
    }
    if ($Expected -and ($selection.runtime_version -cne $Expected.runtime_version -or
        $selection.payload_hash -cne $Expected.payload_hash)) {
        throw 'Native wheel inputs changed after runtime selection'
    }
    return $selection
}

function Initialize-NativeWheelRuntime {
    param(
        [string]$Python, [string]$Profile, [string]$Directory,
        [string]$Payload, [string]$Plugin, [string]$Root
    )
    $script:NativeWheelSelection = Get-NativeWheelSelection -Python $Python `
        -Helper (Join-Path $Payload 'scripts\install_source.py') -Profile $Profile `
        -Directory $Directory -Payload $Payload -Plugin $Plugin
    Set-NativeWheelRuntimeVersion -Root $Root -Version $script:NativeWheelSelection.runtime_version
}

function Set-NativeWheelRuntimeVersion {
    param([string]$Root, [string]$Version)
    $script:SrcVersion = $Version
    $script:VenvDir = Join-Path (Join-Path $Root 'versions') $Version
    $script:VenvPython = Join-Path $script:VenvDir 'Scripts\python.exe'
    $script:LinkDir = $script:VenvDir
    $script:LinkPython = $script:VenvPython
}

function Get-NativeWheelConstructionOptions {
    param(
        [string]$Profile, [string]$Directory, [string]$Payload,
        [string]$Plugin, [string]$Uv, [string]$HealthModule, [switch]$Repair
    )
    if (-not $Profile) { return @{} }
    return @{
        WheelProfile = $Profile; WheelDirectory = $Directory; Payload = $Payload
        Plugin = $Plugin; UvCommand = $Uv; HealthModule = $HealthModule; Repair = $Repair
    }
}

function Test-NativeWheelRuntimeCurrent {
    param([string]$Root, [string]$Payload, [string]$Plugin)
    $python = Resolve-NativeBootstrapPython -Root $Root
    if (-not $python) { throw 'Closed-wheel readiness requires an existing Python 3.11+ interpreter' }
    $nativeError = ''
    [void](Invoke-Hidden -FilePath $python -ArgList @(
        '-I', '-B', (Join-Path $Payload 'scripts\install_source.py'),
        '--native-current', '--root', $Root, '--payload', $Payload, '--component', $Plugin
    ) -ErrorOutput ([ref]$nativeError))
    if ($LASTEXITCODE -notin @(0, 1)) { throw "Closed-wheel readiness failed: $nativeError" }
    return $LASTEXITCODE -eq 0
}

function Resolve-NativeBootstrapPython {
    param(
        [ValidateSet(10, 11)][int]$MinimumMinor = 11,
        [string]$Action = 'inspect',
        [string]$Root = ''
    )
    $probe = @'
import json,sys
from pathlib import Path
assert sys.version_info >= (3,MINIMUM_MINOR)
python = Path(sys._base_executable).resolve(strict=True)
if sys.argv[1].lower() in {'uninstall', 'broker-remove'}:
    root = Path(sys.argv[2]).absolute()
    assert '..' not in root.parts
    assert all(not python.is_relative_to(boundary) for boundary in (root, root.resolve()))
print(json.dumps(str(python)))
'@
    $probe = $probe.Replace('MINIMUM_MINOR', [string]$MinimumMinor)
    $discoveryEnvironment = @{ PYTHON_MANAGER_AUTOMATIC_INSTALL = 'false' }
    function Select-NativeBootstrapCandidates([string[]]$Candidates) {
        foreach ($candidate in @($Candidates | Select-Object -Unique)) {
            try {
                $nativeError = ''
                $selected = Invoke-Hidden -FilePath $candidate -ArgList @(
                    '-I', '-S', '-B', '-c', $probe, $Action, $Root
                ) -ErrorOutput ([ref]$nativeError) -Environment $discoveryEnvironment
                if ($LASTEXITCODE -eq 0) {
                    $python = ($selected | Out-String) | ConvertFrom-Json
                    if ($python -and [IO.File]::Exists($python)) { return $python }
                }
            } catch { }
        }
        return $null
    }
    $candidates = @()
    foreach ($name in @('python3', 'python')) {
        $candidates += @(Get-Command $name -All -CommandType Application -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notmatch '[\\/]WindowsApps[\\/]' } |
            ForEach-Object { $_.Source })
    }
    $python = Select-NativeBootstrapCandidates $candidates
    if ($python) { return $python }
    $candidates = @()
    foreach ($launcher in @(Get-Command py -All -CommandType Application -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notmatch '[\\/]WindowsApps[\\/]' })) {
        try {
            $nativeError = ''
            $listed = Invoke-Hidden -FilePath $launcher.Source -ArgList @('-0p') -ErrorOutput ([ref]$nativeError) -Environment $discoveryEnvironment
            $launcherMatches = @()
            if ($LASTEXITCODE -eq 0) {
                $launcherMatches = @([regex]::Matches("$listed", '(?m)((?:[A-Za-z]:[\\/]|\\\\).*python(?:[0-9.]*)?\.exe)"?\s*$'))
            }
            if ($launcherMatches.Count) {
                foreach ($match in $launcherMatches) { $candidates += $match.Groups[1].Value }
            } else {
                $selectors = @('3.13', '3.12', '3.11')
                if ($MinimumMinor -eq 10) { $selectors += '3.10' }
                foreach ($version in ($selectors + '3')) {
                    $exe = Invoke-Hidden -FilePath $launcher.Source -ArgList @(
                        "-$version", '-I', '-S', '-B', '-c', 'import json,sys; print(json.dumps(sys.executable))'
                    ) -ErrorOutput ([ref]$nativeError) -Environment $discoveryEnvironment
                    if ($LASTEXITCODE -eq 0 -and $exe) { $candidates += ($exe | Out-String) | ConvertFrom-Json }
                }
            }
        } catch { }
    }
    $python = Select-NativeBootstrapCandidates $candidates
    if ($python) { return $python }
    $candidates = @()
    if ($env:OS -eq 'Windows_NT') {
        $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)})
        if ($env:LOCALAPPDATA) { $roots += Join-Path $env:LOCALAPPDATA 'Programs\Python' }
        foreach ($installationRoot in $roots) {
            if (-not $installationRoot) { continue }
            $candidates += @(Get-ChildItem -LiteralPath $installationRoot -Filter 'Python3*' -Directory -ErrorAction SilentlyContinue |
                Sort-Object Name -Descending |
                ForEach-Object { Join-Path $_.FullName 'python.exe' })
        }
    }
    return Select-NativeBootstrapCandidates $candidates
}

function Enter-NativeInstallerDriver {
    param(
        [Parameter(Mandatory)][string]$Payload,
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$Plugin,
        [Parameter(Mandatory)][string]$Action,
        [string[]]$Options = @(),
        [string]$RetainedRuntime,
        [string]$RetainedRuntimeSha256,
        [string]$InstallSource,
        [string]$WheelProfile,
        [string]$WheelDirectory,
        [string]$WheelPython,
        [string]$WheelUv
    )
    $python = if ($WheelProfile) { $WheelPython }
        else { Resolve-NativeBootstrapPython -Action $Action -Root $Root }
    if (-not $python) { throw 'Native install and uninstall require an existing Python 3.11+ bootstrap interpreter; removal requires it outside the runtime' }
    $arguments = @('-I', '-S', '-B', '-X', 'utf8',
        (Join-Path $Payload 'scripts\installer_driver.py'),
        '--payload', $Payload, '--root', $Root,
        '--shell', (Get-Process -Id $PID).Path, '--plugin', $Plugin,
        '--action', $Action.ToLowerInvariant())
    foreach ($option in $Options) { $arguments += @('--option', $option) }
    if ($RetainedRuntime) { $arguments += @('--retained-runtime', $RetainedRuntime) }
    if ($RetainedRuntimeSha256) { $arguments += @('--retained-runtime-sha256', $RetainedRuntimeSha256) }
    if ($InstallSource) { $arguments += @('--install-source', $InstallSource) }
    if ($WheelProfile -or $WheelDirectory -or $WheelPython -or $WheelUv) {
        $arguments += @('--wheel-profile', $WheelProfile, '--wheel-directory', $WheelDirectory,
            '--wheel-python', $WheelPython, '--wheel-uv', $WheelUv)
    }
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
        [switch]$StreamOutput,
        [switch]$StreamErrorOutput,
        [hashtable]$Environment
    )
    if ($StreamOutput -and ($PSBoundParameters.ContainsKey('ErrorOutput') -or
        $PSBoundParameters.ContainsKey('InputText'))) {
        throw 'Streaming native output cannot be combined with captured streams.'
    }
    if ($StreamOutput -and $StreamErrorOutput) {
        throw 'Native output must select either both streams or stderr-only streaming.'
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
    if ($Environment) {
        foreach ($name in $Environment.Keys) { $psi.EnvironmentVariables[$name] = $Environment[$name] }
    }
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $PSBoundParameters.ContainsKey('InputText')
    $proc = [System.Diagnostics.Process]::Start($psi)
    try {
        if ($StreamErrorOutput) {
            $stdout = $proc.StandardOutput.ReadToEndAsync()
            $stderrStream = [Console]::OpenStandardError()
            $capture = New-Object System.IO.MemoryStream
            $buffer = New-Object byte[] 4096
            $read = $proc.StandardError.BaseStream.ReadAsync($buffer, 0, $buffer.Length)
            $write = $null
            $inputWrite = $null
            try {
                if ($psi.RedirectStandardInput) {
                    $encoding = New-Object System.Text.UTF8Encoding($false)
                    $bytes = $encoding.GetBytes($InputText)
                    $inputWrite = $proc.StandardInput.BaseStream.WriteAsync($bytes, 0, $bytes.Length)
                }
                while ($read -or $write -or $inputWrite) {
                    if ($stdout.IsFaulted) { [void]$stdout.GetAwaiter().GetResult() }
                    if ($inputWrite -and $inputWrite.IsCompleted) {
                        [void]$inputWrite.GetAwaiter().GetResult()
                        $proc.StandardInput.Close()
                        $inputWrite = $null
                    }
                    if ($write -and $write.IsCompleted) {
                        [void]$write.GetAwaiter().GetResult()
                        $write = $null
                        $read = $proc.StandardError.BaseStream.ReadAsync($buffer, 0, $buffer.Length)
                    }
                    if ($read -and $read.IsCompleted) {
                        $count = $read.GetAwaiter().GetResult()
                        $read = $null
                        if ($count) {
                            $capture.Write($buffer, 0, $count)
                            $write = $stderrStream.WriteAsync($buffer, 0, $count)
                        }
                    }
                    if ($read -or $write -or $inputWrite) { [Threading.Thread]::Sleep(10) }
                }
                $proc.WaitForExit()
                $output = $stdout.GetAwaiter().GetResult()
                $stderrStream.Flush()
                $capture.Position = 0
                $reader = New-Object System.IO.StreamReader($capture, $proc.StandardError.CurrentEncoding)
                try { $errors = $reader.ReadToEnd() } finally { $reader.Dispose() }
                $global:LASTEXITCODE = $proc.ExitCode
                if ($ErrorOutput) { $ErrorOutput.Value = $errors }
                return $output
            } catch {
                $failure = $_
                if (-not $proc.HasExited) { $proc.Kill() }
                $proc.WaitForExit()
                foreach ($task in @($stdout, $read, $write, $inputWrite)) {
                    if ($task -and -not $task.IsCompleted) {
                        try { [void]$task.GetAwaiter().GetResult() } catch { $failure = $_ }
                    }
                }
                throw $failure
            } finally {
                $capture.Dispose()
            }
        }
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
