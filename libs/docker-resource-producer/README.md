# Disposable Docker identity and diagnostic foundation

This dependency-free source library records explicit Docker transport and
expected birth identity and provides clearly non-authoritative CLI diagnostics.
It does not install plugins, bootstrap authentication, allocate resources, keep
an ownership ledger, assert allocation quiescence, or authorize cleanup.
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

**Authoritative proof and all automatic removal are unavailable.** Separate CLI
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

The active [temporary-resource plan](../../efforts/active/vendored-installer-engine/temporary-container-obligations.md)
owns provider/ledger integration, producer allocation, lost-fence quiescence,
cleanup strategy and live acceptance. Its
[daemon-session refinement](../../efforts/active/vendored-installer-engine/temporary-container-obligations.md#registry-rooted-ownership-and-daemon-session-proof)
requires admitted, non-reconnecting single-daemon operation sessions before
authoritative provider or producer enablement. That adapter is not implemented
by this diagnostic foundation. Ownership views derive from the existing ledger,
not a second resource registry.

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
