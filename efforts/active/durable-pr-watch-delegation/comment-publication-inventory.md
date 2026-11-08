# Compact-comment publication inventory

Back to the [comment-identity contract](comment-identity.md). This records
source entry points for C2, not installed or all-comment adoption. An opaque
body field does not mean a publisher invokes the optional shared preparer.

## Current source paths

Paths below are relative to the repository root.

| Surface | Actual entry and native path | Current disposition |
|---|---|---|
| Tracked PR comments | `plugins/agent-worktrees/src/agent_worktrees/pr_state_cli.py` -> `pr_reviewer_ops.pr_comment` -> provider `post_comment` | Legacy-compatible opaque body; no shared preparation or retained public comment/write identity |
| Tracked PR review bodies | `pr_state_cli.py` -> `pr_reviewer_ops.pr_review` -> provider `submit_review` | Legacy-compatible; bodyless native actions remain bodyless |
| Explicit selected tracked comments/reviews | Existing Worktrees parser/handler -> optional `TrackedPublication` -> `WorkspaceTools` selection/submission/inspection | Optional source embedding with exact trusted tracked/native mapping, committed preview and separate exact submission; not ordinary command or installed adoption |
| GitHub general comments and review bodies | `providers/github.py`: `gh pr comment` and `gh pr review` | Existing provider; empty error string is not a native identity/version receipt |
| Gitea general comments and review bodies | `providers/gitea.py`: issue-comment POST and pull-review POST | Existing provider; response identity is not retained by this interface |
| ADO general comments and review text | `providers/azure_devops.py`: new thread POST; review text is a separate comment before the selected vote | Existing provider; preserve the two effects and their partial outcomes, never invent a comment for a bodyless vote |
| ADO remediation replies | `pr_remediation_cli.py` -> `providers/azure_remediation.py` `Remediator.reply` and existing `Ledger` | Separate existing owner; content includes its own operation marker before admission. Compact preparation/correlation is unqualified |
| ADO remediation evidence comments | `providers/azure_remediation.py` `Remediator.requeue` -> `AdoMutator.evidence_thread` | Separate existing owner; actual evidence comment participates in the selected recovery operation. This inventory grants no requeue |
| Protected workspace comments, replies and owned edits | `agent-pull-requests` workspace CLI / `WorkspaceTools` / native or service client -> `WorkspaceOperations.execute("submit")` -> `execute_review` -> `providers/azure_owned_review.py` | Explicit composition and optional receiver-owned fresh pre-approval allocation are qualified below; not automatic all-publisher or installed adoption |
| Protected inline-anchored new comments | No corresponding selected-action shape in `review_intent.py` | Unsupported by this finite protected contract; an unanchored comment is not inline coverage |
| Standalone issue comments | No issue-publication entry in the reviewed PR workspace/CLI contract | Outside that API; adopter/SDK/MCP issue publishers require their own owner integration |
| Other SDK/MCP or direct forge-tool writes | Workspace descriptors reuse `WorkspaceTools`; separately registered forge tools and direct vendor CLIs do not automatically traverse it | Source descriptors are not tool registration, all-tool enforcement or installed adoption |
| Protected GitHub/Gitea equivalents | No equivalent protected native adapter in the examined submission path | Unsupported in this ADO-only executor; ordinary provider support above is not receipt parity |

This is the bounded inventory of the current Worktrees/PR-owner source paths,
not a census of independently installed tools or external forge clients.
Unqualified and unsupported entries remain open C2/C4 obligations.
The [tracked-consumer refinement](tracked-comment-consumers.md) records the
confirmed error-string/no-selection gap in the legacy path. The optional
[tracked source embedding](../../../plugins/agent-pull-requests/docs/tracked-publication.md)
composes the existing selected-operation owner without changing those defaults.

## Explicit preapproved-body composition

Prepare selected comment bodies **before** final preview, approval and immutable
intent admission. Review-intent/v1 accepts an opaque `body` for a comment, reply
or owned edit and rejects additional fields. `ReplayStore.admit_review`
fingerprints and retains that exact intent before a native attempt, including
the final prepared body. Its unknown fence and native receipts remain
authoritative through retries and restart.

The focused workspace test composes the actual source-origin producer and
configured AI body preparer with the existing CLI front door, protected ADO
serializer and real-file owner. It covers create/reply/owned-edit final bytes,
stable logical identity with a fresh intentional-edit write identity, retained
original intent on restart and no additional send on confirmed retry. This is
synthetic protected source qualification, not a live-provider write or an
executing receiver requirement to invoke preparation.

The original producer retains supplied public IDs and source through preview
and admission. This fixture does not add automatic GUID allocation or require
all existing clients to prepare. Do not decorate inside `execute_review` after
selection, allocate another GUID on retry, accept caller policy labels as
authority or add fields to strict v1 requests. If structured correlation needs
more than the retained opaque body, expand the owning storage/wire contract
through review first.

## Explicit prepared native source selection

The [protected native owner](../../../plugins/agent-pull-requests/docs/protected-review-executor.md#prepared-native-intent-and-original-receipt-projection)
also supports an explicitly selected v2 source embedding after prepared storage
adoption. It retains structured identity through original native execution and
provides a read-only original receipt projection. The
[optional existing workspace receiver](../../../plugins/agent-pull-requests/docs/workspace-integration.md#optional-prepared-review-receiver)
can compose the actual CLI/tool/client/Gateway path after discovery and owner
adoption. The optional
[local preparation binding](../../../plugins/agent-pull-requests/docs/workspace-tools.md#optional-local-comment-preparation)
can compose the actual shared preparer before preview and explicit submission
for protected comment/reply/owned-edit source hosts. It still requires a trusted
host policy/source binding and caller-retained GUIDs; it is not automatic
installed adoption, allocation, fresh readback or DB merge.
Default submission remains v1 and legacy tagged bodies gain no inferred identity.

The optional
[receiver-owned pre-approval path](../../../plugins/agent-pull-requests/docs/workspace-integration.md#optional-pre-approval-selection-receiver)
now allocates fresh comment/reply identities under the same retained-selection
owner before returning a committed v2 preview (#11299). It requires actual
receiver/storage discovery and a trusted policy/profile/source resolver.
The source attachment and full native baseline must match; complete Fleet
preview/submission frames fit before retention. Lost responses and restart
recover the same original bytes, while exact submission and original receipts
remain separate. Bodyless votes stay bodyless; baseline receivers refuse
automatic owned/legacy edits. The separate optional extension below consumes
their scoped/native identity binding. This is optional source
integration, not normal host enrollment, another publisher's adoption or
installed/provider qualification.

The optional allocation **library** additionally accepts an actual protected
generation/source binding for selected owned edits. It resolves prior logical
identity through the same owner's confirmed scoped native receipts and exact
baseline, or allocates a first identity for an explicitly selected genuinely
legacy target. Unknown/conflicting histories and unbound outer markers refuse.
The [owner-store reference](../../../plugins/agent-pull-requests/docs/review-owner-store.md#optional-pre-approval-selection-retention)
owns this API. The optional
[selected-edit receiver extension](../../../plugins/agent-pull-requests/docs/workspace-integration.md#optional-selected-edits)
binds that library through the existing CLI/native/Fleet/Gateway path with
default-off trusted-host selection and a separate additive capability.
It preserves the exact strict baseline declaration and prior fresh clients;
it does not enroll normal hosts or adopt other publishers.
The finite consumer source merged in #11381. Its coupled seven-file packet
passes 250 cases, including 17 actual native/Gateway CLI edit-consumer cases;
the earlier #11374 library packet passes 150, including 20 focused edit cases.
These overlap and do not qualify live providers, installed hosting or other
publication surfaces.

## Remaining qualification

Actual automatic publication consumers, owned-edit and other-surface allocation,
acquisition/DB mapping, broader unknown-outcome native attribution, copied tags, provider
normalization/stripping and installed adoption remain open. A matching marker
or body alone cannot confirm an unknown effect. Preserve existing native
actor, scope, target, content/version and receipt checks.

No new ledger, watcher, service, credential, body archive, live write,
retrospective edit or rollout is authorized.
