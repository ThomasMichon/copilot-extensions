# RedirectionGuard Adoption

- **Slug:** `redirectionguard-adoption`
- **Repo:** copilot-extensions
- **Branch(es):** independent reviewed slices targeting `dev`
- **Created:** 2026-10-20
- **Status:** Active
- **Vision:** Closes the existing installer vision's trustworthy front door and
  repair behavior; preserves uniform runtime resolution and Windows launch
  hardening rather than introducing another resolver or security exception.
- **Umbrella issue:** #7445
- **Related issues:** #7305, #765, #108

## Guiding Intent

Apply the established RedirectionGuard-safe contracts consistently through
interpreter selection and front-door failure handling. A concrete outer runtime
slot is insufficient if an inner Python base or helper executable still
traverses a blocked reparse point. Preserve the selected runtime's import context,
Windows mitigations, configured dependency sources and seven-day seasoning.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Audit coordinator | Owns the audit, reviewed plan, integration and completion accounting. | Managed source worktree `oiled-blueprint`; #7445. |
| Shared-interpreter implementer | Owns only the accepted shared base-interpreter repair and its canonical fan-out. | Managed source worktree `quiet-bearing`; #7445. |
| Front-door implementer | Owns only the accepted bare-provider diagnostics and failure-result repair. | Separate managed source worktree allocated after plan review; #7445. |

## Coordination

- **Topology:** independent per-slice PRs, each with its own managed worktree.
- **Host (owns PRs):** the coordinator owns the proposal/completion PRs; each
  implementer owns its explicitly assigned implementation PR.
- **Delegates:** evidence readers are read-only. Implementation starts only
  after this proposal has passed review and merged.
- **Handoff:** report exact source heads, changed paths, validation and remaining
  gates to the coordinator; do not commit, rebase or stash in another actor's
  worktree.
- Existing #7420 retains Manager runtime construction, bootstrap and installed
  launchers. Existing #7410 retains updater scalar UV selection and profile
  diagnostics. Existing #7254 retains Windows-command execution from WSL.
  Findings in those scopes go to their current owners, not competing patches.
- The current #7420 head also changes the Worktrees Manager-launch adapter.
  Hunk-level audit found no semantic prerequisite for the diagnostics slice:
  it must not modify probe subprocess construction, selected-slot runtime
  invocation, Manager construction/publication/cancellation, or #7420's tests.
  Preserve the existing probe/command tuple APIs and verify both test groups
  together when #7420 rebases. Resolve any actual context conflict deliberately,
  never by taking a whole file or replacing the existing owner's implementation.
- #7305 retains the broader published-update and installed-consumer recovery
  obligation. This effort does not take over that objective or the entire
  Worktree Manager control-plane effort.

## Context

The canonical [uniform runtime resolution pattern](../../../docs/patterns/uniform-runtime-resolution.md)
already requires marker-selected concrete runtimes, with non-traversing legacy
link handling. The [transport-provider contract](../../../plugins/agent-ssh/docs/transport-provider-contract.md)
also documents real Windows helper executables instead of WinGet-link reliance.
Historical #765 and #108 record the earlier repairs.

Current evidence distinguishes two layers: top-level marker resolution is
already correct, while shared base-interpreter conversion can still inspect
`pyvenv.cfg home` through a junction. Separately, a failed Manager health probe
can lose its actionable error and produce a success-shaped bare-launch result.

Windows child-process measurements distinguish an enforced RedirectionTrust
context from ordinary execution: the same real binaries are healthy while
their aliases are inaccessible. This is not evidence that disabling mitigation
or reinstalling those binaries is an appropriate repair.

## Request

> Good. Let's audit current implementations, and drive fixes

The operator confirmed the effort slug `redirectionguard-adoption`.

The following decomposition and validation details are
**agent-recommended implementation planning**, not additional operator requests.
The work is limited to source repairs and their Windows qualification; it does
not add Linux/WSL deployment work or authorize persistent security changes.

## Plan

### Phase 1 - Audit and reviewed scope

- [x] Finish the bounded current-code audit of canonical interpreter handling,
  Manager's pending repair and bare-front-door behavior; retain only
  evidence-backed findings.
- [x] Record overlap boundaries with existing owners and land this proposal
  before implementation.

### Phase 2 - Shared base-interpreter adoption

- [ ] Repair the uncovered shared interpreter path using non-traversing link
  target handling consistent with the existing contract. Preserve executable
  identity, console/windowless selection and venv import context; fail
  explicitly on unusable targets rather than selecting an unrelated Python.
- [ ] Add focused regression coverage and synchronize canonical library copies
  through the existing tool, with the required release metadata.

### Phase 3 - Trustworthy failed front doors

- [ ] Preserve a bounded, sanitized failed-provider probe cause; avoid an
  unsupported stale-binstub diagnosis or a fallback that is not available.
- [ ] Distinguish first-run onboarding, intentional headless use and an
  installed-provider launch failure. Return a failure for the latter without
  changing unrelated successful launch behavior. Own only provider-health
  interpretation, factual diagnostics and bare-dispatch result handling;
  preserve the probe loop and runtime invocation owned by #7420.

### Phase 4 - Integration and completion

- [ ] Land the independently reviewed repairs, reconcile their exact merged
  heads and update the relevant contract documentation.
- [ ] Record Main publication separately from Dev merge. Transfer any remaining
  published-runtime adoption/recovery to its existing named objective #7305,
  without calling a source-only result deployed or fully recovered.

## Validation Plan

- [ ] Run focused existing unit/contract selections for each changed helper and
  caller, including blocked link traversal and preserved venv context.
- [x] Exercise the real interpreter helper in an inherited-RedirectionGuard
  Windows context, using a healthy real binary behind an inaccessible alias.
  Confirm the mitigation remains enabled and test two repeated invocations.
- [ ] Exercise a real failed child provider through the bare-front-door
  consumer, checking sanitized output, available fallback and exact exit status.
- [ ] Run applicable canonical-copy, lint and install-contract guards through
  the repository's bounded test tooling. Record unavailable native/clean-room
  lanes explicitly rather than claiming they passed.
- [ ] Re-read this effort after merges; resolve every item or transfer it to an
  explicitly named tracked objective before marking the effort Done.

## Proposal

The proposal passed automated review and merged in #7447. The bounded source
implementation may proceed; production rollout remains separately qualified.
The hunk-level independent front-door sequencing amendment requires its own
review before that slice starts.

## Journal

### 2026-10-20 - Hunk-level sequencing correction

- The initial same-file hold was more restrictive than the actual dependency.
  A read-only comparison against #7420's exact current head found its probe
  spawning and concrete-runtime hunks separate from health interpretation and
  bare-dispatch results. Shared file ownership alone is not a semantic gate.
- Propose independent diagnostics-only implementation while preserving #7420's
  substantive hunks and the public tuple contracts. Do not refactor the probe
  loop merely to distinguish its currently collapsed spawn/timeout result.
- This amendment passes review before the front-door implementation begins.
  Existing runtime ownership and the published-consumer gate remain unchanged.

### 2026-10-20 - Enforced consumer qualification

- A path-only candidate failed the real contextualized consumer: the physical
  base interpreter ran alone, but CPython reread the blocked configuration home
  when preserving the selected venv and failed during path initialization.
  Extended-path spelling was not the cause. Publication was stopped.
- The revised helper explicitly refuses an inaccessible configuration home with
  a reconstruction diagnostic. It does not rewrite installed configuration,
  drop venv identity or override Python startup environment.
- Qualified that refusal twice through all three selection APIs in an actual
  enforcement-enabled SSH context. A separate disposable physical-home venv
  then ran the candidate's console and GUI selections twice each, preserving
  its prefix and importing a selected-environment-only module. Native policy
  flags remained enabled throughout; private test scratch was removed.
- Existing legacy-runtime reconstruction still belongs to #7420/#7305.
  Qualified source-helper behavior is not a recovered installed Manager.
- #7410 has merged; its existing owner retains published-consumer qualification.
  #7420 remains under its own review/fix loop. The subsequent hunk-level
  sequencing amendment separates the independent diagnostics work.

### 2026-10-20 - Proposal reviewed

- #7447 merged after exact-head approval with no findings and green required
  checks. Synced the coordinator onto the reviewed Dev plan.
- Allocated `quiet-bearing` for the shared-interpreter repair. Its ownership
  excludes the WSL-command feature in #7254 and Manager construction in #7420.
- The front-door repair waits for #7420's adapter changes to land or for its
  owner to incorporate the bounded fix. Existing ownership was notified.

### 2026-10-20 - Audit intake

- Captured the operator's request and confirmed slug. Three bounded evidence
  tracks cover the pending Manager repair, canonical interpreter conversion and
  failed front doors. No deployed files or machine security settings changed.
- The Manager audit initially misread an incomplete patch; its correction
  confirms #7420 already replaces installed UV-based launchers. That change
  remains with its existing owner.
- Completed the three-track source inventory. Top-level marker resolution is
  already correct; the shared inner-base selection and failed-provider state
  remain gaps. The latest Manager repair also touches the front-door adapter,
  so that slice is sequenced behind or integrated with its existing owner.
