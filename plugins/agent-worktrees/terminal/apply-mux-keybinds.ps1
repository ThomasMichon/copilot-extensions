<#
.SYNOPSIS
    OPTIONAL psmux keystroke-passthrough keybinds (Windows). Opt-in.

.DESCRIPTION
    These settings cannot be scoped to a single psmux session: the prefix and
    the keystroke-passthrough root key table (there is no per-session key table)
    are server-global. Applying them automatically would leak onto your personal
    / ad-hoc psmux sessions, so agent-worktrees does NOT apply them for you, and
    its installer never owns ~/.psmux.conf. Run this yourself -- once per machine,
    or from a machine-restore flow -- if you want the keystroke-passthrough
    behavior the worktree panes were designed around.

    By default this is a ONE-TIME action: it persists a clearly-marked managed
    block in ~/.psmux.conf (read by psmux at server startup, so it survives
    server restarts) AND applies the same settings to the running server. The
    block is the ONLY thing it manages in that file -- the rest is left
    untouched, and deleting the marked block removes the settings. Re-running is
    idempotent. Pass -NoPersist to only tune the running server.

    The per-session status bar + behaviors are applied automatically by the
    launcher (see session-options.ps1); this script is only the server-global
    part. Mirrors the Linux/WSL apply-mux-keybinds.sh.

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
# Opt-in intercept: every unprefixed key/mouse event passes straight through to
# the inner application; only the prefix (Ctrl+B) is intercepted by psmux.
set -g prefix C-b
unbind-key -a -T root
# Re-add mouse-wheel passthrough (cleared by the unbind above).
bind-key -T root WheelUpPane   send-keys -M
bind-key -T root WheelDownPane send-keys -M
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
            $lines = $lines[0..($lines.Count - 2)]
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

function Invoke-LiveApply {
    $sessions = & psmux list-sessions 2>$null
    if (-not $sessions) {
        Write-Host 'apply-mux-keybinds: no running psmux server -- the persisted block applies when one starts'
        return
    }
    & psmux set-option -g prefix C-b 2>&1 | Out-Null
    & psmux unbind-key -a -T root 2>&1 | Out-Null
    & psmux bind-key -T root WheelUpPane   send-keys -M 2>&1 | Out-Null
    & psmux bind-key -T root WheelDownPane send-keys -M 2>&1 | Out-Null
    & psmux set-option -g paste-detection off 2>&1 | Out-Null
    Write-Host 'apply-mux-keybinds: applied keystroke passthrough to the running psmux server'
}

if (-not $NoPersist) { Persist-Block }
Invoke-LiveApply
