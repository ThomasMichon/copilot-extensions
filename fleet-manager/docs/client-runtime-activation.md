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
transition; activation does not register or update commands. Removal, retained-good rollback policy,
identity retirement/reinstall fencing and the complete managed installer remain
separate obligations. In particular, do not delete admission or publication
evidence while a retained candidate or registration could be replayed.
Controller/node assembly, release promotion and authorized rollout are not
performed by this command.
