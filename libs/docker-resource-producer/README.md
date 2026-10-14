# Disposable Docker identity primitive

This dependency-free source library pins a Docker endpoint and daemon identity,
verifies born-at-creation reservation labels, and distinguishes authoritative
inventory absence from failed or malformed probes. It does not install plugins,
bootstrap authentication, allocate resources, keep an ownership ledger, or
assert allocation quiescence.

`DockerClient.pin()` resolves CLI context/host/TLS settings once. Subsequent
operations use explicit host and TLS flags rather than ambient context selection.
TLS paths, not certificate/key contents, travel in `Endpoint.to_json()`.
Transport selection follows Docker CLI's host/context environment precedence:
a nonempty `DOCKER_HOST` selects the default endpoint before `DOCKER_CONTEXT`.
The default context's in-memory TLS metadata is resolved to its environment's
certificate directory, not treated as a stored named-context TLS path.
Docker's `--tlsverify` flag enables TLS even when set to false, so non-TLS
endpoints deliberately omit that flag.
Explicit-host TLS retains the CA path and records genuinely absent optional
client certificate/key files. Replay passes explicit empty file options for
absent material so ambient Docker configuration cannot supply another identity
or replace system trust roots. A context's `SkipTLSVerify` enables TLS even
without stored material.
Inaccessible files remain a verification error, not a CA-only fallback.

`Identity` binds a full container ID, or a unique volume name plus creation
evidence, to daemon identity and reservation/creator/generation labels.
`probe()` verifies the daemon before and after complete inventory and exact
inspection. Running, stopped and renamed containers still exist. Label,
creation, daemon and malformed-response mismatches raise `VerificationError`;
an inspect failure never proves absence. `find_reservation()` searches both
resource kinds across all allocation generations.
Calendar validation normalizes fractional precision for Python 3.10 without
changing the original nanosecond birth-identity string.

`remove()` requires explicit cleanup authority and freshly verified identity.
Container removal addresses the full immutable ID, then verifies absence.
**Automatic volume deletion is blocked:** Docker's volume-removal API accepts
only a name and force option, not birth identity or a compare-and-delete
precondition. Inspection followed by name deletion cannot prevent an unrelated
replacement in between. Volume probes remain read-only, and this library never
claims to provide atomic name-based deletion.

The active [temporary-resource plan](../../efforts/active/vendored-installer-engine/temporary-container-obligations.md)
owns provider/ledger integration, producer allocation, lost-fence quiescence,
cleanup strategy and live acceptance. Those guarantees are not delivered by
this primitive alone.

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

Transport semantics: [Docker CLI options](https://github.com/docker/cli/blob/master/cli/flags/options.go).
Volume deletion contract: [Moby client API](https://github.com/moby/moby/blob/master/client/volume_remove.go).
