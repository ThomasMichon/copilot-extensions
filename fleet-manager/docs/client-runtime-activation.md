# Owned client runtime selection

`fleet-manager client-state activate-runtime` publishes the installation's local
runtime selectors for an already admitted, constructed client. This is not
frontdoor/PATH registration, a complete managed installer, Gateway connectivity
or permission to deploy to a live installation.

```text
fleet-manager client-state activate-runtime --root <private-installation> --identity identity.json --selection selection.json --release release.json --proof proof.json --policy policy.json --sources sources.json --staging-parent <separate-private-stage> --target target.json
```

First explicitly [admit](client-admission.md) and
[construct](client-construction.md) the same selected client. Activation never
does those operations implicitly, chooses a different release, obtains
credentials or constructs missing artifacts. The same explicit verification and
construction extras, trusted interpreter, current policy and compatibility
inputs are required for its real local health checks.

The Python API is `activate_client_runtime(..., current_policy=...,
current_target=...)`. Its trusted suppliers must return current inputs, not
saved verification reports. The CLI rereads those files at checkpoints.

## Ownership and publication

Activation reacquires the installation's canonical exclusive ownership and
SQLite transaction, checks the exact durable selection, and pins the completed
candidate. It verifies the external construction input receipt, canonical
completion marker and actual runtime fingerprint. It freshly authenticates the
retained manifest, rechecks API/config/retained-data compatibility, and runs the
candidate's installed console and local diagnostics outside the checkout.
Candidate bytes and current inputs are checked again before publication and
before reporting success. Health failure before publication preserves existing
selectors and the durable admitted floor.

The canonical selector publication lease spans ownership checks, health and
publication. The previous active candidate is retained too. The existing
`versioned-runtime` publisher writes `current-version` and `last-known-good`;
no symlink, junction, launcher, shell profile or external registration is
created. Existing foreign links or selectors without attributable publication
proof refuse rather than being adopted or overwritten.

`client-runtime/client-active.json` binds the initialization identity,
candidate, runtime fingerprint and target digest to one publication attempt.
`client-runtime/client-activations/<id>/` retains its intent, original physical
selector states, backups, canonical context and created-stage producer receipts.
The canonical physical proof includes file identity and content, so a peer's
same-byte replacement is not owned merely because its text matches.
The installed publication helper and its lease dependency are retained from
the same owning wheel's admitted RECORD resources.

An already active candidate still requires fresh policy, compatibility, bytes,
selector ownership and health checks. A successful no-op does not rewrite
selectors, completion or activation records. The report's current target digest
may differ from the digest at the last actual publication.

## Interruption and bounded forward retry

The two selector writes are individually atomic, **not a whole-set atomic
transaction**. Before either write, `client-activation-pending.json` durably
names the exact intended activation and retained evidence. A publication or
acknowledgement failure reports an unknown outcome, not success or an inferred
rollback.

Rerunning the same explicit operation reacquires ownership and accepts only
unchanged original selector objects or objects proved by its complete canonical
producer receipts. It then repeats all current admission, policy, compatibility,
byte and health checks and retries forward to that same candidate and exact
target digest. A target change after intent requires owned repair rather than
silently rebinding a partially published attempt. It never
restores an older release or lowers admission. The history permits at most
sixteen publication attempts. Unreadable/incomplete proofs, foreign objects,
changed intent, an exhausted history or a no-longer-admitted candidate refuse
without further selector mutation. Evidence remains for separately owned repair;
there is no bypass flag or heuristic deletion of uncertain files.

Successful publication is proved before the durable active receipt replaces its
previous value and the pending record is removed. Neither the history nor old
candidates are pruned by activation. A private root is not a security boundary
against a hostile process running as the same principal.

## Reports and remaining lifecycle

`fleet-manager.client-runtime-activation`, version `1`, reports `active: true`
only for verified local selectors. It includes the admitted selection, current
target digest, actual runtime fingerprint, local health, `reused` and
`mutations_performed`. `frontdoor_registered` and `live_readiness_verified`
remain `false`; this report is not a retained lease or authorization token.

[Attributable frontdoor registration](client-frontdoor.md) is a separate explicit
transition; activation does not register or update commands. Exact owned
candidate deletion is a [separate transition](#completed-candidate-removal). Retained-good rollback policy,
identity retirement/reinstall fencing and the complete managed installer remain
separate obligations. In particular, do not delete admission or publication
evidence while a retained candidate or registration could be replayed.
Controller/node assembly, release promotion and authorized rollout are not
performed by this command.

## Owned selector withdrawal

After explicitly unregistering commands, an independently installed owner can
withdraw the installation's runtime selectors:

```text
fleet-manager client-state deactivate-runtime --root <private-installation> --identity identity.json
```

The Python entry point is
`fleet_manager.client_deactivation.deactivate_client_runtime(root, identity)`.
It needs the existing initialized installation and runtime-namespace ownership,
not a selected release, artifact source, current signing policy, compatibility
input or optional dependency. It never executes a candidate. A revoked or
expired release therefore cannot grant new execution through this operation,
but also does not prevent withdrawing an already-owned selector.

Any registered, pending or unsettled-removal command record blocks deactivation.
Use [owned command withdrawal](client-frontdoor.md#owned-withdrawal-and-retry)
first. Deactivation acquires installation ownership, a SQLite transaction and
the canonical selector target-writer lease **once**; both selectors share this
non-reentrant lease. Every present selector must match canonical producer-created
physical proof, including original and new generations in a pending upgrade.
Acknowledged active publications still require complete receipts, even when the
same reference also appears as pending. Only a distinct pending publication may
use its complete newline-framed created-object prefix before a missing finish or
incomplete final record. Those physical records are synced before their selector
renames. They authorize withdrawal of matching objects, not successful activation
or forward retry. The original receipt and unfinished tail remain unchanged.
Same-byte foreign replacements, unattributed selectors, foreign `current` links,
corrupt ownership, invalid complete records or present receipts with invalid
headers refuse before removal. The prepared context is always required. A pending
attempt before its first producer receipt contributes no new physical proof.
Missing already-withdrawn selectors are allowed.

Before the first selector effect, `client-activation-removing.json` durably
snapshots both active and pending activation references. Its
`client-deactivations/<id>/` workspace retains the intent, an **empty-backup**
canonical removal context and removal outcomes. The native recovery primitive
withdraws only the proved selectors; it never restores saved selectors or
executes an older runtime. Original activation history and backups remain.

Activation and command registration refuse while selector removal is unsettled.
After interruption, repeat `deactivate-runtime`: a fresh owner rereads the
retained proofs, tolerates already-absent objects and partially cleared
acknowledgements, and preserves any foreign replacement. Only after both
selectors are absent and directory metadata is synced are active/pending
references and the removal marker cleared. A repeated completed deactivation
checks absence and reports a no-op. No missing ownership is manufactured; a header
alone authorizes no deletion. A present selector still needs a complete earlier
active proof or another valid created record; otherwise only absence can settle.
Activation's execution/reuse and forward-retry paths continue to reject incomplete
producer receipts and do not use this withdrawal-only exception.

The `fleet-manager.client-runtime-deactivation`, version `1`, report includes
`active: false`, `selectors_absent: true`, the unchanged admitted receipt,
`reused` and `mutations_performed`. `pending_activation_withdrawn` explicitly
identifies withdrawal of an unsettled attempt, not its successful activation.
`runtime_removed`, `installation_retired`,
`frontdoor_registered`, `path_modified` and `live_readiness_verified` remain
`false`. All completed/failed runtimes, retained-good bytes, configuration,
user data, admission state and historical proofs remain intact. This does not
stop an already-running client or release its retention custody. An explicit
reactivation still owes fresh admission, policy, compatibility, bytes and health
checks; deactivation is not a rollback authorization or whole-install uninstall.

## Completed-candidate removal

After both command and selector withdrawal have settled, an independently
installed owner can explicitly delete one exact completed client:

```text
fleet-manager client-state remove-runtime --root <private-installation> --identity identity.json --candidate <exact-client-candidate>
```

The Python entry point is
`fleet_manager.client_runtime_removal.remove_client_runtime(root, identity, *, candidate)`.
The candidate is the exact `client-<sequence>-<release-digest>` returned by
construction. There is no root-wide GC, wildcard, guessed candidate, service
stop, forced retention release or identity retirement. Like withdrawal,
removal requires no optional dependency, artifact source, current signing policy
or runtime execution. Expired/revoked releases do not authorize new execution
and do not prevent deleting already-owned bytes.

Installation ownership and a SQLite transaction enclose the canonical
`remove_owned_candidate` cleanup guard: exclusive construction and retention,
selector-publication custody, and current/last-good/dev/live-process protection.
Every selector and active/pending/removing command or activation record must be
absent. A busy or running candidate refuses before a removal intent is created.
The owner's initial check verifies the original external construction input,
both completion records and the complete runtime fingerprint.

Before deletion, `client-runtime-removing.json` and a retained
`client-runtime-removals/<id>/intent.json` record the exact installation,
candidate, completion and physical tree. Files retain identity, mode, size,
mtime and SHA-256; directories retain identity/mode and exact child names.
POSIX links retain their own identity and target, never target traversal.
Windows refuses reparse entries and pins ordinary objects with native
no-share-delete handles. POSIX traversal is descriptor-relative and no-follow;
the root and each member are first claimed by rename into a private,
identity-bound `client-runtime-removals/<id>/claims/` namespace, then validated
and deleted there. An object that wins the source-name race is preserved in
that namespace if it differs from the receipt, not deleted using an older
descriptor's proof. Original-name replacements are also preserved. The
published candidate path can therefore be absent during a partial removal;
the pending marker and retained claims still own the unfinished transition.
The whole remaining source/claimed tree is preflighted before effects.
Completion files are deleted last. New, modified or
same-byte replacement objects refuse, rather than becoming cleanup authority.

While the marker exists, construction, activation and command registration
refuse. Repeat the exact removal after interruption, including owner death:
only already-missing captured objects are tolerated. A partial tree is never
resnapshotted or relabelled as a failed build. Once the exact root is absent,
the owner persists its historical outcome and per-candidate outcome pointer
before clearing the marker. Interrupted outcome writes and acknowledgement
are retryable. A completed repeat checks retained evidence and reports a no-op;
unexplained absence is not success.
Journal writes use one exclusively created, uniquely named temporary per
attempt, followed by fsync, rename and directory sync. Empty, truncated or
complete abandoned temporaries remain evidence and do not block a later
attempt. An unpublished initial marker authorizes no deletion, so a fresh
attempt may validate the untouched completed candidate and create its own
intent without overwriting the abandoned attempt.

The version `1` `fleet-manager.client-runtime-removal` report returns the exact
identity/candidate, `runtime_removed: true`, `reused` and
`mutations_performed`. `active`, `frontdoor_registered`, `installation_retired`,
`path_modified` and `live_readiness_verified` remain `false`. Other runtime and
rollback slots, configuration/data, admission floors, construction inputs and
publication/removal history are preserved. After settlement, explicit
construction can rebuild the same selection only through its ordinary current
policy, compatibility, byte and local-health checks. Under construction custody,
it validates and durably invalidates the prior per-candidate outcome pointer
before creating or cleaning a new slot; historical outcomes remain. Unexplained
absence after reconstruction is therefore a refusal, not an old removal's
no-op. A later removal captures that new physical generation rather than reusing
old deletion authority.
