# Import starts no helper and touches no registry; legacy guards keep their owner.
if ('CopilotExtensions.DaemonInstanceNative' -as [type]) {
    throw 'A legacy compiled daemon-instance adapter is already loaded. Use a fresh PowerShell process after the normal payload update; existing instance custody was not migrated.'
}
$script:DaemonInstanceRuntime = Join-Path $PSScriptRoot 'daemon_instance_runtime.pyz'

function Get-DaemonInstanceProcessBirth {
    param([Parameter(Mandatory)][Diagnostics.Process]$Process)
    [void]$Process.Handle
    return 'windows:' + $Process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(
        [Globalization.CultureInfo]::InvariantCulture
    )
}

function Resolve-DaemonInstancePython {
    param([string]$Selected)
    $executable = $Selected
    $seen = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    while ($true) {
        if ($seen.Count -ge 8) { throw 'Selected Python has excessive virtual-environment indirection.' }
        if (-not $seen.Add($executable)) { throw 'Selected Python has a virtual-environment home cycle.' }
        $directory = [IO.Path]::GetDirectoryName($executable)
        if ([IO.Path]::GetFileName($directory) -ine 'Scripts') { break }
        $config = Join-Path ([IO.Path]::GetDirectoryName($directory)) 'pyvenv.cfg'
        if (-not [IO.File]::Exists($config)) { break }
        if ([IO.Path]::GetFileName($executable) -inotmatch '^pythonw?\.exe$') {
            throw 'Cannot preserve selected Python: its virtual-environment entrypoint is unrecognized.'
        }
        if ([IO.FileInfo]::new($config).Length -gt 65536) {
            throw 'Selected Python virtual-environment configuration exceeds its size bound.'
        }
        $venvHome = $null
        foreach ($line in [IO.File]::ReadAllLines($config)) {
            $separator = $line.IndexOf('=')
            if ($separator -ge 0 -and $line.Substring(0, $separator).Trim() -ceq 'home') {
                $venvHome = $line.Substring($separator + 1).Trim()
            }
        }
        $root = if ($venvHome) { [IO.Path]::GetPathRoot($venvHome) } else { $null }
        if (-not $venvHome -or -not [IO.Path]::IsPathRooted($venvHome) -or -not $root -or
            $root -in @('\', '/') -or $root -match '^[A-Za-z]:$') {
            throw 'Cannot preserve selected Python: its virtual-environment home is unavailable.'
        }
        $executable = Join-Path $venvHome 'python.exe'
        if (-not [IO.File]::Exists($executable) -or $executable -match '[\\/]WindowsApps[\\/]') {
            throw 'Cannot preserve selected Python: its base interpreter is unavailable.'
        }
        $executable = [IO.Path]::GetFullPath($executable)
    }
    return [pscustomobject]@{
        Executable=$executable
        Launcher=$(if ($executable -ine $Selected) { $Selected } else { $null })
    }
}

function New-DaemonInstanceReader {
    param([IO.StreamReader]$Source, [string]$Stream, [int]$Limit)
    $reader = [pscustomobject]@{
        Source=$Source; Stream=$Stream; Limit=$Limit; Buffer=([char[]]::new(4096))
        Offset=0; Count=0; Pending=$null; Ended=$false; SkipLineFeed=$false; Active=$null
    }
    $reader | Add-Member ScriptMethod ReadLineAsync {
        return (New-DaemonInstanceCapture $this -Line)
    }
    $reader | Add-Member ScriptMethod Dispose { $this.Source.Dispose() }
    return $reader
}

function New-DaemonInstanceCapture {
    param($Reader, [switch]$Line)
    if ($null -ne $Reader.Active -and -not $Reader.Active.IsCompleted) {
        throw 'Overlapping daemon instance capture is refused.'
    }
    $capture = [pscustomobject]@{
        Reader=$Reader; Line=[bool]$Line; Limit=$Reader.Limit
        Builder=([Text.StringBuilder]::new()); Result=$null; IsCompleted=$false
        Status=([Threading.Tasks.TaskStatus]::WaitingForActivation); Exception=$null
    }
    $capture | Add-Member ScriptMethod Wait {
        param([int]$Milliseconds)
        return (Wait-DaemonInstanceCapture $this $Milliseconds)
    }
    $Reader.Active = $capture
    if (-not $Reader.Ended -and $Reader.Offset -eq $Reader.Count -and $null -eq $Reader.Pending) {
        $Reader.Pending = $Reader.Source.ReadAsync($Reader.Buffer, 0, $Reader.Buffer.Length)
    }
    return $capture
}

function Wait-DaemonInstanceCapture {
    param($Capture, [int]$Milliseconds)
    if ($Capture.Status -eq [Threading.Tasks.TaskStatus]::Faulted) { throw $Capture.Exception }
    if ($Capture.IsCompleted) { return $true }
    $reader = $Capture.Reader
    $clock = [Diagnostics.Stopwatch]::StartNew()
    try {
        while ($true) {
            if ($reader.Offset -eq $reader.Count -and -not $reader.Ended) {
                if ($null -eq $reader.Pending) {
                    $reader.Pending = $reader.Source.ReadAsync($reader.Buffer, 0, $reader.Buffer.Length)
                }
                $remaining = [Math]::Max(0, $Milliseconds - $clock.ElapsedMilliseconds)
                if (-not $reader.Pending.Wait([int]$remaining)) { return $false }
                $reader.Count = $reader.Pending.Result
                $reader.Offset = 0
                $reader.Pending = $null
                if ($reader.Count -eq 0) { $reader.Ended = $true }
            }
            if ($reader.Ended) {
                if (-not $Capture.Line -or $Capture.Builder.Length -gt 0) {
                    $Capture.Result = $Capture.Builder.ToString()
                }
                $Capture.IsCompleted = $true
                $Capture.Status = [Threading.Tasks.TaskStatus]::RanToCompletion
                return $true
            }
            if ($Capture.Line -and $reader.SkipLineFeed) {
                if ($reader.Buffer[$reader.Offset] -eq [char]10) { $reader.Offset++ }
                $reader.SkipLineFeed = $false
                if ($reader.Offset -eq $reader.Count) { continue }
            }
            $end = $reader.Count
            if ($Capture.Line) {
                $available = $reader.Count - $reader.Offset
                $lf = [Array]::IndexOf($reader.Buffer, [char]10, $reader.Offset, $available)
                $cr = [Array]::IndexOf($reader.Buffer, [char]13, $reader.Offset, $available)
                if ($lf -ge 0) { $end = $lf }
                if ($cr -ge 0 -and $cr -lt $end) { $end = $cr }
            }
            $length = $end - $reader.Offset
            if ($Capture.Builder.Length + $length -gt $Capture.Limit) {
                throw ('Daemon instance helper ' + $reader.Stream + ' exceeded its ' +
                    $Capture.Limit + '-character limit.')
            }
            [void]$Capture.Builder.Append($reader.Buffer, $reader.Offset, $length)
            $reader.Offset = $end
            if ($Capture.Line -and $end -lt $reader.Count) {
                $reader.SkipLineFeed = $reader.Buffer[$reader.Offset] -eq [char]13
                $reader.Offset++
                $Capture.Result = $Capture.Builder.ToString()
                $Capture.IsCompleted = $true
                $Capture.Status = [Threading.Tasks.TaskStatus]::RanToCompletion
                return $true
            }
        }
    } catch {
        $Capture.Exception = $_.Exception
        $Capture.Status = [Threading.Tasks.TaskStatus]::Faulted
        $Capture.IsCompleted = $true
        throw
    }
}

function Wait-DaemonInstanceTask {
    param($Task, [Diagnostics.Stopwatch]$Clock, [double]$Seconds, [hashtable]$State)
    $diagnostics = $null
    if ($null -ne $State) { $diagnostics = $State['diagnostics'] }
    while ($true) {
        if ($null -ne $diagnostics) { [void]$diagnostics.Wait(0) }
        if ($Task.Wait(100)) { break }
        if ($null -ne $State -and $null -ne $State['process'] -and $State.process.WaitForExit(0)) {
            if ($null -eq $State['exitClock']) { $State.exitClock = [Diagnostics.Stopwatch]::StartNew() }
            if ($State.exitClock.Elapsed.TotalSeconds -ge 3.0) {
                throw 'Original helper exited before its response; pipe settlement is unconfirmed; selection was not retried.'
            }
        }
        if ($Clock.Elapsed.TotalSeconds -ge $Seconds) {
            throw 'Daemon instance helper response timed out; the operation outcome is unconfirmed.'
        }
    }
    if ($Clock.Elapsed.TotalSeconds -ge $Seconds) {
        throw 'Daemon instance helper response exceeded its deadline; the operation outcome is unconfirmed.'
    }
}

function Wait-DaemonInstanceProcessExit {
    param($Process, [Diagnostics.Stopwatch]$Clock, [double]$Seconds, [hashtable]$State)
    $diagnostics = $State['diagnostics']
    while (-not $Process.WaitForExit(0)) {
        if ($null -ne $diagnostics -and -not $diagnostics.IsCompleted) {
            [void]$diagnostics.Wait(0)
        }
        $remaining = [Math]::Max(0, 1000 * $Seconds - $Clock.ElapsedMilliseconds)
        if ($remaining -le 0) { return $false }
        if ($Process.WaitForExit([int][Math]::Min(100, $remaining))) { return $true }
    }
    return $true
}

function Close-DaemonInstanceWorker {
    param([hashtable]$State)
    $errors = New-Object 'Collections.Generic.List[string]'
    if ($State.offerPermitted -and $null -eq $State.job) {
        try {
            $abort = $State.input.WriteLineAsync('abort:' + $State.nonce)
            if (-not $abort.Wait(1000)) { $errors.Add('Unadopted Job abort delivery is unconfirmed.') }
        } catch { $errors.Add('Unadopted Job abort delivery is unconfirmed: ' + $_.Exception.Message) }
    }
    try { if ($null -ne $State.job) { $State.job.Dispose() } }
    catch { $errors.Add('Original helper Job closure is unconfirmed: ' + $_.Exception.Message) }
    try { if ($null -ne $State.input) { $State.input.BaseStream.Dispose() } }
    catch { $errors.Add('Original helper input closure is unconfirmed: ' + $_.Exception.Message) }
    if (-not $State.offerPermitted -and $null -eq $State.job -and $null -ne $State.process) {
        try {
            # Start retained this exact creation handle before qualification.
            if (-not $State.process.HasExited) { $State.process.Kill() }
        } catch { $errors.Add('Original pre-offer helper retirement is unconfirmed: ' + $_.Exception.Message) }
    }
    $settled = $true
    $clock = [Diagnostics.Stopwatch]::StartNew()
    foreach ($process in @($State.worker, $State.process)) {
        if ($null -eq $process) { continue }
        try {
            if (-not (Wait-DaemonInstanceProcessExit $process $clock 3.0 $State)) {
                $errors.Add('Original helper process exit is unconfirmed.')
                $settled = $false
            }
        } catch {
            $errors.Add('Original helper process observation failed: ' + $_.Exception.Message)
            $settled = $false
        }
    }
    if ($settled -and $null -ne $State.diagnostics -and -not $State.diagnostics.IsCompleted) {
        try {
            $remaining = [Math]::Max(0, 3000 - $clock.ElapsedMilliseconds)
            if (-not $State.diagnostics.Wait([int]$remaining)) {
                $errors.Add('Original helper diagnostic capture is unconfirmed.')
                $settled = $false
            }
        } catch { $errors.Add('Original helper diagnostic capture failed: ' + $_.Exception.Message) }
    }
    if ($settled -and $null -ne $State.output -and
        $null -ne $State.output.PSObject.Properties['Active'] -and
        $null -ne $State.output.Active -and -not $State.output.Active.IsCompleted) {
        try {
            $remaining = [Math]::Max(0, 3000 - $clock.ElapsedMilliseconds)
            if (-not $State.output.Active.Wait([int]$remaining)) {
                $errors.Add('Original helper output capture is unconfirmed.')
                $settled = $false
            }
        } catch {
            $errors.Add('Original helper output capture failed: ' + $_.Exception.Message)
            $settled = $false
        }
    }
    if ($settled) {
        foreach ($resource in @($State.output, $State.error, $State.worker, $State.process)) {
            try { if ($null -ne $resource) { $resource.Dispose() } }
            catch { $errors.Add('Original helper resource closure failed: ' + $_.Exception.Message) }
        }
    }
    if ($null -ne $State.diagnostics -and $State.diagnostics.IsCompleted) {
        if ($State.diagnostics.Status -eq [Threading.Tasks.TaskStatus]::RanToCompletion) {
            if ($State.diagnostics.Result) { $errors.Add($State.diagnostics.Result.Trim()) }
        } else { $errors.Add('Original helper diagnostic capture failed.') }
    }
    if ($errors.Count -gt 0) {
        $failure = [InvalidOperationException]::new(($errors -join "`n"))
        $failure.Data['DaemonInstanceState'] = $State
        throw $failure
    }
}

function Start-DaemonInstanceWorker {
    param([hashtable]$State, [switch]$NoTimeout)
    $startupSeconds = if ($NoTimeout) { [double]::PositiveInfinity } else { 30.0 }
    if ('CopilotExtensions.DaemonInstanceNative' -as [type]) {
        throw 'A legacy compiled daemon-instance adapter is already loaded. Use a fresh PowerShell process after the normal payload update; existing instance custody was not migrated.'
    }
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'The PowerShell daemon instance adapter requires native Windows.'
    }
    $bootstrapPython = [Environment]::GetEnvironmentVariable('COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON', 'Process')
    if ($bootstrapPython) {
        $root = [IO.Path]::GetPathRoot($bootstrapPython)
        if (-not [IO.Path]::IsPathRooted($bootstrapPython) -or -not $root -or
            $root -in @('\', '/') -or $root -match '^[A-Za-z]:$' -or
            -not [IO.File]::Exists($bootstrapPython) -or
            $bootstrapPython -match '[\\/]WindowsApps[\\/]') {
            throw 'The supplied bootstrap Python must be an existing absolute executable, not an execution alias.'
        }
        $pythons = @($bootstrapPython)
        $prerequisiteError = 'The supplied bootstrap Python must be a working Python 3.10+ executable. No PATH fallback was attempted.'
    } else {
        $seen = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        $pythons = @(
            $ExecutionContext.InvokeCommand.GetCommands(
                'python', [Management.Automation.CommandTypes]::Application, $false
            ) |
                Where-Object { $_.Source -notmatch '[\\/]WindowsApps[\\/]' -and $seen.Add($_.Source) } |
                ForEach-Object { $_.Source }
        )
        $prerequisiteError = 'Python 3.10+ must be available as python on PATH (unsupported interpreters and the Microsoft Store alias stub do not count). Run the harness Python prerequisite setup, or install the standalone prerequisite.'
    }
    if (-not $pythons.Count) { throw $prerequisiteError }
    if (-not [IO.File]::Exists($script:DaemonInstanceRuntime)) {
        throw 'The owning payload daemon instance source archive is unavailable.'
    }
    $current = [Diagnostics.Process]::GetCurrentProcess()
    try { $birth = Get-DaemonInstanceProcessBirth $current }
    finally { $current.Dispose() }
    $State.nonce = [Guid]::NewGuid().ToString('N')
    $packet = @{
        archive = $script:DaemonInstanceRuntime
        lifetime = @{owner_pid = $PID; owner_birth = $birth; nonce = $State.nonce}
    } | ConvertTo-Json -Depth 5 -Compress
    $encoding = New-Object Text.UTF8Encoding($false)
    $encoded = [Convert]::ToBase64String($encoding.GetBytes($packet))
    $bootstrap = @'
import base64,json,sys
if sys.version_info < (3,10):
    print('daemon-python-unsupported',flush=True)
    sys.stderr.write('daemon-instance: Python 3.10+ is required; run the owning setup prerequisite flow.\n')
    sys.exit(1)
print('daemon-python-compatible',flush=True)
packet=json.loads(base64.b64decode(sys.stdin.buffer.raw.readline()).decode('utf-8'))
sys.path.insert(0,packet['archive'])
from agent_procutil._daemon_instance_shell import main
sys.exit(main(packet))
'@
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.Arguments = '-I -S -B -X utf8 -c "' + $bootstrap + '"'
    $start.WorkingDirectory = [Environment]::CurrentDirectory
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = $encoding
    $start.StandardErrorEncoding = $encoding
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $compatible = $false
    for ($index = 0; $index -lt $pythons.Count; $index++) {
        $State.selectedExecutable = $pythons[$index]
        $launch = Resolve-DaemonInstancePython $State.selectedExecutable
        $start.FileName = $launch.Executable
        $start.EnvironmentVariables.Remove('__PYVENV_LAUNCHER__')
        if ($launch.Launcher) { $start.EnvironmentVariables['__PYVENV_LAUNCHER__'] = $launch.Launcher }
        $State.exitClock = $null
        $State.process = New-Object Diagnostics.Process
        $State.process.StartInfo = $start
        $process = $State.process
        try { $started = $process.Start() }
        catch [ComponentModel.Win32Exception] {
            $State.process.Dispose()
            $State.process = $null
            if ($bootstrapPython) { throw }
            continue
        }
        if (-not $started) { throw 'Original helper process creation is unconfirmed; selection was not retried.' }
        [void]$State.process.Handle
        $State.input = $State.process.StandardInput
        $State.input.NewLine = "`n"
        $State.input.AutoFlush = $true
        $State.output = New-DaemonInstanceReader $State.process.StandardOutput 'stdout' 1024
        $State.error = New-DaemonInstanceReader $State.process.StandardError 'stderr' 65536
        $State.diagnostics = New-DaemonInstanceCapture $State.error
        $qualification = $State.output.ReadLineAsync()
        Wait-DaemonInstanceTask $qualification $clock $startupSeconds $State
        if ($qualification.Result -ceq 'daemon-python-unsupported') {
            $refusalClock = [Diagnostics.Stopwatch]::StartNew()
            $refusalSeconds = if ($NoTimeout) { 3.0 } else {
                [Math]::Max(0, [Math]::Min(3.0, 30.0 - $clock.Elapsed.TotalSeconds))
            }
            if (-not (Wait-DaemonInstanceProcessExit $State.process $refusalClock $refusalSeconds $State)) {
                throw 'Unsupported Python process exit is unconfirmed; selection was not retried.'
            }
            $captureClock = if ($NoTimeout) { $refusalClock } else { $clock }
            $captureSeconds = if ($NoTimeout) { $refusalSeconds } else { $startupSeconds }
            try { Wait-DaemonInstanceTask $State.diagnostics $captureClock $captureSeconds $State }
            catch {
                throw ('Unsupported Python diagnostic capture is unconfirmed; selection was not retried. ' +
                    $_.Exception.Message)
            }
            if ($State.process.ExitCode -ne 1) {
                throw 'Unsupported Python returned an unexpected outcome; selection was not retried.'
            }
            if ($index -lt $pythons.Count - 1) {
                foreach ($name in @('input', 'output', 'error', 'process')) { $State[$name].Dispose() }
                foreach ($name in @('input', 'output', 'error', 'process', 'diagnostics')) { $State[$name] = $null }
            }
            continue
        }
        if ($qualification.Result -cne 'daemon-python-compatible') {
            throw 'Daemon instance Python startup ended before compatibility was confirmed; selection was not retried.'
        }
        $compatible = $true
        break
    }
    if (-not $compatible) { throw $prerequisiteError }
    $State.output.Limit = 524288
    $ready = $State.output.ReadLineAsync()
    Wait-DaemonInstanceTask ($State.input.WriteLineAsync($encoded)) $clock $startupSeconds $State
    $State.offerPermitted = $true
    Wait-DaemonInstanceTask ($State.input.WriteLineAsync('offer:' + $State.nonce)) $clock $startupSeconds $State
    Wait-DaemonInstanceTask $ready $clock $startupSeconds $State
    if (-not $ready.Result) { throw 'Daemon instance helper startup ended before a custody receipt.' }
    $receipt = $ready.Result | ConvertFrom-Json
    if ($receipt.version -cne 1 -or $receipt.nonce -cne $State.nonce -or $receipt.owner_pid -cne $PID -or
        $receipt.scope -cne 'windows-job' -or $receipt.job.parent_pid -cne $PID -or
        $receipt.job.parent_birth -cne $birth -or [string]$receipt.job.parent_handle -notmatch '\A[1-9][0-9]*\z') {
        throw 'Daemon instance helper returned an uncorrelated original-Job receipt.'
    }
    $State.job = [Microsoft.Win32.SafeHandles.SafeFileHandle]::new(
        [IntPtr]([long]$receipt.job.parent_handle), $true
    )
    $State.worker = [Diagnostics.Process]::GetProcessById([int]$receipt.job.worker_pid)
    if ((Get-DaemonInstanceProcessBirth $State.worker) -cne $receipt.job.worker_birth -or $State.worker.HasExited) {
        throw 'Daemon instance helper birth identity changed before acknowledgement.'
    }
    Wait-DaemonInstanceTask ($State.input.WriteLineAsync($State.nonce)) $clock $startupSeconds $State
    $State.broken = $false
}

function Read-DaemonInstanceResponse {
    param([hashtable]$State, $Read, [Diagnostics.Stopwatch]$Clock, [double]$Seconds)
    $buffer = New-Object IO.MemoryStream
    $part = 0
    try {
        while ($true) {
            Wait-DaemonInstanceTask $Read $Clock $Seconds $State
            if (-not $Read.Result) { throw 'Daemon instance response ended before its outcome was confirmed.' }
            if ($Read.Result.Length -ge 524288) { throw 'Daemon instance response exceeds its frame bound.' }
            $response = $Read.Result | ConvertFrom-Json
            if ($response.nonce -cne $State.nonce -or $response.sequence -cne $State.sequence) {
                throw 'Daemon instance helper returned an uncorrelated outcome.'
            }
            if ($response.PSObject.Properties.Name -notcontains 'fragment') {
                if ($part -ne 0) { throw 'Daemon instance response lost its fragment sequence.' }
                return $response
            }
            if (($response.part -isnot [int] -and $response.part -isnot [long]) -or
                $response.part -ne $part -or $response.last -isnot [bool] -or
                $response.fragment -isnot [string] -or -not $response.fragment) {
                throw 'Daemon instance helper returned an invalid response fragment.'
            }
            $bytes = [Convert]::FromBase64String($response.fragment)
            $buffer.Write($bytes, 0, $bytes.Length)
            if ($response.last) {
                return ([Text.Encoding]::UTF8.GetString($buffer.ToArray()) | ConvertFrom-Json)
            }
            $part++
            $Read = $State.output.ReadLineAsync()
        }
    } finally { $buffer.Dispose() }
}

function Invoke-DaemonInstanceRequest {
    param([Parameter(Mandatory)][hashtable]$Request, [switch]$Transient)
    $noTimeout = $Request.command -eq 'run-query' -and
        $Request.parameters.ContainsKey('timeout_ms') -and $null -eq $Request.parameters.timeout_ms
    if ($noTimeout -and -not $Transient) {
        throw 'A no-deadline native query requires transient caller custody.'
    }
    $gate = if ($Transient) { [object]::new() } else {
        [string]::Intern('copilot-daemon-instance-shell-v1')
    }
    $pendingState = $null
    $outcomeReceived = $false
    $cleanupAttempted = $false
    [Threading.Monitor]::Enter($gate)
    try {
        $state = if ($Transient) { $null } else { [AppDomain]::CurrentDomain.GetData($gate) }
        if ($null -eq $state) {
            $state = @{
                process=$null; worker=$null; job=$null; input=$null; output=$null; error=$null
                diagnostics=$null; nonce=$null; sequence=0; broken=$true; offerPermitted=$false
            }
            if (-not $Transient) { [AppDomain]::CurrentDomain.SetData($gate, $state) }
            $pendingState = $state
            try { Start-DaemonInstanceWorker $state -NoTimeout:$noTimeout }
            catch {
                $failure = $_
                $cleanupAttempted = $true
                try { Close-DaemonInstanceWorker $state }
                catch {
                    $combined = [InvalidOperationException]::new(
                        ($failure.Exception.Message + "`n" + $_.Exception.Message), $_.Exception
                    )
                    $combined.Data['DaemonInstanceState'] = $state
                    throw $combined
                }
                throw $failure
            }
        }
        if ($state.broken) {
            throw 'Daemon instance helper custody is unavailable. The prior outcome remains unconfirmed; automatic replay is refused.'
        }
        $bootstrapPython = [Environment]::GetEnvironmentVariable('COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON', 'Process')
        $selectedPython = $state['selectedExecutable']
        if (-not $selectedPython -and $null -ne $state.process) {
            $selectedPython = $state.process.StartInfo.FileName
        }
        if ($bootstrapPython -and ($null -eq $state.process -or
            $selectedPython -ine $bootstrapPython)) {
            throw 'The existing daemon instance helper uses another interpreter. Use a fresh PowerShell process for the supplied bootstrap Python; existing custody was not changed.'
        }
        $pendingState = $state
        $environment = @{}
        foreach ($name in @(
            'COPILOT_EXTENSIONS_DAEMON_INSTANCE', 'COPILOT_EXTENSIONS_DAEMON_REGISTRY',
            'COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT', 'COPILOT_EXTENSIONS_OWNED_JOB_SCOPES'
        )) {
            $value = [Environment]::GetEnvironmentVariable($name, 'Process')
            if ($null -ne $value) { $environment[$name] = $value }
        }
        $Request.environment = $environment
        $state.sequence++
        $Request.sequence = $state.sequence
        $Request.nonce = $state.nonce
        $seconds = 30.0
        if ($Request.command -eq 'reconcile') { $seconds = [Math]::Max(30, $Request.parameters.max_seconds + 5) }
        if ($Request.command -in @('run-owned', 'run-query')) {
            $seconds = [Math]::Max(30, $Request.parameters.timeout_ms / 1000.0 + 15)
        }
        if ($noTimeout) { $seconds = [double]::PositiveInfinity }
        $clock = [Diagnostics.Stopwatch]::StartNew()
        try {
            $json = $Request | ConvertTo-Json -Depth 8 -Compress
            $bytes = [Text.Encoding]::UTF8.GetBytes($json)
            $encoded = [Convert]::ToBase64String($bytes)
            if ($encoded.Length -ge 524288) { throw 'Daemon instance request exceeds its frame bound.' }
            $read = $state.output.ReadLineAsync()
            Wait-DaemonInstanceTask ($state.input.WriteLineAsync($encoded)) $clock $seconds $state
            $response = Read-DaemonInstanceResponse $state $read $clock $seconds
            if ($response.nonce -cne $state.nonce -or $response.sequence -cne $state.sequence -or
                $response.ok -isnot [bool]) {
                throw 'Daemon instance helper returned an uncorrelated outcome.'
            }
            $outcomeReceived = $true
        } catch {
            $state.broken = $true
            $failure = $_
            $cleanupAttempted = $true
            try { Close-DaemonInstanceWorker $state }
            catch {
                $combined = [InvalidOperationException]::new(
                    ($failure.Exception.Message + "`n" + $_.Exception.Message), $_.Exception
                )
                $combined.Data['DaemonInstanceState'] = $state
                throw $combined
            }
            throw $failure
        }
        if (-not $response.ok) {
            $failure = [InvalidOperationException]::new('Daemon instance operation refused: ' + $response.error)
            if ($response.PSObject.Properties.Name -contains 'cleanup_confirmed' -and
                $response.cleanup_confirmed -is [bool]) {
                $failure.Data['CleanupConfirmed'] = $response.cleanup_confirmed
            }
            throw $failure
        }
        return $response.result
    } finally {
        try {
            # Runspace cancellation can bypass catch, but must retire pending work.
            if ($null -ne $pendingState -and -not $outcomeReceived -and -not $cleanupAttempted) {
                $pendingState.broken = $true
                $cleanupAttempted = $true
                Close-DaemonInstanceWorker $pendingState
            }
            if ($Transient -and $null -ne $pendingState -and -not $cleanupAttempted) {
                Close-DaemonInstanceWorker $pendingState
            }
        } finally { [Threading.Monitor]::Exit($gate) }
    }
}

function Get-DaemonInstanceRoot {
    param([string]$RegistryRoot)
    if ($RegistryRoot) { return $RegistryRoot }
    return (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'copilot-extensions\daemon-instances')
}

function Read-DaemonInstanceRecord {
    param([Parameter(Mandatory)][string]$Path)
    $record = Invoke-DaemonInstanceRequest @{command='read'; parameters=@{path=$Path}}
    $result = @{}
    foreach ($property in $record.PSObject.Properties) { $result[$property.Name] = $property.Value }
    return $result
}

function New-DaemonInstanceReservation {
    param(
        [Parameter(Mandatory)][string]$Service, [Parameter(Mandatory)][string]$RuntimeRoot,
        [Parameter(Mandatory)][string]$Source, [string]$RegistryRoot,
        [ValidateSet('production','unattended-test','interactive-test')][string]$Purpose='production',
        [double]$TtlSeconds=0, [double]$MaxLifetimeSeconds=0
    )
    return Invoke-DaemonInstanceRequest @{command='reserve'; parameters=@{
        service=$Service; runtime_root=$RuntimeRoot; source=$Source; registry_root=$RegistryRoot
        purpose=$Purpose; ttl=$TtlSeconds; maximum=$MaxLifetimeSeconds
    }}
}

function Get-DaemonInstanceProcessFrame {
    param([Parameter(Mandatory)][Diagnostics.Process]$Process)
    return @{
        pid=$Process.Id; birth=(Get-DaemonInstanceProcessBirth $Process)
        handle=$Process.Handle.ToInt64()
    }
}

function Get-DaemonInstanceReceiptFrame {
    param([Parameter(Mandatory)]$Receipt)
    return @{instance_id=$Receipt.instance_id; registry_root=$Receipt.registry_root}
}

function Set-DaemonInstanceProcess {
    param([Parameter(Mandatory)]$Receipt, [Parameter(Mandatory)][Diagnostics.Process]$Process, [switch]$Admitted)
    return Invoke-DaemonInstanceRequest @{
        command='bind'; receipt=(Get-DaemonInstanceReceiptFrame $Receipt)
        process=(Get-DaemonInstanceProcessFrame $Process)
        admitted=[bool]$Admitted
    }
}

function Complete-DaemonInstanceFailedLaunch {
    param(
        [Parameter(Mandatory)]$Receipt, [hashtable]$ProcessFrame,
        [switch]$CreationRefused, [bool]$AdmissionPossible=$false
    )
    $request = @{
        command='failed-launch'; receipt=(Get-DaemonInstanceReceiptFrame $Receipt)
        creation_refused=[bool]$CreationRefused; admission_possible=$AdmissionPossible
    }
    if ($ProcessFrame) { $request.process = $ProcessFrame }
    $result = Invoke-DaemonInstanceRequest $request
    return ($result.settled -ceq $true)
}

function Enter-DaemonInstance {
    param(
        [Parameter(Mandatory)][string]$Service, [Parameter(Mandatory)][string]$RuntimeRoot,
        [Parameter(Mandatory)][string]$Source, [string]$RegistryRoot,
        [string]$Purpose, [double]$TtlSeconds=0, [double]$MaxLifetimeSeconds=0
    )
    $receipt = Invoke-DaemonInstanceRequest @{command='enter'; parameters=@{
        service=$Service; runtime_root=$RuntimeRoot; source=$Source; registry_root=$RegistryRoot
        purpose=$Purpose; ttl=$TtlSeconds; maximum=$MaxLifetimeSeconds
    }}
    [Environment]::SetEnvironmentVariable('COPILOT_EXTENSIONS_DAEMON_INSTANCE', $null, 'Process')
    [Environment]::SetEnvironmentVariable('COPILOT_EXTENSIONS_DAEMON_REGISTRY', $null, 'Process')
    if ($receipt.job_scopes) {
        [Environment]::SetEnvironmentVariable('COPILOT_EXTENSIONS_OWNED_JOB_SCOPES', $receipt.job_scopes, 'Process')
    }
    return $receipt
}

function Exit-DaemonInstance {
    param([Parameter(Mandatory)]$Receipt, [Diagnostics.Process]$Process)
    if ($Process) {
        $null = Invoke-DaemonInstanceRequest @{
            command='finish-child'; receipt=(Get-DaemonInstanceReceiptFrame $Receipt)
            process=(Get-DaemonInstanceProcessFrame $Process)
        }
    } else {
        if ($Receipt.PSObject.Properties['closed'] -and $Receipt.closed) { return }
        $null = Invoke-DaemonInstanceRequest @{
            command='exit'; receipt=(Get-DaemonInstanceReceiptFrame $Receipt); guard_token=$Receipt.guard_token
        }
        $Receipt | Add-Member -NotePropertyName closed -NotePropertyValue $true -Force
    }
}

function Invoke-DaemonInstanceReconcile {
    param(
        [int]$Limit=128, [double]$MaxSeconds=5, [string]$RegistryRoot,
        [bool]$Terminate=$false, [string]$RuntimeRoot
    )
    if ($Limit -lt 1 -or $Limit -gt 4096 -or $MaxSeconds -le 0 -or
        [double]::IsNaN($MaxSeconds) -or [double]::IsInfinity($MaxSeconds)) {
        throw 'Invalid daemon reconciliation bounds.'
    }
    $support = Invoke-DaemonInstanceRequest @{command='reconcile-contract'}
    if ($null -eq $support -or $support.version -cne 1 -or
        $support.termination_policy -cne $true -or $support.runtime_scope -cne $true) {
        throw 'Daemon instance helper lacks scoped termination-policy support; reconciliation was not submitted.'
    }
    $results = @(Invoke-DaemonInstanceRequest @{command='reconcile'; parameters=@{
        limit=$Limit; max_seconds=$MaxSeconds; registry_root=$RegistryRoot
        terminate=$Terminate; runtime_root=$RuntimeRoot
    }})
    foreach ($row in $results) {
        if ($null -ne $row -and $row.status -eq 'held') {
            [Console]::Error.WriteLine('daemon instance held: ' + $row.instance_id + ' ' + $row.reason)
        }
    }
    return $results
}
