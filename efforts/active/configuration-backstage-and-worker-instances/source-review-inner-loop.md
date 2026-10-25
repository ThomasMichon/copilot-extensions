# Read-only source-review inner loop

Back to the [canonical effort](README.md). Coordination: #8678.

## Scope and standing intent

Run a decoupled source candidate of the production Picker against the existing
owners' real data. The operator selected read-only review, not disposable
configuration edits. Repeated UI review and capture must not require promotion.
This realizes the [Picker vision](../../../visions/picker/README.md)'s
programmatic-parity and auditable-testable-rendering intent.

The implementation details below are **agent-recommended**, reviewed in #8686.
Installed Configuration/Workers acceptance
and the parent effort's remaining completion gates stay open.

## Implementation plan

- [x] Add an explicit source-review entry using the production Picker and the
  existing installed owner read contracts; do not import provider domain
  runtimes, fake the live data, or replace production launchers.
- [x] Display candidate/source identity and read-only mode. Keep candidate UI
  state disposable, without redirecting the owner registries to empty homes.
- [x] Refuse mutation/launch commands at execution boundaries, including
  contributed actions, forms, draft writes and streaming actions. Preserve
  normal Picker behavior outside this opt-in process.
- [x] Skip repair, provisioning, automatic updates/check-cache writes,
  maintenance, resident registration and service ownership. Missing owners
  remain visibly unavailable; do not start or repair them.
- [x] Provide repeatable source launch and production-render capture
  instructions, including local-only review and cleanup.

## Validation plan

- [x] Focused tests cover read admission, mutation rejection before process
  execution, form/stream bypass attempts, disposable state, lifecycle
  suppression, candidate labeling and unchanged ordinary behavior.
- [x] Exercise the actual source entry and native production UI against real
  owner data. Record the candidate revision, selected read sources, capture
  result and unavailable sources without publishing private values.
- [x] Verify installed selectors, owner configuration and service ownership
  do not change through launch, refresh, attempted action and exit.
- [x] Run the existing affected Manager test lane, touched-file lint and
  repository contracts; document unavailable platform or external lanes.
- [x] Land the reviewed implementation and return to the parent effort's
  installed acceptance and completion gates.

This is a trusted developer review mode, not a sandbox for hostile candidate
code or arbitrary contributed commands. Reads use existing provider-owned
contracts; no second daemon, collector, selector or configuration authority is
introduced.

## Qualification

The Windows source CLI produced a real production-render capture without
installation. A separate native production-UI run rendered and refreshed 221
current owner-published rows, refused eleven exercised effect paths, retained
the same existing owner endpoint and ten protected files, and removed its
disposable state. Captures remain local because they contain real data.
Contributed Workers discovery succeeded. An earlier read reached the existing
deadline and showed an error, not a healthy empty catalog; one qualified candidate
read and rendered 144 real catalog entries within that unchanged deadline.
Neither result claims save/activation or installed acceptance.

The running owner lacked the newer resident bootstrap action. Review therefore
uses the established read-only `picker-bootstrap` and `picker-paths` CLI shapes
through complete installed runtimes; roster reads still use the existing feed
without the ordinary list command's demand/recovery side effects. A native
comparison overlapping an independent installed Manager selector change was
discarded and repeated successfully; no rollout was taken over.

The expanded affected test packet initially exposed a registry-render test
loading the entire unrelated engine command surface and missing one of that
surface's dependencies. The test now calls its actual dependency-free renderer
directly, preserving its exhaustive-output assertion without adding a Manager
runtime dependency. The corrected registry/review packet passed (70 tests,
one existing POSIX-only skip); the final helper/boundary packet passed (225
tests, one existing POSIX-only skip). Touched-file lint, documentation consistency
and the thirteen-plugin install contract passed. No module ceiling was widened.
The admitted-read lifecycle packet also exercised the existing process-group/
Windows Job owner. Its native descendant fixture now establishes observable
grandchild readiness before the same three-second timeout; the cleanup/review
packet passed all 38 tests without increasing a production deadline.
Exit-during-read regressions now exercise actual pending bootstrap/catalog
descendants, cancellation and draining before transient-state removal, refusal
of late reads, the normal shutdown-safe source entry, and older-owner bootstrap
fallback through the POSIX owned adapter. The Windows packet passed 157 tests
with three native POSIX skips. A separate native Linux adapter check preserved
340,000 stderr bytes without pipe deadlock and cancelled/drained an active
read with its grandchild heartbeat stopped and temporary state removed.
Additional review controls preserve equals-form candidate labels, refuse both
bulk maintenance confirmations before any mock progress is created, and bind a
catalog command and its selected state root atomically. Real child reads using
the provider's own path resolver select the non-default root both without an
inherited override and with a conflicting override; the parent environment is
unchanged.

Implementation #8723 merged as
`d24a5d2a8269cd9e666e8ce4046bdfff24053242`, after approval of
`74d8948e3de4c5c787416b008acba2713f39f6ae`, required green CI and resolved
review threads. The latest revised native UI run rendered/refreshed 222 current
rows and refused thirteen effect paths, including both bulk confirmations,
with the same owner endpoint, ten protected files unchanged and temporary state
removed. The older installed Workers provider again showed its unchanged read
deadline explicitly. The Coordinator has resumed the parent installed-acceptance
slice; this source-only delivery does not close that gate.

This developer mode does not change installation/bootstrap, so a fresh-machine
installation lane is not applicable; absent-owner refusal is tested instead.
Real local owner/UI integration and native Windows/Linux process-adapter
lifecycle were exercised. Full Linux UI, remote venue and physical
keyboard/display qualification are not claimed: this initial lane is local-only
and the automated UI captures use Textual's production compositor headlessly.
