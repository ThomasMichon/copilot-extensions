<#
  psmux-drive.ps1 -- first-class helper API for driving a HEADED (real
  interactive-console) `copilot` session inside a Windows clean-room scenario
  via psmux (a tmux-alike for Windows) + send-keys.

  Windows counterpart of lib/tmux-drive.sh -- see that file's own header for
  the full rationale (headless `-p`/`--acp` never load extensions at all;
  only a genuinely headed/interactive session does). psmux mirrors tmux's own
  CLI closely enough (new-session/kill-session/capture-pane/list-panes; see
  this repo's own worktree-manager/bin/pane-wrapper.ps1 and
  .github/workflows/ci.yml's "Install psmux" step) that this is a close,
  deliberate port rather than a fresh design.

  UNVALIDATED as of authoring: written by close analogy to the working Linux
  tmux-drive.sh and this repo's OTHER existing psmux callers, but this
  specific file has not yet been run against a real Windows container on this
  machine (Docker here is in Linux-container mode; Windows-container testing
  is the explicit next step, on a different host). Smoke-test send/capture/
  wait_for against a trivial psmux session before trusting it in a full
  scenario run.

  IMPORTANT -- also pass `--experimental` to the driven `copilot` invocation
  whenever the scenario cares about the extension-host mechanism specifically
  (see tmux-drive.sh's own note); this helper does not add it automatically.

  Public helper API (mirrors tmux-drive.sh):
    Test-CrPsmux                          install/verify psmux is on PATH
                                           (already baked into the Windows
                                           clean-room image; this is a
                                           belt-and-braces presence check,
                                           not an installer)
    Start-CrPsmux <Session> <Cwd> <Prompt> [ExtraArgs...]
                                           kill any stale session of the same
                                           name, then start a detached psmux
                                           session in <Cwd> running
                                           `copilot -i <Prompt> <ExtraArgs...>`
    Send-CrPsmuxKeys <Session> <Keys>     psmux send-keys wrapper (appends Enter)
    Get-CrPsmuxCapture <Session>          the pane's captured text
    Wait-CrPsmuxFor <Session> <Pattern> [TimeoutSec]
                                           poll Get-CrPsmuxCapture (-match
                                           <Pattern>) until it matches or
                                           <TimeoutSec> (default 30) elapses;
                                           returns $false on timeout
    Stop-CrPsmux <Session>                kill-session; always succeeds
                                           (best-effort cleanup)

  Deliberately Windows-PowerShell-5.1 compatible (no ternary, no ?.), matching
  clean-room-lib.ps1's own compatibility constraint -- this runs inside the
  same Server Core-based container, no pwsh 7 required.
#>

Set-StrictMode -Version Latest

function Test-CrPsmux {
    $cmd = Get-Command psmux -ErrorAction SilentlyContinue
    return [bool]$cmd
}

function Start-CrPsmux {
    param(
        [Parameter(Mandatory = $true)][string]$Session,
        [Parameter(Mandatory = $true)][string]$Cwd,
        [Parameter(Mandatory = $true)][string]$Prompt,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$ExtraArgs
    )
    & psmux kill-session -t $Session 2>$null | Out-Null
    $argLine = ($ExtraArgs -join ' ')
    # `copilot -i "<prompt>"` (a single prompt argument, as opposed to bare
    # `copilot -i`) runs exactly one turn and then exits on its own -- fast
    # enough (~2s, confirmed) that the underlying psmux pane's process can be
    # gone before Wait-CrPsmuxFor's first poll even runs, killing the WHOLE
    # session out from under it ("psmux: no server running"). Append a long
    # hold so the pane -- and therefore the session -- outlives copilot's own
    # exit; Stop-CrPsmux tears it down explicitly once the caller is done.
    $cmdLine = "copilot -i `"$Prompt`" $argLine; Start-Sleep -Seconds 300"
    & psmux new-session -d -s $Session -c $Cwd -- powershell -NoProfile -Command $cmdLine
}

function Send-CrPsmuxKeys {
    param(
        [Parameter(Mandatory = $true)][string]$Session,
        [Parameter(Mandatory = $true)][string]$Keys
    )
    & psmux send-keys -t $Session $Keys Enter
}

function Get-CrPsmuxCapture {
    param([Parameter(Mandatory = $true)][string]$Session)
    & psmux capture-pane -t $Session -p
}

function Wait-CrPsmuxFor {
    param(
        [Parameter(Mandatory = $true)][string]$Session,
        [Parameter(Mandatory = $true)][string]$Pattern,
        [int]$TimeoutSec = 30
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $captured = Get-CrPsmuxCapture -Session $Session
        if ($captured -match $Pattern) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Stop-CrPsmux {
    param([Parameter(Mandatory = $true)][string]$Session)
    & psmux kill-session -t $Session 2>$null | Out-Null
}
