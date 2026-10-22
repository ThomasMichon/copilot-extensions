# Finite reverse-listener reservation coordination

Back to [Dynamic Reverse Reservations](README.md).

This is an **agent-recommended proposal**, not an existing remote allocation
API, an authentication implementation or permission to change a listener.

## Problem and existing seams

A reverse bind is owned by the SSH server/connection that actually established
it. A local free-port probe, a PID in a discovery record or a client-generated
lease does not reserve that resource. A failed required forward can also
terminate a connection carrying independent service paths.

Existing source provides useful, distinct responsibilities:

| Source | Existing responsibility | Not supplied |
|---|---|---|
| `libs/ssh-manager/.../forward.py` and relay owners | Actual SSH argv, owned process/proxy lifetime, platform-aware forwarding | Participant-scoped remote listener allocation/publication authority |
| Accepted #7720 finite-forward contribution | Additive finite candidate selection, retained actual bind, candidate readiness and bounded existing-channel recovery | Fleet admission, grant projection or authoritative generation publication |
| `fleet-manager/.../gateway_ssh_policy.py` | Explicit machine/environment/direction/host-mediation decisions | Execution authorization or actual socket readiness |
| `fleet-manager/.../gateway_ssh_runtime.py` | Fixed trusted profile-owner/authority protocols, exact observation and plan fencing | Reverse bind resources or a listener lease registry |
| `libs/endpoint-rendezvous` | Local atomic advertisement and serialized owner-checked cleanup | Authenticated remote reservation or peer forwarding permission |
| Service-role Gateway attachment | Candidate/active generation separation | Generic TCP forwarding or an SSH allocator |
| `plugins/agent-ssh` transport contract | One source-qualified normalized registry/profile projection | An additional writer of topology, enrollment or runtime discovery |

The new primitive must compose those owners, not reproduce them.

## OpenSSH permission constraint

OpenSSH's [`ssh(1)` reverse forwarding](https://man.openbsd.org/ssh#R) can ask the
server for port zero and report the assigned port on supported multiplexed
clients. That is not a portable narrow forwarding grant.

The pinned portable implementation's
[`auth-options.c::handle_permit`](https://github.com/openssh/openssh-portable/blob/26b1363c7cbf5d01c730f45fc0b4abc5686beaf7/auth-options.c)
rejects non-wildcard ports at or below zero for both permission forms.
Consequently `permitlisten="localhost:0"` is invalid, not an authorization
recipe for arbitrary server-assigned endpoints. Replacing it with a wildcard
would widen policy. Literal `localhost`, `127.0.0.1` and `::1` matching must
also remain explicit.

The proposed bounded strategy accepts only the existing permission owner's
finite positive loopback tuples and their participant/direction context.
That owner must also authorize peer connections to the selected tuple.
Neither the caller nor discovery can add a `permitopen` grant. If the only
currently authorized tuple is occupied, the honest result is unavailable.
A different permission mechanism requires its own accepted owner contract.

This is source-backed analysis, not proof about an installed OpenSSH build or
native Windows multiplex support.

### Required pattern reconciliation before implementation

The existing governing transport pattern required OS-assigned forwarded ports
on both ends. Selecting a positive tuple from an authorized finite set is a
departure, even when an actual retained bind prevents duplicate ownership.
The permission constraint alone does not authorize that departure.

Phase 1 therefore includes a reviewed
[service-vision reconciliation](../../../visions/plugin-services/README.md#minimal-network-exposure),
plus a narrow
[transport-pattern amendment](../../../docs/patterns/service-transport.md#constrained-reverse-ssh-listener-grants)
and its [pattern-index qualification](../../../docs/patterns/README.md).
**Do not implement the finite-positive strategy until the governing vision and
pattern amendments are both reviewed and merged, or a conforming alternative
is selected.** The vision states the constrained, temporary, exclusively owned
and permission-preserving outcome; the pattern supplies the finite-tuple
mechanism rather than copying its API or permission syntax into the vision.
The amendment retains OS-assigned local/ordinary-forward defaults, applies only
where reverse-SSH allocation cannot be narrowly authorized otherwise, requires
existing finite permissions on both directions and actual retained bind/
authenticated readiness/generation custody, and preserves honest exhaustion.
It grants neither wildcard forwarding nor a target policy change.

## Source and integration boundary

Proposed new source:

- `fleet-manager/src/fleet_manager/reverse_listener_reservations.py`;
- `fleet-manager/tests/test_reverse_listener_reservations.py`;
- `fleet-manager/tests/test_reverse_listener_reservations_native.py`;
- `fleet-manager/docs/reverse-listener-reservations.md` and ordinary package
  documentation/changefile metadata.

These paths belong to the new bounded contribution. Do not edit existing
Gateway authentication, enrollment, profile policy, shared forwarding helpers,
transport providers, installers or resident supervisors under this reservation.
Any real integration patch first needs that source owner's exact acceptance.

The module will be a finite callable coordinator under a supplied existing
owner, not another background loop, CLI daemon, endpoint registry or persistent
lease store. Its public documentation must state which real adapter exists;
an interface and its synthetic implementation do not qualify deployment.
In particular, #7720 is the sole finite-forward source owner. Do not duplicate
its candidate iteration, classify arbitrary SSH failures as conflicts, or
create another reconnect/process owner in this module. The exact accepted
provider API must be consumed rather than guessed from the issue.

## Supplied-owner contract

Use one explicitly selected, independently trusted owner for the operation.
It supplies current participant/session/policy identity, finite permitted
tuples, generation/correlation observations, actual bind handles and the
existing authoritative discovery/effect journal. Parsing an immutable
document, a digest or a type does not authenticate that owner.

The proposal separates four operations:

1. **Observe and admit.** Bind the request's stable correlation to the current
   authenticated participant/session and policy observation. Refuse stale,
   withdrawn, unsupported, conflicting or unresolved previous operations
   before requesting another effect.
2. **Bind and retain through the existing leaf.** Invoke the accepted #7720
   owner contract once under the admitted correlation and finite operation
   budget. That owner alone selects among already authorized candidates and
   retains the actual successful process/listener. Only its positively
   classified bind conflict permits another candidate; authentication,
   permission, transport and ambiguous failures are not port conflicts.
   This coordinator must not retry the leaf after an unknown result or
   create a probe-close/rebind fallback.
3. **Verify and publish.** The owner proves expected target readiness on that
   resource and rechecks current policy/generation. Its existing authoritative
   journal atomically compares the expected predecessor and publishes the
   replacement. The coordinator cannot invent this transaction by writing
   another endpoint file.
4. **Reconcile and retire.** Confirmed rejection closes only the unpublished
   owned candidate. A lost bind/publication result stays attached to its
   original correlation for owner observation. It must not trigger a new
   allocation or dispose of a possibly published candidate speculatively.
   Retirement and renew/release compare exact generation/resource custody.

The actual typed API and resource/error shapes will be fixed during the
bounded implementation against these obligations. Explicitly distinguish
no-effect refusal, confirmed conflict/exhaustion, applied publication and
unknown effect. Unexpected programming errors propagate; no success-shaped
fallback or raw stderr/transcript logging is proposed.

The minimum integration evidence from the finite-forward owner is a selected
tuple within the admitted finite set, the retained resource/connection custody,
candidate-specific readiness, and the original operation correlation.
Confirmed no-effect refusal/exhaustion must be distinguishable from an
unresolved effect. A healthy process, a selected integer or a callback name is
not that contract. These are requirements to reconcile with #7720, not a
declaration that its future method or wire shape already exists.

## Concurrency, failure and connection isolation

The actual server/OS bind arbitrates competing candidates. A discovery CAS
alone does not reserve a port; a bind alone does not authorize publication.
Both are necessary, owned at their existing respective boundaries.

An unpublished candidate must not evict a healthy predecessor. The existing
supervisor retains ownership of connection topology, bounded reconnect,
backoff and shutdown. This coordinator cannot split a live multiplexed
connection, disable `ExitOnForwardFailure` or add another keeper merely to
claim blast-radius isolation.

Unknown holders remain untouched. TTL expiry, failed liveness and stale
metadata may withdraw an advertisement through the owning transaction; they
do not authorize killing a process or reclaiming another session's listener.
Late cleanup for generation N must not withdraw N+1. A Gateway restart cannot
turn a stale process-local lease into current admission.

Absolute operation limits, candidate count, diagnostic vocabulary and
retained uncertainty must be explicit. The implementation must not extend a
budget on each retry or repeatedly submit an unknown effect under fresh IDs.

## Qualification and honest completion

The initial isolated regression will use existing test containment and
synthetic loopback resources to retain an occupied old tuple, demonstrate the
fixed-tuple failure, and compare finite candidate recovery/exhaustion. It must
inspect owned resource closure before the outer test supervisor can hide a
leak. Concurrent bind attempts and stale/lost-result cases must exercise the
real coordinator rather than mocking past its state transitions.

An installed-wheel consumer check proves artifact/source independence. It
does not prove real SSH authorization, remote target identity or a deployed
owner. Windows and Linux are separate native outcomes; macOS and unavailable
OpenSSH tiers remain explicit rather than inferred from POSIX.

No existing isolated `sshd`/SSH key fixture was found in the inspected tracked
test inventory. Do not mint credentials or configure a host to fill that gap.
Actual OpenSSH/finite-grant and existing-supervisor/discovery integration must
either be supplied by an accepted existing owner and qualified, or transferred
to an exact named tracked objective before source-campaign completion.

Pure/unit, real socket, actual SSH, native caller cleanup, installer and live
rollout are distinct evidence tiers. Preserve the repository's required
admitted-unit policy; native/integration cases stay in their proper on-demand
lane. Passing a socket model is not permission to close the full recovery
objective, release/rollout gate or broader fleet campaign.
