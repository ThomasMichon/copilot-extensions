# Finite Vault copy-transaction contracts

[Canonical Vault Service Plan](README.md#finite-copy-transaction-contracts) |
[Global Validation](README.md#validation-plan) |
[Campaign crosswalk](../backlog-reconciliation/journal/2026/11.20-vault-copy-and-existing-keepers.md)

These are complete proposed P2 credential/recovery cases, not implementation
or permission to use a Vault. The eighteen technical state rows live only
in the canonical README. Preserve its Goal, R1-R4 relay priority, P0 design
and applicable owner/slice admission. Existing CLI/service, cache/journal,
credential/provider and recovery custodians retain their work; no relay
implementer, credential, process or resource is reassigned.

The three qualified whole originals have no discussion refinements. The
removed revisions cited by their authors preserve historical proposals;
they are not current implementation or qualification evidence. Keep copy
capability, relocation and explicit overwrite distinct: shipping ordinary
`copy`/`cp` does not accept the removed destination/fencing or overwrite work.

## Relocation and cache fences - #13077

Source: [complete request #13077](https://github.com/ThomasMichon/copilot-extensions/issues/13077).
The removed part of #12158 covered both `move` and `copy` explicit
destinations outside the service group and relocation cache fencing.
Historical `98a23d71` is not proof those semantics are currently delivered.

### P13077-1 - Destination and move safety

Restore both `move` and `copy` explicit destination paths outside the
service group, retaining the existing group-scope safety rules for `move`.
Do not silently reduce the request to copy alone or broaden move authority.
Existing credential and destination ownership continues to govern effects.

### P13077-2 - Generation-bounded invalidation

A relocation fence invalidates cached values only at or below its captured
generation ceiling. It must not delete the whole cache entry and thereby
discard pending journal records or any value written after that ceiling.
Preserve BOTH pending journal records AND post-ceiling writes; preserving
one is not acceptance of the other.

### P13077-3 - Capture order and lookup expiry

Capture the generation ceiling before revoking cached values, so a
concurrent write between revocation and later observation is not wrongly
included in or allowed to escape the selected fence. Apply floor expiry
during lookup itself; eventual maintenance pruning cannot keep an expired
floor hiding otherwise valid values.

### V13077-1 - Destination regressions

Exercise the actual move/copy destination consumers with synthetic
credential storage, including outside-group paths, ordinary existing forms
and move's existing group-scope refusals. Do not mock past the consumer or
perform a real credential move/copy to validate documentary receiving.

### V13077-2 - Preservation regressions

At the real cache-fence boundary, retain an old fenced value, pending
journal records and a post-ceiling write. Assert only the selected old
value becomes unavailable; the journal and newer value survive unchanged.
An empty cache or a test bypassing invalidation cannot prove this result.

### V13077-3 - Ordering and expiry regressions

Add focused regressions for both remaining original findings: a controlled
write between capture/revocation observations proves the ceiling is taken
before revocation, and lookup of an expired floor exposes a valid value
without requiring a maintenance pass. Preserve existing journal,
generation and ordinary consumer behavior; no new native-host matrix is
required by this finite source request.

## Running-service copy support - #13083

Source: [complete request #13083](https://github.com/ThomasMichon/copilot-extensions/issues/13083).
An updated CLI may reach an older or rolled-back service that returns
`Unknown action: copy`. An appended restart hint is useful historical
behavior, not pre-mutation support detection.

### P13083-1 - Provider-first support

Follow the existing [service-contract evolution pattern](../../../docs/patterns/service-contract-evolution.md).
Advertise copy support through the health response of the actual running
Vault service, independently of package versions or a newer installed
payload. Expand the provider before migrating callers; retain existing
request forms and ordinary operations throughout the declared window.
This proposal selects no new daemon, universal handshake or per-call
preflight subprocess.

### P13083-2 - Selected-peer preflight

Before sending the copy mutation, check the selected service's advertised
support. Missing support must return a clear upgrade/restart explanation
without trying the unsupported mutation or disabling unrelated legacy
operations. Re-resolve support when the executing peer changes and retain
effect-side enforcement and unknown-outcome semantics under the existing
pattern. Document actual contract/capability identity, supported peers,
provider-first migration and upgrade/rollback support window in the
implementation's Contract impact assessment; package equality is not
capability selection. This is not permission to restart a service.

### V13083-1 - Mixed-version boundary

Use focused real parser/transport consumers with synthetic current/prior
peers: old caller/new provider preserves old behavior; new caller/new
provider selects copy support; new caller/unsupported old provider gives
the useful declared pre-effect refusal. Cover missing/invalid capability
metadata without guessing support or rejecting every ordinary operation.

### V13083-2 - Rollback and unknown outcomes

Cover supported provider rollback and peer change, including the boundary
between observing health and sending the mutation. Preserve strict
unsupported semantics, actual peer selection and existing supported
operations. Lost or uncertain mutation acceptance never permits blind
replay, downgrade or a false success. Keep these focused controls out of
an exhaustive required-PR/native matrix; this request does not authorize
live service replacement or credential operations.

## Explicit overwrite and uncertain renames - #13085

Source: [complete request #13085](https://github.com/ThomasMichon/copilot-extensions/issues/13085).
#12158 deliberately landed copy that refuses an existing destination.
Historical `308337809860824b7028aedb32d31ff1b19a9fd8` preserves the removed
staged overwrite in `_finish_staged`, not a qualified current overwrite.

### P13085-1 - Explicit overwrite scope

Reintroduce the separately requested `copy -f` / `overwrite: true`
behavior through the existing CLI/service and credential/provider owners.
Keep ordinary no-overwrite refusal and existing unrelated operations.
The historical sequence stages under a temporary title, renames the
destination to a backup, renames the staged copy into place, then removes
the backup. Retain its recovery obligations without treating this plan
as authority to perform any rename, overwrite, backup removal or cleanup.
Any changed wire behavior retains #13083's independently required
version-skew contract rather than assuming synchronized releases; this
does not make the whole #13083 tracker a new implementation prerequisite.

### P13085-2 - Uncertain rename reconciliation

A destination-to-backup rename may commit even when its call times out or
reports failure. Do not assume the destination was untouched from that
error. Settle each uncertain rename by re-listing the group before
deciding whether to restore, preserving actual credential/provider
custody and honestly unresolved outcomes. If the final staged-to-destination
rename and restoration both fail, retain both surviving recovery entries
instead of assuming restoration or erasing evidence.

### P13085-3 - Recoverable error reporting

Every error path must name any surviving staged or backup entry so the
operator can locate the retained material for manual recovery. Report BOTH
names when both survive, including a committed-but-timed-out backup rename.
Keep useful entry-name diagnostics secret-safe; do not print credential
values. An unnamed temporary survivor or generic failed-copy message does
not meet the original recovery contract.

### V13085-1 - Committed backup timeout

With synthetic group/provider state, make the backup rename commit and
then report timeout/failure. Exercise the actual overwrite/recovery
consumer, verify re-listing before its restore decision, and assert the
original or explicitly named backup remains recoverable. Merely making
the rename fail before effects does not reproduce this finding.

### V13085-2 - Failed final rename and restore

Make the final rename fail AND restoration fail. Assert the error names
every surviving staged and backup entry, re-listing guides the decision
and no credential is silently lost or claimed restored. Retain ordinary
successful copy/overwrite and existing-destination refusal controls.
These are focused source regressions, not permission for a real Vault
mutation or a new all-platform native programme.

## Receiving, resolution and custody

Each G13077/G13083/G13085 separately resolves every corresponding Plan,
local Validation and continuing Coordination clause at its original
applicable tier OR through explicit accepted transfer to a named tracked
objective preserving every unresolved clause and actual custody.
Unavailable evidence stays unresolved, not passed. No unrelated relay,
browser, federation or whole-service implementation gate is added to
recording this finite proposed acceptance.

Normal review and actual complete receiving precede current whole
source/receiver/custody readback and separate source-tracker judgments.
Neither proposal merge nor later administrative retirement changes a
technical checkbox, authorizes implementation or releases a credential,
claim, native operation or resource.
