# Legacy first-use foreground receiving contract

[Canonical effort](README.md) |
[Operator selection](inception-transcript.md#legacy-first-use-foreground-mapping) |
[Lifetime amendment](update-service-lifetimes.md) |
[Execution owner](execution-driver.md)

**State:** Reviewed receiving contract; the opt-in Python-only source candidate
has local contained validation. Whole-chain implementation and qualification
remain pending.
**Coordination:** #7758 under #7028; prospective caller/test migration in #9026.

**Receiving implementation slice:** the source now contains an additive,
opt-in Python provisioning receiver with the revision-1 identity below and
explicit `python-provision-only` scope. Request-local admission commits its
owned `running` state atomically rather than exposing replayable pending work.
This is not native construction support or activation of legacy `-Wait`.
The delivery and validation items below retain their whole-chain meaning;
none is closed by this narrower source slice.

The candidate was integrated with Dev source
`9467a6db8016970aa880eba48e48ed2f9d5352ec` and its unchanged original
22-control selection passed on Windows then Linux, serially, through the
canonical contained runner and original default limits. This proves the
bounded Python receiving/parser/store/process controls at that source, not
native foundation construction, installed-peer rollback, legacy `-Wait`
activation or accepted-service independence. Historical timeout causes remain
unknown and are not waived by later passes.

## Goal and scope

Map the current legacy fixed `provision -Wait` first-use route to the ordinary
foreground contract selected by the operator. Finite foundation construction
and provisioning follow the caller. Report interruption, uncertainty and
recovery honestly; an absent ordinary receipt never becomes successful
completion.

Explicit background execution and independently accepted service setup/start
remain separate lifetime contracts. Internal `Mode=launch/owner`, `ServiceMode`,
automatic/manual classification, a created PID and a running finite-bootstrap
record do not select background execution or establish service acceptance.

This specializes the parent's open P4bD and lifetime L1-L6 obligations. It is
not another installer, queue, bootstrap engine or completion objective. It does
not complete the Manager reference, selected-source footprint bootstrap, app
conversion or the parent's remaining installation and validation rows.

## Why the receiving contract comes first

The historical #9018 contract expected caller-independent accepted work and
the original ordinary receipt. The current lifetime amendment supersedes the
ordinary-background-worker-only model, without withdrawing historical credit.
Changing an unmerged caller/test to the selected foreground semantics therefore
requires an explicit prospective contract revision, not a weaker assertion.

The inspected first-use path has no execution-mode declaration. Its native
frontends detach finite first-use/foundation work, and its Python receiver
enqueues, starts a detached worker and observes. The launch acknowledgement is
explicitly non-durable; the ordinary receipt appears after enqueue, following
foundation construction. A finite foundation operation under execution-lock
custody is not an accepted Dispatch service-start operation.

The exposed caller-loss failure occurs after a birth-verified first-use owner
has exited, while its ordinary receipt log contains no complete receipt. That
parser outcome precedes DB/count/history checks; it does not prove a missing or
corrupt database. Returning `False` after terminal owner exit or dropping the
receipt/history invariant would hide the failure.

The current Dispatch job enters ordinary containment, not the canonical
native-lifetime admission owner. Its eligibility environment selector does not
grant escape. This plan neither clears containment nor changes caller Jobs to
make that diagnostic green.

## Receiving proposal (agent-recommended)

### One caller-owned finite chain

Use the existing native first-use frontends, canonical foundation production,
trusted request admission, `RequestSpec`/`Runner` construction and participant
installer path. Apply foreground ownership across the whole finite chain, not
only the final Python observer. Preserve headlessness, output/error propagation,
source and installation identity, and supported Windows/POSIX behavior.

The current request-local `run_explicit` primitive is a candidate building
block, not a sufficient implementation by itself. Enqueue commits `pending`
before the foreground drain starts it. Hard termination in that interval must
not leave foreground work eligible for later generic background execution.

The receiving owner must retain enough original operation and process custody
to report interruption or uncertainty and perform explicit, identity-bound
recovery. Reuse existing request/history and kernel-identity machinery; this
proposal does not prescribe a new mode column, queue or reconciliation service.
Never infer that no commit occurred from an empty stdout receipt. Preserve
original identities and uncertain effects rather than replaying a fresh request.

Lifetime must participate in compatibility/coalescing identity. Foreground
cancellation cannot confer ownership over a peer request, silently join a
background lifetime, drain unrelated pending work or terminate another owner.
Do not treat an interim busy outcome as installation success. The normal
explicit update path still owes bounded join/continuation or replacement
through its existing owner; stale records and old-runtime recovery cannot
permanently veto the incoming installation.

### Expand the receiver before migrating callers

Proposed semantic identity: `copilot-extensions.first-use-execution`, revision
`1`, with `foreground-finite-v1` support. This names required behavior, not an
implemented declaration or package-version requirement.

Advertise support through the owning selected foundation/runtime generation's
existing suitable immutable descriptor or operation-discovery seam, expanding
that seam provider-first where necessary. Resolve the generation that will
actually execute, not a newer payload or a mutable package stamp. Reuse a
generation's result; add no universal handshake or per-call discovery process.

The current `-Wait` frontend requests foreground semantics only after support
can be established. Keep CLI parsing strict. Preserve supported legacy callers'
documented baseline during expansion, with an explicit peer/deprecation and
provider-rollback window. An unversioned legacy baseline cannot establish the
new foreground guarantee merely by accepting an ignored field.

This choice does not select implicit background compatibility for the modern
`-Wait` route. If a selected peer cannot enforce foreground semantics, use an
explicitly equivalent supported path or report the actual contract gap before
effects. Keep the normal provider-first incoming upgrade path available without
requiring a healthy old runtime; do not turn incumbent provenance or cleanup
into an installation veto. Never effectfully probe new syntax and blindly
replay an older form.

### Keep accepted service work independently owned

Before service-owner acceptance, foreground cancellation stops finite work and
unaccepted candidates retain owned cleanup. At acceptance, the existing service
lifecycle owner retains startup/outcome, process-tree and immutable-runtime
custody. Cancelling its observer cannot kill accepted service work, roll it
back or authorize overlapping writes. Unaccepted remaining services do not
start after cancellation.

No background/service acceptance may be manufactured from the non-durable
launch acknowledgement, fixture preparation labels or a finite construction
record. Unsupported caller-Job/cgroup independence remains explicit. The
separate resident Python WMI proof is not qualification of this Python-free
finite path.

## Delivery plan (agent-recommended)

- [x] Review and merge this receiving contract and operator capture before
  runtime realization; #13605 preserves the complete parent Goal and original
  bounds. No technical validation is closed by that review.
- [x] Reconcile the finite receiving writer through #7758's existing execution
  coordination ([scope](https://github.com/ThomasMichon/copilot-extensions/issues/7758#issuecomment-7030981006)).
  Shared process/native and compiler-free changes retain the
  [leased-instance](../leased-daemon-instances/README.md) and
  [lean-runtime](../lean-runtime-dependencies/README.md) owners. A generic role
  table, closed tracking issue or absent PR search is not a custody transfer.
- [ ] Deliver receiving foreground support, including admission/start crash
  handling and truthful outcome/recovery, through existing owners. No healthy
  incumbent or second bootstrap engine becomes a prerequisite.
- [ ] Qualify the receiver through the actual finite first-use entrypoints,
  then migrate dependent native fronts and callers conditionally. Inventory
  Bridge and Vault runtime-gate consumers rather than assuming Dispatch is the
  sole consumer or deploying the suite in lockstep.
- [ ] Update #9026's caller-loss expectation prospectively: foreground finite
  work stops and its original outcome/recovery remains honest. Preserve real
  ordinary receipt/history/runtime-consumer checks for successful completion;
  do not call a missing receipt a completed request.
- [ ] Record source/release and separately authorized adoption at their actual
  scope. Native-lifetime admission and the complete parent L1-L6/Plan/Validation
  gates remain open until their own outcomes are proved.

## Validation plan (agent-recommended)

- [ ] Through actual parsers/process seams, complete a foreground cold
  foundation and fixed provisioning operation without detaching finite work;
  retain ordinary receipt, exit/result, source, queue history and real consumer
  assertions.
- [ ] Exercise Ctrl+C, caller death and caller-owned tree/Job/cgroup cleanup
  before durable admission, during construction, after admission and during
  finite execution. Owned finite work stops; interruption/uncertainty is
  truthful and no unrelated owner is terminated.
- [ ] Kill in the enqueue-commit/start interval. A later generic drain cannot
  execute abandoned foreground work. Explicit recovery uses original custody
  and identities, preserves history and does not replay uncertain effects.
- [ ] Cover busy and compatible/incompatible coalescing paths: no peer draining,
  cross-lifetime joining or observer cancellation of another operation. Prove
  the normal explicit entry's bounded continuation/replacement path without a
  permanent stale-record or incumbent-recovery veto.
- [ ] Repeat before and after independently accepted service setup/start.
  Accepted work and retained runtime/process trees survive caller cleanup;
  bounded controls stop. Do not infer independence from a PID, immediate-parent
  exit or a blanket any-Job predicate.
- [ ] Cover new/new, supported old/new, new/old, missing/invalid declarations
  and supported provider rollback. No silent downgrade, ignored required
  semantics, unknown-argument tolerance or effectful probe/replay is permitted.
- [ ] Use existing bounded unit/contained consumer lanes and relevant clean-room
  first-touch coverage. Exercise actual supported Windows and POSIX contracts;
  name macOS or its concrete remaining limitation rather than treating Linux
  as proof for every POSIX platform.
- [ ] Select the existing canonical native-lifetime owner for any independence
  claim, including its disposable-host, private-scratch, lease, birth-pin and
  cleanup requirements. Extend an existing adapter only through its owner and
  reviewed contract; an eligibility flag or ordinary contained pass is not
  native admission. Existing operational holds and separate target approval
  remain binding.

## Completion boundary

A merged proposal or passing contained diagnostic does not implement this
contract, qualify service lifetime, release another owner's resources or
complete P4bD. This slice advances L1 while preserving the separately required
L2-L6, incoming readiness/complete selection, state/session preservation,
kill-by-default replacement, containing release and authorized adoption
obligations. No new paid venue, daemon, runtime rollout, WMI fallback,
containment relaxation or Job weakening is authorized here.
