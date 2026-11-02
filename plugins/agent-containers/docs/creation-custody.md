# Original provider creation prerequisites

Back to the [Containers guide](../README.md). These are **internal storage and
selected-slot retention prerequisites**, not a supported retained provider
launch, CLI operation or native qualification. Both dependency profiles still declare
`retainedCreation: false`.

## Existing owner, explicit new birth

[`dispatch_creation`](../src/agent_containers/dispatch_creation.py) retains the
provider's part of one original Bridge operation on the existing
`dispatch-bindings/<physical-id>.json` record. It adds no parallel ledger,
scheduler, credential store or task history.

Ordinary `register_birth` and the existing allocation/CLI paths still write
binding version `1`. Only an explicit internal
`register_birth(..., creation_custody=True)` creates version `2`, through the
same exclusive publication owner. It cannot replace an existing birth.
No current allocator or CLI selects that option. Existing version-1 bindings,
even apparently unused ones, cannot be retrospectively upgraded: missing
historical custody is not confirmed absence of a prior launch.

Version `2` retains the ordinary physical/birth/task fields and requires
`creation`, `worker_admission_generation` and `attempted_expires_at`.
Its ordinary worker generation must remain zero, with no ordinary native
owner or credential publication. `creation` starts as `null`; a retained record
has exactly `association`, boolean `closed` and nullable `started_at`.
Duplicate JSON keys, missing fields, conflicting ordinary history or invalid
associations refuse, rather than appearing empty.

The exact association retains the original Bridge `admission`
(`installation`, positive integer `epoch`, `producer`), `handoff_id`,
`key_digest`, `facts_digest`, `session_id`, `consumer_incarnation`, and the
provider `runtime` observation (`root`, immutable `version`, `payload_hash`,
`incarnation`). The enclosing record already owns the physical container,
birth and task identity. It does not copy a credential, full creation request
or native history.

## Storage operations and limits

All mutations serialize under the provider's existing lifecycle/admission
kernel lock. A first `retain` rechecks the exact restricted dispatch-only
physical birth and refuses a live lifecycle hold or session admission.
Replaying the same association returns its existing observations without
reacquiring effects; replacing any original owner or handoff refuses.

`begin` records one outstanding attempt and returns `true` only once.
A repeated observation returns `false`, never another launch grant.
`close` refuses future begins without removing an already outstanding attempt.
Original reads and identical retention replay remain available after closure
and process exit. Wrapper-admission cleanup and TTL/liveness expiry cannot
remove this record. There is no automatic clear, cancellation, confirmed-absence
or settlement operation.

Version-2 births cannot use ordinary task verification, worker admission,
credential projection or spawn construction. Guest liveness/closure reports
unknown rather than allowing destructive lifecycle work to treat this
unsupported cohort as idle. Older readers reject version `2`; rollback must
preserve it as unsupported, never strip its fields and retry an ordinary form.
Existing version-1 callers and allocation defaults retain their behavior.

These are durable **observations**, not proof of current Bridge authority,
execution under the recorded slot, continuing slot retention, final provider
admission, guest-held responsibility or native acceptance. No provider or guest
is launched by these APIs. Actual selected-execution/retention integration,
guest/native effect fences, strict complete-cohort inventory and same-operation
outcome reconciliation remain required before declaring retained support.
An outstanding attempt stays unknown until its actual owner can reconcile it;
it cannot be replayed as a fresh native conversation.

## Exact execution retention prerequisite

The payload-owned `scripts/runtime_profile.py` exposes internal
`select_execution(root)` and `retain_execution(root, selection)` primitives.
Selection reads the immutable slot's declaration; retention names that same
slot rather than resolving the mutable selector again. It acquires canonical
[`versioned-runtime` retention custody](../../../libs/versioned-runtime/README.md#candidate-and-rollback-retention-python-api),
rechecks the completed payload hash and rereads the exact declaration under
the lease. Missing, legacy, changed or conflicting evidence refuses without
provisioning, activation or fallback.

The context yields the unchanged selection and a **borrowed** kernel descriptor.
Callers must not close it. Ordinary return and errors release the context's
descriptor; a mere saved integer or file path is not continuing custody.
Deliberately inherited POSIX descriptors keep the same kernel protection until
their final close, including after the original context exits. Windows retention
remains process-owned; this API does not promise inherited child protection
there. No lease file is deleted and no second pin registry is introduced.

The [canonical resource builder](../../../libs/versioned-runtime/standalone_build.py)
packages the synchronized helper family and
the unchanged profile owner into each wheel and independently buildable sdist.
An installed provider therefore uses its own immutable resources, not an
ambient installer-script import. The build helper, profile and build controls
participate in the source fingerprint; promotion verifies the registered build
hook and each injected wheel member against those inputs.

Internal `agent_containers.execution_runtime.retain_current_execution` reads
this process's `sys.executable` slot, requires the expected version and payload
hash and a package inside that slot, then enters the same retention context.
Containment uses resolved resource origins, including the initializer, context
and helper family; a file or package-directory link to mutable code outside
the slot refuses before importing the profile owner.
It does not consult `current-version`, provision, activate or invoke a mutable
payload shim. Missing packaged resources or conflicting/legacy slot evidence
refuse; an editable source import is not an immutable executing runtime.

No current Bridge, provider CLI or transport launch path calls these contexts.
They do not transfer responsibility to a wrapper, guest or native host, and do
not change either profile's `retainedCreation: false` declaration. Actual
launch integration and continuing responsibility through reconciliation remain
open.
