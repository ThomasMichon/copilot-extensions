# Governed watch-owner cutover

Agent-recommended implementation and validation plan for the
[Durable PR-Watch Delegation effort](README.md), selected for review before
implementation. This prerequisite serves the installation-cell and graceful
cutover contracts; it adds no resident service or external supervisor.

## Existing boundary

The watch owner already serves an authenticated discoverable endpoint and
persists subscriptions and acknowledged pending deliveries. Its current startup
immediately adopts that state and starts polling/callback work. Its manual
restart stops the predecessor, waits for the singleton lease, and starts a
successor. Durable state protects replay, but does not provide passive health,
installer-driven promotion, or a recoverable runtime selection transaction.

The binding sources are `docs/install-contract.md` requirements 10-12,
`docs/patterns/marketplace-installation-cells.md`, and
`docs/patterns/graceful-daemon-cutover.md`. Reuse canonical `zdd` routing,
cutover, and attributable recovery primitives and installation-context receipt,
governance, slot completion, and selection helpers. Keep plugin-specific watch
adoption/drain semantics in a small owner adapter; do not copy Agent Index's
service coordinator or invent a second deployment framework.

## Service transaction

1. The owning installer provisions and validates an immutable completed target
   under the exact installation identity. Activation discovers only an
   attributable incumbent in that installation. No incumbent means no daemon
   is started solely because an install/update occurred.
2. A durable installation transaction binds prior marker/manifest/route,
   completed target, receipt generations, process identity, and random
   transaction authorization. Runtime selection and service routing are
   recoverable parts of that transaction, not independent success-shaped
   writes. Recheck governance before selection, promotion, and reconciliation.
3. Spawn a headless passive target through its installation-local launcher.
   It exposes instance-specific readiness and ownership evidence only. It
   does not restore active subscriptions, start polling/callbacks, mutate watch
   state, or publish shared active discovery. Context alone is not promotion
   or cleanup authority.
4. Before successor adoption, close predecessor mutation admission while
   preserving authenticated lifecycle control. Drain every already-accepted
   register/unregister handler, poller state mutation, and callback/ACK write.
   Finish already-accepted legacy callback batches under bounded deadlines;
   acknowledged retry events remain durable. A drain timeout does not authorize
   forced retirement or a second state writer.
5. Transfer the installation-scoped state-writer authority only after that
   quiescent boundary. The promoted target reloads the final persisted state
   rather than an earlier passive snapshot, preserving registration IDs, event
   IDs, retry metadata, and deadlines. Its read-ready promotion and route
   publication are exact-instance, transaction-authorized operations. Never
   allow two live generations to independently persist snapshots or dispatch
   callbacks for the same registration.
6. Publish the healthy successor's route and selected runtime/manifest under
   the recoverable transaction, then retire the exact predecessor. Old cleanup
   cannot clear the successor's endpoint or release its ownership. Bound control
   operations to installation, PID/start identity, runtime version, and instance
   authorization; never kill by name or familiar path.
7. Before the commit point, failure restores prior selection and resumes the
   attributable predecessor only after reconciling state-writer authority and
   reloading any committed state. After commit, recovery finishes the healthy
   target and retires only receipt-proven obsolete instances. Persist the point
   at which successor adoption can produce state effects so rollback cannot
   re-enable a stale predecessor snapshot. An uncertain route/marker/control
   mutation must be reconciled, not treated as permission to spawn a second
   owner or fall back to legacy.

The shared orchestrator supplies lifecycle machinery; the adapter must reconcile
its callbacks and commit points to this watch-state boundary before coding.
If the existing orchestrator needs a generic hook to express the pre-adoption
drain, extend and test that shared seam rather than bypassing it in the plugin.

## Compatibility and recovery

- Preserve ordinary no-context legacy commands and acknowledged notification
  schemas. Cell-specific mutation never falls back to an unqualified root.
- First upgrade from a pre-cutover lease-only owner uses an explicit,
  identity-checked transition: request the existing graceful shutdown, prove
  lease release, and adopt its final persisted state. An unreachable holder,
  drain failure, or uncertain shutdown blocks activation; it does not license
  parallel startup. Later upgrades use the governed routed contract.
- Default-off user-local governance still controls adoption. Existing
  unattributed legacy state blocks automatic cell activation until explicitly
  attributed through the canonical migration contract; do not silently copy,
  merge, or discard it. Do not take over the separately coordinated suite-wide
  legacy-migration work.
- Cell stop/restart and installer recovery retain pending deliveries. Abrupt
  death may leave a callback alive while recovery replays it, so consumer
  generation fencing and concurrency-safe idempotency remain required.
  Graceful cutover does not claim exactly-once subprocess execution.
- Clients follow only validated same-cell discovery. Draining or unavailable
  owners produce explicit retryable/uncertain results; missing RPC success
  never means a registration did not commit.
- Installers own activation; internal lifecycle seams are not a new public
  operator `deploy` requirement. On-demand watch bootstrap remains on-demand.

## Validation matrix

Use disposable installation homes, real subprocess boundaries, finite
deadlines, and existing contained tooling. Never interrupt a shared live watch
owner, coordinator, callback, or peer.

- Passive startup leaves subscriptions, callback counters, and active discovery
  unchanged. Promotion refuses foreign receipts, stale generations, wrong
  process identity, and missing transaction authorization.
- Real upgrade drains concurrent register/unregister requests, poll results,
  slow acknowledged callbacks, and queued legacy batches before adoption.
  The final state includes every committed registration/cancellation/ACK;
  graceful generations never overlap callback delivery for one registration.
- Crash before promotion, after writer transfer, after marker CAS, after route
  publication, and before retirement converges to one attributable owner and
  a coherent selected marker/manifest/route. Validate both rollback and
  commit-forward, including an unreachable predecessor and an ambiguous
  committed write.
- Governance changes after passive health refuse final promotion, preserve the
  prior owner, and clean up only the transaction's exact passive instance.
  Recovery cannot bypass disabled policy or a maintenance marker.
- Pending acknowledged events retain event/registration identity across
  promotion/recovery. Crash after consumer effect but before ACK still
  replays safely. Unrelated subscriptions continue after adoption.
- First-touch installation without an incumbent does not start a new resident
  daemon. First upgrade from a lease-only owner preserves pending state and
  refuses an unreachable holder rather than starting a duplicate.
- Two independently governed cells invoke the actual shipped PR-owner payload,
  retain separate state and endpoints, and cannot observe, stop, promote,
  repair, or retire each other's owner. Use actual adoption primitives,
  never a fabricated receipt or another plugin's payload.
- Native Windows and Linux tests exercise headless startup, bounded shutdown,
  identity-bound cleanup, and recovery. Record unavailable clean-room/live
  lanes with their exact reason and named follow-up before effort closure.

Required source gates remain owning-plugin tests, changed shared-library tests,
fatal Python lint, canonical generation/vendor checks, module-size,
headless-launch, install-contract, changefiles, current-head review, and required
CI. Every changed generated consumer declares its release impact.

## Sequencing

Land this reviewed intent before service implementation. Implement and validate
the lifecycle and installation-cell prerequisite through reviewable changes,
then prove release promotion and authorized rollout before reconciling the saved
dispatch integration. The parent effort owns deployment, final validation,
archive, and worktree finalization; this plan does not replace those gates.
