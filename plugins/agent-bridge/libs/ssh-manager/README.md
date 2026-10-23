# ssh-manager

Shared SSH ControlMaster connection multiplexer for Copilot CLI plugins.

Provides a single `ConnectionManager` that owns one SSH ControlMaster
connection per remote host. Plugins that need SSH (agent-bridge,
agent-codespaces) import this library instead of spawning SSH directly.

## Features

- **Connection multiplexing** -- one ControlMaster per host, all commands
  share the same TCP connection
- **Pluggable config sources** -- `ConfigSource` protocol for SSH profile,
  CodeSpace, or custom SSH configurations
- **Health monitoring** -- `ssh -O check` with structured status reporting
- **Platform-aware** -- Unix sockets on Linux/macOS/WSL, direct-SSH fallback
  on native Windows
- **Async-first** -- built on asyncio, matches agent-bridge patterns
- **Owned Windows proxies** -- native proxy children run behind a per-SSH
  loopback broker with explicit no-window flags and byte-transparent pipes
- **Dial log** -- each connection attempt (`gh codespace ssh --config` fetch,
  ControlMaster start, direct-mode exec or stdio channel, health reconnect; dedicated
  port-forward and relay-channel ssh processes aren't logged yet) is one structured,
  redacted JSON line in a bounded per-target log under
  `~/.ssh-manager/dial-log/` (`SSH_MANAGER_DIAL_LOG_DIR` overrides it): kind,
  outcome (a direct-mode stdio channel records `spawned`: only its ssh start is
  known there, so it is neither `ok` nor a failure), elapsed time, attempt, account `pinned`/`ambient`, and a short
  stderr tail; user-only (0700 directory, 0600 files) on POSIX, one file per exact
  target (a readable prefix plus a digest). Recording is best-effort and never raises into a dial (on an event loop's thread it does no file I/O: a bounded background writer appends the line, dropping it when saturated);
  `ssh_manager.dial_log.summary()` counts dials per outcome over the last 10
  minutes and hour. File locks work from an empty lock file, without an
  unowned seed-byte write on Windows. Lock contention retains the two-second
  best-effort deadline; admitted records remain serialized without interleaving.
- **Safe carrier startup diagnostics** -- failed framed-HELLO negotiation reports
  its stage, exception type, observed pre-cleanup exit code (or unknown), and an
  allowlisted stderr reason. Stderr is drained concurrently with raw protocol
  stdout, including after successful startup; raw stderr, arguments and request
  payloads are never included in these diagnostics. A reason is a diagnostic
  hint, not proof of the remote failure's cause. `carrier.diagnostics()` adds
  `startup_failure` after failure and clears it on successful negotiation.
  Cleanup bounds the final stderr drain and closes its owned read transport
  even when a descendant keeps the remote pipe open; it does not change
  process-tree ownership. An exited transport is retired under the failure lock
  with its epoch invalidated before a replacement opens. Requests are registered
  only after startup and bound to that transport's epoch and output queue.
  Retirement fails the old transport's outstanding requests with the existing
  transport-loss error, including requests without a timeout; it neither fails
  fresh startup waiters nor resubmits operations on a replacement. Ownership
  metadata lasts only as long as the corresponding pending request. Retirement
  uses the existing transport-failure path, preserving subscription termination,
  eligible subscription restoration, reconnect backoff and automatic recovery.
  Existing reconnect/idle-retirement ownership is established before cancellable
  process cleanup so interruption cannot orphan live owners.

## Usage

```python
from ssh_manager import ConnectionManager, SSHProfileSource

manager = ConnectionManager()
source = SSHProfileSource(host_alias="my-server")

info = await manager.ensure_connected("my-server", source)
result = await manager.exec_command("my-server", "uname -a")
print(result.stdout)

await manager.disconnect("my-server")
```

`exec_command(..., on_output=callback)` optionally delivers
`callback("stdout" | "stderr", chunk_bytes)` while the existing SSH command is
running, before its final `CommandResult`. Captured output and stdin behavior
are preserved. The existing command owner retains timeout/cancellation cleanup;
the callback does not create another transport, retry policy or process owner.

## Optional finite reverse-forward selection

`ssh_manager.reverse_candidates.FiniteReverseForward` subclasses the existing
`SupervisedRelayForward` process and monitor owner; it does not start a second
keeper. Existing fixed-port consumers and their constructor remain unchanged.
This opt-in boundary is for the constrained reverse-SSH grants described in
`docs/patterns/service-transport.md`, not ordinary local service allocation.

Supply a unique tuple of one to sixteen `ReverseEndpoint(host, port)` values,
an explicit printable `request_id`, and async
`observe(ReverseAttempt) -> ReverseEvidence`. Endpoints are exact `localhost`,
`127.0.0.1` or `::1` literals and positive TCP ports. Their spelling is
preserved in SSH arguments, not treated as interchangeable permission grants.
Both `permitlisten` and any peer `permitopen` must already be authorized by the
existing enrollment/policy owner. This parser and the observation callback do
not create that authority.

Each real SSH attempt has a distinct `attempt_id`, the original request
correlation, endpoint, target port and owned process PID. `process_settled`
reports only the existing settle check, **not SSH acceptance or authenticated
readiness**. The trusted observer must independently bind server-side ownership
and end-to-end serving to that actual attempt. It returns the same attempt ID
with `ready`, `occupied`, `refused` or `unknown`. A TCP accept, an alive SSH
process, or a caller-supplied generation alone is insufficient evidence.
Evidence may additionally carry an allowlisted reason: `collision`,
`authentication`, `host-key`, `permission`, `transport`, `readiness` or
`unclassified`. Arbitrary exception/credential text is not accepted as a reason.

Only a positive owner classification of `occupied` **and** SSH's failed-forward
result advance to the next candidate. Exit255 and OpenSSH's generic failure
warning cannot distinguish collision from permission refusal, so they do not
permit rotation by themselves. The successful SSH process/listener remains
held throughout observation and return. No free-port probe is closed and later
rebound. The existing target-port resolver, headless proxy owner, process-tree
cleanup and reconnect gate are reused.
The default local target remains the initially supplied target port when the
remote listener changes; a failing/invalid explicit resolver is refused rather
than silently forwarding to another candidate port. Every monitor-driven
candidate rechecks the reconnect gate. A stopped venue retires the owner;
an inconclusive gate defers connection through the existing backoff.
The finite owner bounds each gate wait by the available operation budget,
accepts only actual boolean results and redacts provider exceptions in its
diagnostics. Invalid gate data remains unknown rather than becoming a truthy
permission or a false stopped-venue report. A synchronous target-port resolver
must supply an immediate local value, not perform blocking I/O.
The existing fixed-port gate also omits raw provider exception values from its
warning, without changing that path's routing or retry behavior.
Late failure parsing retains a hidden, untrimmed 128-character carry and inspects
at most sixteen additional 4 KiB stderr chunks within the remaining deadline.
This preserves markers split across settle/read boundaries without logging or
exposing the raw diagnostic in the result.

`establish()`/`start()` retain their awaitable shape; inspect immutable
`reverse_status` afterward. `ready` supplies the exact attempt for the existing
fleet owner's generation-checked discovery publication. A dead/retired process
cannot retain a ready status. Exhaustion yields `ReverseForwardError` with
`status.state == "unavailable"` and `reason == "candidates-exhausted"`.
Other bounded result states distinguish refusal, stopped ownership and unknown
outcomes; raw adapter errors are not rendered in their messages.

An observer error, timeout, wrong correlation or interruption latches `unknown`
under the original request. The existing monitor stops rather than allocating
another candidate; `stop()` cleans owned transports but does not settle that
unknown. The caller must reconcile through its original operation/authority
before any replacement request or instance. Every non-cancellation observer
exception, including a provider-raised `ReverseForwardError`, is normalized to
the original operation's unknown result rather than trusted as local control
state. The observer verifies readiness only; fleet publication remains outside
this callback. Interrupted spawn ownership is
retained through eventual cleanup, with a separate five-second cleanup wait;
pending cleanup remains explicitly owned rather than discarded at that wait.
The operation itself has one finite deadline across all candidates.
Retirement, candidate establishment and monitor-driven replacement share one
task-owned lifecycle boundary. A concurrent caller receives an explicit
`unavailable/lifecycle-busy` error without altering the active operation's
status; it does not install a replacement during another attempt's cleanup.
Stored readiness is invalidated before an exited transport is replaced.
`stop()` cancels an in-flight owner operation and waits for its owned cleanup;
an interrupted attempt remains unknown, not a fabricated successful release.
Concurrent stops join the same shutdown completion rather than cancelling
another shutdown's caller. Cancellation is reserved for establishment or
reconnect work, and `stop_nowait()` cooperates with an already-running shutdown.
Shutdown waits for lifecycle ownership to be released, not for the entire caller
task to finish: that caller can safely join shutdown from its own `finally`.
For generation-bound consumer cleanup, use `await stop_attempt(attempt_id)`:
the match and local retirement share that lifecycle boundary. A stale attempt
returns `None` without stopping a reconnected successor. A matching attempt
returns its final `ReverseStatus`, including any still-unknown observation.
If unconditional shutdown is already running, attempt-specific cleanup joins it
before acquiring lifecycle ownership, then rechecks the attempt. A caller's
fenced `finally` cleanup cannot block the shutdown it is joining.
This local fence does not replace the fleet owner's publication/enrollment
generation checks; unconditional `stop()` remains explicit owner shutdown.
All detached cleanup stays tracked until it finishes. A `stop_nowait()` followed
by `start()` is refused with `cleanup-pending` while old retirement could still
sweep descendants. Cancellation during refusal cleanup latches unknown, and a
late-spawn process whose termination fails remains referenced so a later explicit
stop can retry its owned cleanup. Completing that cleanup does not settle an
unknown remote observation or publication.
Retirement is confirmed only when the captured process has an observed exit
code. A helper returning without raising is insufficient; an unconfirmed exit
keeps the process reference, records unknown and prevents replacement.

This library supplies no authenticated production observer, enrollment
projection, fleet publication ledger, installer or adoption. Local socket and
process tests qualify resource retention/cleanup, **not OpenSSH permissions,
remote authentication or deployed recovery**. Real OpenSSH qualification needs
an explicitly supplied isolated fixture with existing credentials; no key or
system sshd configuration is provisioned by this API or its tests.

## Windows proxy lifecycle

Managed commands, forwards, and probes use
`ssh_manager.proxy.create_ssh_subprocess`. On Windows, a configured
`ProxyCommand` runs in an owned loopback broker. OpenSSH launches a windowless
binary stdio client, which authenticates to that broker using a per-launch
capability. HostName, Port, credential-path expansion, and host-key checks stay
unchanged. The client and broker forward opaque bytes without a terminal or
text decoder.
Native Windows OpenSSH and Git for Windows retain their respective proxy-command
quoting and shell semantics.

Windows SSH roots and broker-owned proxy children omit Win32-OpenSSH's inherited
`c28fc6f98a2c44abbbd89d6a3037d0d9_POSIX_FD_STATE` environment key. That metadata
describes an ancestor's file descriptors, not the new redirected handles; passing
it through can silently break SSH stdio. All other ambient or explicitly supplied
environment entries are preserved without modifying the caller's environment.
An explicit empty environment stays empty. When the descriptor-state key is
absent, the original environment argument is left unchanged, including ambient
inheritance. POSIX environment handling is unchanged.

The broker lives in the calling process and closes with its SSH root.
`terminate_ssh_process_tree` also awaits broker cleanup on timeout or
cancellation. No persistent broker service or cached loopback port is created.
The Windows client requests strict GUI selection from `agent_procutil`.
Renamed or relocated physical GUI targets are accepted based on that selection
intent, not a filename check; an unavailable GUI interpreter is refused rather
than substituting the ordinary console fallback.
POSIX transports retain native ProxyCommand execution.

## As a Dependency

In your plugin's `pyproject.toml`:

```toml
dependencies = [
    "agent-ssh-manager @ file:///${PROJECT_ROOT}/../../libs/ssh-manager",
]
```

Or install both libraries into a development virtual environment:

```bash
uv pip install -e libs/agent-procutil -e libs/ssh-manager
```
