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
| GitHub general comments and review bodies | `providers/github.py`: `gh pr comment` and `gh pr review` | Existing provider; empty error string is not a native identity/version receipt |
| Gitea general comments and review bodies | `providers/gitea.py`: issue-comment POST and pull-review POST | Existing provider; response identity is not retained by this interface |
| ADO general comments and review text | `providers/azure_devops.py`: new thread POST; review text is a separate comment before the selected vote | Existing provider; preserve the two effects and their partial outcomes, never invent a comment for a bodyless vote |
| ADO remediation replies | `pr_remediation_cli.py` -> `providers/azure_remediation.py` `Remediator.reply` and existing `Ledger` | Separate existing owner; content includes its own operation marker before admission. Compact preparation/correlation is unqualified |
| ADO remediation evidence comments | `providers/azure_remediation.py` `Remediator.requeue` -> `AdoMutator.evidence_thread` | Separate existing owner; actual evidence comment participates in the selected recovery operation. This inventory grants no requeue |
| Protected workspace comments, replies and owned edits | `agent-pull-requests` workspace CLI / `WorkspaceTools` / native or service client -> `WorkspaceOperations.execute("submit")` -> `execute_review` -> `providers/azure_owned_review.py` | Existing source embedding with immutable intent and native receipts; explicit preapproved-body composition is qualified below, not automatic preparation |
| Protected inline-anchored new comments | No corresponding selected-action shape in `review_intent.py` | Unsupported by this finite protected contract; an unanchored comment is not inline coverage |
| Standalone issue comments | No issue-publication entry in the reviewed PR workspace/CLI contract | Outside that API; adopter/SDK/MCP issue publishers require their own owner integration |
| Other SDK/MCP or direct forge-tool writes | Workspace descriptors reuse `WorkspaceTools`; separately registered forge tools and direct vendor CLIs do not automatically traverse it | Source descriptors are not tool registration, all-tool enforcement or installed adoption |
| Protected GitHub/Gitea equivalents | No equivalent protected native adapter in the examined submission path | Unsupported in this ADO-only executor; ordinary provider support above is not receipt parity |

This is the bounded inventory of the current Worktrees/PR-owner source paths,
not a census of independently installed tools or external forge clients.
Unqualified and unsupported entries remain open C2/C4 obligations.

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
adoption. This is not automatic preparation, fresh readback or DB merge.
Default submission remains v1 and legacy tagged bodies gain no inferred identity.

## Remaining qualification

Actual automatic publication consumers, durable GUID allocation, acquisition/DB
mapping, broader unknown-outcome native attribution, copied tags, provider
normalization/stripping and installed adoption remain open. A matching marker
or body alone cannot confirm an unknown effect. Preserve existing native
actor, scope, target, content/version and receipt checks.

No new ledger, watcher, service, credential, body archive, live write,
retrospective edit or rollout is authorized.
