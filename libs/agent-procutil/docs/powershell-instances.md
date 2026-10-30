# PowerShell daemon instance custody

[Back to agent-procutil](../README.md)

Dot-source `daemon_instances.ps1`, then use `New-DaemonInstanceReservation`,
`Set-DaemonInstanceProcess`, `Enter-DaemonInstance`, `Exit-DaemonInstance` and
`Invoke-DaemonInstanceReconcile`. These operations use the existing shared
registry and schema, not a second service or inventory.

## Original shell and private transport

Import refuses a loaded legacy adapter before replacing any existing guard
functions; otherwise it only defines the adapter. The first explicit operation lazily starts one
windowless, private-pipe Python helper for the original PowerShell process.
AppDomain state and a process-wide monitor serialize concurrent runspaces;
subsequent calls share that channel and the original instance's nested guard
references. Source-only distributions carry the complete
`daemon_instance_runtime.pyz` beside the adapter, including installed dtssh and
WSL keepalive entrypoints. Python is resolved normally from PATH, without
interpreter attestation or download.

The kernel's input-pipe server PID and the same retained process handle/birth
authenticate the original shell. The helper is never registered as that shell's
daemon. Child binding/completion duplicates the caller's retained
`Process.Handle` through that same shell pin, checks native PID and birth, and
requires actual exit for child completion. It does not reconstruct custody by
reopening a reported child PID. Unknown launch outcomes retain their record.
Missing or uncorrelated transport outcomes break the channel and refuse
automatic replay.

An already-loaded legacy compiled instance adapter refuses this migration
before activation. Use a fresh PowerShell process after the normal authorized
payload update; the new adapter never fabricates ownership of an old guard.

## Disposable lifetime and completion

The existing caller-owned Job protocol keeps the helper and its descendants
bound to the original shell. Before disposable admission is published, a
separate strict kill-on-close Job contains that shell and its future children;
only the helper holds its handle. Helper loss therefore retires the disposable
shell, while shell loss retires the helper in the kernel.

Closing the final admission guard marks its record finished and stops lease
observation, but does not release that process-lifetime Job. Renewable deadlines
cannot move the original observer-local monotonic hard bound, including during
busy-record inspection. Production still has no expiry and is not assigned the
disposable shell Job. Reconciliation retains the original shell's self-cleanup
refusal, shared cursor/byte-range locks and exact-file ownership checks.

Portable transaction/ABI-call doubles and PowerShell facade controls are
distinct from actual Windows process/Job and desktop qualification. The retained
compiled `native_daemon.ps1` and `native_owned.ps1` process launchers are separate
migration boundaries; this adapter does not make their execution compiler-free.
