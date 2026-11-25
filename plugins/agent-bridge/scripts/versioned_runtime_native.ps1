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
        [string]$Plugin, [string]$Uv, [string]$HealthModule, [switch]$Repair,
        [string]$HealthCallable = ''
    )
    if (-not $Profile) { return @{} }
    return @{
        WheelProfile = $Profile; WheelDirectory = $Directory; Payload = $Payload
        Plugin = $Plugin; UvCommand = $Uv; HealthModule = $HealthModule; Repair = $Repair
        HealthCallable = $HealthCallable
    }
}

function Test-NativeWheelRuntimeCurrent {
    param([string]$Root, [string]$Payload, [string]$Plugin)
    $python = Resolve-NativeBootstrapPython -Root $Root
    if (-not $python) { throw 'Closed-wheel readiness requires the prepared uv-managed bootstrap Python' }
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
        [string]$Root = '',
        [string]$BootstrapDirectory = $PSScriptRoot,
        [AllowEmptyString()][string]$UvPath
    )
    . (Join-Path $BootstrapDirectory 'bootstrap-uv.ps1')
    $uvOptions = @{}
    if ($PSBoundParameters.ContainsKey('UvPath')) { $uvOptions.UvPath = $UvPath }
    $selection = Resolve-UvBootstrapPython `
        -RequestFile (Join-Path $BootstrapDirectory 'installer-instance-python-version.txt') `
        -Operation Inspect -IsolateSelection @uvOptions -InvokeCaptured {
            param($FilePath, $ArgList, $Environment)
            $nativeError = ''
            $output = Invoke-Hidden -FilePath $FilePath -ArgList $ArgList `
                -Environment $Environment -ErrorOutput ([ref]$nativeError) `
                -CaptureEncoding ([Text.UTF8Encoding]::new($false))
            return [pscustomobject]@{
                ExitCode = $LASTEXITCODE; Stdout = [string]$output; Stderr = $nativeError
            }
        }
    if ($selection.Status -ne 'selected') { return $null }
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
    $discoveryEnvironment = Get-UvBootstrapEnvironment
    $discoveryEnvironment.PYTHON_MANAGER_AUTOMATIC_INSTALL = 'false'
    try {
        $nativeError = ''
        $selected = Invoke-Hidden -FilePath $selection.Python -ArgList @(
            '-I', '-S', '-B', '-c', $probe, $Action, $Root
        ) -ErrorOutput ([ref]$nativeError) -Environment $discoveryEnvironment
        if ($LASTEXITCODE -eq 0) {
            $python = ($selected | Out-String) | ConvertFrom-Json
            if ($python -and [IO.Path]::IsPathRooted($python) -and
                $python -notmatch '[\\/]WindowsApps[\\/]' -and [IO.File]::Exists($python)) {
                return $python
            }
        }
    } catch { }
    return $null
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
        [string]$WheelUv,
        [string]$BootstrapPython
    )
    if ($BootstrapPython -and (
        $Action -notin @('install', 'update', 'provision') -or
        -not [IO.Path]::IsPathRooted($BootstrapPython) -or
        -not [IO.File]::Exists($BootstrapPython) -or
        $BootstrapPython -match '[\\/]WindowsApps[\\/]'
    )) {
        throw 'Explicit bootstrap Python requires an existing absolute interpreter for install, update or provision.'
    }
    $python = if ($WheelProfile) { $WheelPython }
        elseif ($BootstrapPython) { $BootstrapPython }
        else { Resolve-NativeBootstrapPython -Action $Action -Root $Root }
    if (-not $python) { throw 'Native install and uninstall require the prepared uv-managed bootstrap Python; removal requires it outside the runtime' }
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

function Invoke-BoundedNativeCapture {
    param(
        [Diagnostics.Process]$Process,
        [Diagnostics.Stopwatch]$Clock,
        [int]$TimeoutMilliseconds,
        [int]$CleanupMilliseconds,
        [string]$InputText
    )
    $inputWrite = $null
    $failure = $null
    $workDeadline = $TimeoutMilliseconds - $CleanupMilliseconds
    try {
        $stdout = $Process.StandardOutput.ReadToEndAsync()
        $stderr = $Process.StandardError.ReadToEndAsync()
        if ($Process.StartInfo.RedirectStandardInput) {
            $encoding = New-Object Text.UTF8Encoding($false)
            $bytes = $encoding.GetBytes($InputText)
            $inputWrite = $Process.StandardInput.BaseStream.WriteAsync($bytes, 0, $bytes.Length)
        }
        while ($true) {
            if ($Clock.ElapsedMilliseconds -ge $workDeadline) {
                throw [TimeoutException]::new('Bounded native capture exceeded its execution deadline.')
            }
            if ($inputWrite -and $inputWrite.IsCompleted) {
                [void]$inputWrite.GetAwaiter().GetResult()
                $Process.StandardInput.Close()
                $inputWrite = $null
            }
            foreach ($task in @($stdout, $stderr)) {
                if ($task.IsCompleted) { [void]$task.GetAwaiter().GetResult() }
            }
            if (-not $inputWrite -and $stdout.IsCompleted -and $stderr.IsCompleted -and
                $Process.WaitForExit(0)) {
                return [pscustomobject]@{
                    ExitCode = $Process.ExitCode; Stdout = $stdout.Result; Stderr = $stderr.Result
                }
            }
            [Threading.Thread]::Sleep([int][Math]::Min(10, [Math]::Max(
                1, $workDeadline - $Clock.ElapsedMilliseconds
            )))
        }
    } catch {
        $failure = $_
        $global:LASTEXITCODE = 1
        $rootExited = $false
        try {
            if (-not $Process.HasExited) { $Process.Kill() }
            $remaining = [Math]::Max(0, $TimeoutMilliseconds - $Clock.ElapsedMilliseconds)
            $rootExited = $Process.WaitForExit([int]$remaining)
        } catch { }
        # This handle owns the direct child, not its enclosing Job/process tree.
        $failure.Exception.Data['RootExitConfirmed'] = $rootExited
        $failure.Exception.Data['CleanupConfirmed'] = $false
        throw $failure
    } finally {
        $streams = @($Process.StandardOutput, $Process.StandardError)
        if ($Process.StartInfo.RedirectStandardInput) { $streams += $Process.StandardInput }
        foreach ($stream in $streams) {
            try { $stream.Dispose() }
            catch {
                if (-not $failure) { throw }
                $failure.Exception.Data['CaptureDisposalFailure'] = $_.Exception.Message
            }
        }
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
        [hashtable]$Environment,
        [ValidateRange(0, 2147483647)][int]$TimeoutMilliseconds = 0,
        [ValidateRange(1, 2147483647)][int]$CleanupMilliseconds = 3000,
        [Text.Encoding]$CaptureEncoding
    )
    if ($TimeoutMilliseconds -gt 0) {
        if (-not $PSBoundParameters.ContainsKey('ErrorOutput') -or $null -eq $ErrorOutput) {
            throw 'Bounded native capture requires a non-null ErrorOutput reference.'
        }
        if ($StreamOutput -or $StreamErrorOutput -or $CleanupMilliseconds -ge $TimeoutMilliseconds) {
            throw 'Bounded native capture requires captured streams and cleanup reserved inside its timeout.'
        }
        $clock = [Diagnostics.Stopwatch]::StartNew()
    }
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
        foreach ($name in $Environment.Keys) {
            if ($null -eq $Environment[$name]) {
                $psi.EnvironmentVariables.Remove($name)
            } else {
                $psi.EnvironmentVariables[$name] = $Environment[$name]
            }
        }
    }
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    if ($CaptureEncoding) {
        $psi.StandardOutputEncoding = $CaptureEncoding
        $psi.StandardErrorEncoding = $CaptureEncoding
    }
    $psi.RedirectStandardInput = $PSBoundParameters.ContainsKey('InputText')
    $proc = [System.Diagnostics.Process]::Start($psi)
    try {
        if ($TimeoutMilliseconds -gt 0) {
            $result = Invoke-BoundedNativeCapture -Process $proc -Clock $clock `
                -TimeoutMilliseconds $TimeoutMilliseconds -CleanupMilliseconds $CleanupMilliseconds `
                -InputText $InputText
            $global:LASTEXITCODE = $result.ExitCode
            $ErrorOutput.Value = $result.Stderr
            return $result.Stdout
        }
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
