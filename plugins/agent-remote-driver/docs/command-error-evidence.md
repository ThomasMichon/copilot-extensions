# Standalone CLI command error evidence

Parent: [Agent Remote Driver](../README.md).

Prepared payloads retain command failures from `bin/control-session.mjs` and
`bin/list-sessions.mjs` through the shared
[native evidence owner](../../../libs/process-telemetry/README.md#native-error-only-support).
Its private user-local store is the same one used by the shared command reader.
No application Python, uv, daemon or new command option is needed at failure time.

## Captured boundaries

The standalone control CLI reports argument, local input, ownership, transport
and known control failures using a closed, producer-safe cause catalog. A known
cause is retained without copying its original message, descriptor, state path,
content file, credentials or command arguments.

The listing CLI retains inability to read its registry and unexpected entry
failures. An empty fleet and individual failed health probes remain normal
reporting results, including their existing successful exit status. They do not
become command failures or trigger another fleet sweep.

Imported `controlSession()` remains an application API: its caller owns reporting.
These adapters run only in the standalone command error paths. A failure before
the entry can execute, such as an entry syntax/static-import failure, remains
outside this in-process boundary.

## Primary result and evidence status

Successful stdout, request/control protocols, claim-state fencing, partial
effects and no-replay recovery remain unchanged. The control CLI's single JSON
stderr object retains its existing fields and adds optional advisory
`diagnostics.status`:

| Status | Meaning |
|--------|---------|
| `recorded` | The validated native writer reported completion without a degradation warning. |
| `unavailable` | The optional binding, platform or writer could not supply evidence. |
| `incomplete` | Diagnostic work exhausted its bound or lost completion; an attempted final write outcome is unknown. |

Readers must allow this field to be absent on older commands and must not infer
control ownership, permission, retry safety or application success from it.
Logging failures never turn a completed effect into an automatic retry.
The listing command uses its existing human stderr seam for unavailable evidence.

The native writer runs through one headless, bounded, error-only handoff. It receives
fixed command/source identity, a selected static cause and a qualified diagnostic
directory, never application stdin or payloads. Application stdout/stderr are not
interposed. Its five-second logging deadline does not limit application execution.
Windows uses the existing system PowerShell and the canonical provider's
in-process managed ACL/file/lock operations. That closure does not compile C#
or start workload children. The helper is deliberately non-detached: libuv's
caller-owned kill-on-close Job retires that direct child if Node exits, while
`spawnSync` enforces the deadline while Node lives. Supported Windows environments
must preserve that Job relationship; the native caller-loss control qualifies it
without closing the outer test Job. Native qualification observes the real writer and
its OS console hosts through ordinary completion, original-caller loss and a delayed-helper timeout,
before the enclosing fixture Job closes. Adding a child-producing operation
requires requalifying that lifetime contract, not assuming a direct timeout
would retire arbitrary descendants.

Linux's writer does use subshells and GNU tools. The adapter uses the existing
util-linux `setpriv` parent-death signal and GNU `timeout` private process-group
owner around Bash. Normal timeout and loss of the original caller terminate the
whole finite group; 250 ms is reserved inside the same five-second budget for
TERM-to-KILL cleanup. These native utilities supplement the provider's
Bash/GNU/util-linux prerequisites; missing support means unavailable evidence,
not installation or an uncontained fallback. Cold startup or contention can
exhaust the deadline; incomplete evidence is never relabeled recorded or retried.
The helper receives a copied environment without `BASH_ENV` or `ENV`. Bash's
privileged startup mode suppresses imported functions and startup-option
injection; it does not change the process's user or elevate privileges.

The event's final CLI exit code remains unknown because the CLI has not exited
when reporting. Its PID belongs to the native emitter, not the Node command or
remote session. Source identity includes the selected native bundle digest;
this is not cross-process invocation-context propagation or a power-loss promise.

## Artifact binding and discovery

The [native-only materializer](../../../libs/payload-invocation/docs/native-consumers.md)
publishes `scripts/command-errors/index.json` and a complete digest-named
five-file bundle. The CLI derives this location from its own module, verifies
consumer/schema/closure/digests, and accepts only its compiled safe cause keys.
It does not search installed plugin roots or treat an old library version as proof.
The event's bundle digest identifies the five native writer resources, not the
entire CLI or operating-system utility implementation.
Native asset reads reject nonregular leaves before opening and use nonblocking
admission to refuse a FIFO swapped into place between inspection and opening.

Use the shared reader and its documented user-local store for subsequent-session
inspection. Missing, unsupported or corrupt assets produce an unavailable status,
not interpreter acquisition or application replay. In particular, a raw,
unmaterialized source copy may have no selected bundle; use a prepared artifact
for capture qualification.

Bridge's compact receiving projection includes the adapter module so its CLI
imports remain complete. That projection does not yet compose the selected
native resource bundle: its failures explicitly report unavailable evidence.
The parent command-error inventory retains that separate adoption obligation.

## Tests

`tests/command-error-evidence.test.mjs` stages the actual producer inside private
fixtures and executes both real CLIs. Fixture preparation uses the repository's
pinned development Python; that is a build/test prerequisite, not a venue runtime
dependency. Existing control and listing tests protect redaction, pending-state
custody, takeover and partial-effect behavior.
Additional controls count zero helper handoffs on success and one on failure,
preserve a storage obstacle, and observe actual native retirement before outer
test cleanup. Linux caller-loss qualification blocks the unmodified writer on
its real lock and cancels its original caller; a separate delayed-native-call
control covers its group deadline. Windows observes the actual managed-only
writer and its delayed direct-helper timeout. A further control waits until the
helper has actually entered a deliberate stall before terminating Node, then
checks retirement before waiting for inherited streams or outer cleanup.
These boundaries prevent an outer
test runner from hiding a cleanup leak; sampled process observations are
combined with inspection of the provider's finite source closure, not presented
as exhaustive operating-system tracing.
