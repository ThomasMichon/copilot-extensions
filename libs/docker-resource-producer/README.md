# Disposable Docker identity and local verification

This dependency-free source library records explicit Docker transport and
expected birth identity, provides non-authoritative CLI diagnostics, and supports
separately admitted direct-local daemon verification.
It does not install plugins, bootstrap authentication, allocate resources, keep
an ownership ledger, assert allocation quiescence, or grant cleanup permission.
The existing worktree claims ledger remains the sole ownership authority.

`DockerClient.pin()` resolves CLI context/host/TLS settings once. Subsequent
operations use explicit host and TLS flags rather than ambient context selection.
TLS paths, not certificate/key contents, travel in `Endpoint.to_json()`.
Transport selection follows Docker CLI's host/context environment precedence:
a nonempty `DOCKER_HOST` selects the default endpoint before `DOCKER_CONTEXT`.
The CLI's default-context inspection supplies its normalized host, so accepted
host shorthand is never persisted as an incomplete endpoint. Socket and SSH
transports do not replay unused TLS settings.
The default context's in-memory TLS metadata is resolved to its environment's
certificate directory, not treated as a stored named-context TLS path.
TLS directory fallback uses `HOME` on POSIX or `USERPROFILE` on Windows from
the supplied child environment, never the parent process's home. If that
environment lacks both an explicit certificate/config directory and its home,
pinning refuses rather than inspecting ambient credentials.
Docker's `--tlsverify` flag enables TLS even when set to false, so non-TLS
endpoints deliberately omit that flag.
Explicit-host TLS retains the CA path and records genuinely absent optional
client certificate/key files. Replay passes explicit empty file options for
absent material so ambient Docker configuration cannot supply another identity
or replace system trust roots. A context's `SkipTLSVerify` enables TLS even
without stored material.
Inaccessible files remain a verification error, not a CA-only fallback.
Verified TLS with neither CA nor client material is rejected before invocation,
because empty file options can make Docker CLI omit its TLS configuration.

`Identity` binds a full container ID, or a unique volume name plus creation
evidence, to daemon identity and reservation/creator/generation labels.
`diagnostic_observation()` samples daemon information, complete inventory and
birth metadata. Label, creation, daemon and malformed-response mismatches raise
`VerificationError`; an inspect failure never becomes a missing-resource result.
`diagnostic_reservations()` returns labeled candidates across resource kinds and
generations. Both return `DiagnosticObservation` values with
`authority: diagnostic-only` and `observed_exists`, not provider `exists` or
`producer_verification` fields; boolean coercion refuses.
Calendar validation normalizes fractional precision for Python 3.10 without
changing the original nanosecond birth-identity string.

**CLI snapshots cannot authorize proof or removal.** Separate CLI
connections can observe daemon A, inventory on B and A again, reporting an
observational absence even though A still owns the resource. Before/after checks
do not bind those reads to one daemon. `probe()`, `find_reservation()` and
authorized container `remove()` therefore refuse before further invocation.
Diagnostics cannot settle a claim or discharge a finalize obligation.

**Automatic volume deletion also has an independent restriction:** Docker's
volume-removal API accepts only a name and force option, not birth identity or a
compare-and-delete precondition. Inspection followed by name deletion cannot
prevent an unrelated replacement in between. Named volumes remain owned and
held; this library never claims atomic name-based deletion.

**Full container IDs also do not make Docker deletion ID-only.** Moby's removal
path calls `GetContainer()`, which tries an exact name after an absent full ID.
If the original container disappears after inspection, a different container
named with that ID can be deleted instead. A same-daemon connection, another
inspection or post-delete absence cannot exclude that race. Automatic container
removal therefore also refuses before connecting; safe deletion needs a
separately reviewed atomic identity-conditional mechanism.

The active [temporary-resource plan](../../efforts/active/vendored-installer-engine/temporary-container-obligations.md)
owns provider/ledger integration, producer allocation, lost-fence quiescence,
cleanup strategy and live acceptance. Its
[daemon-session refinement](../../efforts/active/vendored-installer-engine/temporary-container-obligations.md#registry-rooted-ownership-and-daemon-session-proof)
requires admitted, non-reconnecting single-daemon operation sessions before
authoritative provider or producer enablement. Ownership views derive from the existing ledger,
not a second resource registry.

## Direct-local operation sessions

`LocalDockerVerifier(endpoint, admission)` uses a single socket for `/info`,
complete inventory, birth inspection and absence observations. It never opens a
second connection inside an operation. Broken streams,
redirects, malformed/oversized/truncated responses, daemon mismatch and expired
absolute operation deadlines raise `VerificationError`; none means absence.
The limited HTTP parser validates every chunk delimiter and the complete
trailer terminator. Chunk extensions are unsupported and refuse explicitly.
A single buffered reader preserves all unread bytes between requests; padding
or unsolicited bytes cannot be discarded to reveal a later apparent response.
Byte accounting rejects surplus buffered responses, and a nonblocking socket
readability check refuses pending bytes or a disconnected peer at each request
boundary. This is a sequential protocol with no response pipelining.
The absolute deadline is rechecked after decoding and before an operation returns.
Excessive JSON nesting is a verification error; failed admission and later
decoding failures close both the connection and response reader without reconnecting.
Admission also closes every resource on interruption or unexpected exceptions
while preserving cancellation unchanged. Volume inventories accept only null
or an empty list as a present no-warning value; other warning shapes refuse.
This adapter spawns no child processes and requires no background daemon.

`LocalDaemonAdmission` is a provider-trusted **configuration capability**, never
claim JSON or an owner-supplied flag. The trusted provider must explicitly assert
that the configured Unix socket terminates at one Docker daemon, not a
request-routing proxy. Filesystem permissions or successful `/info` cannot prove
that semantic property. The adapter additionally pins and rechecks the local
path's inode chain, refusing symlinks, foreign owners and writable routes/sockets
(except root-owned sticky ancestors such as the OS temp root). Group-writable
Docker sockets are not admitted by this initial strict adapter.

Only POSIX direct Unix sockets are implemented. TCP, SSH, named pipes, Windows
and unknown/intermediary routes remain unavailable for authoritative operations;
there is no CLI or remote fallback. Native Windows tests cover diagnostic
behavior and unsupported-route refusal, not an implemented Windows Docker
adapter. The next provider slice must load admission from its trusted
configuration independently of the claim's endpoint before enabling callbacks.

`remove(identity, authorized=True)` refuses for both containers and volumes
before connecting, even with cleanup authority. Sessions allow only GET
requests; there is no lower-level mutation escape hatch. Reservation
search covers both kinds/epochs but never attests `allocation_finished`; the
generic ledger's epoch/quiescence rules remain required. Source fixtures are not
live engine or producer integration acceptance.

## Validation

The tests execute a fixture-only Docker process with private temporary JSON
state. They never invoke Docker or allocate/remove real resources. Use the
shared-library procedure in [TESTING.md](../../TESTING.md) with an existing
pytest interpreter, wrapped by the adopting harness's bounded test launcher:

```text
<bounded-test-launcher> -- <pytest-python> -m pytest libs/docker-resource-producer/tests -q
```

Source tests and API inspection are not a live or native Docker lifecycle proof.
Required PR CI runs this fixture-only suite on the declared minimum Python 3.10
when the library or its CI selector changes. The selector uses the actual PR
head, and minimum-runtime setup does not replace the guard job's Python.
Inherited Git repository/config overrides cannot redirect the scope probe.
The same path selector gates a focused canonical-library promotion regression
and includes changes to the promotion tool and its test.

Transport semantics: [Docker CLI options](https://github.com/docker/cli/blob/master/cli/flags/options.go).
Volume deletion contract: [Moby client API](https://github.com/moby/moby/blob/master/client/volume_remove.go).
Container deletion/name fallback: [Moby removal](https://github.com/moby/moby/blob/3f38b15147b14ce0ce7b11558a1aa9e9f14db103/daemon/delete.go)
and [container lookup](https://github.com/moby/moby/blob/3f38b15147b14ce0ce7b11558a1aa9e9f14db103/daemon/container.go).
