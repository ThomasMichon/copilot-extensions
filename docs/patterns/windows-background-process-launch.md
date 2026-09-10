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
| Long-lived Python daemon with no recurring console descendants | `windowless_python()` plus `detached_kwargs()` | No root console; survivability is explicit; occasional captured console children use the short-lived primitive |
| Long-lived Python daemon with recurring console descendants | Console-subsystem Python plus `windowless_daemon_kwargs()` | One inherited hidden console contains descendants that would otherwise allocate their own Default Terminal hosts |
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
root; normal exit, timeout, and cancellation close its listener and proxy child.
Its narrow `pythonw.exe` client duplicates OpenSSH's inherited OS pipe handles
as binary streams; it must not depend on Python's GUI-mode `sys.stdin/stdout`.
Do not rewrite SSH's HostName or Port: credential and known-hosts paths may
expand those values.

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
  `# headless-guard: allow <reason>` marker.

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

Keep the live test out of the fast required lane when it needs a Windows host;
the static guard and unit contract remain the portable CI gate.

## See Also

- [cross-platform-parity](cross-platform-parity.md)
- [service-lifecycle-supervision](service-lifecycle-supervision.md)
- [ephemeral-process-reaping](ephemeral-process-reaping.md) — this pattern
  gets a process invisibly launched; that one gets it reliably reaped
- [`windows-launch-hardening` effort](../../efforts/active/windows-launch-hardening/README.md)
