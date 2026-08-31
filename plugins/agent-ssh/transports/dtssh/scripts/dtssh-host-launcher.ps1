#Requires -Version 7.0
<#
.SYNOPSIS
    Self-healing launcher for `dtssh host` — exposes THIS machine's user-side
    interactive sessions (agent-worktrees / psmux / copilot) over SSH-via-DevTunnel
    so they can be joined and driven from other mesh boxes, and keeps that reach
    alive.

.DESCRIPTION
    Runs as the interactive user (launched by a Startup-folder shortcut at logon,
    or manually via `install-host.ps1 start`), NOT as a scheduled task or Windows
    service. This is deliberate on two counts:

      1. What we reach is user-side. The psmux sessions and the copilot agents
         inside them only exist within the user's interactive logon and need the
         user's own ADO/GitHub credentials. A headless/SYSTEM host would have
         nothing to attach and none of the user's auth — so binding the host to
         the interactive session is correct, not a limitation.
      2. Corp policy blocks non-elevated scheduled-task creation on these Dev
         Boxes, so a Startup-folder launcher is the reproducible no-admin
         mechanism.

    dtssh hosts a dedicated loopback sshd (default port 2222) that authenticates
    THIS interactive Entra user, so a remote `ssh <alias>` lands as the same user
    and can attach the running psmux server. `--persist` reuses a stable client
    identity + tunnel + host key across logons so the client-side alias and pinned
    host key stay valid.

    SELF-HEALING WATCHDOG. Bare `dtssh host --persist` does NOT always recover when
    the Azure relay silently drops the tunnel: the process keeps running and :2222
    keeps listening locally, but `devtunnel show` reports **0 host connections**, so
    remote clients can no longer reach the box. To fix that, this launcher runs
    `dtssh host` as a MONITORED child and, every -HealthCheckSec, queries the
    tunnel's relay host-connection count. If it reads 0 for -ConsecutiveFailures
    checks in a row (or the child exits), it restarts the child. Because of
    `--persist`, a restart REUSES the same tunnel id + host key (no rotation, no
    client re-`discover` needed).

    The relay count alone is NOT sufficient: the dtssh host can stay connected to
    the relay (host-conn > 0) while its dedicated sshd CHILD dies, leaving :$Port
    with no listener and remote reach silently broken until an interactive re-host
    (#576). So each cycle ALSO probes the sshd and restarts the child when it is
    not serving, even when the relay count looks healthy.

    The sshd probe reads the SSH IDENTIFICATION BANNER, not just a bare TCP
    connect. A bare connect is ALSO insufficient: a *wedged* sshd — one whose
    pre-auth slots are saturated because half-open/idle pre-auth connections
    piled up past `MaxStartups` (OpenSSH default 10:30:100) — keeps ACCEPTING the
    TCP connection but never sends its banner, so a connect-only probe reads
    healthy while remote reach is fully broken. Observed in the wild as a
    multi-day silent outage: 500+ Established pre-auth connections on :$Port,
    both the relay count and a bare-connect probe green, yet every `ssh <alias>`
    (even from the host itself) closed pre-banner. Reading the banner is the
    discriminator that lets the watchdog detect the wedge and restart — and the
    restart's Clear-DedicatedSshd reaps the piled-up connections. A soft
    early-warning also logs when the Established-connection count on :$Port
    crosses -PreAuthWarnThreshold, before saturation fully wedges the port.

    The tunnel id is resolved from dtssh's own persisted record
    (`%LOCALAPPDATA%\dtssh\host\service-<alias>.tunnel`), falling back to matching
    the alias against `devtunnel list` descriptions. If no tunnel id can be
    resolved, the launcher degrades to process-only monitoring (restart on child
    exit) so it never becomes worse than the old one-shot behavior.

    HEADLESS. The `dtssh host` child is started with a detached, redirected,
    out-of-process `Start-Process -NoNewWindow` — NOT in-process (`& $dtssh`).
    Invoking a long-running console app in-process from a hidden pwsh re-shows the
    launcher's console, popping a visible dtssh/pwsh window at logon. `-NoNewWindow`
    sets CreateNoWindow (CREATE_NO_WINDOW) so no console window is ever allocated —
    which, unlike `-WindowStyle Hidden`, the DefTerm handoff cannot re-show as a
    window (windows-launch-hardening #786). Redirected stdio keeps the host fully
    headless while the real child handle stays pid-trackable by the watchdog.

    Idempotent / single-instance: a named mutex + a running-host check ensure a
    second launch at logon (or a manual start) never double-hosts.

.PARAMETER Alias
    Client-side alias to publish — the canonical machine name, i.e. the machine's
    `machines.yaml` registry key (default: the lowercased COMPUTERNAME). Machines
    whose COMPUTERNAME differs from their desired alias MUST pass `-Alias`
    explicitly.

.PARAMETER Port
    Loopback port for the dedicated sshd (default 2222).

.PARAMETER Tunnel
    Optional explicit Dev Tunnel id to host (passed through to `dtssh host
    --tunnel`). Normally omitted — `--persist` reuses the recorded tunnel.

.PARAMETER User
    Optional user override passed through to `dtssh host --user`.

.PARAMETER HealthCheckSec
    Seconds between relay health checks (default 120).

.PARAMETER ConsecutiveFailures
    Number of consecutive 0-host-connection checks before restarting the child
    (default 2). With the default interval that heals a wedged tunnel in ~4 min.

.PARAMETER GracePeriodSec
    Seconds to wait after (re)starting `dtssh host` before the first health check
    (default 45) — lets the relay register the host before we judge it.

.PARAMETER PreAuthWarnThreshold
    Established-connection count on :$Port at which the launcher logs a soft
    pre-saturation warning (default 80 — below OpenSSH's default MaxStartups full
    cutoff of 100). Advisory only: the banner probe, not this count, drives
    restarts, so legitimate concurrent sessions are never force-killed by the
    count alone.

.PARAMETER PreAuthReapThreshold
    Established-connection count on :$Port at which the launcher PREEMPTIVELY
    restarts the host to reap the pile-up (default 128), before saturation fully
    wedges the port. This remains a last-resort fallback when process-tree
    classification is unavailable. When classification succeeds, the fallback
    defers while a command-bearing SSH session is active.

.PARAMETER IdleSessionWarnThreshold
    Top-level dedicated-sshd session trees with no non-sshd descendants above
    which the launcher logs released-build relay leakage (default 8).

.PARAMETER IdleSessionReapThreshold
    Idle session-tree count at which the launcher recycles the host (default
    16), but only when no command-bearing session tree is active. Idle
    forwarding-only sessions may be included; active forwarded TCP
    channels are classified command-bearing and protected.

.PARAMETER NoMonitor
    Legacy one-shot mode: start `dtssh host` hidden and exit, with no health
    monitoring. Kept as a fallback.

.NOTES
    Requires: dtssh on PATH or at %LOCALAPPDATA%\dtssh\bin\dtssh.exe, and a
    logged-in devtunnel session (`dtssh login`).
#>
param(
    [string]$Alias = "$(($env:COMPUTERNAME).ToLowerInvariant())",
    [int]$Port                = 2222,
    [string]$Tunnel,
    [string]$User,
    [int]$HealthCheckSec      = 120,
    [int]$ConsecutiveFailures = 2,
    [int]$GracePeriodSec      = 45,
    [int]$PreAuthWarnThreshold = 80,
    [int]$PreAuthReapThreshold = 128,
    [int]$IdleSessionWarnThreshold = 8,
    [int]$IdleSessionReapThreshold = 16,
    [switch]$NoMonitor
)

$ErrorActionPreference = 'Stop'

# ── Paths, logging ───────────────────────────────────────────────────────

$dtExe = if (Get-Command dtssh -ErrorAction SilentlyContinue) {
    (Get-Command dtssh).Source
} else {
    Join-Path $env:LOCALAPPDATA 'dtssh\bin\dtssh.exe'
}

# Make sure dtssh + the no-admin OpenSSH sshd are resolvable even when launched
# from a bare Startup pwsh that didn't inherit the prepped PATH.
foreach ($d in @((Join-Path $env:LOCALAPPDATA 'dtssh\bin'), (Join-Path $env:LOCALAPPDATA 'OpenSSH-Win64'))) {
    if ((Test-Path $d) -and ($env:Path -notlike "*$d*")) { $env:Path = "$d;$env:Path" }
}

$logDir   = Join-Path $env:LOCALAPPDATA 'agent-ssh-dtssh'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log      = Join-Path $logDir 'dtssh-host.log'
$childOut = Join-Path $logDir 'dtssh-host.out.log'
$childErr = Join-Path $logDir 'dtssh-host.err.log'
$MaxLogSizeBytes = 5MB

function Write-Log {
    param([string]$m, [string]$Level = 'INFO')
    $line = "$(Get-Date -Format o)  [$Level] $m"
    try {
        if ((Test-Path $log) -and (Get-Item $log).Length -gt $MaxLogSizeBytes) {
            $archive = "$log.1"
            if (Test-Path $archive) { Remove-Item $archive -Force -ErrorAction SilentlyContinue }
            Rename-Item $log $archive -ErrorAction SilentlyContinue
        }
        Add-Content -LiteralPath $log -Value $line -ErrorAction SilentlyContinue
    } catch { }
}

function Test-DevTunnelLogin {
    # dtssh has no `login --status` subcommand; query the bundled devtunnel CLI.
    param([Parameter(Mandatory)][string]$DtsshPath)
    $devtunnel = Join-Path (Split-Path $DtsshPath -Parent) 'devtunnel.exe'
    if (-not (Test-Path $devtunnel)) {
        $cmd = Get-Command devtunnel -ErrorAction SilentlyContinue
        if (-not $cmd) { return $false }
        $devtunnel = $cmd.Source
    }
    try {
        $json = & $devtunnel user show --json 2>$null | Out-String
        return ($json -match '"status"\s*:\s*"Logged in"')
    } catch { return $false }
}

if (-not (Test-Path $dtExe)) { Write-Log "ERROR: dtssh not found at $dtExe" 'ERROR'; exit 1 }

# ── Scrub inherited terminal-multiplexer session vars (nesting guard) ─────
# If this launcher was started from inside a psmux/tmux pane (e.g. a manual
# `install-host.ps1 start` from the operator's own terminal), it inherits
# TMUX / TMUX_PANE / PSMUX_SESSION[_NAME] pointing at that pane's session.
# Those propagate down to the `dtssh host` child, its dedicated sshd, and thus
# EVERY incoming SSH session -- which then believes it is nested inside a psmux
# session, so `psmux new` / create-attach refuses ("sessions should be nested
# with care, unset PSMUX_SESSION to force"). Clear them so hosted SSH sessions
# start with a clean, un-nested multiplexer environment.
$scrubbed = @()
foreach ($v in @('TMUX', 'TMUX_PANE', 'PSMUX_SESSION', 'PSMUX_SESSION_NAME')) {
    if (Test-Path "Env:$v") { Remove-Item "Env:$v" -ErrorAction SilentlyContinue; $scrubbed += $v }
}
if ($scrubbed.Count) { Write-Log "scrubbed inherited multiplexer session vars for hosted SSH sessions: $($scrubbed -join ', ')" }

# ── Tunnel-id resolution (for relay health checks) ───────────────────────

function Resolve-TunnelId {
    <#
      dtssh persists the tunnel id per alias at
      %LOCALAPPDATA%\dtssh\host\service-<alias>.tunnel. Prefer that; fall back to
      matching the alias against `devtunnel list` descriptions. Returns $null if
      it cannot be determined (→ process-only monitoring).
    #>
    param([string]$AliasName)

    $rec = Join-Path $env:LOCALAPPDATA "dtssh\host\service-$AliasName.tunnel"
    if (Test-Path $rec) {
        $id = (Get-Content $rec -Raw -ErrorAction SilentlyContinue).Trim()
        if ($id) { return $id }
    }

    # Fallback: scan devtunnel list for a dtssh tunnel advertising this alias.
    try {
        $raw = & devtunnel list 2>&1
        if ($LASTEXITCODE -eq 0) {
            foreach ($line in ($raw -split "`r?`n")) {
                if ($line -match '^\s*(\S+\.usw2|\S+\.\w{3,4})\s' -and $line -match 'dtssh') {
                    $cand = $Matches[1]
                    $desc = & devtunnel show $cand 2>&1 | Out-String
                    if ($desc -match "`"a`"\s*:\s*`"$([regex]::Escape($AliasName))`"") { return $cand }
                }
            }
        }
    } catch { }
    return $null
}

function Get-HostConnections {
    <# -1 = check failed/unknown, 0+ = relay host-connection count #>
    param([string]$TunnelId)
    try {
        $raw = & devtunnel show $TunnelId 2>&1
        if ($LASTEXITCODE -ne 0) { Write-Log "devtunnel show failed (exit $LASTEXITCODE): $raw" 'WARN'; return -1 }
        $text = $raw -join "`n"
        if ($text -match 'Host connections\s*:\s*(\d+)') { return [int]$Matches[1] }
        Write-Log "could not parse host connections from devtunnel show" 'WARN'
        return -1
    } catch { Write-Log "health check exception: $_" 'WARN'; return -1 }
}

# ── dtssh host process management ────────────────────────────────────────

function Get-RunningHostProc {
    Get-CimInstance Win32_Process -Filter "Name='dtssh.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match '\bhost\b' } | Select-Object -First 1
}

function Clear-DedicatedSshd {
    # dtssh spawns a dedicated child sshd on :$Port that it does NOT reap when the
    # host process dies. An orphaned listener then blocks the next start (it can't
    # bind :$Port, so the new host exits). Reap any sshd still listening on the
    # dedicated loopback port. Scoped to :$Port + Name='sshd' so the SYSTEM sshd
    # (:22) is never touched.
    try {
        $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        foreach ($spid in @($listeners.OwningProcess | Sort-Object -Unique | Where-Object { $_ })) {
            $sp = Get-Process -Id $spid -ErrorAction SilentlyContinue
            if ($sp -and $sp.Name -eq 'sshd') {
                Stop-Process -Id $spid -Force -ErrorAction SilentlyContinue
                Write-Log "reaped orphaned dedicated sshd (pid $spid) on :$Port"
            }
        }
    } catch { Write-Log "sshd reap failed: $_" 'WARN' }
}

function Test-SshdServing {
    <#
      True only if the dedicated sshd on 127.0.0.1:$ProbePort completes a TCP
      connect AND emits an SSH identification banner ("SSH-...") within the
      timeout.

      This is deliberately STRONGER than a bare TCP connect. Two failure modes it
      must catch:
        - #576: the sshd CHILD dies, so :$Port has no listener at all (connect
          fails outright).
        - the pre-auth WEDGE: sshd is alive and still ACCEPTS the TCP connection,
          but its pre-auth slots are saturated (half-open/idle pre-auth
          connections piled up past MaxStartups), so it never sends its banner.
          A connect-only probe reads healthy here while remote reach is fully
          broken. Reading the banner is the discriminator; a restart then reaps
          the piled-up connections via Clear-DedicatedSshd.

      An SSH server sends its identification string first, before the client
      writes anything, so a plain connect+read (no handshake) surfaces the banner.
      A read timeout / short read / non-"SSH-" prefix all count as NOT serving.
    #>
    param([int]$ProbePort, [int]$TimeoutMs = 4000)
    $client = $null
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $iar = $client.BeginConnect('127.0.0.1', $ProbePort, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne($TimeoutMs)) { return $false }
        $client.EndConnect($iar)
        if (-not $client.Connected) { return $false }
        $stream = $client.GetStream()
        $stream.ReadTimeout = $TimeoutMs
        $buf = [byte[]]::new(64)
        # Accumulate until we have enough bytes to test the "SSH-" prefix: a single
        # Read() can return a partial banner if it arrives split across TCP
        # segments, and a premature StartsWith check would false-negative and
        # needlessly restart a healthy sshd. A read timeout on a wedged sshd throws
        # (caught below => not serving).
        $got = 0
        while ($got -lt 4) {
            $n = $stream.Read($buf, $got, $buf.Length - $got)
            if ($n -le 0) { break }   # peer closed before sending a banner
            $got += $n
        }
        if ($got -lt 4) { return $false }
        return [System.Text.Encoding]::ASCII.GetString($buf, 0, $got).StartsWith('SSH-')
    } catch {
        # Includes the IOException thrown when the banner read times out on a
        # wedged sshd (accepts TCP, sends nothing) — correctly => not serving.
        return $false
    } finally {
        if ($client) { $client.Dispose() }
    }
}

function Get-EstablishedConnCount {
    <#
      Count Established TCP connections to the dedicated loopback sshd port. A
      large, growing count of pre-auth connections precedes a MaxStartups wedge;
      used as an early-warning signal (advisory — see PreAuthWarnThreshold).
      Returns -1 if the count can't be read.
    #>
    param([int]$ProbePort)
    try {
        return @(Get-NetTCPConnection -LocalPort $ProbePort -State Established -ErrorAction SilentlyContinue).Count
    } catch { return -1 }
}

function Get-DedicatedSshdSessionPressure {
    <#
      Classify the dedicated sshd's top-level session trees. A completed short
      dtssh command can leave the relay socket and its two sshd-session
      processes alive after every client process has exited. Those leaked trees
      have no command descendants. Interactive shells, commands, and SFTP
      sessions retain a non-sshd descendant and are classified active.

      A forwarding-only `ssh -N` session also has no command descendant, so the
      idle count is deliberately not called "provably stale". A tree that owns
      an Established TCP connection other than its inbound :$ProbePort socket
      is classified active, protecting an in-flight forwarded stream. Reaping
      is gated on zero command-bearing/forwarding roots; idle persistent
      forwards reconnect after the host recycle.
    #>
    param([int]$ProbePort)

    try {
        $listener = Get-NetTCPConnection -LocalPort $ProbePort -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $listener) { return $null }

        $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
        $byId = @{}
        foreach ($proc in $processes) { $byId[[int]$proc.ProcessId] = $proc }

        $children = @{}
        foreach ($proc in $processes) {
            $parent = [int]$proc.ParentProcessId
            $parentProc = $byId[$parent]
            if ($parentProc -and $proc.CreationDate -lt $parentProc.CreationDate) {
                continue  # stale ParentProcessId after PID reuse
            }
            if (-not $children.ContainsKey($parent)) { $children[$parent] = @() }
            $children[$parent] = @($children[$parent]) + $proc
        }

        $forwardingPids = [System.Collections.Generic.HashSet[int]]::new()
        foreach ($connection in @(
            Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue
        )) {
            if ($connection.LocalPort -ne $ProbePort) {
                [void]$forwardingPids.Add([int]$connection.OwningProcess)
            }
        }

        $roots = @($children[[int]$listener.OwningProcess] |
            Where-Object { $_.Name -eq 'sshd-session.exe' })
        $idle = 0
        $active = 0
        foreach ($root in $roots) {
            $queue = [System.Collections.Generic.Queue[int]]::new()
            $queue.Enqueue([int]$root.ProcessId)
            $seen = [System.Collections.Generic.HashSet[int]]::new()
            $hasCommandDescendant = $false
            while ($queue.Count -gt 0) {
                $current = $queue.Dequeue()
                if (-not $seen.Add($current)) { continue }
                if ($forwardingPids.Contains($current)) {
                    $hasCommandDescendant = $true
                }
                if (-not $children.ContainsKey($current)) { continue }
                foreach ($child in @($children[$current])) {
                    $queue.Enqueue([int]$child.ProcessId)
                    if ($child.Name -notin @('sshd-session.exe', 'conhost.exe')) {
                        $hasCommandDescendant = $true
                    }
                }
            }
            if ($hasCommandDescendant) { $active++ } else { $idle++ }
        }

        return [pscustomobject]@{
            TotalRoots  = $roots.Count
            IdleRoots   = $idle
            ActiveRoots = $active
        }
    } catch {
        Write-Log "sshd session-tree classification failed: $_" 'WARN'
        return $null
    }
}

function Start-HostProc {
    <# Start `dtssh host --persist` as a hidden monitored child; return the Process. #>
    Clear-DedicatedSshd   # free :$Port — a prior child may have orphaned its sshd
    $dtArgs = @('host', '--persist', '--alias', $Alias, '--port', "$Port")
    if ($Tunnel) { $dtArgs += @('--tunnel', $Tunnel) }
    if ($User)   { $dtArgs += @('--user', $User) }
    Write-Log "starting: dtssh $($dtArgs -join ' ')"
    # -NoNewWindow sets CreateNoWindow (CREATE_NO_WINDOW) so NO console is
    # allocated -- unlike -WindowStyle Hidden, which the DefTerm handoff ignores and
    # can flash (windows-launch-hardening #786). It keeps the REAL child handle (pid
    # tracking + kill in the watchdog) and the native stdio->file redirects.
    $proc = Start-Process -FilePath $dtExe -ArgumentList $dtArgs -NoNewWindow -PassThru `
        -RedirectStandardOutput $childOut -RedirectStandardError $childErr -ErrorAction Stop
    Write-Log "dtssh host started hidden (pid $($proc.Id))"
    return $proc
}

function Stop-HostProc {
    <#
      Stop a dtssh host process and the dedicated loopback sshd it spawned (so
      :$Port is freed for the restart). The sshd reap is port-scoped and never
      touches the separate Windows OpenSSH service sshd (:22).
    #>
    param([int]$HostPid)
    if ($HostPid) {
        try { Stop-Process -Id $HostPid -Force -ErrorAction SilentlyContinue; Write-Log "stopped dtssh host (pid $HostPid)" }
        catch { Write-Log "failed to stop dtssh host pid ${HostPid}: $_" 'WARN' }
    }
    Clear-DedicatedSshd
}

# ── Devtunnel login sanity (non-fatal) ───────────────────────────────────

if (-not (Test-DevTunnelLogin -DtsshPath $dtExe)) {
    Write-Log "WARNING: no signed-in devtunnel session detected; run 'dtssh login'. Continuing anyway." 'WARN'
}

# ── Legacy one-shot mode (no monitoring) ─────────────────────────────────

if ($NoMonitor) {
    if (Get-RunningHostProc) { Write-Log "dtssh host already running; nothing to do (NoMonitor)"; exit 0 }
    Write-Log "NoMonitor: hidden 'dtssh host --persist --alias $Alias --port $Port'"
    $null = Start-HostProc
    exit 0
}

# ── Single-instance (named mutex) ────────────────────────────────────────

$mutexName = "Global\DtsshHostLauncher_$Alias"
$mutex = $null
try {
    $created = $false
    $mutex = [System.Threading.Mutex]::new($true, $mutexName, [ref]$created)
    if (-not $created) {
        if (-not $mutex.WaitOne(0)) {
            Write-Log "another launcher instance already running (mutex $mutexName); exiting"
            exit 0
        }
        Write-Log "acquired orphaned mutex — previous launcher crashed" 'WARN'
    }
} catch [System.Threading.AbandonedMutexException] {
    Write-Log "acquired abandoned mutex — previous launcher crashed" 'WARN'
}

# ── Monitor loop ─────────────────────────────────────────────────────────

$tunnelId = Resolve-TunnelId $Alias
if ($tunnelId) { Write-Log "monitoring tunnel '$tunnelId' (alias $Alias): every ${HealthCheckSec}s, restart after $ConsecutiveFailures zero-connection checks" }
else { Write-Log "could not resolve tunnel id for alias '$Alias' — process-only monitoring (restart on child exit)" 'WARN' }

# Adopt an already-running host (e.g. from a prior boot) instead of duplicating.
$hostProc = $null
$existing = Get-RunningHostProc
if ($existing) {
    $hostProc = Get-Process -Id $existing.ProcessId -ErrorAction SilentlyContinue
    if ($hostProc) { Write-Log "adopted existing dtssh host (pid $($hostProc.Id))" }
}

$failCount = 0
try {
    while ($true) {
        if ($null -eq $hostProc -or $hostProc.HasExited) {
            if ($hostProc -and $hostProc.HasExited) { Write-Log "dtssh host exited (code $($hostProc.ExitCode)); restarting" 'WARN' }
            $hostProc = Start-HostProc
            $failCount = 0
            if (-not $tunnelId) { $tunnelId = Resolve-TunnelId $Alias; if ($tunnelId) { Write-Log "resolved tunnel id after start: $tunnelId" } }
            Start-Sleep -Seconds $GracePeriodSec
            continue
        }

        # Health = relay connected AND the dedicated sshd is actually SERVING.
        # Checking only the relay host-connection count misses the case where the
        # dtssh host stays connected but its sshd CHILD dies (#576); checking only
        # a bare TCP connect misses the pre-auth WEDGE (sshd accepts TCP but never
        # banners once MaxStartups is saturated). So probe the relay AND read the
        # sshd banner, and restart on either failure. A restart's Clear-DedicatedSshd
        # reaps any piled-up pre-auth connections that caused a wedge.
        $relayOk = $true
        if ($tunnelId) {
            $conns = Get-HostConnections $tunnelId
            if ($conns -eq 0) { $relayOk = $false }
            # $conns -eq -1: transient/unknown — do not treat as a failure.
        }
        $sshdOk = Test-SshdServing $Port

        # Soft pre-saturation early-warning (advisory; does not force a restart).
        $estConns = Get-EstablishedConnCount $Port
        if ($estConns -ge $PreAuthWarnThreshold) {
            Write-Log "pre-auth pressure: $estConns established connection(s) on :$Port (>= $PreAuthWarnThreshold; MaxStartups wedge risk)" 'WARN'
        }

        # Released dtssh builds can leak one host-side forwarded connection and
        # sshd-session tree per completed command. Recycle once that idle
        # population is abnormal, but never while a command-bearing session is
        # active. The upstream relay teardown / ClientAlive fixes remain the
        # root solution; this keeps released binaries bounded.
        $sessionPressure = Get-DedicatedSshdSessionPressure $Port
        if ($sessionPressure -and $sessionPressure.IdleRoots -ge $IdleSessionWarnThreshold) {
            Write-Log "relay-session pressure: $($sessionPressure.IdleRoots) idle/no-command root(s), $($sessionPressure.ActiveRoots) command-bearing root(s)" 'WARN'
        }
        if (
            $sessionPressure -and
            $IdleSessionReapThreshold -gt 0 -and
            $sessionPressure.IdleRoots -ge $IdleSessionReapThreshold
        ) {
            if ($sessionPressure.ActiveRoots -gt 0) {
                Write-Log "SESSION REAP deferred: $($sessionPressure.ActiveRoots) command-bearing SSH session(s) active" 'WARN'
            } else {
                Write-Log "SESSION REAP: $($sessionPressure.IdleRoots) idle/no-command SSH root(s) (>= $IdleSessionReapThreshold) — restarting host to drain released-build relay leakage" 'WARN'
                Stop-HostProc $hostProc.Id
                Start-Sleep -Seconds 3
                $hostProc = Start-HostProc
                $failCount = 0
                Start-Sleep -Seconds $GracePeriodSec
                continue
            }
        }

        # Last-resort hard ceiling. Preserve the previous unconditional
        # saturation behavior: if classification is wrong or an active session
        # never settles, eventual reachability still wins over a full wedge.
        if (
            $PreAuthReapThreshold -gt 0 -and
            $estConns -ge $PreAuthReapThreshold
        ) {
            Write-Log "SATURATION REAP: $estConns established connection(s) on :$Port (>= $PreAuthReapThreshold) — restarting host to drain the pile-up before it wedges" 'WARN'
            Stop-HostProc $hostProc.Id
            Start-Sleep -Seconds 3
            $hostProc = Start-HostProc
            $failCount = 0
            Start-Sleep -Seconds $GracePeriodSec
            continue
        }

        if ($relayOk -and $sshdOk) {
            if ($failCount -ne 0) { Write-Log "recovered: healthy (relay connected, sshd :$Port serving banner)" }
            $failCount = 0
        } else {
            $failCount++
            $reasons = @()
            if (-not $relayOk) { $reasons += '0 relay host-connections' }
            if (-not $sshdOk)  { $reasons += "sshd :$Port not serving (no SSH banner$(if ($estConns -ge $PreAuthWarnThreshold) { "; $estConns pre-auth conns — likely MaxStartups wedge" }))" }
            Write-Log "UNHEALTHY: $($reasons -join '; ') (consecutive $failCount/$ConsecutiveFailures)" 'WARN'
            if ($failCount -ge $ConsecutiveFailures) {
                Write-Log "restarting dtssh host after $failCount unhealthy checks" 'WARN'
                Stop-HostProc $hostProc.Id
                Start-Sleep -Seconds 3
                $hostProc = Start-HostProc
                $failCount = 0
                Start-Sleep -Seconds $GracePeriodSec
                continue
            }
        }

        Start-Sleep -Seconds $HealthCheckSec
    }
} finally {
    if ($mutex) { try { $mutex.ReleaseMutex() } catch { }; $mutex.Dispose() }
}
