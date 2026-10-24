# Read-only source-review inner loop

Back to the [canonical effort](README.md). Coordination: #8678.

## Scope and standing intent

Run a decoupled source candidate of the production Picker against the existing
owners' real data. The operator selected read-only review, not disposable
configuration edits. Repeated UI review and capture must not require promotion.
This realizes the [Picker vision](../../../visions/picker/README.md)'s
programmatic-parity and auditable-testable-rendering intent.

The implementation details below are **agent-recommended**. Review this
amendment before implementation. Installed Configuration/Workers acceptance
and the parent effort's remaining completion gates stay open.

## Implementation plan

- [ ] Add an explicit source-review entry using the production Picker and the
  existing installed owner read contracts; do not import provider domain
  runtimes, fake the live data, or replace production launchers.
- [ ] Display candidate/source identity and read-only mode. Keep candidate UI
  state disposable, without redirecting the owner registries to empty homes.
- [ ] Refuse mutation/launch commands at execution boundaries, including
  contributed actions, forms, draft writes and streaming actions. Preserve
  normal Picker behavior outside this opt-in process.
- [ ] Skip repair, provisioning, automatic updates/check-cache writes,
  maintenance, resident registration and service ownership. Missing owners
  remain visibly unavailable; do not start or repair them.
- [ ] Provide repeatable source launch and production-render capture
  instructions, including local-only review and cleanup.

## Validation plan

- [ ] Focused tests cover read admission, mutation rejection before process
  execution, form/stream bypass attempts, disposable state, lifecycle
  suppression, candidate labeling and unchanged ordinary behavior.
- [ ] Exercise the actual source entry and native production UI against real
  owner data. Record the candidate revision, selected read sources, capture
  result and unavailable sources without publishing private values.
- [ ] Verify installed selectors, owner configuration and service ownership
  do not change through launch, refresh, attempted action and exit.
- [ ] Run the existing affected Manager test lane, touched-file lint and
  repository contracts; document unavailable platform or external lanes.
- [ ] Land the reviewed implementation and return to the parent effort's
  installed acceptance and completion gates.

This is a trusted developer review mode, not a sandbox for hostile candidate
code or arbitrary contributed commands. Reads use existing provider-owned
contracts; no second daemon, collector, selector or configuration authority is
introduced.
