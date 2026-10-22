# Client release admission

`fleet-manager client-state initialize|inspect|admit` records durable admission of an explicitly selected,
authenticated client release. It does not construct, execute, activate, update
or uninstall a runtime. A successful admission receipt is not an execution
grant, a Gateway credential or evidence of fleet readiness.

## Explicit inputs and commands

The caller supplies an existing private installation directory and a trusted
initialization identity:

| Identity field | Meaning |
|----------------|---------|
| `schema`, `version` | `fleet-manager.client-installation`, `1` |
| `authority`, `installation` | Exact release authority and local installation identity |
| `role` | Exactly `client`; hosting roles refuse |
| `initialized_by`, `receipt` | Attributable initialization owner and receipt identity |
| `selection_owner` | Trusted owner of this installation's release-selection decision |

```text
fleet-manager client-state initialize --root <private-root> --identity identity.json
fleet-manager client-state inspect --root <private-root> --identity identity.json
fleet-manager client-state admit --root <private-root> --identity identity.json --selection selection.json --release release.json --proof proof.json --policy policy.json --sources sources.json --staging-parent <separate-private-stage>
```

Initialization refuses a nonempty foreign directory, mismatched identity or
corrupt/unsupported existing state. It never repairs permissions or resets an
admitted sequence. Inspection does not create missing state, acquire a
construction lease, stage artifacts or obtain credentials.
The initial SQLite transaction is committed in an identity-qualified private
temporary directory before atomic publication at the final database path.
Interrupted temporary initialization can be retried under the same construction
lease; only its recognized ordinary database/journal files may be discarded.
Unexpected temporary entries or a foreign final database remain refused and
preserved.
On POSIX, the temporary directory is retained until the final rename's parent
directory has been synced. A retry after an interrupted publication completes
that durability barrier before removing the temporary directory or succeeding.
The construction lock file, its containing lease directory and the installation
directory are synced before initial database publication, so the durable
database cannot be acknowledged ahead of its required ownership metadata.

The selected-release input reuses `ReleaseSelection` with schema
`fleet-manager.release-selection`, version `1`. It binds `decision`, positive
integer `revision`, `owner`, `authority`, `installation` and
`role` to the exact manifest's `release_digest`, positive `sequence` and sorted,
unique `components` client closure. Signing validity or a higher sequence does
not supply a missing selection decision. The sources and authentication inputs
use the existing release-authentication and retained-artifact-stage contracts.
The CLI requires a staging parent separate from the installation root.

## Durable state and ownership

The installation contains `client-admission.sqlite3`, SQLite application ID
`0x464D4349`, schema version `1`. Its exact schema contains one installation
identity row and at most one admission row. The latter records the highest
accepted sequence, manifest digest, selected decision and decision digest, plus
the original admission time. It is local lifecycle state, not the fleet
operation ledger or node journal.

Initialization/admission reuse the canonical versioned-runtime construction
lease named `client-admission`, with persistent ownership metadata under
`.construction-leases`. Deleting that lock file is not a recovery procedure:
an existing database without its expected lock refuses further admission.
Read-only inspection remains a snapshot, not proof of exclusive ownership or
permission to perform later effects.
Unsupported WAL format is rejected from the database header before opening
SQLite, so inspection cannot create WAL sidecars. POSIX header reads use a
bounded, isolated standard-library helper process: closing an independent
database descriptor in the inspecting process could release another live
SQLite connection's process-wide `fcntl` locks. SQLite owns all database
descriptors in the calling process; the helper does not execute candidate code.

Admission holds exclusive ownership and a full-synchronous `BEGIN IMMEDIATE`
transaction while freshly verifying current policy, signature lifetime,
selection and retained artifact bytes. A lower sequence refuses. Different
manifest or selection content at the same sequence refuses. An identical retry
is idempotent only after fresh verification; revoked or expired authorization
still refuses. A reported uncertain commit must be reconciled by a fresh owned
retry/reread, never by reducing the floor or inventing a completed receipt.
Explicit admission preflight can recover a hot SQLite rollback journal under
the construction lease before staging inputs. Ordinary inspection remains
read-only and does not recover or rewrite state.

Roots and state/ownership files must be ordinary and private; files must also
be singly linked.
Windows validates owner/DACL grants on the root and state files, including
inheritance-only grants that could expose subsequently created state. POSIX
requires current-user ownership and no group/other permission bits. These
checks do not isolate state from a hostile process running as the same user,
an administrator or another principal with overriding system privileges.

## Remaining lifecycle boundary

Reports explicitly retain `constructed`, `healthy`, `active`,
`activation_authorized` and `live_readiness_verified` as `false`. No selector,
runtime slot, service registration, hosting role or network connection is
created by admission. The separate, explicit
[`client-state construct`](client-construction.md) transition consumes this
durable admission; its constructed/healthy result is not an admission report or
an activation grant. Activation, retained-good recovery, owned uninstall and
installation identity retirement remain separate transitions under the reviewed
client lifecycle plan.

The agent-free `fleet-manager-client-admission` clean-room scenario exercises
the actual freshly installed console in a disposable base image. It requires
both installed-consumer and missing-resource checks to run without skips; a
dependency-feed failure is a classified jam, never substituted with a unit pass.
