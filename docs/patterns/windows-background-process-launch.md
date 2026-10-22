# Pattern: windows-background-process-launch

**Serves:** *Vision plugin-services* through cross-platform parity: background
work has no user-visible terminal side effect on Windows.
**Exemplars:** `agent-procutil`, `ssh-manager`, and the PowerShell
`conhost.exe --headless` service launchers.

## Problem

A service commonly runs beneath `pythonw.exe`, a hidden scheduled task, or
another consoleless parent. Starting a console-subsystem child from that parent
can ask Windows to allocate a terminal. The allocation is delegated to the
user's configured Default Terminal, so a supposedly hidden health probe may
create a visible window and steal focus.

`STARTUPINFO` plus `SW_HIDE` does not make `CREATE_NEW_CONSOLE` safe. It requests
a new console first and asks one implementation to hide it second; Default
Terminal is free to surface the delegated window. Machine configuration may
change the symptom, but it is never part of the launch contract.

## Launch-kind matrix

| Launch kind | Windows mechanism | Required properties |
|---|---|---|
| Short-lived child with captured or redirected stdio | A console-subsystem root plus `agent_procutil.no_window_kwargs()` / `no_window_flags()`, or the owning shared library's equivalent | `CREATE_NO_WINDOW`; pipes and exit status preserved; timeout owns the complete tree |
| Non-interactive OpenSSH transport with redirected stdio | `ssh_manager.proxy.create_ssh_subprocess()` | `DETACHED_PROCESS` for the SSH root; a configured native proxy runs through an owned loopback broker; pipes, exit status, and cleanup remain owned |
| Console descendant controlled by a third-party OpenSSH `ProxyCommand` | A windowless binary stdio client connects to an owned loopback broker; the broker launches the console child with `no_window_kwargs()` | Preserve SSH's original host/port and token expansion; authenticate the local channel; protocol bytes and child cleanup remain owned |
| Long-lived Python daemon with no recurring console descendants | `agent_procutil.spawn_daemon(argv, cwd=..., log_path=...)` with `windowless_python()` (or `windowless_python()` plus `detached_kwargs(breakaway=True)` at sites not yet migrated) | No root console; lifetime escape is explicit; explicit durable cwd; output logged; occasional captured console children use the short-lived primitive |
| Long-lived Python daemon with recurring console descendants | `spawn_daemon(..., console_children=True)` with a console-subsystem Python (or `windowless_daemon_kwargs(breakaway=True)` at sites not yet migrated) | One inherited hidden console contains descendants; durable lifetime is separate from headlessness |
| PowerShell startup or scheduled launcher whose output is not captured | `conhost.exe --headless <interpreter> ...` | Headless console inherited by descendants; stable installed target; explicit stop/cutover ownership |
| Intentional interactive terminal | An explicit interactive launcher | Reviewable exception with `# headless-guard: allow <reason>` when low-level flags are necessary |

Do not use `conhost.exe --headless` for a process whose stdout, stderr, or exit
status must be captured: the host owns that stream boundary. Do not use
`DETACHED_PROCESS` for an arbitrary captured child. The narrow OpenSSH exception
is safe only because every call is non-interactive, owns all stdio through pipes
or null handles, and tears down the tree by root PID. Do not launch recurring
console descendants from `pythonw.exe`: per-child `CREATE_NO_WINDOW` is
insufficient for programs such as terminal-multiplexer clients that allocate
their own console host. Give that daemon a console-subsystem root under
`windowless_daemon_kwargs()` so every cycle inherits one hidden console tree.

A `ProxyCommand` is a separate launch boundary: detaching `ssh.exe` does not
control the console flags of a native proxy descendant. Do not put a console or
pseudoconsole wrapper around an opaque protocol stream. Move that descendant
behind an owned loopback broker instead, pump bytes without decoding them, and
apply the normal captured-child launch primitive at the point that creates it.
The shared SSH launcher owns this broker in-process for the lifetime of one SSH
root; normal exit, timeout, and cancellation close its listener and proxy child,
and both the proxy child and the SSH root are bound to a kill-on-close Job
Object owned by the spawning process (`agent_procutil.spawn_in_kill_on_close_job`),
so they also die when that process exits for any reason, a hard kill included,
when the helper returned a `JobHandle`. If job creation or assignment fails, the
helper returns `None`: ordinary cleanup still applies, but a hard-killed owner
can still orphan the child. With the job armed, a short-lived CLI that exits
before its background cleanup finishes no longer orphans a `gh cs ssh --stdio`
proxy.
Its narrow `pythonw.exe` client duplicates OpenSSH's inherited OS pipe handles
as binary streams; it must not depend on Python's GUI-mode `sys.stdin/stdout`.
Do not rewrite SSH's HostName or Port: credential and known-hosts paths may
expand those values.
The shared Windows SSH spawn boundary also drops only Win32-OpenSSH's inherited
descriptor-state environment key: ancestor descriptor types do not describe the
new redirected handles. Preserve other environment entries, explicit empty
environments, caller immutability, and the POSIX launch contract.

## Lifetime is independent of visibility

Choose both the console shape and the ownership lifetime before spawning.
`CREATE_NO_WINDOW`, `DETACHED_PROCESS`, `conhost --headless`, `windowsHide`,
and a new POSIX session do not by themselves establish escape from a Windows
Job Object. A child can be invisible, healthy, and alive after its immediate
parent exits, yet still be killed when a command owner's kill-on-close Job is
closed. Successful installer exit is therefore not a durable-service witness.

Keep bounded helpers and their descendants inside caller-owned containment.
For a durable daemon whose contract requires it to outlive the installer or
request, opt out explicitly with the owning primitive:
`agent_procutil.spawn_daemon()` (breakaway by default; `console_children=True`
for recurring console descendants), or, at sites not yet migrated to it,
`windowless_daemon_kwargs(breakaway=True)` / `detached_kwargs(breakaway=True)`.
`spawn_daemon` also requires an explicit durable `cwd` (the runtime root or
home, never the caller's cwd or a plugin payload) and appends the daemon's
output to a `log_path` (stderr optionally to its own `stderr_log_path`) instead
of discarding it.
Do not remove the caller's Job or make every child break away to repair one
daemon. A caller already in a Job uses the shared, bounded WMI lifetime broker
before launching: Windows can accept breakaway while retaining an ancestor Job
under a venv redirector. A caller Job query failure refuses the launch. A
direct launch's breakaway denial uses the same broker. Its windowless, stdlib-only
bootstrap uses the actual base interpreter with WMI's supported detached-process
flag, so a venv redirector cannot change the identity being adopted. It receives
launch data through an authenticated one-use channel,
stages the actual child suspended, and transfers a pinned native process
handle before resume. Shell interpolation, credential-bearing command lines,
temporary launch files and wrapper PIDs are not part of this contract.
The caller receives the actual daemon's PID and exit/control methods.
An uncommitted failed launch is reaped before retry; both direct and broker
failure produce `DaemonSpawnError`, never an apparently successful contained
daemon. Independent cleanup branches preserve that failure type. If process
exit cannot be confirmed, the error carries `cleanup_confirmed=False` and no
contained retry is permitted.
A caller for which the degraded lifetime is still better than no daemon may
opt in to `contained_fallback=True`: only after broker failure does the daemon
start inside the caller's Job, with a warning and log entry. That daemon does
not outlive the Job. Explicit `breakaway=False` and contained test mode never
invoke the broker.
Contained tests deliberately suppress escape and retain process-tree cleanup.
The native survival regression separately exercises Jobs with and without
breakaway permission, with two complete launch/stop cycles, exact argv and
environment delivery, split logs and foreground/window observation. The
broker-only handle-control case proves timeout, termination and actual exit
status rather than polling an unrelated wrapper.

PowerShell `Start-Process` and .NET `CreateNoWindow` provide no Job-escape
promise. A one-shot installer should call the service's common user-mode ensure
path, or an installed launcher using the canonical lifetime primitive, rather
than call a plain `conhost` spawn "detached". OS-managed scheduled activation
remains a distinct lifetime boundary; its stable registration is not a
substitute for verifying the installer's start-now path.

Test the actual production launch seam from a kill-on-close Job: wait for
readiness, confirm the launching command exits successfully, close the caller
Job, then independently verify the daemon remains healthy. Include a bounded
child control that must die, pin fixture process identities, and clean up only
owned fixture processes. Test-only containment suppression and the production
survival lane are separate assertions.

## Routing before launching

Avoiding the window is the last line of defense; avoid unnecessary processes
first. A same-machine target uses the local API or runtime directly. Machine
identity comparisons are normalized and case-insensitive before choosing an SSH
or other remote transport. Periodic supervision must not turn a local liveness
check into recurring self-SSH process churn.

## Automated enforcement

`tools/check-headless-launch.py` is required in CI and pre-push:

- an `agent-procutil` adopter may not hand-roll Windows creation flags;
- production plugin and canonical shared-library code may not reference
  `CREATE_NEW_CONSOLE`;
- a genuine interactive or low-level exception requires an inline
  `# headless-guard: allow <reason>` marker;
- in an `agent-procutil` adopter, a raw `subprocess.Popen` that detaches with
  `detached_kwargs` / `windowless_daemon_kwargs` (in the call or its enclosing
  function) must instead launch through `spawn_daemon`. A detached child that
  is not a resident daemon carries `# daemon-spawn: allow <reason>` on its
  `Popen` line. `tools/daemon-spawn.allow` is a shrink-only, reasoned baseline
  of the sites not yet migrated: each entry covers one launch, and a stale
  entry fails the check;
- an installer (`plugins/*/scripts/install*.ps1`,
  `plugins/*/transports/*/scripts/install*.ps1`) may not start a process with
  `Start-Process` of `conhost`: that child stays in the installer's Job. Start
  a daemon now through its user-mode ensure path (built on `spawn_daemon`), or
  WMI `Win32_Process.Create` for a PowerShell-only daemon; a child the caller
  deliberately owns (a foreground supervisor that waits on it, a warned last
  resort) carries `# daemon-spawn: allow <reason>` on the statement or the
  comment line directly above it.

`agent_procutil.spawn_daemon` is an approved headless primitive: a launch
through it is windowless by construction. It approves only its own call; a raw
`subprocess` spawn beside it in the same function is still checked.

Canonical shared libraries and every shipped vendored copy are scanned directly.
The vendored-library synchronization guard is also required in CI and pre-push,
so one plugin cannot silently ship a divergent process primitive.

## Review and validation

Mocked flag assertions prove wiring, not behavior. A Windows launch-path change
also needs a focused live regression:

1. Launch from the real windowless parent (`pythonw.exe`, scheduled launcher, or
   service daemon).
2. Exercise a real console child and any configured proxy/transport descendant.
3. Observe at least two periodic cycles with Win32 window enumeration and
   foreground tracking; record zero visible windows, Default Terminal hosts, and
   focus transitions.
4. Force timeout/cancellation and prove the full process tree exits.
5. Verify local routing does not cross a remote process boundary.
6. For durable daemons, close the launching caller's containment boundary after
   a successful invocation and verify survival; parent exit alone is insufficient.

Keep the live test out of the fast required lane when it needs a Windows host;
the static guard and unit contract remain the portable CI gate.

## See Also

- [cross-platform-parity](cross-platform-parity.md)
- [service-lifecycle-supervision](service-lifecycle-supervision.md)
- [ephemeral-process-reaping](ephemeral-process-reaping.md) — this pattern
  gets a process invisibly launched; that one gets it reliably reaped
- [`windows-launch-hardening` effort](../../efforts/active/windows-launch-hardening/README.md)
