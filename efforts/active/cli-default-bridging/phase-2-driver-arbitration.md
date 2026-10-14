# Phase 2: exclusive remote driving, human takeover, and re-hook

Parent: [CLI-Default Bridging](README.md).
Tracker: [#5930](https://github.com/ThomasMichon/copilot-extensions/issues/5930).
This is the implementation plan; unchecked items are not runtime guarantees.

## Requested policy

> Human input can automatically revoke, but we'll need a way to be able to "hook it back up".

Human takeover and a usable return to remote driving are both required.
Revoking a claim must not permanently sever the session or its observers.
The implementation choices below are **agent-recommended**; the operator did
not independently specify the wire format or recovery algorithm.

## Authority boundary

The standalone extension remains the target-local authority. Do not add a
bridge dependency, shared broker, durable agency store, or reaper daemon.
Reuse the existing authenticated loopback endpoint and discovery convention.
Session Host's generation-scoped claim/compare-and-release is the behavioral
precedent, not a Python dependency to impose on this payload-only plugin.

Distinguish:

- endpoint access and observation;
- authority to grant/re-hook remote driving;
- the current driver's generation-bound mutation credential.

Knowing a session ID, claiming an owner name, observing an event, or holding
a revoked driver credential must never restore mutation authority. Re-hook
uses the target's authorized control surface, not the revoked credential.
State and diagnostics must not expose mutation credentials.

The initial guarantee is exclusive mutation **through this driver surface**.
It is not a claim to intercept arbitrary keyboard input or every other SDK
client. Human input observed through the native session stream is a takeover
signal, not an authenticated physical-presence credential. Source metadata is
attribution, not permission to acquire a driver claim.

## Proposed behavior

### One current driver; independent observers

- [ ] Give each extension lifetime an instance identity, and each grant or
      ownership transition a fresh generation. Fence send, abort, steer,
      release, and re-hook against the expected target identity/generation.
- [ ] Claim acquisition is atomic: simultaneous contenders cannot both win.
      A driver name is diagnostic, not a substitute for its credential.
- [ ] Keep `/health` and `/events` usable independently of write ownership,
      within existing resource bounds. Revocation does not drop all observers.
- [ ] Advertise the fenced-control protocol/capabilities in discovery and
      health. Distinguish the discovery document's schema version from the
      control protocol. A legacy token-only mutation fails explicitly rather
      than bypassing the new fence.

### Human takeover

- [ ] Observe root-session user input before the decoupled SSE forwarding
      queue. Keep the callback bounded and synchronous; never make safety
      depend on the lossy observer queue or an SSE subscriber.
- [ ] Tag admitted remote sends with target-generated provenance so their
      resulting user-message events do not revoke their own claim.
      Correlate against admitted operations; do not trust an arbitrary
      caller-supplied source label as authority.
- [ ] Root input not attributable to the admitted remote driver revokes its
      mutation claim and puts the target into a human-held state. Ignore
      sub-agent events as root-human takeover evidence. Treat unsupported or
      ambiguous provenance conservatively, with a visible reason.
- [ ] Make the revocation boundary observable: old-generation mutations and
      delayed continuations fail after the target processes takeover.
      Do not claim revocation occurs at the physical keystroke, or that it
      undoes an operation the native runtime already accepted.
- [ ] Do not automatically abort human work or erase session history merely
      to change ownership. Whether a native operation was already accepted
      must be reported honestly rather than disguised as an unexecuted call.

The inspected SDK has `MessageOptions.source` and user-message source metadata,
but the prompt-submitted hook has no denial decision. Types alone do not prove
source preservation, notification timing, or native admission fencing. Exercise
those properties directly before declaring human-takeover validation complete.
An unsupported native path must not silently act as if takeover is enforced.

### Deliberate re-hook

- [ ] Provide an explicit, authenticated re-hook operation and a practical
      standalone command/helper for it; requiring an operator to hand-copy
      bearer tokens or reconstruct curl requests is not the intended UX.
- [ ] Re-hook transitions out of human-held state only through that authorized
      operation. Ordinary reconnection, a retry, observer attachment, or an
      old credential cannot silently undo human takeover.
- [ ] Return or acquire a **fresh** driver claim. Never reactivate the old
      generation, even for the same driver name.
- [ ] A new human input racing re-hook wins a new ownership transition;
      stale re-hook requests cannot overwrite it. Keep observers and the
      running session available through the transition.

An explicit re-hook, rather than automatic reacquisition after an idle timer,
is the agent-recommended interpretation of "hook it back up." This is not a
claim that the server can cryptographically distinguish a human gesture from
an otherwise authorized control-plane call.

### Concurrent operations and recovery

- [ ] Serialize control admission per target with bounded state, not an
      unbounded request queue. Fence effects at the last responsible point.
- [ ] Treat steer as one operation: after awaiting abort, revalidate ownership
      before sending. A takeover in that gap must prevent the stale follow-up
      send and report any already-applied abort explicitly.
- [ ] Reject stale/replayed mutations without repeating their effects. A
      generation plus monotonically checked operation sequence is one bounded
      candidate; do not solve this with an unbounded result cache.
- [ ] Release compares the current generation and credential. A delayed old
      release must not release a new driver's claim.
- [ ] Disconnecting an SSE observer is not release, and losing a controller
      connection is not proof that its native request never executed.
      Reconcile or fail closed on ambiguous in-flight outcomes.
- [ ] Define recovery from a crashed controller and a restarted extension.
      A new extension instance must not accept old mutation credentials;
      unresolved native work must not become a false idle/success result.
      State the deliberate recovery/re-hook path without requiring another
      coordination plugin.

## Implementation slices

1. Pure target-local ownership reducer and SDK-free HTTP integration, with
   deterministic generation/credential/sequence and race tests.
2. Production SDK provenance/event wiring, plus standalone claim/re-hook UX,
   versioned discovery, documentation, and the plugin changefile.
3. Native, isolated proof of two-controller exclusion, human takeover, and
   fresh re-hook. Reconcile failed/unsupported native cases explicitly.

Keep implementation in the existing plugin and reuse its SDK-free session
adapter and HTTP test seams. Do not migrate agent-bridge to the SDK. A future
Phase 3 TTY command bridge must participate in the same ownership boundary;
HTTP fencing alone must not be presented as fencing direct mux writes.

## Validation plan

| Lane | Required proof |
|------|----------------|
| Pure ownership tests | One simultaneous winner; wrong instance/generation/credential refused; stale release and replay refused; human-held state survives ordinary reconnect; fresh re-hook; bounded recovery state |
| HTTP/SDK adapter integration | Real HTTP calls reach the strict SDK fake only for the owner; observers still work; legacy mutation fails; takeover during awaited abort prevents steer-send; re-hook race cannot restore a revoked generation; no credential leakage in diagnostics |
| Native local/clean-room | Explicit native/SDK versions; source labels actually preserved; real user input observed independently of SSE; two controllers against one live session; rejected calls have no native mutation; re-hook restores driving with a new claim |
| Recovery | Lost responses, disconnects, extension restart, and already-accepted native requests distinguished; no false success, silent remote reacquisition from human-held state, duplicate send, or forced destructive reset |
| Compatibility | Existing read-only discovery/listing remains usable; newly required mutation protocol is advertised and documented; Windows and Linux test seams retained |

Native tests may consume model credits and must use the correctly classified
test lane, not relabel a prompted run as an agent-free Tier-P scenario. Keep
workloads synthetic, credentials redacted, cleanup caller-owned, and evidence
about the actual candidate rather than an installed older payload.

Review and merge this plan before implementation. The phase's live-concurrency
checkbox and parent validation track (b) remain open until their native proof
actually passes. All other version-floor, cross-venue, interaction, comparison,
and default-promotion gates remain unchanged.
