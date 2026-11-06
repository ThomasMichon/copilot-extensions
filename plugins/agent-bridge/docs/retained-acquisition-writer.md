# Retained acquisition Worktrees writer

Back to [local session acquisition](session-acquisition.md).

The optional `retained_acquisition_worktree_writer: 1` health capability
supports **ACP acquisition request version 3**. It is an independently
negotiated extension within HTTP generation 52, not a new global HTTP floor.
The client uses the existing per-attempt protocol/capability fence before
posting, including after daemon cutover. Request versions 1 and 2 retain
their existing syntax and behavior; an older provider cannot accept version
3 while silently ignoring its required fields.

Version 3 requires the existing exact `project` and `worktree_path`, plus
`worktree_writer`:

| Field | Meaning |
|---|---|
| `version` | Requirement version 1 |
| `identity.installation_root` | Expected selected Worktrees installation |
| `identity.runtime_generation` | Expected immutable-slot 32-hex build identity |
| `creation_nonce` | Original workspace incarnation, checked against exact owner reads |

This object rejects unknown fields. It accepts no caller command, interpreter,
environment or successful-qualification assertion. Other request versions
cannot carry it. CLI mode with this requirement refuses before owner/native
effects; ordinary unpinned CLI acquisition remains supported.

## Selection and recovery

The provider resolves its own existing pinned Worktrees selection and runs
`resource-relationship capabilities` through that exact command. The response
must identify the expected executing serializer and its retained-relationship
and creation-nonce preservation contract, plus `retained_session_writes: 1`.
Selected-root metadata alone does not qualify it.

The original request and provider-selected command commit together in the
existing delivery-keyed acquisition journal before launch. The command remains
part of that journal's existing effect history through launch and delivery.
A new frontend or concurrent retry recovers it without selecting a replacement.
Later same-task acquisition retains the same requirement and command; an
unqualified historical acquisition cannot be retrofitted. Missing or conflicting
recovery facts refuse with the existing custody intact.

Acquisition reads, delegated registration and binding use the retained owner.
The native target stores only a pointer to the original acquisition journal.
Later native resume and handoff recover and requalify that same command, check
the original workspace, and use it for registration, succession and lineage
reads. Required lineage errors are not silently treated as legacy best-effort
success. Unpinned operations keep their existing fail-open behavior.

The historical `acquisition:` caller-affinity prefix is also used by keyed
workspace creation and singleton initialization. Without an explicit writer
pointer or a matching acquisition attempt, it does not select this journal:
those continuations retain their existing lifecycle owner. An explicit writer
pointer still requires its original journal, and a matching attempt with
missing or malformed effects refuses rather than becoming an unpinned call.

Registration, binding and succession carry the original requirement through
Worktrees' [qualified executing-session option](../../agent-worktrees/docs/resource-relationship.md#qualified-executing-session-writes).
Those commands invoke their existing registered handlers in the qualified CLI,
not a resident selected only by ordinary verb versions. The actual handler
checks the executing build and locked workspace incarnation before mutation.
This operation-scoped path neither disables nor qualifies the resident globally.
An older owner without the declaration refuses before native effects; the
requirement is never dropped on retry.

Provider refresh treats that pointer as session-owned rather than provider
metadata and preserves it across stopped-session recovery. The refreshed target
must still be local and match the original workspace before it is persisted
or launched. Required resume never falls back to creating another conversation,
even when a caller requests recreation.

Required handoffs retain the original successor, native identity, opening seed and progress in
the same acquisition journal before launch. Post-spawn lineage failures retry
against that successor, not a new conversation. An unconfirmed start with no
recoverable holder, changed inputs/native identity or unknown seed outcome
refuses with the retained handoff intact. A completed retry returns the same
successor without reseeding.

A registration failure after native creation leaves that same conversation
stopped and recoverable. Resume retries registration before reporting success
or linking a successor, including when it reattaches to a surviving host.
While registration is pending, background recovery remains durably disabled.
Failure detaches and clears the frontend client without reaping its host/native,
so a normal send cannot bypass guarded registration recovery. Startup leaves
that dormant session stopped; successful guarded registration re-enables its
ordinary background recovery.

The merged pinned launcher owns retention, current installation governance and
pre-import checks of the original completion/build identity. Selector movement
does not replace this writer. Removed or recreated slots refuse; this feature
does not veto an authorized installer replacement or preserve a second registry.

## Qualification boundary

The optional `retained_acquisition_writer_inspection: 1` health capability
adds `acquisition-inspect <delivery-id> --worktree-writer`, using the existing
`GET /api/v1/acquisitions/{delivery_id}?worktree_writer=true`. The CLI
negotiates that capability on each transport attempt. Without the option,
inspection retains its previous response and performs no writer probes.

The required form reopens only that delivery's original journal binding.
It never selects a replacement command, retrofits a legacy acquisition,
acquires a native, delivers a prompt or repairs a record. It requalifies the
retained executing serializer and reads the original workspace through that
same command. Changed project, path or creation nonce refuses. The journal
and active Bridge instance must still match after those reads; unknown,
passive, draining or unready owners do not yield successful evidence.

The additive `worktree_writer_observation` contains schema
`copilot-extensions.acquisition-worktree-writer`, version 1, the original
delivery/reservation/task/workspace fields, the retained `worktree_writer`
requirement, live serializer contract and active Bridge instance
(installation root, PID and routing generation). It exposes no command or
environment. Missing acquisitions retain their 404; an unqualified writer
inspection returns 409. This is a fresh read-only observation, not a durable
ready permit, native-liveness proof or successful-delivery assertion.

This capability qualifies Bridge's delegated writer route, **not** the resident
serializer or every other producer of the shared Worktrees record. Existing
relationship transactions retain their own effect-side expected-nonce/writer
checks. Independent original-workspace and complete participating-writer
admission remain required before ordinary governed assignment is assembled.
No ordinary Dispatch writer, allocation, rollout, cleanup or native-replacement
permission follows from the capability.

Focused source tests cover request negotiation, pre-native refusal, journal
recovery, concurrent replay and an HTTP round trip. A finite source-process
control executes the actual Worktrees parser/serializer for registration and
succession while retaining an existing protected claim and nonce, with a
reachable ordinary-verb-compatible but unqualified resident. Unpinned control
writes reach that resident; required writes never do. Changed identity, nonce,
project and path refuse at all three receiving commands. Its declared
build identity and native ACP transport are controlled fixtures; it is not
installed-runtime, native Copilot or closed-cohort acceptance.
