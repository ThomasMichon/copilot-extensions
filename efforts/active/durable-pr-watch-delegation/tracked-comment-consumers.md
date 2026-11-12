# Tracked comment consumers through the existing selected-operation owner

Back to the [canonical effort](README.md) and
[publication inventory](comment-publication-inventory.md). This is a
**reviewed C2 refinement**. The optional
[source embedding](../../../plugins/agent-pull-requests/docs/tracked-publication.md)
implements the finite tracked consumer without changing ordinary commands.
Normal/installed-host adoption and permission to publish native feedback remain separate.

## What the actual tracked path retains

`pr_state_cli.py` calls `pr_reviewer_ops.pr_comment` or `pr_review` after
resolving the active tracked PR. Those functions call provider `post_comment`
or `submit_review`. The provider interface returns an error string, with an
empty string reporting success. It does not return a structured native receipt.
`PRRecord` retains PR publication/association state, not an immutable selected
comment operation, final prepared body or per-action write outcome.

GitHub uses `gh pr comment` / `gh pr review`. Gitea discards the successful
comment/review response. ADO posts review text as a separate thread before
casting its vote. A failed vote can therefore follow a successful comment.
Neither an empty error string nor the tracked PR's source metadata can recover
that comment's original selected identity or prove safe replay.

Do not decorate these provider methods, allocate GUIDs inside a retry, add a
comment history to `PRRecord`, or infer an original receipt from cached text.
Leave the legacy path at its documented semantics until explicitly selected
consumer composition is qualified.

## Smallest existing-owner composition

Use the already delivered workspace selection, preparation and submission
owners for an explicitly selected tracked consumer. The optional trusted host
binding supplies the actual workspace client/tools and selected-operation
inputs. It does not construct an owner, load credentials, discover another
service or import a second plugin's private runtime merely because a command
was invoked. Reuse the existing workspace API and its capability exchange;
do not introduce another publication endpoint, ledger or allocation format.

The tracked command remains responsible for resolving its selected PR and
rejecting provider/configuration mismatch. Before preparing or submitting,
bind that exact provider/endpoint/account/repository/PR and action semantics to
the actual workspace's native scope. A matching PR number, caller role label
or source marker is insufficient. A changed active association cannot silently
retarget a retained operation; recovery still addresses its original owner.

Retain the original operation through the same ReplayStore selection owner.
Allocate and compose before returning final preview, then require explicit
submission of that exact intent and source attachment. Preparation is not
submission, and the invocation's body or verdict flag cannot approve a changed
prepared body. Use the actual profile/source policy owner, not the tracked
PR author's frozen attribution decision, for this comment's producer.
Original-key recovery must not depend on resolving today's active PR or
reconstructing bytes from today's profile.

An executing receiver must advertise the required selection/prepared contract
and action support before the new path is chosen, and recheck its real storage
and writer support at dispatch. Missing support refuses the selected path
before effects, never falls back to the legacy provider write. Old CLI syntax,
opaque-body operations and output retain their meaning. Any new syntax needs
provider-first discovery against the exact executing CLI, not a package-version
guess or an effectful probe.

## Preserve the selected surface and each effect

| Selected operation | Required semantics |
|---|---|
| General PR comment | One selected unanchored comment; not an inline comment or review body |
| ADO review with text and a vote | Separate ordered comment and verdict actions, with their own original outcomes |
| ADO comment-only review | Selected comment with no invented vote |
| Bodyless ADO vote | Verdict only; no filler comment, comment GUID or attribution body |
| Bodyless comment-only invocation | Preserve its existing no-op semantics or explicitly refuse the optional composition; do not manufacture a write |
| GitHub/Gitea review body | Native review-body semantics, not an ordinary comment followed by an assumed-equivalent vote |

The current protected executor is ADO-specific. This first tracked composition
must report unsupported for GitHub/Gitea protected publishing until their actual
native adapters and receipt contracts are separately qualified. Ordinary
provider support is not protected-path support. Do not silently route them
through ADO, reinterpret their review bodies, or narrow the all-provider parent
exit to this finite integration.

Return the existing owner results without collapsing partial action outcomes
to one success flag or error string. After a confirmed text action and an
unknown/failed vote, the original text identity remains retained; recovery must
not post it again. Lost results, restart and ownership handoff retain the
original intent and unknown fences. A confirmed receipt is original native
delivery evidence, not fresh version proof, handled feedback or DB mapping.

## Serial source delivery and validation

1. Review this refinement before changing tracked consumer behavior.
2. Bind one optional source consumer through the actual tracked resolution,
   workspace capability/selection, shared preparer and retained owner. Keep
   defaults unchanged and dependencies optional; no host enrollment is implied.
3. Qualify final preview followed by explicit exact submission through the real
   CLI/tool/client seam, native serializer and real-file owner. Cover changed
   tracked targets, missing/rolled-back support, original recovery after target
   closure or profile changes, lost results, restart and same-key concurrency.
4. Prove comment-success/vote-failure and unknown-vote recovery do not resend
   text; bodyless/no-op semantics remain intact. Qualify scope/actor/source
   mismatch and unsupported native surfaces with no fallback write.
5. Update the inventory with only the delivered entry and its source tests.
   Keep other issue/inline/reply/edit publishers, GitHub/Gitea native parity,
   actual protected-source grants, normal hosts and installed/shadow acceptance
   open under the canonical parent.

No live feedback, vote, merge, credential acquisition, state migration,
retrospective edit, host activation, deployment or resource release is granted.
