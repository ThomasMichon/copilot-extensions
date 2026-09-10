# Phase 6 — Legacy Attribution, Rollback, and Retained Evidence

[Effort](README.md) · [Architecture](design.md) ·
[Governance note](installation-mode-governance.md) ·
[Normative install contract](../../../docs/install-contract.md#installation-mode-governance) ·
[Migration issue #1110](https://github.com/ThomasMichon/copilot-extensions/issues/1110)

## Purpose

Phase 6 adds the explicit management steps that move a runtime plugin from
legacy ownership into one installation cell, let an operator roll that choice
back deliberately, and remove only compatibility artifacts that the cell can
still prove it owns.

The exact JSON schemas remain owned by the install contract:

- [installation activation](../../../docs/install-contract.md#installation-activation-schema)
- [legacy ownership tombstone](../../../docs/install-contract.md#legacy-ownership-tombstone-schema)
- [deactivation record](../../../docs/install-contract.md#deactivation-record-schema)
- [legacy retirement record](../../../docs/install-contract.md#legacy-retirement-record-schema)

This note explains how those records work together, what is retained, and how
to diagnose a deactivated-but-not-cleaned cell without restating the whole
contract.

## Lifecycle summary

### 1. Explicit legacy attribution

`attribute_legacy_state(...)` is the management entry point that claims clear,
explicitly enumerated legacy artifacts for one destination cell.

- The caller must provide the target install context, the expected marketplace
  and plugin identities, and the exact legacy paths being claimed.
- The helper holds the legacy lock together with the marketplace genesis lock
  and the destination cell's install lock, then requires matching maintenance
  authorization before any mutation.
- The destination cell's namespace and install receipts must still be `active`.
- Linked or reparsed paths, ambiguous ownership, orphaned transfers, and legacy
  state already claimed by another cell are preserved unchanged for diagnosis.

On success, attribution writes two things:

1. `<legacy-root>\.installation-ownership.json`, which ties the claimed legacy
   footprint to one activation generation in one destination cell.
2. `<plugin-root>\installation-activation.json` with `mode: namespaced`,
   `state: active`, and `legacy.disposition: retained-inert`.

The legacy files stay on disk. Attribution changes who is allowed to mutate
them; it does not delete them.

### 2. Explicit rollback or deactivation

`deactivate_installation(...)` is the management entry point that returns
authority to legacy mode.

- The caller must supply the exact activation, namespace, and install
  generations it observed; any drift returns `revalidation-required`.
- The caller must name exactly one target:
  - a matching tombstone generation for a legacy-attribution rollback, or
  - `expect_tombstone_absent` for a pure cell deactivation with no legacy claim.
- A rollback path still requires the legacy lock plus matching maintenance
  authorization.

On success, deactivation always writes a new activation generation at
`<plugin-root>\installation-activation.json` with `mode: legacy`,
`state: deactivated`. For an attribution rollback it also writes the paired
`deactivations\activation-<generation>.json` record and then clears the matched
tombstone under the same lock discipline.

Pure cell deactivation is narrower: it succeeds only when the caller proves no
tombstone exists and the declared legacy probe is already `absent`.

### 3. Ownership-checked compatibility retirement

`retire_legacy_compatibility(...)` removes only the legacy compatibility
artifacts that a healthy namespaced cell can still prove it owns.

- The current activation must still be the expected namespaced generation.
- The legacy tombstone must still validate for the current cell and generation.
- The retirement target items must match the tombstone's recorded attribution.
- The replacement runtime or service must contribute an explicit
  `health.status: "ready"` report.

On success, the helper removes only the ownership-matched artifacts that were
explicitly targeted and records the result under
`<plugin-root>\retirements\activation-<generation>--<retirement-id>.json`.
Repeating the same target is an idempotent read of the existing record. If a
retired artifact reappears later, the helper returns `preserved` and leaves the
record intact.

## What is retained

| Artifact | Meaning | Cleared automatically? | Current retention behavior |
|----------|---------|------------------------|----------------------------|
| `<legacy-root>\.installation-ownership.json` | Temporary proof that one legacy footprint was attributed to one cell activation | No background cleanup; only explicit rollback clears a matching tombstone | Persists until an explicit rollback clears it, or indefinitely if it becomes orphaned and needs diagnosis |
| `<plugin-root>\installation-activation.json` | Monotonic record of the currently authoritative mode for the cell | No | Attribution and deactivation update it in place; deactivation retains the record with `mode: legacy`, `state: deactivated` |
| `<plugin-root>\deactivations\activation-<generation>.json` | Durable audit record describing which activation generation was rolled back or deactivated | No | Retained indefinitely by the shared library; reused for idempotent replay checks |
| `<plugin-root>\retirements\activation-<generation>--<retirement-id>.json` | Durable audit record describing which compatibility artifacts were retired after health and ownership checks | No | Retained indefinitely by the shared library; reused for idempotent replay checks |

There is currently no age-based expiry, scavenger, or rotation for deactivation
or retirement records in the shared installation-context implementation.
Tombstones are different: a successful rollback clears the matching tombstone,
but a stale or foreign tombstone is deliberately preserved so later repair can
see exactly why mutation failed closed.

## Diagnosing a deactivated-but-not-cleaned cell

The informal phrase "inactive cell" means a cell that has been deactivated but
not yet removed. The authoritative state is:

- `<plugin-root>\installation-activation.json` shows `mode: legacy`,
  `state: deactivated`.
- `<plugin-root>\deactivations\activation-<prior-generation>.json` explains
  whether that state came from a legacy-attribution rollback or a pure
  cell-deactivation step.
- `legacy.disposition` in the activation record explains what happened to the
  legacy surface:
  - `restored` means a matching tombstone existed and was cleared during
    rollback.
  - `absent` means deactivation happened only after the caller proved no
    tombstone remained and the declared legacy probe was absent.

What remains behind after deactivation is intentionally conservative:

- the cell root still keeps any version slots, snapshots, logs, cache, launchers,
  and state that deactivation itself did not remove;
- any deactivation or retirement records remain as audit evidence; and
- any already-retired legacy wrapper or service artifact stays removed.

Deactivation changes authority and records evidence. It is not a cleanup pass.

## Current cleanup boundary

The first operative exemplar is `agent-machines`. Its explicit
`cell-uninstall` flow is a separate, receipt-authorized step that requires the
installation to have been deactivated first and preserves durable `state/`
content by design. Separately, deactivation and compatibility retirement leave
their own audit evidence behind. As a result, an operator can legitimately see:

- a deactivated activation record,
- retained audit records under `deactivations/` and `retirements/`, and
- preserved cell-local state that still needs an explicit uninstall or cleanup
  decision.

That shape is expected with the current implementation. Phase 6's documentation
item is therefore about making the retained evidence legible, not claiming that
all retained artifacts already have a general expiry policy.
