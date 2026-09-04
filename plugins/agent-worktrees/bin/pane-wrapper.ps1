#Requires -Version 7.0
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
            Add-Type -Namespace 'AwProcessOwnership' -Name 'PaneJob' -MemberDefinition @'
[StructLayout(LayoutKind.Sequential)]
public struct IO_COUNTERS { public ulong ReadOperationCount; public ulong WriteOperationCount; public ulong OtherOperationCount; public ulong ReadTransferCount; public ulong WriteTransferCount; public ulong OtherTransferCount; }
[StructLayout(LayoutKind.Sequential)]
public struct JOBOBJECT_BASIC_LIMIT_INFORMATION { public long PerProcessUserTimeLimit; public long PerJobUserTimeLimit; public uint LimitFlags; public UIntPtr MinimumWorkingSetSize; public UIntPtr MaximumWorkingSetSize; public uint ActiveProcessLimit; public UIntPtr Affinity; public uint PriorityClass; public uint SchedulingClass; }
[StructLayout(LayoutKind.Sequential)]
public struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION { public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation; public IO_COUNTERS IoInfo; public UIntPtr ProcessMemoryLimit; public UIntPtr JobMemoryLimit; public UIntPtr PeakProcessMemoryUsed; public UIntPtr PeakJobMemoryUsed; }
[DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string lpName);
[DllImport("kernel32.dll", SetLastError=true)] public static extern bool SetInformationJobObject(IntPtr hJob, int JobObjectInfoClass, IntPtr lpJobObjectInfo, uint cbJobObjectInfoLength);
[DllImport("kernel32.dll", SetLastError=true)] public static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);
[DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr GetCurrentProcess();
public const int JobObjectExtendedLimitInformation = 9;
public const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;
'@ -ErrorAction Stop
        }
        $t = [AwProcessOwnership.PaneJob]
        $h = $t::CreateJobObject([IntPtr]::Zero, $null)
        if ($h -eq [IntPtr]::Zero) { return }
        $info = New-Object 'AwProcessOwnership.PaneJob+JOBOBJECT_EXTENDED_LIMIT_INFORMATION'
        $info.BasicLimitInformation.LimitFlags = $t::JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        $len = [Runtime.InteropServices.Marshal]::SizeOf($info)
        $ptr = [Runtime.InteropServices.Marshal]::AllocHGlobal($len)
        try {
            [Runtime.InteropServices.Marshal]::StructureToPtr($info, $ptr, $false)
            if (-not $t::SetInformationJobObject($h, $t::JobObjectExtendedLimitInformation, $ptr, [uint32]$len)) { return }
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
$initialPromptB64 = ''
$initialPromptReceiptB64 = ''
$ahpTokenFile = ''
while ($rest.Count -ge 2) {
    $key = [string]$rest[0]
    if ($key -eq '-AwWt') {
        $awWt = [string]$rest[1]
    } elseif ($key -eq '--aw-prompt-b64') {
        $initialPromptB64 = [string]$rest[1]
    } elseif ($key -eq '--aw-prompt-receipt-b64') {
        $initialPromptReceiptB64 = [string]$rest[1]
    } elseif ($key -eq '-AwAhpTokenFile') {
        $ahpTokenFile = [string]$rest[1]
    } else {
        break
    }
    $rest = if ($rest.Count -gt 2) { @($rest[2..($rest.Count - 1)]) } else { @() }
}

if ($rest.Count -eq 0) { exit 0 }

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
    & $rest[0] @($rest[1..($rest.Count - 1)])
    $exitCode = $LASTEXITCODE
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
