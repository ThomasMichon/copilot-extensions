# Four-service read-only shadow

[Parent goal](README.md#goal) · [Recovery plan](README.md#installed-portfolio-recovery-and-reversal)

> A proper "read-only" mode would permit recovery, and live polling reads from state past that point, but not anything which writes back to live mux sessions, performs git operations, writes copilot sessions or state, changes settings outside the install footprint, or changes input config. If session-hosts, supervisors, the agent-index index, or other "shareables" support multi-cast, then subscribing to them in read-only mode is acceptable.

## What to build (proposed)

Use the same recovery reader in two modes: finite recovery exits at the first
verified checkpoint; a shadow continues to refresh. Neither path falls through
to normal activation. `current-version` on the explicitly addressed source
installation selects the preferred export owner. If unavailable, sweep only
permitted read surfaces. In particular **no Git operations**, even status,
fetch-on-read or indirect repo discovery. Missing evidence is reported, not
replaced with guesses. Stage binaries before entering this mode.

1. **Prove the boundary before using live inputs.** Enforce it at process start,
   before imports/hooks can ensure, register, repair or elect anything. Supply
   only read capabilities, private endpoints and writable paths within the one
   scratch footprint (including DB/WAL, temp files, log, cursor and cache).
   No live control credentials or inherited mutating handles. Check child
   processes as well as handlers. A read that marks a message seen, renews a
   live lease, triggers indexing, opens a live DB for migration or starts an
   unavailable provider is not eligible. Never turn off a real owner's guard.
2. **Prove one snapshot plus one refresh per owner.**

   | Shadow | Read and compare | Must not happen |
   |---|---|---|
   | Worktrees | Registered worktrees, incarnation and status at the source revision | Git, checkout reconciliation, head claims or worktree mutation |
   | Manager | Workspace/mux mappings and independent session-host/supervisor observations | Create, attach a controller, send keys, resize, adopt, ensure or retire |
   | Bridge | Durable session identity, inbox/turn status and settlement observations | Consume/ack messages, resume or write a Copilot session, spawn or forward |
   | Dispatch | Task/steer/attachment state, tombstones and unresolved effect receipts | Claim, lease, dequeue, emit, schedule or replay an external action |

3. **Follow live changes without becoming a participant.** Poll bounded snapshots
   by revision, with backoff and footprint-local cursors. Use multicast only
   when the producer demonstrates an independent, non-consuming subscriber:
   it cannot ack for another consumer, extend a session's lifetime, acquire
   control, or remove queued events. A slow/disconnected shadow must not block
   the producer. On gaps, reconnect from a verified checkpoint or resnapshot.
   Prove the same for an Index read; otherwise leave the optional feed unavailable.
4. **Compose and reverse.** Route peer reads to shadow siblings by default;
   allow only named, audited live read adapters. Start four real generation-A
   daemons in scratch, keep the incumbent running, then select shadow B and
   return to shadow A. Only the scratch selector changes. After an independent
   fixture writer makes a create/edit/delete during B, A must catch up rather
   than present its retained stale view. Compare normalized records against the
   source at matching watermarks; report lag, duplicates, gaps, unavailable
   fields and mismatches separately. Liveness is not state equivalence.

## Acceptance and venue

### Bridge discovery from zero

Start with an empty Bridge database, not an export of its current listings.
Inventory potential targets from agent-enabled repository registrations and
worktrees, Copilot session metadata, live session-host and mux observations,
and registered provider rosters (including active and dormant Codespaces and
containers). Reuse each source owner's read-only listing. A provider timeout
means incomplete coverage, not an empty namespace. Do the full sweep at cold
start or explicit recovery, then refresh changed sources incrementally.

Separate a **potential source**, a **running agent with no verified route**,
and an **exact reconnectable session**. Deduplicate observations using provider,
venue, checkout/worktree incarnation and durable native session identity.
Names, old endpoints, workspace locks and PIDs alone do not prove a route.
For a surviving session require a non-mutating identity handshake with the
original host/transport. Report stale or conflicting evidence without guessing.
Listing a dormant venue must not start it; discovery must not send a prompt,
adopt a mux, consume an inbox, create a worker or revive an old claim.

Worktrees and Bridge treat mux sessions and session-host instances as
independently owned resources expected to outlive their launching app version.
An incoming version may reconnect to the verified original instance through
its owner's existing reattachment path. It must not use a create-if-missing
or resume-with-replacement fallback. A missing original is reported as
missing; an unavailable probe or ambiguous identity stays unverified. Neither
condition makes the old record a request to provision a replacement. Creating
a new session or host requires a separate, explicitly agent- or
operator-directed action, not an automatic recovery step.

Test A -> B -> A with the same surviving mux and session-host incarnations;
record their identities before and after each switch. Then make the original
unavailable and separately present a different instance under the old name.
Require zero creation calls in all cases, exact reattachment only in the
survivor case, and no takeover of the lookalike.

The cold fixture must include an unregistered surviving mux session, duplicate
evidence for the same native session, a stale endpoint with a reused PID, an
agent-enabled checkout with no session, active and stopped provider venues,
and an unavailable provider. Score inventory identities and verified routes
separately against the fixture owners. Existing Bridge rows are a comparison,
not the expected universe. `live-sessions list` remains a registered-session
view; widening the discovery catalog must not label a candidate as live.

- [ ] With synthetic inputs, deny writes at the filesystem/IPC/process
  boundaries and count attempted effects. Exercise startup, polling, error,
  reconnect, rollback and teardown; zero prohibited attempts. Compare source
  state and control-call counters, not just file hashes on a changing host.
- [ ] Run producers and an ordinary subscriber concurrently. The shadow sees
  independent updates including deletes; the ordinary subscriber still gets
  every expected event. Lost feed produces explicit lag, not false freshness.
- [ ] Four private health endpoints return footprint, generation, observation
  mode and watermark across A -> B -> A. Incumbent selector, routes, mux,
  configuration and writer leases never change. Closing the shadow releases
  only its own subscriptions, endpoints and processes.
- [ ] WSL/container: fixture producers and read-only mounts, no host control
  sockets, credentials or writable host home. Record this as Linux evidence.
  Windows separately exercises native pipes, ACLs, Jobs and surviving hosts.
  Only connect to an existing installation after passing denied-effect tests
  on that platform. Writer takeover remains a later, separate testbed gate.

### Proposed local Session Host launch receipt (owner decision pending)

**Decision for the Bridge launch-custody and Driver Session Host maintainers:**
can the existing host registration owner preserve a launch-only observation
independently of subsequent candidate updates? Prefer extending that owner to
adding another store. If it cannot enforce separation of writers and compatible
round trips, retain candidate-only discovery. This is a proposal, not a selected
storage format or a grant to implement attachment.

**Current boundary.** `LocalLaunchOwner.retain_process` holds the original
process object and observes its OS birth before readiness. `retain_host` calls
`_register_host_launch`, which publishes outside the Bridge DB. The existing
`HostIndex.register` also accepts later location updates: it preserves frontend
ownership fields, not independent evidence about the original Host. Its
`locked_inventory` is a storage read, not an incarnation check. Acquisition and
creation `read_launch` can read closed attempts, but both use the Bridge DB;
neither survives loss of that DB. Driver's readiness file is written by the
Host and contains an attachment nonce. Neither it nor a new registration is a
source for reconstructing original evidence after the fact.

**Recommended producer and reader, if accepted.** For an admitted local
launch, let the launch owner bind its original handoff and session identity
to the host PID and OS process birth while it still holds the live process
object. Scope the observation to the OS instance and principal; where those
cannot be distinguished reliably, return unknown. Publish once through the
existing registration owner under its lock. An ordinary register, claim,
forward refresh or restore must neither mint nor rewrite that observation.
A conflicting launch or a torn write is not first registration. Retirement
or deletion invalidates the association; never recreate it from a candidate.
Exclude endpoint, nonce, grant, prompt and credential material from the
reader's projection. A private file alone does not establish who wrote it:
the owners must establish writer provenance and prevent candidate-controlled
metadata from populating the trusted fields, or decline this design.

After loss of the frontend and DB, the registration owner would read that
scoped observation and compare the OS identity of the named process, without
opening the candidate endpoint. Return an **original process observed** only
on an exact match; a reused PID is a different incarnation. Distinguish an
observably exited original from a denied or unavailable OS read, ambiguous
record, unsupported producer or unsupported reader. All leave the agent route
unresolved. Local results do not authorize remote probing. Do not query an
attachment interface, and never fall back to reading the nonce. Absence of
this optional capability leaves ordinary inventory and the measured
`GET /api/v1/sessions/{id}/status` **404** unchanged for an unmanaged session.

**Acceptance before implementation.** Record the source owners' decision
on the proposal PR: accept the existing registration owner with those writer,
retirement and reader constraints; nominate a different existing durable
launch owner and its cold read; or retain the 404 if neither qualifies.
Automated review and CI alone do not decide persistence ownership.

**Implementation and installed gates after acceptance.** Start a synthetic
original through the real launch owner, discard its frontend and DB, and use
the public opt-in Bridge consumer. Compare the original with a replacement
at the same address, a reused PID, an exited host and an unreadable source.
Attempt to change the observation via ordinary registration and via an older
writer. It must remain valid and unchanged or become explicitly unavailable,
never silently certify the replacement. Exercise both mixed-version orders
and return to the original installed build. Compare ordinary inventory and
status before and after; assert zero candidate connections, attachments,
creates, replacements, prompts or control grants. Require source review,
required CI and merge, then separate Windows and Linux installed proof.
Neither design acceptance nor observation recovers acknowledged writes.
