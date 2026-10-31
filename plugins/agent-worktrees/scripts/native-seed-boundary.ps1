#Requires -Version 7.0
# agent-worktrees:native-seed-boundary-v1
# Dot-sourced into an existing lifetime owner, never launched as another waiter.

function Get-AwNativeSeedRuntime {
    param([string]$Root)
    if (-not [IO.Path]::IsPathRooted($Root)) { return $null }
    try {
        $version = [IO.File]::ReadAllText((Join-Path $Root 'current-version')).Trim()
        if ($version -notmatch '^[A-Za-z0-9][A-Za-z0-9._+-]{0,255}$') { return $null }
        $slot = Join-Path (Join-Path $Root 'versions') $version
        $python = Join-Path $slot 'Scripts\python.exe'
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
    param([string]$Root, [string[]]$Arguments)
    $runtime = Get-AwNativeSeedRuntime $Root
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
        if ($process.ExitCode -ne 0) { return $null }
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

function Invoke-AwNativeSeedBackend {
    param(
        [string]$Root,
        [string]$Record,
        [string]$SeedId,
        [string[]]$Command,
        [string]$LaunchAttemptId,
        [string]$LaunchResolutionId,
        [string]$LaunchParentId
    )
    $prepareArgs = @('--phase', 'prepare', '--record', $Record)
    if ($SeedId) { $prepareArgs += @('--seed-id', $SeedId) }
    foreach ($pair in @(
        @('--launch-attempt-id', $LaunchAttemptId),
        @('--launch-resolution-id', $LaunchResolutionId),
        @('--launch-parent-id', $LaunchParentId)
    )) {
        if ($pair[1]) { $prepareArgs += @($pair[0], $pair[1]) }
    }
    $prepared = Invoke-AwNativeSeedPhase $Root ($prepareArgs + @('--') + $Command)
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
    }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    $receiptUncertain = $false
    $outputCopies = [Collections.Generic.List[object]]::new()
    $consoleControl = $null
    try {
        $consoleControl = Register-AwNativeSeedConsoleControl
        try {
            if (-not $process.Start()) {
                [Console]::Error.WriteLine('Native backend start is unconfirmed; the handoff is retained.')
                return 3
            }
        } catch [ComponentModel.Win32Exception] {
            $null = Invoke-AwNativeSeedPhase $Root (@('--phase', 'spawn-failed') + $callbackArgs)
            [Console]::Error.WriteLine('Native backend could not start; inspect its retained launch outcome.')
            return 3
        }
        # Explicit output handles also preserve the original stdin handle in
        # consoleless children. Never pipe a real terminal's interactive streams.
        if ($info.RedirectStandardOutput) {
            $source = $process.StandardOutput.BaseStream
            $outputCopies.Add([pscustomobject]@{
                Source = $source; Task = $source.CopyToAsync([Console]::OpenStandardOutput())
                Draining = $false; Complete = $false
            })
        }
        if ($info.RedirectStandardError) {
            $source = $process.StandardError.BaseStream
            $outputCopies.Add([pscustomobject]@{
                Source = $source; Task = $source.CopyToAsync([Console]::OpenStandardError())
                Draining = $false; Complete = $false
            })
        }
        try {
            $birth = $process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(
                [Globalization.CultureInfo]::InvariantCulture
            )
            $spawned = Invoke-AwNativeSeedPhase $Root (
                @('--phase', 'spawned', '--pid', [string]$process.Id, '--process-start-time', $birth) +
                $callbackArgs
            )
            $receiptUncertain = -not ($spawned -and $spawned.recorded -eq $true)
        } catch { $receiptUncertain = $true }
        # Keep the actual child handle until exit; detaching the mux is not exit.
        Wait-AwNativeSeedBackend $process $outputCopies.ToArray()
        $code = $process.ExitCode
        $returnedArgs = @('--phase', 'returned', '--exit-code', [string]$code) + $callbackArgs
        if ($receiptUncertain) { $returnedArgs += '--receipt-uncertain' }
        $null = Invoke-AwNativeSeedPhase $Root $returnedArgs
        if ($code -eq 0 -and $receiptUncertain) { return 3 }
        return $code
    } finally {
        if ($consoleControl) {
            $null = [AwNativeSeed.ConsoleControl]::SetConsoleCtrlHandler($consoleControl.Pointer, $false)
            [GC]::KeepAlive($consoleControl.Callback)
        }
        $process.Dispose()
    }
}
