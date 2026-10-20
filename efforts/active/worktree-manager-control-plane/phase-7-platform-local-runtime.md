# Phase 7 - Platform-local installed runtime

- **Parent:** [Worktree Manager control plane](README.md#phase-7a---platform-local-installed-runtime-7305)
- **Status:** Proposed; implementation starts after plan review and merge.
- **Coordination:** [#7305](https://github.com/ThomasMichon/copilot-extensions/issues/7305)
- **Participant / PR owner:** Platform runtime implementer (`spry-workbench`).

## Request and guiding intent

**Sanitized summary of operator authorization:** repair the Manager's versioned
installer/runtime through the normal source, review, release and authorized
local-update flow. Preserve the configured package feed and seven-day dependency
policy. Other update-flow and sibling-runtime repairs retain their current
owners. No deployed-file patch, package-source substitution, seasoning waiver,
arbitrary service restart or fleet rollout is authorized.

The implementation sequence and validation matrix below are
**agent-recommended** realizations of that approved boundary, not additional
operator requests. This plan closes the existing installer vision's
`one-line-bootstrap`, `self-updating` and out-of-plugin delivery intent; it does
not introduce a new deployment/source policy.

## Established problem and scope

The generated Manager launchers execute `uv run --project <slot>`. That performs
a universal project solve at invocation time, including Linux-only requirements
on Windows. A configured index can legitimately reject one of those artifacts
under its enforced cutoff; changing a dependency floor or passing a native
platform flag did not qualify the actual consumer in #7305.

`self_install_payload` currently materializes first-party libraries and copies
source, but excludes the source `.venv` and publishes `.install-complete`
without constructing an importable installed environment. A copied payload or
version marker is therefore not installed-runtime health.

Own Manager bootstrap, payload/runtime construction, completeness checks and
generated launchers, plus directly related tests and documentation. Reuse the
existing stdlib installer dependency policy and attributable first-party
materialization mechanisms where applicable; ship any required policy bytes
with the standalone Manager, never import an installed sibling plugin.

Do not modify Linux dependency pins, source-selection policy, the initializer's
exit/error propagation, Terminal profile error presentation, daemon ownership,
restart recovery, Bridge transport or Index construction. Those remain separate
scopes. A newly discovered shared-file conflict requires coordination before
implementation; public-file absence is not proof of unpublished custody.

## Plan

### 1. Review and coordinate

- [ ] Land this proposal through the normal automated review/CI/merge gate.
- [ ] Recheck #7305 coordination and any overlapping PRs before implementation;
      preserve its initializer/profile-diagnostic owner. Bind only this declared
      Phase 7a slice, never the entire umbrella.

### 2. Construct a complete platform-local runtime

- [ ] Reuse the supported `uv venv` plus non-editable `uv pip install`
      construction path, resolving only for the selected interpreter/platform.
      Install the Manager and materialized first-party libraries into the
      candidate environment, without editable references to fetched or temporary
      source. Preserve native configured indexes, authentication, TLS, stronger
      cutoffs and the canonical retained-input admission/execution boundary.
      Do not invent an independent dependency-policy implementation.
- [ ] Keep source/pointer/symlink checks and existing cutover lease/recheck
      semantics. Do not overwrite a healthy active or currently executing
      environment to repair metadata. Construct a new candidate before selection;
      failed construction leaves the prior marker, slot and live sessions usable.
- [ ] Validate with the candidate's own interpreter: actual module import,
      expected version and bounded CLI health. Account for environment relocation:
      probe at its final runtime path before publishing the active marker, and
      retain rollback through a failed final-path probe. No temporary build path
      may remain necessary for ordinary invocation.
- [ ] Publish completion only after successful runtime qualification, then use
      the existing activation/cutover path. Same-version readiness must detect a
      source-only legacy slot or a missing/broken environment and report a
      truthful repair/refusal, not claim success from an old completion marker.

### 3. Align launch and first touch

- [ ] Generate Windows `.cmd`/PowerShell and POSIX launchers that resolve the
      selected version and execute that slot's own interpreter. Ordinary version,
      profile and CLI calls perform no dependency solve and require no `uv` on
      PATH. Preserve argument boundaries, exit codes and UTF-8 protocol stdout;
      missing runtime errors remain actionable on stderr.
- [ ] Make both bootstrap scripts enter the same platform-local construction
      contract before importing runtime-dependent Manager modules. The first
      touch must work without an existing Manager, its venv, plugin runtime or
      monorepo checkout, and then invoke the installed command rather than
      re-solving the fetched source. Propagate construction failure without
      launching a half-installed Manager.
- [ ] Update the Manager installation/repair documentation and directly affected
      install-contract descriptions. Add the required Manager changefile; leave
      release version stamping to promotion.

### 4. Qualify and deliver

- [ ] Complete the validation matrix, record exact results and review the final
      documentation impact before implementation publication.
- [ ] Address review feedback, merge with required checks, and observe actual
      promotion of the changed bytes. A Dev merge is not a released runtime.
- [ ] Run the already-authorized local unified update after release, not a
      standalone production installer. Prove the selected Manager version and
      real profile JSON consumer. The complete-update exit-0 gate remains
      jointly dependent on the retained #7305 diagnostic owner and sibling
      blockers; do not relabel partial rollout as success.
- [ ] Journal delivery and resolve this slice, transferring any concrete
      remaining gate to its named tracked owner. Leave the parent effort Active
      while its other phases remain open; release/finalize this source workspace
      only through the managed lifecycle.

## Validation plan

- [ ] Focused unit/contracts: actual platform dependency markers and non-editable
      first-party installs; supported dependency-policy invocation and rejected
      admission; missing interpreter, failed install/import/version/CLI health;
      legacy source-only slots, idempotence, metadata-only repair, lease
      contention, concurrent installers and failed-publication rollback.
- [ ] Simple end-to-end: construct a disposable standalone materialized slot,
      execute its generated native launcher with paths/arguments containing
      spaces, and read actual version/profile JSON and exit status. Remove the
      fetched source and remove `uv` from the invocation PATH; assert launch
      still works without a new solve. Verify final-path execution after
      candidate relocation, not just pre-swap imports.
- [ ] Dependency-policy negatives: an ineligible dependency fails construction
      without weaker retries, source/index changes, activation or damage to the
      previous slot. Prove Windows construction does not request Linux-only
      D-Bus dependencies; Linux retains the declared requirement.
- [ ] Fresh-box: extend/run the existing Manager bootstrap clean-room scenario
      where practical. Exercise both shell adapters, including native Windows
      PowerShell 5.1 and 7 failure/argument behavior. Record unavailable lanes
      and reasons explicitly; a warm reconcile is not first-touch evidence.
- [ ] Run the smallest repository-standard contained Manager suites covering
      these changes, touched-Python lint and relevant install-contract/vendor
      guards. Escalate only on discovered failures; retain all existing budgets
      and process containment. Native launch changes owe zero-window/focus
      regression evidence rather than a mock of process creation.
- [ ] Published consumer: after promotion and the authorized local unified
      update, verify selected-version/importability, actual launcher output and
      Terminal profile JSON, the update's final exit status, and preservation of
      the original live CLI. No GUI/mux activation or remote-machine mutation
      is needed merely to qualify this bounded headless runtime contract.

## Journal

- **2026-10-20** - Proposed this bounded source repair after public coordination
  on #7305. No code or installed-runtime change has been made. Review of this
  proposal is the next gate; other participants retain their existing work.
