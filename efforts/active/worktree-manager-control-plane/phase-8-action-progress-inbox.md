# Registered action progress through Inbox

[Canonical Manager effort](README.md#proposed-finite-action-progress-inbox-consumer-from-5343)

This is the finite implementation and acceptance record for #5343 and the existing
P5343/V5343 contract. It closes the Picker's existing render-derive-not-own,
nonblocking and addressed-background-publication intent. It does not reopen
the delivered Inbox primitive or all background producers.

This closes the [Picker vision](../../../visions/picker/README.md)'s
`render-derive-not-own`, `responsive-by-budget` and
`renderable-and-assertable-headless` intent. It follows the
[design principles and invariants](../../../docs/patterns/README.md):
right-size the surface, preserve process contracts and keep provider authority
outside the renderer. No standing intent or primitive contract changes.

## Source boundary and ownership

`engine_pivot_actions._run_task_action_progress` creates the UI progress dictionary.
Its reader publishes independent `pct`, `msg`, `done`, `error` and final-message
snapshots through the modal's Inbox. `ProgressScreen._action_stream_panel`
consumes the current value without draining it.
The existing cancellation callback belongs to the provider runtime, and its
cache invalidation/refresh remains separately owned.

The existing `Inbox.post`/`peek` API supplies coalesced latest-value publication.
However, the main Picker inbox uses `drain_apply`, which deliberately removes
non-callable data too. Merely posting progress dictionaries into that inbox
would lose values before the modal could peek them. The progress modal is the
actual consumer and already has its own render tick.

Source `patient-knob` owns this finite proposal and implementation under the
public #5343 reservation. Existing provider execution, configuration, other
progress modes, native timing and all resource owners retain their scopes.
There is no new runtime service, progress store or process-lifecycle authority.

## Mechanism

The reviewed mechanics retain the original outcome and do not authorize live
effects. The source implementation is accepted in #11948.

- Use the existing Inbox primitive at the owning action-progress modal, with
  one addressed latest-value slot for that run. Do not mix its data with the
  main screen's closure-draining inbox or change primitive drain semantics.
- Keep cumulative progress state private to the reader. Publish independent
  snapshots through `post`; never mutate a snapshot after publication or write
  the render-owned progress dictionary from that thread.
- Consume the latest snapshot with nondestructive `peek` on the modal's existing
  render/tick and keyboard paths. Apply its state only on the owning UI thread,
  before deciding completion, cancellation or the displayed button/status.
  Repeated renders retain the same current message without requiring another
  producer frame, a new timer or a drain.
- Bind the modal/publication to the exact originating run. A closed or replaced
  run's frames, completion, queued wake or old modal input cannot change, close
  or cancel a newer run. Preserve the existing cancellation event and runtime's
  authority over its own child.
- Preserve early/pre-mount updates and a retained latest snapshot when a wake
  cannot be delivered; the existing tick can observe it after mounting. Do not
  infer success from a wake or blindly retry provider execution.
- Retain percentage clamping, optional percentage/message updates, success final
  messages, failures and explicit terminal state. An ordinary reader failure
  must settle as an error rather than strand the modal in Working.
- Reuse the existing native `_open_progress` construction seam if the producer
  needs its owning modal handle. Other maintenance/profiles/Sync/Finalize
  entrypoints and their navigation, confirmation and cancellation semantics
  remain unchanged.

No CLI syntax, NDJSON producer schema, provider manifest, installation behavior
or independently updated service contract changes. The implementation uses
the existing provider callback and Inbox APIs; old payload compatibility is
not replaced by a version assertion.

## Plan

- [x] I0: Review and merge this source proposal before functional edits
  (#11944).
- [x] I1: Capture failing controls on the actual registered action producer,
  including no render-dictionary mutation before UI consumption.
- [x] I2: Connect reader publication to the modal's existing Inbox and
  nondestructive current-value rendering, with exact run and cancellation fences.
- [x] I3: Preserve completion, provider error/final message and unexpected-reader
  failure; keep provider refresh and other progress consumers unchanged.
- [x] I4: Pass the affected consumer packet, source guards and required review/CI,
  merge the implementation and reconcile only the finite P5343/V5343 rows.

## Validation Plan

- [x] IV1: Exercise real worker callbacks and the actual modal renderer: multiple
  updates, partial percentage/message updates, coalescing, repeated peeks,
  completion, failure and final message. Show that only UI consumption changes
  the UI-owned state.
- [x] IV2: Gate the reader deterministically across cancellation, closing A,
  starting B and late A progress/completion/error. Include an old queued wake
  and old modal input; B's state and cancellation callback remain untouched.
- [x] IV3: Cover early completion before mounting and unavailable wake delivery,
  retaining current-value display rather than manufacturing a successful wake.
- [x] IV4: Use an owned inert streaming child through the real runtime/parser
  and native Textual progress modal for the simple consumer check. Preserve
  existing success/error/cancel behavior and explicit fixture cleanup. No
  real provider, shared mux, service or operator session is a fixture.
- [x] IV5: Run the directly affected progress/action/Inbox packet and existing
  applicable lint, install-contract, module-size and documentation checks.
  Keep the original primitive and nonblocking-I/O credit intact.
- [x] IV6: Record evidence tiers honestly. Unprepared modal/reader fixtures
  establish first-use consumer behavior, not a fresh-machine installer pass.
  No installer is changed. Live external-provider or installed/native timing
  claims require their own applicable authority and evidence; they are not
  invented prerequisites or inferred from headless fixtures.

## Completion boundary

This finite source work is complete only after the actual producer-to-Inbox-to-
modal flow and every applicable control pass, required review and CI pass,
the implementation merges and the canonical effort records its accepted rows.
Other progress producers, provider lifecycle, native performance, release,
authorized rollout and the wider Manager/Picker campaign remain separate.

## Journal

### 2026-11-10 - Finite source acceptance

#11948 actually merged at 17:05:34 UTC as
`d46ec448ba52aa3b5b8606f3083a89167ad3b1c5`. Genuine Copilot
approval5845984279 covers `55e0ca1d65443bd53cedbc0cc0c8f421f32dcc7d`,
with no active issues and no inline findings. The actual required PR gate,
guard/lint, eligible-unit, lockdown and source-attribution checks passed before
merge. The 106-case local consumer packet and focused source guards remain
the evidence recorded below.

The original issue and complete current Plan/local/global Validation and
Coordination clauses were reread against this implementation. Only the finite
P5343/V5343/G5343 family is accepted. Existing Inbox/nonblocking credit and
provider/process/resource custodians remain unchanged; this is neither parent
Manager completion nor release, installed-native acceptance or rollout.

### 2026-11-10 - Implementation candidate

The source plan merged in #11944 before functional edits. Three focused
pre-change controls failed on actual reader mutation and bypassed publication.
The affected seven-file packet now passes 106 cases, including nine new
deterministic reader/modal controls and the expanded actual inert-child/Textual
success, provider-error and cancellation cases. The packet also exercises
existing Inbox, progress and Sync consumers. No source-specific assertion is
weakened; 329 unrelated cases are excluded by the focused selector.

Snapshots stay private to their producing reader and publication never mutates
an earlier snapshot. The modal retains current data through repeated peeks,
early completion and failed wakes. Old completion/error, queued wake, tick and
input cannot alter or cancel a replacement. Reader and refresh failures settle
terminally; a secondary refresh failure retains the original provider error.

These are source/unit and simple real-consumer checks on Windows with Python
3.13 x64. First-use modal fixtures are not a clean-machine installation check:
no installer changes. No real external provider, productive session or installed
native timing was exercised; that lane has no execution/rollout authority in
this slice. Runtime publication and installed acceptance remain separate.
I4, IV5 and the original P5343/V5343/G5343 remain open until the remaining
source checks, implementation review and actual merge are reconciled.
