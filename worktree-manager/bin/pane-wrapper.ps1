#Requires -Version 7.0
# worktree-manager:pane-boundary-v1
# worktree-manager:native-console-owner-v1
# pane-wrapper.ps1 -- Windows counterpart of pane-wrapper.sh. Wraps the psmux
# pane command so the child's real exit code is observable (the launcher cannot
# see it -- the child runs inside the pane), records a durable `pane_exited`
# activity mark, and shows a crash diagnostic before the pane closes.
#
# Behavior (mirrors pane-wrapper.sh):
#   exit 0, runtime >= threshold : exit 0 silently (normal session end)
#   exit 130 (Ctrl+C)            : exit 0 silently (intentional interrupt)
#   exit 0, runtime < threshold  : pause with diagnostic (startup crash)
#   any other non-zero exit      : pause with diagnostic (error/crash)
#
# Always exits 0 so the pane isn't trapped. Uses $args with NO param() block so
# `--allow-all` and other `--`-prefixed passthrough args are never treated as
# parameters to this wrapper (the same re-tokenization hazard documented in the
# #102 note in launch-session.ps1). An optional leading `-AwWt <id>` carries the
# worktree id for the activity mark and is consumed here (never forwarded).
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$minRuntime = if ($env:WORKTREE_PANE_MIN_RUNTIME) { [int]$env:WORKTREE_PANE_MIN_RUNTIME } else { 3 }
$waitTimeout = if ($env:WORKTREE_PANE_WAIT_TIMEOUT) { [int]$env:WORKTREE_PANE_WAIT_TIMEOUT } else { 60 }
$promptStartupGrace = if ($env:WORKTREE_PROMPT_STARTUP_GRACE) { [int]$env:WORKTREE_PROMPT_STARTUP_GRACE } else { 3 }

# --- Owner-tether: reap the whole pane subtree when this pane exits (#1433) ----
# This wrapper is the pane's root process; the Copilot session (and its node /
# conhost descendants) run beneath it. When a psmux pane is closed or reaped,
# Windows terminates only this root process -- its descendants are orphaned,
# reparent to a non-interactive svchost, and accumulate for days (#1433, #713).
#
# Placing this process in a Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
# makes the OS terminate every process in the job the instant the last handle to
# the job closes -- which happens when THIS process exits by any means (the user
# quitting Copilot, psmux kill-session during a reap/finalize, or a force-close).
# Children spawned after assignment inherit the job, so the whole Copilot subtree
# is covered. This is the kernel-level, event-driven form of the fabric's
# owner-liveness tether (the pane's logical owner IS this wrapper's lifetime), so
# no descendant can outlive the pane. Detaching a psmux client does NOT exit this
# wrapper (the session persists), so reattach-never-kill is preserved -- only an
# actual pane exit/reap fires the kill.
#
# Fully guarded and fail-open: any failure (old OS, P/Invoke error, already in a
# non-nestable job) leaves behavior exactly as before. The job handle is kept in
# a script-scoped variable for this process's lifetime on purpose -- closing it
# early would fire the kill immediately. Escape hatch: WORKTREE_NO_PANE_JOB=1.
#
# This mirrors agent-bridge's proven `winjob.setup_kill_on_close_job()` (the
# daemon orphan-prevention job, #90) -- the same CreateJobObject +
# KILL_ON_JOB_CLOSE + AssignProcessToJobObject(self) pattern, in PowerShell for
# the launcher hot path. (Pure orphan-prevention: no BREAKAWAY_OK -- children
# inherit and die with the pane; the breakaway flag is only for a survivor that
# must escape its owner's job, which a pane never does.)
function Set-AwPaneKillOnCloseJob {
    if ($env:WORKTREE_NO_PANE_JOB -eq '1') { return }
    if (-not $IsWindows) { return }
    try {
        if (-not ('AwProcessOwnership.PaneJob' -as [type])) {
            # Bind native calls without starting the C# compiler on the pane hot path.
            $assembly = [Reflection.Emit.AssemblyBuilder]::DefineDynamicAssembly(
                [Reflection.AssemblyName]::new('AwProcessOwnership'),
                [Reflection.Emit.AssemblyBuilderAccess]::Run
            )
            $type = $assembly.DefineDynamicModule('PaneJob').DefineType(
                'AwProcessOwnership.PaneJob', [Reflection.TypeAttributes]'Public, Abstract, Sealed'
            )
            $bindings = @(
                @('CreateJobObject', 'CreateJobObjectW', [IntPtr], [type[]]@([IntPtr], [string])),
                @('SetInformationJobObject', 'SetInformationJobObject', [bool], [type[]]@([IntPtr], [int], [IntPtr], [uint32])),
                @('AssignProcessToJobObject', 'AssignProcessToJobObject', [bool], [type[]]@([IntPtr], [IntPtr])),
                @('GetCurrentProcess', 'GetCurrentProcess', [IntPtr], [type[]]@())
            )
            foreach ($binding in $bindings) {
                $method = $type.DefinePInvokeMethod(
                    $binding[0], 'kernel32.dll', $binding[1],
                    [Reflection.MethodAttributes]'Public, Static, PinvokeImpl',
                    [Reflection.CallingConventions]::Standard, $binding[2], $binding[3],
                    [Runtime.InteropServices.CallingConvention]::Winapi,
                    [Runtime.InteropServices.CharSet]::Unicode
                )
                $method.SetImplementationFlags([Reflection.MethodImplAttributes]::PreserveSig)
            }
            $null = $type.CreateType()
        }
        $t = [AwProcessOwnership.PaneJob]
        $h = $t::CreateJobObject([IntPtr]::Zero, $null)
        if ($h -eq [IntPtr]::Zero) { return }
        # Windows' extended-limit ABI is 144 bytes on 64-bit, 112 on 32-bit;
        # LimitFlags follows the two 64-bit time limits at byte 16 on both.
        $len = if ([IntPtr]::Size -eq 8) { 144 } else { 112 }
        $ptr = [Runtime.InteropServices.Marshal]::AllocHGlobal($len)
        try {
            [Runtime.InteropServices.Marshal]::Copy([byte[]]::new($len), 0, $ptr, $len)
            [Runtime.InteropServices.Marshal]::WriteInt32($ptr, 16, 0x2000)
            if (-not $t::SetInformationJobObject($h, 9, $ptr, [uint32]$len)) { return }
            $null = $t::AssignProcessToJobObject($h, $t::GetCurrentProcess())
        } finally {
            [Runtime.InteropServices.Marshal]::FreeHGlobal($ptr)
        }
        # Keep the handle alive for this process's lifetime (do NOT close it):
        # the kill fires when the last handle closes -- which we want to be OUR
        # exit, not now. The OS releases it when this process terminates.
        $script:AwPaneJobHandle = $h
    } catch {}
}
Set-AwPaneKillOnCloseJob

$rest = @($args)
$awWt = ''
$awProject = ''
$awMuxSession = ''
$awPsmuxBin = ''
$awManagerRoot = ''
$initialPromptB64 = ''
$initialPromptReceiptB64 = ''
$ahpTokenFile = ''
while ($rest.Count -ge 2) {
    $key = [string]$rest[0]
    if ($key -eq '-AwWt') {
        $awWt = [string]$rest[1]
    } elseif ($key -eq '-AwProject') {
        $awProject = [string]$rest[1]
    } elseif ($key -eq '-AwMuxSession') {
        $awMuxSession = [string]$rest[1]
    } elseif ($key -eq '-AwPsmuxBin') {
        $awPsmuxBin = [string]$rest[1]
    } elseif ($key -eq '-AwManagerRoot') {
        $awManagerRoot = [string]$rest[1]
    } elseif ($key -eq '--aw-prompt-b64') {
        $initialPromptB64 = [string]$rest[1]
    } elseif ($key -eq '--aw-prompt-receipt-b64') {
        $initialPromptReceiptB64 = [string]$rest[1]
    } elseif ($key -eq '-AwAhpTokenFile') {
        $ahpTokenFile = [string]$rest[1]
    } else {
        break
    }
    # Direct branch-local assignment (not `$rest = if (...) {...} else {...}`):
    # PowerShell's if-as-expression assignment silently unwraps a single-
    # element array result to a bare scalar even when each branch forces
    # array typing with @() -- exactly the shape reached here once every
    # known control-flag pair has been stripped and one payload token (e.g.
    # bare `copilot`) remains. Assigning inside each branch instead of
    # through the if-expression's return value avoids that unwrap.
    if ($rest.Count -gt 2) {
        $rest = @($rest[2..($rest.Count - 1)])
    } else {
        $rest = @()
    }
}

if ($rest.Count -eq 0) { exit 0 }

function Invoke-AwPaneBoundaryProcess {
    param([string]$FileName, [string[]]$Arguments, [int]$TimeoutMs = 5000)
    $process = $null
    $script:AwPaneBoundaryProcessStatus = 'unavailable'
    try {
        $info = [Diagnostics.ProcessStartInfo]::new($FileName)
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardInput = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        foreach ($name in @('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', '__PYVENV_LAUNCHER__',
                             'GH_TOKEN', 'GITHUB_TOKEN', 'AGENT_WORKTREES_AHP_AUTH_TOKEN')) {
            $null = $info.Environment.Remove($name)
        }
        $info.Environment['WORKTREE_MANAGER_ROOT'] = $awManagerRoot
        foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
        $process = [Diagnostics.Process]::Start($info)
        $process.StandardInput.Close()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutMs)) {
            $script:AwPaneBoundaryProcessStatus = 'timeout'
            $process.Kill($true)
            $null = $process.WaitForExit(1000)
            return $null
        }
        if (-not [Threading.Tasks.Task]::WaitAll(
            [Threading.Tasks.Task[]]@($stdout, $stderr), 1000
        )) {
            $script:AwPaneBoundaryProcessStatus = 'output-unconfirmed'
            return $null
        }
        if ($process.ExitCode -ne 0) {
            $script:AwPaneBoundaryProcessStatus = "exit:$($process.ExitCode)"
            return $null
        }
        $script:AwPaneBoundaryProcessStatus = 'completed'
        return $stdout.Result
    } catch {
        if ($process) {
            try { if (-not $process.HasExited) { $process.Kill($true) } } catch {}
        }
        return $null
    } finally {
        if ($process) { $process.Dispose() }
    }
}

function Invoke-AwPaneBoundary {
    param([string]$Action, [string]$Installation, [int]$ObservedExit = 0)
    try {
        if (-not [IO.Path]::IsPathRooted($awManagerRoot)) { return $null }
        $version = [IO.File]::ReadAllText((Join-Path $awManagerRoot 'current-version')).Trim()
        if ($version -notmatch '^[A-Za-z0-9][A-Za-z0-9._+-]{0,255}$') { return $null }
        $slot = Join-Path (Join-Path $awManagerRoot 'versions') $version
        $python = Join-Path $slot 'Scripts\python.exe'
        $bootstrap = Join-Path $slot '.runtime-bootstrap\runtime_launcher.py'
        foreach ($path in @($python, $bootstrap, (Join-Path $slot '.install-complete.json'))) {
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
        }
        $boundaryArgs = @(
            '-I', '-S', '-B', '-X', 'utf8', $bootstrap,
            '-m', 'worktree_manager.pane_lifecycle', $Action,
            '--contract-version', '1',
            '--root', $awManagerRoot, '--project', $awProject, '--worktree-id', $awWt,
            '--mux-session', $awMuxSession, '--session-incarnation', $awSessionIncarnation,
            '--pane-id', $awNativePane
        )
        if ($Action -eq 'start') {
            $boundaryArgs += @('--worktree-path', $PWD.ProviderPath, '--mux-bin', $awPsmuxBin)
        } else {
            $boundaryArgs += @('--expected-installation', $Installation)
            if ($Action -eq 'end') {
                $boundaryArgs += @('--exit-code', "$ObservedExit", '--runtime', "$runtime")
                if ($env:WORKTREE_LAUNCH_ID) { $boundaryArgs += @('--launch-id', $env:WORKTREE_LAUNCH_ID) }
            }
        }
        $raw = Invoke-AwPaneBoundaryProcess $python $boundaryArgs
        if (-not $raw) { return $null }
        $reply = ConvertFrom-Json -InputObject $raw -ErrorAction Stop
        if (
            $reply.schema -ne 'worktree-manager.pane-lifecycle' -or $reply.version -ne 1 -or
            $reply.action -ne $Action -or $reply.installation -notmatch '^[0-9a-f]{64}$' -or
            $reply.result.applied -ne $true
        ) { return $null }
        return $reply
    } catch { return $null }
}

$awSessionIncarnation = ''
$awNativePane = ''
$awBoundaryInstallation = ''
$awChildAttempted = $false
if ($awManagerRoot -and $awProject -and $awWt -and $awMuxSession -and $awPsmuxBin) {
    $native = Invoke-AwPaneBoundaryProcess $awPsmuxBin @(
        'display-message', '-p', '#{session_id}:#{session_created}|#{pane_id}'
    ) 2000
    if ($native -and $native.Trim() -match '^(\$[0-9]+:[0-9]+)\|(%[0-9]+)$') {
        $awSessionIncarnation = $Matches[1]
        $awNativePane = $Matches[2]
        $admitted = Invoke-AwPaneBoundary 'start'
        if ($admitted) { $awBoundaryInstallation = $admitted.installation }
    }
}

# Retire validation failures without claiming an observed child exit.
try {
$ahpChildToken = $null
$ahpChildFeatures = $null
if (-not [string]::IsNullOrWhiteSpace($ahpTokenFile)) {
    try {
        $encryptedToken = [IO.File]::ReadAllText($ahpTokenFile)
        $secureToken = $encryptedToken | ConvertTo-SecureString
        $tokenPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR(
            $secureToken
        )
        try {
            $ahpChildToken = (
                [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPtr)
            )
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPtr)
        }
        if ([string]::IsNullOrWhiteSpace($ahpChildToken)) {
            throw 'empty AHP token handoff'
        }
        $featureList = @(
            ([string]$env:COPILOT_CLI_ENABLED_FEATURE_FLAGS -split ',')
            | ForEach-Object { $_.Trim() }
            | Where-Object { $_ }
        )
        if ($featureList -notcontains 'AHP_CLIENT') {
            $featureList += 'AHP_CLIENT'
        }
        $ahpChildFeatures = $featureList -join ','
    } catch {
        [Console]::Error.WriteLine(
            "[agent-worktrees] invalid AHP token handoff: $($_.Exception.Message)"
        )
        exit 2
    } finally {
        Remove-Item -LiteralPath $ahpTokenFile `
            -Force -ErrorAction SilentlyContinue
    }
}

# Native interactive handoff seed. psmux cannot preserve a multi-word pane argv
# element, so handoff-cutover sends UTF-8 base64 + a receipt token as space-free
# wrapper control arguments. Decode after psmux has reconstructed this argv,
# append the real Copilot flag as a PowerShell array element, then acknowledge
# before the child starts.
if (-not [string]::IsNullOrWhiteSpace($initialPromptB64)) {
    try {
        $initialPrompt = [Text.Encoding]::UTF8.GetString(
            [Convert]::FromBase64String($initialPromptB64)
        )
        $receiptPath = [Text.Encoding]::UTF8.GetString(
            [Convert]::FromBase64String($initialPromptReceiptB64)
        )
        if ([string]::IsNullOrWhiteSpace($receiptPath)) {
            throw 'missing initial-prompt receipt path'
        }
        # Use the long form: when the child is a PowerShell script launcher,
        # short -i is consumed by PowerShell's parameter binder and rejected as
        # an ambiguous common-parameter abbreviation before ValueFromRemainingArguments.
        $rest += @('--interactive', $initialPrompt)
        $receiptDir = Split-Path -Parent $receiptPath
        New-Item -ItemType Directory -Path $receiptDir -Force -ErrorAction Stop | Out-Null
        $receiptTmp = "$receiptPath.$PID.tmp"
        [IO.File]::WriteAllText($receiptTmp, 'launching')
        Move-Item -LiteralPath $receiptTmp -Destination $receiptPath -Force -ErrorAction Stop
    } catch {
        [Console]::Error.WriteLine(
            "[agent-worktrees] invalid initial-prompt transport: $($_.Exception.Message)"
        )
        exit 2
    }
}

$start = Get-Date
$awChildAttempted = $true
$awConsoleControl = $null
$awConsoleHelper = Join-Path $PSScriptRoot 'native-seed-boundary.ps1'
try {
if (Test-Path -LiteralPath $awConsoleHelper -PathType Leaf) {
    . $awConsoleHelper
    $awConsoleControl = Register-AwNativeSeedConsoleControl
}
if ($ahpChildToken) {
    try {
        $startInfo = [Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = [string]$rest[0]
        $startInfo.UseShellExecute = $false
        foreach ($argument in @($rest[1..($rest.Count - 1)])) {
            $startInfo.ArgumentList.Add([string]$argument)
        }
        $startInfo.Environment['GH_TOKEN'] = $ahpChildToken
        $null = $startInfo.Environment.Remove('GITHUB_TOKEN')
        $null = $startInfo.Environment.Remove(
            'AGENT_WORKTREES_AHP_AUTH_TOKEN'
        )
        $startInfo.Environment['COPILOT_CLI_ENABLED_FEATURE_FLAGS'] = (
            $ahpChildFeatures
        )
        $child = [Diagnostics.Process]::Start($startInfo)
        $ahpChildToken = $null
        $child.WaitForExit()
        $exitCode = $child.ExitCode
    } catch {
        [Console]::Error.WriteLine(
            "[agent-worktrees] AHP child launch failed: $($_.Exception.Message)"
        )
        $exitCode = 1
    }
} else {
    $LASTEXITCODE = $null
    try {
        & $rest[0] @($rest[1..($rest.Count - 1)])
        $commandSucceeded = $?
        $exitCode = $LASTEXITCODE
        if (-not $commandSucceeded -and $null -eq $exitCode) { $exitCode = 127 }
    } catch {
        [Console]::Error.WriteLine("[agent-worktrees] child launch failed: $($_.Exception.Message)")
        $exitCode = 127
    }
}
} finally {
    if ($awConsoleControl) {
        $null = [AwNativeSeed.ConsoleControl]::SetConsoleCtrlHandler($awConsoleControl.Pointer, $false)
        [GC]::KeepAlive($awConsoleControl.Callback)
    }
}
if ($null -eq $exitCode) { $exitCode = 0 }
$runtime = [int]((Get-Date) - $start).TotalSeconds

# A prompt receipt is provisional until the child survives startup. If native
# --interactive is rejected or the launcher fails immediately, overwrite it so
# the parent keeps the predecessor and reaps this failed successor.
if ($receiptPath -and (
        $exitCode -ne 0 -or $runtime -lt $promptStartupGrace
    )) {
    try {
        $receiptTmp = "$receiptPath.$PID.tmp"
        [IO.File]::WriteAllText($receiptTmp, "failed:$exitCode")
        Move-Item -LiteralPath $receiptTmp -Destination $receiptPath -Force
    } catch {}
}

if ($awManagerRoot) {
    if ($awBoundaryInstallation) {
        $null = Invoke-AwPaneBoundary 'end' $awBoundaryInstallation $exitCode
    }
} else {
# Legacy callers retain their prior callbacks until migrated to copied shells.
# Durable pane-exit mark (Tier-A): the only place the psmux pane's real exit code
# is observable. Best-effort, detached, fail-silent -- must never delay pane
# teardown. Correlates to the launch flow via WORKTREE_LAUNCH_ID (inherited from
# the mux server env).
try {
    # Launch the resolved runtime python directly -- NOT the bare
    # `agent-worktrees` stub. `Start-Process -FilePath 'agent-worktrees'` goes
    # through ShellExecute; PowerShell command-discovery resolves the bare name
    # to the `.ps1` binstub (preferred over the `.cmd`), and ShellExecute of a
    # `.ps1` runs its "edit" file association -- opening the stub in Notepad
    # instead of executing it. Running `python.exe -m agent_worktrees` is a real
    # executable and is executed, not opened.
    $_r = Join-Path $env:USERPROFILE '.agent-worktrees\bin\resolve-runtime.ps1'
    $awPy = if (Test-Path -LiteralPath $_r) { . $_r; $AwPy } else { $null }
    if ($awPy) {
        $env:PYTHONPATH = ''  # package is installed in the venv (no lib/ shadow)
        $awArgs = @('-m', 'agent_worktrees', 'activity-log', 'pane_exited',
                    '--source', 'launcher',
                    '--field', "exit_code=$exitCode", '--field', "runtime=$runtime")
        if ($awWt) { $awArgs += @('--worktree-id', $awWt) }
        if ($env:WORKTREE_LAUNCH_ID) { $awArgs += @('--launch-id', $env:WORKTREE_LAUNCH_ID) }
        # conhost --headless: -WindowStyle Hidden alone is ignored by the DefTerm
        # handoff and can flash a console; conhost gives the writer its own headless
        # console so its output stays isolated (windows-launch-hardening #786). The
        # returned conhost handle exits when the wrapped writer does, so the bounded
        # WaitForExit below still lets the durable mark land.
        $script:AwActivityProc = Start-Process -FilePath 'conhost.exe' `
            -ArgumentList (@('--headless', "`"$awPy`"") + $awArgs) `
            -WindowStyle Hidden -PassThru -ErrorAction Stop
    }
} catch {}

# The pane_exited writer above is a member of this pane's kill-on-close job, so
# it would be terminated the instant this wrapper exits. It is a sub-second local
# write; wait briefly for it to finish so the durable mark lands before the job
# reaps the subtree. Bounded + fail-silent -- never let a stuck write trap the
# pane.
if ($script:AwActivityProc) {
    try { $null = $script:AwActivityProc.WaitForExit(5000) } catch {}
}
if (
    -not [string]::IsNullOrWhiteSpace($awProject) -and
    -not [string]::IsNullOrWhiteSpace($awWt)
) {
    try {
        $managerRoot = Split-Path -Parent $PSScriptRoot
        . (Join-Path $managerRoot 'bin/manager-runtime.ps1')
        $removeArgs = @(
            'remove',
            "--project=$awProject", "--worktree-id=$awWt"
        )
        # `--mux-session`, when known (#3838 fix): lets the daemon-side
        # registry reject this removal as a stale no-op if a newer
        # session already superseded this one for the same
        # (project, worktree_id) -- an old pane's delayed teardown must
        # never tombstone a newer, still-live session's mapping.
        if (-not [string]::IsNullOrWhiteSpace($awMuxSession)) {
            $removeArgs += "--mux-session=$awMuxSession"
        }
        # The real mux session's own stable identity (Copilot review
        # finding on PR #3906): the display session name alone is NOT
        # unique per launch incarnation -- both launchers derive the same
        # deterministic `wt-<worktree_id>`-shaped name for the same
        # worktree. psmux's own #{session_id}:#{session_created} IS unique
        # per real session object and stays identical across repeated joins
        # of the same still-live session -- query it live from inside this
        # same pane rather than trusting a threaded value. Use the
        # launcher's ALREADY-resolved binary when known (Copilot review
        # finding): a bare `psmux` on PATH can silently resolve to a
        # different/missing binary, making this probe fail silently and
        # disabling the guard without any visible error.
        $psmuxBin = if (-not [string]::IsNullOrWhiteSpace($awPsmuxBin)) { $awPsmuxBin } else { 'psmux' }
        try {
            $incarnation = (& $psmuxBin display-message -p '#{session_id}:#{session_created}' 2>$null)
            if (-not [string]::IsNullOrWhiteSpace([string]$incarnation)) {
                $removeArgs += "--session-incarnation=$incarnation"
            }
        } catch {}
        # Wait for the canonical spawn handshake, not the metadata operation.
        # Its worker survives the pane's kill-on-close Job via agent-procutil.
        Invoke-ManagerMuxCallback -PayloadRoot $managerRoot -CallbackArguments $removeArgs
    } catch {}
}
}

# Intentional interrupt -- exit silently so post-exit finalization runs.
if ($exitCode -eq 130) { exit 0 }
# Normal exit after running long enough -- nothing to report.
if ($exitCode -eq 0 -and $runtime -ge $minRuntime) { exit 0 }

# Something worth showing the user -- crash, error, or suspiciously fast exit.
Write-Host ''
Write-Host '------------------------------------------------------------'
if ($exitCode -eq 0) {
    Write-Host "  Session exited immediately (runtime: ${runtime}s)"
    Write-Host '  This usually means a startup error occurred.'
} elseif ($exitCode -ge 128) {
    Write-Host "  Session terminated abnormally (exit code $exitCode)"
} else {
    Write-Host "  Session exited with code $exitCode"
}
Write-Host ''
if ($env:WORKTREE_SETUP_LOG -and (Test-Path $env:WORKTREE_SETUP_LOG)) {
    Write-Host "  Setup log: $env:WORKTREE_SETUP_LOG"
    Write-Host ''
}
Write-Host "  Press any key to close, or wait ${waitTimeout}s..."
Write-Host '------------------------------------------------------------'
# Timed wait for a keypress (bash uses `read -t`); poll KeyAvailable so the
# timeout is honored. No console (KeyAvailable throws) -> just sleep the timeout.
try {
    $deadline = (Get-Date).AddSeconds($waitTimeout)
    while ((Get-Date) -lt $deadline) {
        if ([Console]::KeyAvailable) { [Console]::ReadKey($true) | Out-Null; break }
        Start-Sleep -Milliseconds 150
    }
} catch {
    Start-Sleep -Seconds $waitTimeout
}
exit 0
} finally {
    if (-not $awChildAttempted -and $awBoundaryInstallation) {
        $null = Invoke-AwPaneBoundary 'cancel' $awBoundaryInstallation
    }
}
