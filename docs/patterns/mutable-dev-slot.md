# Pattern: immutable development generations

**Serves:** *Vision plugin-services* `immutable-versioned-runtime` and
`seasoned-external-dependencies`. The historical filename remains stable for
existing links; it does not retain a mutable/editable deployment exception.
This is the required target, not a claim that every installer has adopted it.

**Owners:** `libs/versioned-runtime` supplies claim, construction, completion,
selection and retention primitives. `agent-codespaces` supplies the explicit
development installer and deployed `dev-release` / `dev-status` verbs.

## Problem

A contributor needs to exercise an unmerged worktree through the actual deployed
CLI without editing a deployed numbered slot or waiting for a release. Reusing
one active `versions/dev` venv makes a failed dependency refresh corrupt the
previously working development runtime. Retrying a reduced capability there
does not restore the original environment.

## Standard approach

An explicit `dev` action claims development ownership, captures the original
rollback target once, and builds a fresh `versions/dev-<generation>/` directory.
The generation is an ordinary immutable slot: canonical construction ownership
covers every dependency write, isolated health checks precede completion, and
completion transfers to retention before publication.

Freeze the complete source and admitted build/dependency inputs before
construction. Install non-editable code from that snapshot; generation identity
binds its source hash and assembly inputs separately from package version.
A later source edit cannot change deployed imports. Every iteration builds a
distinct generation or reuses its exact healthy completed match, never a
renamed built venv or a live worktree link. Failure
reclaims only the owned incomplete candidate, keeps the working generation and
selectors unchanged, and propagates a nonzero result.

Select the completed target as current and request its normal start. An owner
is superseded because its full generation identity differs from current, never
because a version or hash sorts later. The existing lifecycle health-gates the
incoming owner, hands off queued/scheduled work, switches routing and drains the
displaced owner before retirement. "Not current" is not an immediate-exit rule.
Compatible reverse selection uses the same protocol; incompatible durable state
refuses explicitly. Release-channel ordering remains a separate selection
policy, not an owner-obsolescence test.

Clean merged Dev deployment uses this same immutable lifecycle under
[Source-Managed Runtime Installs](../../efforts/active/source-managed-runtime-installs/README.md)
(#7028/#7029), without acquiring an interactive worktree claim. Neither path
implicitly enables development mode for ordinary install/update.

## Legacy behavior and migration

The existing pilot's `dev` action installs editable packages against its
owner's worktree, even when dependency environments are distinct protected
generations. That is legacy behavior to replace, not full immutable deployment.
Do not advertise source edits as immediately deployed or hot-patch a completed
runtime. Until an adopter supplies frozen, non-editable construction and the
complete cutover contract, report the gap rather than inventing a supported
command. The [Immutable Dev Slot effort](../../efforts/active/immutable-dev-slot/README.md)
retains #5472's per-adopter acceptance.

Existing claims and `versions/dev` remain readable/restorable for compatibility.
Migration never refreshes or removes a completed legacy environment in place;
it retains the original rollback target until an authorized healthy transition.

## A claim gates who may build and publish

`<root>/dev-claim.json` retains the dependency-free
`copilot-extensions.dev-slot-claim` schema:

```json
{
  "schema": "copilot-extensions.dev-slot-claim",
  "version": 1,
  "owner": "<absolute worktree checkout path>",
  "host": "<hostname>",
  "pid": 12345,
  "claimed_at": "2026-01-01T00:00:00Z",
  "previous_version": "1.2.3",
  "selected_generation": "dev-0123456789abcdef0123456789abcdef"
}
```

The existing claim format above is retained compatibility, not a new identity
format: frozen-source identity belongs in canonical generation evidence.
`selected_generation` is optional for legacy claims and records the most recent
completed development generation published by this owner. Construction,
completion and activation validate the canonical claim; losing or replacing
ownership refuses publication. Claim acquisition precedes any venv writes.

Same-owner reclaims preserve both the original `previous_version` and the
selected generation. A conflicting owner requires an explicit `--force`;
claim conflicts remain exit 2, distinct from installation errors. A forced
takeover is a new claim, not permission to rewrite an existing generation.

Claims persist across process lifetimes with no TTL or liveness-based expiry.
The finite construction supervisor is different: its caller-held pipe owns one
candidate and reclaims an incomplete build on failure or interruption.

## Release and retention

The deployed CLI uses the staged canonical helper, not a source checkout or
another plugin's venv:

```text
dev-claim   --owner <path> [--previous-version V] [--force]
dev-release --owner <path> [--force]
dev-status
```

Release restores the original completed rollback target and settles the claim
under the selector publication guard. An unsuccessful restore retains ownership
and returns an error. With no recorded predecessor, release does not invent one.
Legacy claim records and `versions/dev` remain supported.

GC preserves the claimed selected development generation, the original rollback
target, current/last-known-good selection, live construction/retention owners,
and live runtime processes. Superseded development generations become ordinary
GC candidates only after those protections end. Claim ownership is not a
blanket exemption allowing cleanup to delete someone else's build.

## Runtime accessibility

Installers stage `versioned_runtime.py` at the runtime root. Deployed
`dev-release` / `dev-status` work without a checkout and never import a sibling
runtime. `agent-worktrees finalize` reads the stable claim record, warns about
the finalizing worktree's outstanding ownership, and names the release command;
it does not silently release a claim.

## Ordinary installers

A completed numbered slot with matching attributable payload content is reused
byte-for-byte. Conflicting content refuses reconstruction and directs the
operator to a new version or an explicit claimed development generation.
Malformed completion or slot identity requires explicit health repair, never
destructive reconstruction inferred from a failed probe. Failed unpublished
candidates are reclaimed through canonical held construction and cleanup guards.
Out-of-slot reconciliation still runs on true reuse.

The payload fingerprint covers every runtime install input, including source,
vendored libraries and selected sibling packages. `--force` never authorizes
rewriting a completed numbered or development generation.
