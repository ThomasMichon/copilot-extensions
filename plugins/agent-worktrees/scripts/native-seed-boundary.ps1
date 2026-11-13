#Requires -Version 7.0
# agent-worktrees:native-seed-boundary-v1
# agent-worktrees:native-suspended-creation-v1
# Dot-sourced into an existing lifetime owner, never launched as another waiter.

function Get-AwNativeSeedRuntime {
    param([string]$Root)
    if (-not [IO.Path]::IsPathRooted($Root)) { return $null }
    try {
        $version = [IO.File]::ReadAllText((Join-Path $Root 'current-version')).Trim()
        if ($version -notmatch '^[A-Za-z0-9][A-Za-z0-9._+-]{0,255}$') { return $null }
        $slot = Join-Path (Join-Path $Root 'versions') $version
        $environment = if (Test-Path -LiteralPath (Join-Path $slot 'application.json') -PathType Leaf) {
            Join-Path $slot '.venv'
        } else { $slot }
        $python = Join-Path $environment 'Scripts\python.exe'
        $bootstrap = Join-Path $slot '.runtime-bootstrap\runtime_launcher.py'
        foreach ($file in @($python, $bootstrap, (Join-Path $slot '.install-complete.json'))) {
            if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return $null }
        }
        return @{ Python = $python; Bootstrap = $bootstrap }
    } catch { return $null }
}

function Register-AwNativeSeedConsoleControl {
    if (-not $IsWindows) { return $null }
    if (-not ('AwNativeSeed.ConsoleControl' -as [type])) {
        $assembly = [Reflection.Emit.AssemblyBuilder]::DefineDynamicAssembly(
            [Reflection.AssemblyName]::new('AwNativeSeed'),
            [Reflection.Emit.AssemblyBuilderAccess]::Run
        )
        $module = $assembly.DefineDynamicModule('ConsoleControl')
        $handler = $module.DefineType(
            'AwNativeSeed.ControlHandler', [Reflection.TypeAttributes]'Public, Sealed', [MulticastDelegate]
        )
        $constructor = $handler.DefineConstructor(
            [Reflection.MethodAttributes]'Public, HideBySig, RTSpecialName',
            [Reflection.CallingConventions]::Standard, [type[]]@([object], [IntPtr])
        )
        $constructor.SetImplementationFlags([Reflection.MethodImplAttributes]'Runtime, Managed')
        $invoke = $handler.DefineMethod(
            'Invoke', [Reflection.MethodAttributes]'Public, HideBySig, NewSlot, Virtual',
            [bool], [type[]]@([uint32])
        )
        $invoke.SetImplementationFlags([Reflection.MethodImplAttributes]'Runtime, Managed')
        $handlerType = $handler.CreateType()
        $type = $module.DefineType(
            'AwNativeSeed.ConsoleControl', [Reflection.TypeAttributes]'Public, Abstract, Sealed'
        )
        foreach ($binding in @(
            @('GetConsoleCP', [uint32], [type[]]@()),
            @('SetConsoleCtrlHandler', [bool], [type[]]@([IntPtr], [bool]))
        )) {
            $method = $type.DefinePInvokeMethod(
                $binding[0], 'kernel32.dll',
                [Reflection.MethodAttributes]'Public, Static, PinvokeImpl',
                [Reflection.CallingConventions]::Standard, $binding[1], $binding[2],
                [Runtime.InteropServices.CallingConvention]::Winapi,
                [Runtime.InteropServices.CharSet]::Unicode
            )
            $method.SetImplementationFlags([Reflection.MethodImplAttributes]::PreserveSig)
        }
        # A native callback cannot run a PowerShell scriptblock on its OS thread.
        # Handle only Ctrl+C/Break here; the backend receives the same event and
        # decides whether to cancel a turn or exit. Close/logoff retain OS policy.
        $method = $type.DefineMethod(
            'Handle', [Reflection.MethodAttributes]'Public, Static', [bool], [type[]]@([uint32])
        )
        $il = $method.GetILGenerator()
        $il.Emit([Reflection.Emit.OpCodes]::Ldarg_0)
        $il.Emit([Reflection.Emit.OpCodes]::Ldc_I4_1)
        $il.Emit([Reflection.Emit.OpCodes]::Cgt_Un)
        $il.Emit([Reflection.Emit.OpCodes]::Ldc_I4_0)
        $il.Emit([Reflection.Emit.OpCodes]::Ceq)
        $il.Emit([Reflection.Emit.OpCodes]::Ret)
        $null = $type.CreateType()
    } else {
        $handlerType = 'AwNativeSeed.ControlHandler' -as [type]
    }
    if ([AwNativeSeed.ConsoleControl]::GetConsoleCP() -eq 0) { return $null }
    $callback = [AwNativeSeed.ConsoleControl].GetMethod('Handle').CreateDelegate($handlerType)
    $pointer = [Runtime.InteropServices.Marshal]::GetFunctionPointerForDelegate($callback)
    if (-not [AwNativeSeed.ConsoleControl]::SetConsoleCtrlHandler($pointer, $true)) {
        throw 'Could not retain console control ownership; no backend started.'
    }
    return @{ Callback = $callback; Pointer = $pointer }
}

function Invoke-AwNativeSeedPhase {
    param([string]$Root, [string[]]$Arguments, [hashtable]$Runtime, [hashtable]$Environment, [hashtable]$Diagnostics)
    $runtime = if ($Runtime) { $Runtime } else { Get-AwNativeSeedRuntime $Root }
    if (-not $runtime) { return $null }
    $process = $null
    try {
        $info = [Diagnostics.ProcessStartInfo]::new($runtime.Python)
        $info.UseShellExecute = $false
        $info.WorkingDirectory = $PWD.ProviderPath
        $info.CreateNoWindow = $true
        $info.RedirectStandardInput = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $info.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
        $info.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
        if ($Environment) {
            foreach ($key in $Environment.Keys) { $info.Environment[$key] = $Environment[$key] }
        }
        foreach ($argument in (@(
            '-I', '-S', '-B', '-X', 'utf8', $runtime.Bootstrap,
            '-m', 'agent_worktrees.launch_seed_native',
            '--contract-version', '1', '--root', $Root
        ) + $Arguments)) { $info.ArgumentList.Add([string]$argument) }
        $process = [Diagnostics.Process]::Start($info)
        $process.StandardInput.Close()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(10000)) {
            $process.Kill($true)
            $null = $process.WaitForExit(1000)
            return $null
        }
        if (-not [Threading.Tasks.Task]::WaitAll(
            [Threading.Tasks.Task[]]@($stdout, $stderr), 1000
        )) { return $null }
        if ($process.ExitCode -ne 0) {
            $diagnostic = $stderr.GetAwaiter().GetResult()
            if ($null -ne $Diagnostics) { $Diagnostics['stderr'] = $diagnostic }
            [Console]::Error.Write($diagnostic)
            if ($env:WORKTREE_SETUP_LOG) {
                try {
                    [IO.File]::AppendAllText($env:WORKTREE_SETUP_LOG, $diagnostic)
                } catch {}
            }
            return $null
        }
        $raw = $stdout.GetAwaiter().GetResult()
        $reply = ConvertFrom-Json -InputObject $raw -ErrorAction Stop
        $document = [System.Text.Json.JsonDocument]::Parse($raw)
        try {
            # ConvertFrom-Json coerces ISO-shaped strings on supported PS 7 hosts.
            $element = [System.Text.Json.JsonElement]::new()
            if ($document.RootElement.TryGetProperty('argv', [ref]$element)) {
                $literalArgv = [Collections.Generic.List[string]]::new()
                foreach ($argument in $element.EnumerateArray()) {
                    if ($argument.ValueKind -ne [System.Text.Json.JsonValueKind]::String) { return $null }
                    $literalArgv.Add($argument.GetString())
                }
                $reply.argv = $literalArgv.ToArray()
            }
            if (
                $document.RootElement.TryGetProperty('environment', [ref]$element) -and
                $element.ValueKind -ne [System.Text.Json.JsonValueKind]::Null
            ) {
                $environment = @{}
                foreach ($property in $element.EnumerateObject()) {
                    if ($property.Value.ValueKind -ne [System.Text.Json.JsonValueKind]::String) { return $null }
                    $environment[$property.Name] = $property.Value.GetString()
                }
                $reply.environment = [pscustomobject]$environment
            }
            if ($document.RootElement.TryGetProperty('headed', [ref]$element)) {
                $literalArgv = [Collections.Generic.List[string]]::new()
                foreach ($argument in $element.GetProperty('argv').EnumerateArray()) {
                    if ($argument.ValueKind -ne [System.Text.Json.JsonValueKind]::String) { return $null }
                    $literalArgv.Add($argument.GetString())
                }
                $reply.headed.argv = $literalArgv.ToArray()
                foreach ($name in @('session_id', 'state_file', 'cwd')) {
                    $reply.headed.$name = $element.GetProperty($name).GetString()
                }
            }
            return $reply
        } finally { $document.Dispose() }
    } catch {
        if ($process) {
            try { if (-not $process.HasExited) { $process.Kill($true) } } catch {}
        }
        return $null
    }
    finally {
        if ($process) { $process.Dispose() }
    }
}

function Test-AwHeadedPreparation {
    param([string]$Root, [hashtable]$Runtime)
    if (-not $Runtime) { return $false }
    if (-not $script:AwNativeSeedContracts) { $script:AwNativeSeedContracts = @{} }
    $key = $Runtime.Python + '|' + $Runtime.Bootstrap
    if (-not $script:AwNativeSeedContracts.ContainsKey($key)) {
        $contract = Invoke-AwNativeSeedPhase $Root @('--describe-contract') -Runtime $Runtime
        $script:AwNativeSeedContracts[$key] = [bool](
            $contract -and $contract.contract -ceq 'agent-worktrees.native-seed' -and
            ($contract.version -is [int] -or $contract.version -is [long]) -and $contract.version -eq 1 -and
            $contract.backend_preparation_modes -is [array] -and
            $contract.backend_process_receipts -is [array] -and
            $contract.backend_preparation_modes -ccontains 'headed-live-owner-v1' -and
            $contract.backend_process_receipts -ccontains 'headed-native-child-v1'
        )
    }
    return $script:AwNativeSeedContracts[$key]
}

function Read-AwAdmissionBytes {
    param([IO.Stream]$Stream, [int]$Size, [Diagnostics.Stopwatch]$Clock)
    $bytes = [byte[]]::new($Size)
    $offset = 0
    while ($offset -lt $Size) {
        $remaining = 30000 - [int]$Clock.ElapsedMilliseconds
        if ($remaining -le 0) { throw 'Original admission deadline expired.' }
        $Stream.ReadTimeout = $remaining
        $read = $Stream.Read($bytes, $offset, $Size - $offset)
        if ($read -eq 0) { throw 'Original admission channel closed.' }
        $offset += $read
    }
    return ,$bytes
}

function Get-AwAdmissionRefusalReason {
    param([string]$Stderr, [bool]$HostExited)
    if ($HostExited) { return 'Session host exited before admission completed' }
    $prefix = 'Startup admission refused: '
    $line = @($Stderr -split "`r?`n" | Where-Object { $_.StartsWith($prefix, [StringComparison]::Ordinal) })
    if (-not $line.Count) { return $null }
    $reason = (($line[-1].Substring($prefix.Length)) -replace '\s+', ' ').Trim()
    if ($reason.Length -gt 512) { $reason = $reason.Substring(0, 509) + '...' }
    return $reason
}

function Invoke-AwHeadedAdmission {
    param(
        [Net.Sockets.TcpListener]$Listener, [Diagnostics.Process]$Process,
        [string]$Birth, [string]$Nonce, [object]$Headed,
        [string]$Root, [string[]]$Callback, [hashtable]$Runtime,
        [string[]]$AdmissionArguments
    )
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $client = $null
    $document = $null
    try {
        $accept = $Listener.AcceptTcpClientAsync()
        while (-not $accept.Wait(50)) {
            if ($Process.HasExited -or $clock.ElapsedMilliseconds -ge 30000) { return $false }
        }
        $client = $accept.GetAwaiter().GetResult()
        $stream = $client.GetStream()
        $header = Read-AwAdmissionBytes $stream 4 $clock
        if ([BitConverter]::IsLittleEndian) { [Array]::Reverse($header) }
        $size = [BitConverter]::ToUInt32($header, 0)
        if ($size -lt 1 -or $size -gt 1048576) { return $false }
        $bytes = Read-AwAdmissionBytes $stream ([int]$size) $clock
        $document = [System.Text.Json.JsonDocument]::Parse([Text.Encoding]::UTF8.GetString($bytes))
        $request = $document.RootElement
        $keys = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($property in $request.EnumerateObject()) {
            if (-not $keys.Add($property.Name)) { return $false }
        }
        if (-not $keys.SetEquals([string[]]@(
            'version', 'nonce', 'host_pid', 'parent_pid', 'argv', 'cwd', 'session_id', 'environment'
        ))) { return $false }
        $nativeArguments = $request.GetProperty('argv')
        $environment = $request.GetProperty('environment')
        if (
            $request.GetProperty('version').GetInt32() -ne 1 -or
            $request.GetProperty('nonce').GetString() -cne $Nonce -or
            $request.GetProperty('host_pid').GetInt32() -ne $Process.Id -or
            $request.GetProperty('parent_pid').GetInt32() -ne $PID -or
            $request.GetProperty('cwd').GetString() -cne $Headed.cwd -or
            $request.GetProperty('session_id').GetString() -cne $Headed.session_id -or
            $environment.ValueKind -ne [System.Text.Json.JsonValueKind]::Object -or
            @($environment.EnumerateObject()).Count -ne 0 -or
            $nativeArguments.GetArrayLength() -ne @($Headed.argv).Count -or
            $Process.HasExited -or
            $Process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(
                [Globalization.CultureInfo]::InvariantCulture
            ) -cne $Birth
        ) { return $false }
        $index = 0
        foreach ($argument in $nativeArguments.EnumerateArray()) {
            if ($argument.GetString() -cne $Headed.argv[$index++]) { return $false }
        }
        $diagnostics = @{}
        $admitted = Invoke-AwNativeSeedPhase $Root (
            @('--phase', 'admit') + $Callback + $AdmissionArguments
        ) -Runtime $Runtime -Diagnostics $diagnostics
        $accepted = [bool]($admitted -and $admitted.recorded -eq $true -and -not $Process.HasExited)
        $answer = '{"version":1,"accepted":' + $accepted.ToString().ToLowerInvariant()
        if (-not $accepted) {
            $reason = Get-AwAdmissionRefusalReason ([string]$diagnostics['stderr']) $Process.HasExited
            if ($reason) {
                $answer += ',"reason":' + [System.Text.Json.JsonSerializer]::Serialize($reason, [string])
            }
        }
        $reply = [Text.Encoding]::UTF8.GetBytes($answer + '}')
        $header = [BitConverter]::GetBytes([uint32]$reply.Length)
        if ([BitConverter]::IsLittleEndian) { [Array]::Reverse($header) }
        $stream.WriteTimeout = 1000
        $stream.Write($header, 0, 4)
        $stream.Write($reply, 0, $reply.Length)
        return $accepted
    } catch { return $false }
    finally {
        if ($document) { $document.Dispose() }
        if ($client) { $client.Dispose() }
        $Listener.Stop()
    }
}

function Wait-AwNativeSeedBackend {
    param([Diagnostics.Process]$Process, [object[]]$Transfers)
    if (-not $Transfers -or -not $Transfers.Count) { $Process.WaitForExit(); return }
    $childWait = [Threading.ManualResetEvent]::new($false)
    $unusedEvent = $childWait.SafeWaitHandle
    # Borrow the actual child handle; this wait must never close its owner's handle.
    $childWait.SafeWaitHandle = [Microsoft.Win32.SafeHandles.SafeWaitHandle]::new($Process.Handle, $false)
    $unusedEvent.Dispose()
    try {
        while (-not $Process.HasExited) {
            $handles = [Collections.Generic.List[Threading.WaitHandle]]::new()
            $active = @($Transfers | Where-Object { -not $_.Complete })
            $handles.Add($childWait)
            foreach ($transfer in $active) {
                $handles.Add(([IAsyncResult]$transfer.Task).AsyncWaitHandle)
            }
            $ready = [Threading.WaitHandle]::WaitAny($handles.ToArray())
            if ($ready -eq 0) { break }
            $transfer = $active[$ready - 1]
            if ($transfer.Task.IsFaulted) {
                $null = $transfer.Task.Exception
                if (-not $transfer.Draining) {
                    $transfer.Draining = $true
                    $transfer.Task = $transfer.Source.CopyToAsync([IO.Stream]::Null)
                } else {
                    $transfer.Source.Dispose()
                    $transfer.Complete = $true
                }
            } else {
                $transfer.Complete = $true
            }
        }
        $Process.WaitForExit()
        try {
            $null = [Threading.Tasks.Task]::WaitAll(
                [Threading.Tasks.Task[]]@($Transfers | ForEach-Object { $_.Task }), 1000
            )
        } catch {
            # Transport loss must not suppress the actual exit and return callback.
        }
    } finally { $childWait.Dispose() }
}

function Open-AwNativeHeadFence {
    param([string]$Record)
    if (-not $IsWindows) { throw 'Native PowerShell head fencing requires Windows.' }
    $stream = [IO.File]::Open(
        [IO.Path]::ChangeExtension($Record, '.lock'),
        [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite
    )
    $clock = [Diagnostics.Stopwatch]::StartNew()
    try {
        while ($true) {
            try {
                $stream.Lock(0, 1)
                return $stream
            } catch [IO.IOException] {
                if ($clock.ElapsedMilliseconds -ge 2000) { throw }
                [Threading.Thread]::Sleep(50)
            }
        }
    } catch {
        $stream.Dispose()
        throw
    }
}

function Invoke-AwNativeSeedBackend {
    param(
        [string]$Root,
        [string]$Record,
        [string]$SeedId,
        [string[]]$Command,
        [string]$LaunchAttemptId,
        [string]$LaunchResolutionId,
        [string]$LaunchParentId,
        [switch]$AdmissionOnly,
        [string]$PreserveSeedId,
        [string]$NativeResumeSession,
        [scriptblock]$BeforeResume,
        [IntPtr[]]$InheritedHandles = @(),
        [switch]$RequireDirectClient
    )
    if ($InheritedHandles.Count -and -not $BeforeResume) {
        throw 'Explicit inherited handles require the pre-execution creation owner.'
    }
    if ($RequireDirectClient -and -not $BeforeResume) {
        throw 'Direct-client ownership requires pre-execution publication.'
    }
    $runtime = Get-AwNativeSeedRuntime $Root
    $prepareArgs = @('--phase', 'prepare', '--record', $Record)
    $admissionArgs = @()
    if ($AdmissionOnly) { $admissionArgs += '--admission-only' }
    foreach ($pair in @(
        @('--preserve-seed-id', $PreserveSeedId),
        @('--native-resume-session', $NativeResumeSession)
    )) {
        if ($pair[1]) { $admissionArgs += @($pair[0], $pair[1]) }
    }
    $prepareArgs += $admissionArgs
    $headedSupported = Test-AwHeadedPreparation $Root $runtime
    if ($headedSupported) {
        $prepareArgs += @('--backend-mode', 'headed-live-owner-v1')
    }
    if ($SeedId) { $prepareArgs += @('--seed-id', $SeedId) }
    foreach ($pair in @(
        @('--launch-attempt-id', $LaunchAttemptId),
        @('--launch-resolution-id', $LaunchResolutionId),
        @('--launch-parent-id', $LaunchParentId)
    )) {
        if ($pair[1]) { $prepareArgs += @($pair[0], $pair[1]) }
    }
    $prepared = Invoke-AwNativeSeedPhase $Root ($prepareArgs + @('--') + $Command) -Runtime $runtime
    if (
        -not $prepared -or $prepared.version -ne 1 -or
        @($prepared.argv).Count -lt 1 -or
        -not $prepared.attempt_id -or -not $prepared.resolution_id -or -not $prepared.operation_id
    ) {
        [Console]::Error.WriteLine('Native backend preparation is unconfirmed; no backend started.')
        return 3
    }
    $callbackArgs = @(
        '--record', $Record, '--launch-attempt-id', [string]$prepared.attempt_id,
        '--launch-resolution-id', [string]$prepared.resolution_id,
        '--operation-id', [string]$prepared.operation_id
    )
    if ($prepared.parent_id) { $callbackArgs += @('--launch-parent-id', [string]$prepared.parent_id) }
    if ($RequireDirectClient) {
        $direct = $false
        try {
            $selected = [string]$prepared.argv[0]
            if (
                $IsWindows -and -not $prepared.headed -and
                [IO.Path]::IsPathRooted($Command[0]) -and [IO.Path]::IsPathRooted($selected) -and
                [IO.Path]::GetFullPath($Command[0]).Equals(
                    [IO.Path]::GetFullPath($selected), [StringComparison]::OrdinalIgnoreCase
                ) -and [IO.Path]::GetExtension($selected) -ieq '.exe'
            ) {
                $image = [IO.File]::OpenRead($selected)
                try { $direct = $image.ReadByte() -eq 77 -and $image.ReadByte() -eq 90 }
                finally { $image.Dispose() }
            }
        } catch { $direct = $false }
        if (-not $direct) {
            $null = Invoke-AwNativeSeedPhase $Root (@('--phase', 'spawn-failed') + $callbackArgs) -Runtime $runtime
            [Console]::Error.WriteLine('Direct native client preparation is unconfirmed; no backend started.')
            return 3
        }
    }
    $info = [Diagnostics.ProcessStartInfo]::new([string]$prepared.argv[0])
    $info.UseShellExecute = $false
    $info.WorkingDirectory = $PWD.ProviderPath
    try { $info.CreateNoWindow = [Console]::IsInputRedirected }
    catch { $info.CreateNoWindow = $true }
    $info.RedirectStandardOutput = [Console]::IsOutputRedirected
    $info.RedirectStandardError = [Console]::IsErrorRedirected
    if ($info.CreateNoWindow -and -not $info.RedirectStandardOutput -and -not $info.RedirectStandardError) {
        $info.RedirectStandardOutput = $true
    }
    foreach ($argument in @($prepared.argv | Select-Object -Skip 1)) {
        $info.ArgumentList.Add([string]$argument)
    }
    if ($prepared.environment) {
        $info.Environment.Clear()
        foreach ($property in $prepared.environment.PSObject.Properties) {
            $info.Environment[$property.Name] = [string]$property.Value
        }
        $listener = $null
        $admissionNonce = $null
        if ($prepared.headed) {
            if (
                -not $headedSupported -or $prepared.headed.version -ne 1 -or -not $prepared.headed.session_id -or
                -not [IO.Path]::IsPathRooted($prepared.headed.state_file) -or
                $prepared.headed.cwd -cne $PWD.ProviderPath -or
                @($prepared.headed.argv).Count -lt 1 -or
                $info.Environment['AGENT_BRIDGE_SESSION_HOST_NONCE'] -notmatch '^[a-f0-9]{64}$'
            ) {
                [Console]::Error.WriteLine('Headed preparation is unconfirmed; no backend started.')
                return 3
            }
            $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
            $listener.Start(1)
            $admissionNonce = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
            $owner = [Diagnostics.Process]::GetCurrentProcess()
            try {
                $info.Environment['AGENT_DRIVER_STARTUP_ADMISSION'] = ConvertTo-Json -Compress -InputObject @{
                    version = 1; port = $listener.LocalEndpoint.Port; nonce = $admissionNonce
                    parent_pid = $PID
                    parent_birth = $owner.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(
                        [Globalization.CultureInfo]::InvariantCulture
                    )
                    environment_keys = @()
                }
            } finally { $owner.Dispose() }
        }
    }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    $nativeChild = $null
    $receiptUncertain = $false
    $outputCopies = [Collections.Generic.List[object]]::new()
    $consoleControl = $null
    $headFence = $null
    try {
        $consoleControl = Register-AwNativeSeedConsoleControl
        try {
            if ($NativeResumeSession) {
                try { $headFence = Open-AwNativeHeadFence $Record }
                catch {
                    [Console]::Error.WriteLine('Native head-writer fence is unavailable; no backend started.')
                    return 3
                }
                $admitted = Invoke-AwNativeSeedPhase $Root (
                    @('--phase', 'admit') + $callbackArgs + $admissionArgs
                ) -Runtime $runtime
                if (-not ($admitted -and $admitted.recorded -eq $true)) {
                    [Console]::Error.WriteLine('Native head admission changed; no backend started.')
                    return 3
                }
                $null = $info.Environment.Remove('AGENT_WORKTREES_RETAINED_NATIVE_RESUME')
            }
            try {
                if ($BeforeResume) {
                    . (Join-Path $PSScriptRoot 'native-seed-process.ps1')
                    $nativeChild = New-AwNativeSeedSuspendedProcess $info -InheritedHandles $InheritedHandles
                    $process.Dispose()
                    $process = $nativeChild.Process
                    try {
                        $published = & $BeforeResume $nativeChild.CreationHandle $process.Id $nativeChild.Birth
                        if ($published -isnot [bool] -or -not $published) {
                            throw 'Native pre-execution publication was not confirmed.'
                        }
                    } catch {
                        # Only the actual, still-uncommitted creation is settled.
                        $nativeChild.Dispose()
                        $null = Invoke-AwNativeSeedPhase $Root (@('--phase', 'spawn-failed') + $callbackArgs) -Runtime $runtime
                        [Console]::Error.WriteLine('Native pre-execution handoff refused; no backend executed.')
                        return 3
                    }
                    if (-not $nativeChild.Resume()) {
                        $receiptUncertain = $true
                        [Console]::Error.WriteLine('Native resume is unconfirmed; retaining the actual child wait and outcome.')
                    }
                } elseif (-not $process.Start()) {
                    [Console]::Error.WriteLine('Native backend start is unconfirmed; the handoff is retained.')
                    return 3
                }
            } catch [ComponentModel.Win32Exception] {
                $null = Invoke-AwNativeSeedPhase $Root (@('--phase', 'spawn-failed') + $callbackArgs) -Runtime $runtime
                [Console]::Error.WriteLine('Native backend could not start; inspect its retained launch outcome.')
                return 3
            }
        } finally {
            if ($headFence -and -not $prepared.headed) {
                $headFence.Dispose()
                $headFence = $null
            }
        }
        # Explicit output handles also preserve the original stdin handle in
        # consoleless children. Never pipe a real terminal's interactive streams.
        if ($info.RedirectStandardOutput) {
            $source = if ($nativeChild) { $nativeChild.StandardOutput } else { $process.StandardOutput.BaseStream }
            $outputCopies.Add([pscustomobject]@{
                Source = $source; Task = $source.CopyToAsync([Console]::OpenStandardOutput())
                Draining = $false; Complete = $false
            })
        }
        if ($info.RedirectStandardError) {
            $source = if ($nativeChild) { $nativeChild.StandardError } else { $process.StandardError.BaseStream }
            $outputCopies.Add([pscustomobject]@{
                Source = $source; Task = $source.CopyToAsync([Console]::OpenStandardError())
                Draining = $false; Complete = $false
            })
        }
        try {
            $birth = $process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(
                [Globalization.CultureInfo]::InvariantCulture
            )
            $spawnArgs = @('--phase', 'spawned', '--pid', [string]$process.Id, '--process-start-time', $birth) + $callbackArgs
            $callbackEnvironment = @{}
            $canRecord = $true
            if ($prepared.headed) {
                $canRecord = Invoke-AwHeadedAdmission $listener $process $birth $admissionNonce `
                    $prepared.headed $Root $callbackArgs $runtime $admissionArgs
                $deadline = [Diagnostics.Stopwatch]::StartNew()
                while (
                    $canRecord -and -not $process.HasExited -and $deadline.ElapsedMilliseconds -lt 30000 -and
                    -not (Test-Path -LiteralPath $prepared.headed.state_file -PathType Leaf)
                ) { [Threading.Thread]::Sleep(20) }
                $canRecord = $canRecord -and (Test-Path -LiteralPath $prepared.headed.state_file -PathType Leaf)
                $spawnArgs += @(
                    '--headed-state-file', [string]$prepared.headed.state_file,
                    '--headed-session-id', [string]$prepared.headed.session_id
                )
                $callbackEnvironment['AGENT_BRIDGE_SESSION_HOST_NONCE'] = $info.Environment['AGENT_BRIDGE_SESSION_HOST_NONCE']
            }
            $spawned = if ($canRecord) {
                Invoke-AwNativeSeedPhase $Root $spawnArgs -Runtime $runtime -Environment $callbackEnvironment
            } else { $null }
            $receiptUncertain = $receiptUncertain -or -not ($spawned -and $spawned.recorded -eq $true)
        } catch { $receiptUncertain = $true }
        if ($headFence -and -not $receiptUncertain) {
            $headFence.Dispose()
            $headFence = $null
        }
        # Keep the actual child handle until exit; detaching the mux is not exit.
        Wait-AwNativeSeedBackend $process $outputCopies.ToArray()
        $code = $process.ExitCode
        $returnedArgs = @('--phase', 'returned', '--exit-code', [string]$code) + $callbackArgs
        if ($receiptUncertain) { $returnedArgs += '--receipt-uncertain' }
        # Terminal observation uses the current provider after runtime replacement.
        $null = Invoke-AwNativeSeedPhase $Root $returnedArgs
        if ($code -eq 0 -and $receiptUncertain) { return 3 }
        return $code
    } finally {
        if ($headFence) { $headFence.Dispose() }
        if ($listener) { $listener.Stop() }
        if ($consoleControl) {
            $null = [AwNativeSeed.ConsoleControl]::SetConsoleCtrlHandler($consoleControl.Pointer, $false)
            [GC]::KeepAlive($consoleControl.Callback)
        }
        if ($nativeChild) { $nativeChild.Dispose() } else { $process.Dispose() }
    }
}
