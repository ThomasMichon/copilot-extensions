# CLI parity inventory

Back to [Author/Reviewer perspectives](author-reviewer-perspectives.md) and the
[canonical effort](README.md). This is an **agent-recommended qualification
inventory**, not a declaration of parity or a new command specification.

## Baseline and evidence

Source inventory: Worktrees `pr_cli.py`, `pr_state_cli.py`,
`pr_remediation_cli.py`, `pr_publication_labels_cli.py` and `finalize_cli.py`;
PR plugin `__main__.py`, `native_cli.py`, `workspace_cli.py` and
`workspace_tools.py`. The owning parsers remain the complete argument/default
reference. This table groups semantics without copying volatile flag lists.

The existing PR plugin has default GitHub-specific command paths and an
explicit native adapter. Its four native verbs do not prove complete HTTP
Author/Reviewer parity. Native `merge` currently rejects explicit merge-method,
auto and branch-deletion switches; native `create` rejects a base override.
These are existing explicit refusals to preserve during compatibility, not
proof that all legacy operations can be retired.

| Existing operation family | Required successor evidence | Current qualification |
|---------------------------|-----------------------------|-----------------------|
| `create-pr`, `pr-create` / `pr create` | Correct source/target, publication checks, head/base policy, exact body, attribution, claim recovery, existing-PR reconciliation | Existing native `create` composition; full HTTP/source-publication parity open |
| `push-changes`, update and revision reporting | Same PR head, exact source custody, stale-approval handling, no unrelated source import | Worktrees source owner retained; new front-door integration open |
| `set-pr` / tracked association | Explicit target/provider/branch selection, multiple PRs, durable original obligation | Worktrees metadata/claim owner retained; central registration cannot replace it |
| `pr-ready` | Draft-to-ready without implicitly granting consent | Successor integration open |
| `pr-status`, snapshot/status | Tracked versus explicit target, no-live, actual native votes/gates/consent, unavailable evidence | Native `status` baseline; full HTTP parity open |
| `pr-watch cursor/wait` | Default criteria, cursor races, meaningful transitions, independent timeout semantics and consent wake | Existing native watch and replay baseline; new cursor journal is separate until qualified |
| `pr-watch subscribe/stop/ownerstatus` | Same registration/cancel ownership, identities, ACK/restart and independent deadlines | Existing durable watches; caller-driven sets/selections still open |
| `pr-watch threads`, thread resolution | On-demand bodies, current/reviewed candidate, author/reviewer ownership and provider-native resolution | Existing native reads; workspace source is ADO-first; three-provider end-to-end open |
| `pr-comment`, `pr-review` | Distinct non-verdict comment and named review; selected exact-candidate writeback and unknown outcomes | Protected workspace submission source exists; installed HTTP/CLI adoption open |
| `pr-watch reply/requeue` | Existing ADO thread/revision/idempotency and eligible evaluation/retry semantics | ADO-only bounded native operations; no generic rerun claim |
| `pr-nudge` | Bound reviewer request or truthful unsupported/no-op; no coding-agent mention side effect | Successor integration open |
| `pr-publication-labels` and author metadata | Metadata-only repair, exact body/reviewer/work-item meaning, preserve unrelated values | Existing bounded native label repair; author amendment A0-A5 retains full scope |
| `pr-merge` | Distinguish consent, human-only and direct completion; exact head, required policy/checks/holds, no privilege escalation | Native adapter uses target policy; explicit CLI differences are not parity |
| `pr-complete` | Source reconciliation after authoritative merge, preserve subsequent work; not finalize | Worktrees source authority retained; successor invokes it rather than remote source mutation |
| `pr-abandon` | Exact operator-approved close/cancel and revision confirmation; no inferred abandonment | Successor integration open; terminal observation grants no close permission |
| `pr-research`, checks and evidence helpers | Same target policy, independent review/check evidence, caller-owned workspace and supported refusal | Existing policy/evidence owners retained; migration inventory remains open |

This is the initial source inventory, not proof it is exhaustive. Before
retirement, enumerate registered parser aliases, dispatch-before-argparse
branches, supported scripts, documentation examples and actual consumers.
Reconcile new upstream operations rather than freezing this table as a second
parser reference.

## Operation-by-operation proof

Each mapped operation records:

- The executing old/new contract, provider and caller/target binding.
- Accepted argv, defaults, strict invalid-option refusal, stdout/stderr and
  exit-status equivalence. Do not equate different native `wait` and
  `pr-watch wait` timeout conventions.
- Actual provider effects, repository policy, source/publication/claim/resource
  consequences and absence of duplicate publication or credential fallback.
- Confirmed versus known-unattempted versus unknown outcomes, original-ID
  recovery, supported old client/new service and new client/old service cases.
- Current-head/provider/account/platform evidence and unqualified lanes.

Use selected native/HTTP integration fixtures and separately admitted live
provider controls. A parser forwarding assertion alone cannot prove semantic
equivalence. Do not remove any legacy form or change its defaults because a
subset of tests passes.
