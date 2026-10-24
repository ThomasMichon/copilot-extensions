# Resident repository map amendment

[Back to the effort](README.md)

## Intent and evidence

The operator requests a daemon-flattened map of registered repository
information that consumers can obtain quickly in one call. This closes the
repository-identity and navigation-before-content visions without moving
registry ownership into Dispatch or the Manager.

Installed acceptance reached the immediate Configuration chrome and loading
state, but not the Workers resource table. With isolated writable registrar,
override and run directories, the installed catalog took 23.235 seconds
through its Python module and 26.047 seconds through its payload launcher.
Manager's list deadline is 20 seconds. A profile attributed 15.017 seconds to
provider-template repository discovery, which first enumerates repositories
and then resolves registered roots one by one. Raising the deadline or
parallelizing those subprocesses is not the selected repair.

The existing resident exposes authenticated, bounded Worktrees reads on its
control plane. Repository inventory currently uses a separate CLI list plus
registered-only point resolution. Reuse the resident control plane and its
election, routing and drain ownership; do not add a service, port or scheduler.

## Plan

The exact wire names and metadata below are agent-recommended implementation
details, not additional operator requests.

- [ ] Land this amendment and the standing repository-map intent before implementation.
- [ ] Add one producer-owned repository-map projection to the existing resident. Read the strict registry once per refresh; resolve registered local roots in process, preserving management classes, registered-only authority and explicit per-entry unavailability.
- [ ] Publish complete immutable snapshots with contract version, owner generation, source revision and observation time. Malformed or unreadable registry state must not publish a healthy empty map. Preserve last-good data only as explicitly stale/advisory.
- [ ] Expose a bounded authenticated `repos map --json` read through the existing resident control plane. Request handlers serve the published map without scanning registries, probing repositories, launching children or starting another daemon. Missing, incompatible, draining and unready service states remain explicit.
- [ ] Refresh the map through the existing producer lifecycle, including registry changes and periodic root-availability reconciliation. Preserve election, supersession, shutdown and drain behavior.
- [ ] Replace Workers template discovery's list-plus-point-resolution loop with one map read. Do not import the Worktrees domain runtime into Dispatch, copy its registry, fall back to directory discovery, or mistake a read-only root for execution authority.
- [ ] Document the read contract and run the validation below, then resume the parent installed acceptance and closure gates.

## Validation

- [ ] Producer controls cover empty, malformed and duplicate registry input; all supported management classes; absent, relative and unavailable local roots; source changes; and root disappearance without a registry edit.
- [ ] Reader controls prove one whole-map response, bounded unknown/unavailable outcomes, stale labeling, generation/scope validation, and no request-time registry/filesystem computation or child process.
- [ ] Real local control-server/client tests exercise authentication, map publication, independent readers, invalidation and shutdown without touching the operator's service.
- [ ] Dispatch tests preserve healthy peers, deterministic ordering and findings while proving one map read and zero per-repository resolution calls.
- [ ] A lightweight real consumer-boundary check covers the installed-style CLI/JSON seam and fresh isolated state on the available native platform; record the reason for any unavailable platform or external lane.
- [ ] After normal promotion and authorized rollout, repeat the real installed Configuration/Workers test with the unchanged 20-second provider deadline. Save-only, edit, cancel and explicit activation receipts remain required; no operator worker activation is permitted by this test.

## Boundaries

This is the parent effort's installed-acceptance repair, not a takeover of
release control, Companion implementation or the provider-template extension
campaign. No other agent is delegated or contacted. Existing approved
implementation and documentation remain delivered; the effort stays Active
until its complete acceptance and closure gates are resolved.
