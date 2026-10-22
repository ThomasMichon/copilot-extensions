# Supplied-owner reverse-listener coordination

`fleet_manager.reverse_listener_reservations` supplies one bounded callable
transaction above an explicitly trusted existing owner. It does **not** supply
a production SSH adapter, permission/enrollment owner, discovery registry,
durable effect journal, daemon or reconnect loop. The default CLI and Gateway
do not import or select it. The existing finite-forward contribution under
#7720 owns actual SSH candidate selection, resource/readiness and process
custody; its accepted interface is consumed without patching shared source.

## Admission is an owner operation

`ReservationRequest` binds one stable correlation, authenticated participant and
session, owner epoch, current policy digest, expected target identity,
predecessor generation and an immutable finite list of positive loopback tuples.
The request digest includes the literal host spelling and candidate order.
`localhost`, `127.0.0.1` and `::1` are distinct grant inputs, not aliases that
this library can substitute. Port zero, wildcards, duplicate/mutable grants
and more than 16 candidates are rejected. The limit matches the concrete
finite-forward candidate contract, rather than silently truncating a larger
grant at integration time.

Constructing these types or computing a digest authenticates nothing. The
caller must independently select the trusted owner. Its `admit` operation
authenticates current policy, identity, target and owner epoch; checks both
reverse `permitlisten` and peer `permitopen` authority; compares the expected
generation; and binds the complete request to the correlation atomically in
its **existing** journal. Reusing a correlation with different inputs must
refuse. Reusing an unresolved operation returns `Pending`, not another grant
or another bind. Terminal receipts describe a historical committed operation,
not proof that its listener is still the current live generation.

The coordinator-facing `ReservationOwner` protocol is an explicit integration
requirement, not a claim that a deployed owner already implements it. An adapter
must consume the actual accepted finite-forward contract and the real
authoritative owner transaction; a structural match or synthetic implementation
does not qualify that integration.

### Concrete transport-candidate handoff

`ssh_manager.reverse_candidates.FiniteReverseForward` is the accepted #7720
transport leaf from #7779, separate from this coordinator. It is asynchronous,
accepts 1-16 exact
`ReverseEndpoint` values and a stable `request_id`, and exposes an immutable
attempt/status plus an asynchronous trusted `observe` callback. Its existing
supervised-forward process/reconnect owner remains the resource owner.

An integrating owner must bind this request's original correlation to
`request_id`, retain the selected attempt/resource under its admission custody,
and distinguish the attempt ID, target port and owned process from authenticated
remote readiness. A locally settled process is not server bind/identity proof.
The observer must return candidate-specific authenticated evidence for that
same attempt. Current grants and the original operation's unresolved state
also fence the existing reconnect gate.

For confirmed unpublished cleanup, the owner binds `Candidate.resource` to
the original attempt ID and invokes `await stop_attempt(attempt_id)` under its
own journal fence. A stale `None` result does not confirm disposal of the
requested resource. An `unknown` status remains unsettled after local
process retirement. A separate status check followed by unconditional
`stop()` cannot supply this attempt fence.

The candidate leaf's default readiness timeout is longer than this
coordinator's total budget. An adapter must pass/enforce the actual remaining
budget; it cannot accept that default or start a new budget for each candidate.
No production adapter, observer or authoritative journal is fabricated here. The
separately accepted production owner and its integration remain prerequisites;
this source handoff is not installed readiness.

## One attempt, retained resources

`await reserve_reverse_listener(request, owner=owner)` spends one absolute 30-second
budget across admission, reservation, publication and any confirmed cleanup.
It calls each boundary at most once and never restarts the budget. Every
supplied adapter must enforce its remaining timeout at the actual effect
boundary. An in-process Python call cannot forcibly cancel arbitrary provider
code; an adapter that ignores that requirement is unsupported. The coordinator
and owner hooks are asynchronous so the existing asynchronous transport can be
awaited without inventing a blocking event-loop shim or another background owner.

The owner consumes admission once through the accepted finite-forward leaf,
re-admitting current permission and identity before dispatch. That leaf alone
classifies actual bind conflicts, selects among already granted candidates,
retains the successful resource, and owns its SSH process. This coordinator
contains no candidate chooser, probe-close/rebind fallback, failure-text
classifier or reconnect implementation. An occupied single permitted tuple
remains unavailable; it never authorizes a new tuple.

The owner then verifies **candidate-specific authenticated target readiness**,
rechecks current admission, and atomically compares the expected predecessor
and publishes the generation in its authoritative journal. The coordinator
rejects mismatched admission, candidate or publication receipts as unknown.
It cannot implement the owner's atomic transaction by writing another file.

## Uncertainty and cleanup

`Rejected` distinguishes refusal, unavailability and generation/request conflict.
During reservation it confirms that no owned candidate or publication remains.
During publication it is an irrevocable no-publication verdict, but cleanup is
still pending until the owner confirms `discarded`.

Only that confirmed publication rejection permits `discard`. The owner must
atomically compare the exact epoch, correlation, admission and resource against
its rejected journal state before closing its unpublished candidate. A failed
or ambiguous discard remains unknown. The admission journal must not advertise
a terminal rejection while candidate cleanup is unresolved.

Timeouts, transport errors, malformed receipts, expired admission and lost
acknowledgements return explicit `unknown` with `reobserve_required: true`.
This includes Python 3.10's distinct `asyncio.TimeoutError`, not only the
built-in timeout class. Caller cancellation still propagates with journal
custody retained; it is not mistaken for a confirmed rejection.
They retain the original correlation, never start another allocation and never
speculatively dispose of a possibly published candidate. The existing owner
must journal dispatch before any effect so caller interruption or an unexpected
programming error cannot erase custody. Programming errors propagate.
Reobservation uses that same owner's journal; this module has no alternative
store or implicit retry. Public result dictionaries contain neither custody
bytes, raw exception messages nor authenticated identity material.

Published-predecessor retirement, renew/release and connection topology remain
with their existing owners. Listener age, a TTL, failed liveness, stale
discovery or an unknown process never grants cleanup authority. A stale
generation cannot withdraw or close its successor.

## Evidence boundaries

The focused pure contracts exercise real coordinator transitions with a
synthetic authoritative journal. Native loopback cases reproduce a failed
fixed tuple, retain the old unrelated listener, qualify bounded candidate bind
and generation arbitration, and observe owned closure **before** outer test
containment exits. Their synthetic supplied-owner socket leaf and identity
handshake are not OpenSSH permission, authentication or supervisor evidence.

A real built-wheel consumer invokes the callable from a noneditable package
target in isolated Python outside the checkout, then removes only that owned
target while preserving unrelated fixture state. Each platform's execution is
separate evidence; passing on Windows does not imply Linux or macOS.

### Explicit isolated OpenSSH consumer lane

The native test module additionally supplies an opt-in **Linux-only** fixture
for the accepted SSH leaf and the actual coordinator. It reuses the canonical
synthetic journal fixture; it is not a production permission, enrollment,
observer, journal/CAS or lifecycle adapter.

Set `FLEET_MANAGER_TEST_OPENSSH=1` explicitly when running the contained
standalone suite to enable its three consumer scenarios:

```sh
FLEET_MANAGER_TEST_OPENSSH=1 python tools/run-standalone-tests.py fleet-manager --python <prepared-test-interpreter>
```

The lane requires an existing unprivileged Linux user and working `ssh`,
`sshd`, `ssh-keygen` and server prerequisite directories. Missing prerequisites
fail the explicitly requested lane; the test never installs binaries, elevates
or repairs the host. Without the opt-in, or on Windows/macOS, these cases are
explicitly skipped without creating keys or starting a server.

Each case creates private temporary test keys, host pins and configuration for
a loopback-only user-mode server. Its key and server policy allow only the
fixture's finite reverse listeners and exact peer target. `StrictModes` and
strict host-key checking remain enabled. The private temporary directory is
below the actual user's home because OpenSSH rejects authorized-key paths with
a world-writable `/tmp` ancestor; no shared SSH configuration is edited.

The serving observer uses fixture-owned collision evidence and an HMAC
application challenge. Real SSH selection, retained processes and exact
attempt cleanup remain with the existing leaf. The cases prove retained
publication/idempotence, unknown publication acknowledgement with original
receipt reobservation and no rebinding, and stale-generation cleanup that
preserves an independent established path. Fixture clients, server, sockets,
handler tasks and temporary key files are closed and checked before outer
containment can mask a leak.

These are isolated source-consumer results, not Windows/macOS OpenSSH
qualification, production native SSH recovery, live discovery/supervisor
integration, permission projection, installation or rollout. Their distinct
owners and remaining qualification gates are recorded in
[the canonical effort](../../efforts/active/dynamic-reverse-reservations/README.md).
