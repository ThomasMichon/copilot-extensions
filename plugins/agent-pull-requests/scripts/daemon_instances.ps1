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

function Wait-DaemonInstanceTask {
    param($Task, [Diagnostics.Stopwatch]$Clock, [double]$Seconds)
    while (-not $Task.Wait(100)) {
        if ($Clock.Elapsed.TotalSeconds -ge $Seconds) {
            throw 'Daemon instance helper response timed out; the operation outcome is unconfirmed.'
        }
    }
    if ($Clock.Elapsed.TotalSeconds -ge $Seconds) {
        throw 'Daemon instance helper response exceeded its deadline; the operation outcome is unconfirmed.'
    }
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
    $settled = $true
    $clock = [Diagnostics.Stopwatch]::StartNew()
    foreach ($process in @($State.worker, $State.process)) {
        if ($null -eq $process) { continue }
        try {
            $remaining = [Math]::Max(0, 3000 - $clock.ElapsedMilliseconds)
            if (-not $process.WaitForExit([int]$remaining)) {
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
    if ($errors.Count -gt 0) { throw ($errors -join "`n") }
}

function Start-DaemonInstanceWorker {
    param([hashtable]$State)
    if ('CopilotExtensions.DaemonInstanceNative' -as [type]) {
        throw 'A legacy compiled daemon-instance adapter is already loaded. Use a fresh PowerShell process after the normal payload update; existing instance custody was not migrated.'
    }
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'The PowerShell daemon instance adapter requires native Windows.'
    }
    try {
        $python = @(Get-Command -Name python -CommandType Application -ErrorAction Stop)[0]
    } catch [System.Management.Automation.CommandNotFoundException] {
        throw 'Python 3.10+ must be available as python on PATH. Run the harness Python prerequisite setup, or install the standalone prerequisite.'
    }
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
    sys.stderr.write('daemon-instance: Python 3.10+ is required on PATH; run the harness prerequisite setup or install the standalone prerequisite.\n')
    sys.exit(1)
packet=json.loads(base64.b64decode(sys.stdin.buffer.raw.readline()).decode('utf-8'))
sys.path.insert(0,packet['archive'])
from agent_procutil._daemon_instance_shell import main
sys.exit(main(packet))
'@
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $python.Path
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
    $State.process = [Diagnostics.Process]::Start($start)
    $State.input = $State.process.StandardInput
    $State.input.NewLine = "`n"
    $State.input.AutoFlush = $true
    $State.output = $State.process.StandardOutput
    $State.error = $State.process.StandardError
    $State.diagnostics = $State.error.ReadToEndAsync()
    $ready = $State.output.ReadLineAsync()
    Wait-DaemonInstanceTask ($State.input.WriteLineAsync($encoded)) $clock 30
    $State.offerPermitted = $true
    Wait-DaemonInstanceTask ($State.input.WriteLineAsync('offer:' + $State.nonce)) $clock 30
    Wait-DaemonInstanceTask $ready $clock 30
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
    Wait-DaemonInstanceTask ($State.input.WriteLineAsync($State.nonce)) $clock 30
    $State.broken = $false
}

function Invoke-DaemonInstanceRequest {
    param([Parameter(Mandatory)][hashtable]$Request)
    $gate = [string]::Intern('copilot-daemon-instance-shell-v1')
    [Threading.Monitor]::Enter($gate)
    try {
        $state = [AppDomain]::CurrentDomain.GetData($gate)
        if ($null -eq $state) {
            $state = @{
                process=$null; worker=$null; job=$null; input=$null; output=$null; error=$null
                diagnostics=$null; nonce=$null; sequence=0; broken=$true; offerPermitted=$false
            }
            [AppDomain]::CurrentDomain.SetData($gate, $state)
            try { Start-DaemonInstanceWorker $state }
            catch {
                $failure = $_
                try { Close-DaemonInstanceWorker $state }
                catch { throw ($failure.Exception.Message + "`n" + $_.Exception.Message) }
                throw $failure
            }
        }
        if ($state.broken) {
            throw 'Daemon instance helper custody is unavailable. The prior outcome remains unconfirmed; automatic replay is refused.'
        }
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
        $clock = [Diagnostics.Stopwatch]::StartNew()
        try {
            $json = $Request | ConvertTo-Json -Depth 8 -Compress
            $bytes = [Text.Encoding]::UTF8.GetBytes($json)
            $encoded = [Convert]::ToBase64String($bytes)
            if ($encoded.Length -ge 524288) { throw 'Daemon instance request exceeds its frame bound.' }
            $read = $state.output.ReadLineAsync()
            Wait-DaemonInstanceTask ($state.input.WriteLineAsync($encoded)) $clock $seconds
            Wait-DaemonInstanceTask $read $clock $seconds
            if (-not $read.Result) { throw 'Daemon instance response ended before its outcome was confirmed.' }
            $response = $read.Result | ConvertFrom-Json
            if ($response.nonce -cne $state.nonce -or $response.sequence -cne $state.sequence -or
                $response.ok -isnot [bool]) {
                throw 'Daemon instance helper returned an uncorrelated outcome.'
            }
        } catch {
            $state.broken = $true
            $failure = $_
            try { Close-DaemonInstanceWorker $state }
            catch { throw ($failure.Exception.Message + "`n" + $_.Exception.Message) }
            throw $failure
        }
        if (-not $response.ok) { throw ('Daemon instance operation refused: ' + $response.error) }
        return $response.result
    } finally { [Threading.Monitor]::Exit($gate) }
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
    param([int]$Limit=128, [double]$MaxSeconds=5, [string]$RegistryRoot)
    if ($Limit -lt 1 -or $Limit -gt 4096 -or $MaxSeconds -le 0 -or
        [double]::IsNaN($MaxSeconds) -or [double]::IsInfinity($MaxSeconds)) {
        throw 'Invalid daemon reconciliation bounds.'
    }
    $results = @(Invoke-DaemonInstanceRequest @{command='reconcile'; parameters=@{
        limit=$Limit; max_seconds=$MaxSeconds; registry_root=$RegistryRoot
    }})
    foreach ($row in $results) {
        if ($null -ne $row -and $row.status -eq 'held') {
            [Console]::Error.WriteLine('daemon instance held: ' + $row.instance_id + ' ' + $row.reason)
        }
    }
    return $results
}
