# CLI parity inventory

Back to [Author/Reviewer perspectives](author-reviewer-perspectives.md) and the
[canonical effort](README.md). This is an **agent-recommended qualification
inventory**, not a declaration of parity. Proposed boundary refinements below
remain distinct from delivered command specifications.

## Baseline and evidence

Source inventory: Worktrees `pr_cli.py`, `pr_state_cli.py`,
`pr_remediation_cli.py`, `pr_publication_labels_cli.py` and `finalize_cli.py`;
PR plugin `__main__.py`, `native_cli.py`, `workspace_cli.py` and
`workspace_tools.py`. The owning parsers remain the complete argument/default
reference. This table groups semantics without copying volatile flag lists.

The existing PR plugin has default GitHub-specific command paths and an
explicit native adapter. Native author capability discovery, metadata update
and source push supplement status, create, merge and wait; none proves complete
HTTP Author/Reviewer parity. Native `merge` currently rejects explicit merge-method,
auto and branch-deletion switches; native `create` rejects a base override.
These are existing explicit refusals to preserve during compatibility, not
proof that all legacy operations can be retired.

| Existing operation family | Required successor evidence | Current qualification |
|---------------------------|-----------------------------|-----------------------|
| `create-pr`, `pr-create` / `pr create` | Correct source/target, publication checks, head/base policy, exact body, attribution, claim recovery, existing-PR reconciliation | Existing native `create` composition; full HTTP/source-publication parity open |
| `push-changes`, update and revision reporting | Same PR head, exact source custody, stale-approval handling, no unrelated source import | Bounded native author/source adapter delivered in #10402; complete client migration open |
| `set-pr` / tracked association | Explicit target/provider/branch selection, multiple PRs, durable original obligation | Worktrees metadata/claim owner retained; central registration cannot replace it |
| `pr-ready` | Draft-to-ready without implicitly granting consent | [Finite explicit native source delivered in #11643](#draft-to-ready-native-convergence); complete HTTP/provider/installed parity open |
| `pr-status`, snapshot/status | Tracked versus explicit target, no-live, actual native votes/gates/consent, unavailable evidence | Native `status` baseline; full HTTP parity open |
| `pr-watch cursor/wait` | Default criteria, cursor races, meaningful transitions, independent timeout semantics and consent wake | Existing native watch and replay baseline; new cursor journal is separate until qualified |
| `pr-watch subscribe/stop/ownerstatus` | Same registration/cancel ownership, identities, ACK/restart and independent deadlines | Existing durable watches; caller-driven sets/selections still open |
| `pr-watch threads`, thread resolution | On-demand bodies, current/reviewed candidate, author/reviewer ownership and provider-native resolution | [Finite dedicated native read source merged in #11657](native-thread-read.md); [selected ADO resolution source merged in #11728](native-thread-resolution.md); [selected reopening source merged in #11748 after contract review #11741](native-thread-resolution.md#selected-native-reopening-proposal); installed/HTTP and three-provider live acceptance open |
| `pr-diff`, review source/evidence | Explicit target binding, unchanged tracked selection, truthful provider support/completeness and candidate limits | [Finite native source merged in #11661 after reviewed contract #11659](native-diff-read.md); ADO unified diff remains unsupported; installed/provider/HTTP parity and workspace generations remain separate |
| `pr-comment`, `pr-review` | Distinct non-verdict comment and named review; selected exact-candidate writeback and unknown outcomes | Protected workspace submission source exists; installed HTTP/CLI adoption open |
| `pr-watch reply/requeue` | Existing ADO thread/revision/idempotency and eligible evaluation/retry semantics | ADO-only bounded native operations; no generic rerun claim |
| `pr-nudge` | Bound reviewer request or truthful unsupported/no-op; no coding-agent mention side effect | [Finite explicit native source merged in #11647](native-reviewer-request.md); Windows/provider/installed/HTTP parity open |
| `pr-publication-labels` and author metadata | Metadata-only repair, exact body/reviewer/work-item meaning, preserve unrelated values | Bounded native metadata repair delivered in #10402; author amendment A4-A5 retains full scope |
| `pr-merge` | Distinguish consent, human-only and direct completion; exact head, required policy/checks/holds, no privilege escalation | Native adapter uses target policy; explicit CLI differences are not parity |
| `pr-complete` | Source reconciliation after authoritative merge, preserve subsequent work; not finalize | [Finite local-source adapter proposed under #11753](native-source-reconciliation.md); existing Worktrees source authority and legacy semantics retained; not remote source mutation or merge evidence |
| `pr-abandon` | Exact operator-approved close/cancel and revision confirmation; no inferred abandonment | [Local creation-cancellation source merged in #11653](native-creation-cancellation.md), following #11651; native-platform/installed and remote close/comment/claim integration remain open |
| `pr-research`, checks and evidence helpers | Same target policy, independent review/check evidence, caller-owned workspace and supported refusal | Existing policy/evidence owners retained; migration inventory remains open |

This is the initial source inventory, not proof it is exhaustive. Before
retirement, enumerate registered parser aliases, dispatch-before-argparse
branches, supported scripts, documentation examples and actual consumers.
Reconcile new upstream operations rather than freezing this table as a second
parser reference.

The [existing-owner HTTP report contract](owner-report-read.md), reviewed in #11665,
selects a bounded authenticated thread/diff exchange, not workspace generation
acquisition or installed client migration. Its optional source embedding and
client, merged in #11668, preserve complete native reports and acquisition
exits with finite Gateway task/drain custody. The
[native owner contract](native-report-owner.md), implemented in #11686,
adds explicit GitHub.com account selection and one request budget. The
[bounded response composition](bounded-report-response.md) merged in #11702,
and [explicit standalone hosting](standalone-report-host.md) in #11712.
These finite source leaves do not complete installed client migration,
live-provider or operation-by-operation HTTP/CLI qualification.

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

## Draft-to-ready native convergence

**Contract reviewed in #11641; finite native source merged in #11643.
Source qualification below is not installed
acceptance.** This finite source slice
closes part of the PR-domain
[provider-neutral surface](../../../visions/plugins/agent-pull-requests/README.md#first-class-provider-neutral-pr-surface)
and Worktrees'
[foreign-repository reach](../../../visions/plugins/agent-worktrees/pull-requests/README.md#foreign-repo-pr-operations).
It follows the existing
[version-skew pattern](../../../docs/patterns/service-contract-evolution.md)
and [author target/custody contract](author-publication.md#ownership-and-exclusions).
It neither creates a second forge transport nor retires any existing command.

### Source baseline and intended boundary

The current Worktrees `pr-ready` selects a tracked PR and implements native
un-draft or legacy hold-label release. Its Azure DevOps provider's `mark_ready`
explicitly refuses. The dedicated PR CLI has no native readiness command.
Forwarding alone therefore cannot qualify this gap.

The proposed optional operation is **`draft-ready`** in the existing
`agent-worktrees.pr-author-capabilities`, version 1, target-qualified operation
map. Absent or non-true means unsupported, not permission to probe an effect.
The first implementation advertises support for the selected, enabled Azure
DevOps binding only. Other providers remain explicitly unqualified until their
own conformance evidence passes.

| Surface | Proposed form and owner |
|---------|-------------------------|
| Worktrees explicit target | `pr-author-ready --repo <registered-target> --number <positive-number> --json` |
| Dedicated PR native client | `ready --repo <registered-target> --number <positive-number> --worktrees-command <selected-runtime> --json` |
| Optional delivered policy | Existing native `--config` selection and `worker-policy-config` support; no ambient fallback on an invalid or mismatched file |
| Compatibility path | Existing tracked `pr-ready [worktree]` retains its syntax, defaults, result and legacy-hold behavior |

The explicit author verb is necessary because existing `pr-ready --repo/--pr`
still selects a tracked PR, even without a positional worktree. Reinterpreting
that syntax would change old callers' target selection. The new verb requires
both target and PR number and rejects tracked selectors. It composes the existing
provider owner rather than creating another implementation. The managed caller
still needs its existing claimant admission; no target checkout is required.
Target resolution owns provider, endpoint, credentials and enabled repository
policy, not the caller's default
PR configuration or a same-number tracked PR. The implementation must not
silently redirect an old tracked invocation into the new path.

### Effects and outcomes

Only the selected active draft PR's native draft field may change. The new
operation does not release legacy or current hold labels, grant consent, set
auto-completion, merge, vote, resolve discussions, request reviewers, push
source, create a PR, settle a claim or finalize a worktree. Labels, title,
description, reviewers, votes, source/target identity and unrelated completion
options remain unchanged by its mutation payload. Existing effect authority
and repository restrictions remain required; capability reporting grants none.

The provider owner obtains an authoritative target-bound read before writing;
missing/invalid native draft or lifecycle evidence is not false/active by
default. Wrong repository/PR identity, terminal state or an already-ready PR
refuses without mutation. Already-ready is a non-applicable operation, not
evidence that this caller performed a transition.

The new explicit result uses `agent-worktrees.pr-ready`, version 1, with exact
`repo`, positive `number`, `provider`, `ok` and `actions.draft.status`:

| Action status | Meaning |
|---------------|---------|
| `not-attempted` | Admission, evidence, applicability or support refused before the write |
| `confirmed` | The single native un-draft write returned and authoritative same-target readback confirms an active non-draft PR |
| `failed` | A completed, authoritative response/readback establishes the requested transition did not succeed |
| `unknown` | A write may have applied, or post-write identity/lifecycle/readback is unavailable or inconsistent |

Only `confirmed` returns `ok: true` and exit 0. Every other outcome exits
nonzero, preserves the known selected PR identity and supplies an actionable
diagnostic. Do not claim atomic compare-and-swap or protection against a later
participant edit when the provider supplies neither. A lost response,
malformed result or timeout after dispatch remains unconfirmed at the native
client too; reconcile the same PR through observation before another intentional
attempt. No automatic replay, alternate transport or effectful fallback occurs.

### Expansion, migration and qualification

Expand the actual Worktrees/provider owner and its support declaration first;
then conditionally enable the dedicated native client against that selected
runtime. Package version, a newer payload or another executable cannot establish
support. Old callers retain the tracked baseline on new providers; new callers
on old or rolled-back providers refuse this optional feature before mutation
while existing operations remain usable. No old syntax is deprecated here.
HTTP service mutation, ordinary host/worker consumer replacement, installed
adoption and three-provider parity remain later acceptance, not this slice.

- [x] Qualify the explicit Worktrees parser, claimant admission, target policy
  and provider owner with synthetic active-draft, already-ready, terminal,
  malformed, wrong-target and missing-capability controls.
- [x] Exercise the actual native frontend-to-Worktrees/provider path, not only
  argv assertions. Prove draft-only mutation and preservation of independent
  consent/holds, votes, reviewers and completion options.
- [x] Inject pre-write refusal, rejected write, lost response and post-write
  readback/identity/lifecycle failures. Preserve honest outcomes and prove no
  create, push, consent, retry or raw-forge fallback.
- [x] Cover old/new callers, old/new selected runtimes and provider rollback,
  additive capability-map fields, strict invalid arguments and invalid explicit
  policy. Preserve the existing tracked ready/legacy-hold regression suite.
- [ ] Record source and native-platform qualification separately from any
  authorized live-provider or installed-consumer evidence. Source tests and a
  merged proposal alone close neither this matrix nor the parent effort.

The merged finite source passes 54 Worktrees author/readiness/tracked-ready
controls and 84 native frontend cases with five Windows-only cases skipped on
Linux. The actual frontend, Worktrees parser/resolver and Azure DevOps provider
compose in one fixture; a separate real subprocess exercises capability and
explicit worker-policy argument forwarding. Provider responses are synthetic.
No live forge, Windows execution, installed host or HTTP mutation parity is
qualified by these results. The legacy tracked Azure DevOps `mark_ready`
refusal is unchanged; explicit author readiness is separately advertised.
Capability launch/timeout failures and valid claimant refusals remain
`not-attempted`, preserving their diagnostics; null provider identity is
accepted only for an otherwise valid pre-dispatch refusal, never an attempted
outcome. Missing, empty or non-string capability providers refuse before
dispatch. Synthesized failures use the same schema/version and retain the
provider once resolved; both actual owner-result controls were rerun after
that frontend correction.
The actual projectless dispatcher also preserves the versioned not-attempted
refusal after a valid delivered-policy capability probe, including project
resolution exceptions and strict invalid-option rejection.
