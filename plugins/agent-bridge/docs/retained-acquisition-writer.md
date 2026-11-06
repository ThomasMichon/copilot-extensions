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
and creation-nonce preservation contract. Selected-root metadata alone does
not qualify it.

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

The merged pinned launcher owns retention, current installation governance and
pre-import checks of the original completion/build identity. Selector movement
does not replace this writer. Removed or recreated slots refuse; this feature
does not veto an authorized installer replacement or preserve a second registry.

## Qualification boundary

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
succession while retaining an existing protected claim and nonce. Its declared
build identity and native ACP transport are controlled fixtures; it is not
installed-runtime, native Copilot or closed-cohort acceptance.
