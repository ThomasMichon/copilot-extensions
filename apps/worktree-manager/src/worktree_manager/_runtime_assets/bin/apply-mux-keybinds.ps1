<#
.SYNOPSIS
    OPTIONAL host-wide PSMux passthrough tuning (Windows). Opt-in.

.DESCRIPTION
    Ordinary worktree create/join applies passthrough automatically to its
    private PSMux server through session-options.ps1. This optional helper is
    different: invoke it deliberately to persist user-level defaults and apply
    the sibling fragment to EVERY listed live session's server, including
    personal/ad-hoc sessions. Prefix and root key tables are server-global;
    a private worktree server contains their scope.

    By default this is a ONE-TIME action: it persists a clearly-marked managed
    block in ~/.psmux.conf (read by psmux at server startup, so it survives
    server restarts) AND applies the same settings to the running server. The
    block is the ONLY thing it manages in that file -- the rest is left
    untouched. Deleting the marked block removes persisted defaults, not settings
    already applied to live servers. Re-running is idempotent. Pass -NoPersist
    to tune live servers without writing the file.

    Wheel input uses PSMux's native mouse handling, not tmux mouse-key binds.
    The receiving application must satisfy the target's mouse capability
    rules; see plugins/agent-worktrees/docs/mux.md in the source repository.
    Ordinary tmux keeps its own opt-in apply-mux-keybinds.sh mechanism.

.PARAMETER NoPersist
    Tune the running psmux server only; do not write ~/.psmux.conf.
#>
[CmdletBinding()]
param(
    [switch]$NoPersist
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command psmux -ErrorAction SilentlyContinue)) {
    Write-Error 'apply-mux-keybinds: psmux not found on PATH'
    exit 1
}

$Conf  = Join-Path $env:USERPROFILE '.psmux.conf'
$Begin = '# >>> agent-worktrees mux keybinds (opt-in) >>>'
$End   = '# <<< agent-worktrees mux keybinds (opt-in) <<<'

$BlockBody = @'
# Managed by agent-worktrees `apply-mux-keybinds.ps1` -- you elected to install
# these by running that script. Delete this whole block (markers included) to
# remove them, or re-run the script to refresh it.
#
# Only the prefix (Ctrl+B) is intercepted; unbound keys reach the application.
set -g prefix C-b
unbind-key -a -T root
# PSMux handles wheel events natively, independently of the root key table.
# Its application-capability gate decides forwarding versus scrollback.
set -g mouse on
set -g scroll-enter-copy-mode off
# Disable Windows Ctrl+V paste interception (psmux fires this outside the key
# binding system, so clearing the root table alone does not stop it).
set -g paste-detection off
'@

function Persist-Block {
    $lines = @()
    if (Test-Path $Conf) {
        # Drop any existing managed block; preserve everything else verbatim.
        $skip = $false
        foreach ($ln in (Get-Content -LiteralPath $Conf)) {
            if ($ln -eq $Begin) { $skip = $true; continue }
            if ($ln -eq $End)   { $skip = $false; continue }
            if (-not $skip)      { $lines += $ln }
        }
        # Trim trailing blank lines so repeated runs don't accumulate them.
        while ($lines.Count -gt 0 -and [string]::IsNullOrWhiteSpace($lines[-1])) {
            if ($lines.Count -eq 1) { $lines = @() }
            else { $lines = $lines[0..($lines.Count - 2)] }
        }
    }
    $out = @()
    $out += $lines
    if ($lines.Count -gt 0) { $out += '' }  # one blank separator
    $out += $Begin
    $out += ($BlockBody -split "`r?`n")
    $out += $End
    Set-Content -LiteralPath $Conf -Value $out -Encoding utf8
    Write-Host "apply-mux-keybinds: persisted managed block to $Conf"
}

function Resolve-AwPsmuxBin {
    <# WinGet installs psmux as a 0-byte App Execution Alias reparse stub under
       %LOCALAPPDATA%\Microsoft\WinGet\Links\psmux.exe that PowerShell 7.4.x
       cannot launch -- a native `& psmux ...` throws a terminating error
       ("StandardOutputEncoding is only supported when standard output is
       redirected"). Resolve the stub to the real
       ...\WinGet\Packages\...\psmux.exe so native launches work on any pwsh. #>
    param($Cmd)
    if (-not $Cmd) { return 'psmux' }
    $src = $Cmd.Source
    try {
        $item = Get-Item -LiteralPath $src -ErrorAction Stop
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -eq 0) {
            $real = Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages') `
                -Recurse -Filter 'psmux.exe' -ErrorAction SilentlyContinue |
                Select-Object -First 1 -ExpandProperty FullName
            if ($real) { return $real }
        }
    } catch {}
    return $src
}

function Invoke-LiveApply {
    $muxBin = Resolve-AwPsmuxBin (Get-Command psmux -ErrorAction SilentlyContinue)
    $sessions = & $muxBin list-sessions 2>$null
    if (-not $sessions) {
        Write-Host 'apply-mux-keybinds: no running psmux server -- the persisted block applies when one starts'
        return
    }
    # psmux runs a SEPARATE server per session (key tables are per-server), and
    # command-line `bind-key`/`unbind-key` silently no-op there -- only
    # `source-file` reliably applies key-table directives. So apply the deployed
    # passthrough fragment to EVERY live session's server via `source-file -t`
    # (not just the last_session one), so all existing panes pick it up.
    $fragment = Join-Path $PSScriptRoot 'psmux-passthrough.conf'
    if (-not (Test-Path $fragment)) {
        Write-Warning "apply-mux-keybinds: passthrough fragment not found at $fragment (run 'agent-worktrees update')"
        return
    }
    $applied = 0
    foreach ($line in $sessions) {
        $name = ($line -split ':', 2)[0].Trim()
        if (-not $name) { continue }
        try { & $muxBin source-file -t $name $fragment 2>&1 | Out-Null; $applied++ } catch {}
    }
    Write-Host "apply-mux-keybinds: applied keystroke passthrough to $applied live psmux session(s) via source-file"
}

if (-not $NoPersist) { Persist-Block }
Invoke-LiveApply
