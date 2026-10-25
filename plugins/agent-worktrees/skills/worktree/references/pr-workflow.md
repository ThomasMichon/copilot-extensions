# Worktree PR Workflow (PR mode)

Use the exact `argv[0]` from the agent-worktrees session command catalog for
every direct runtime operation below. Replace
`<agent-worktrees catalog argv[0]>` with that path and never search `PATH`.
Substitute the raw path and quote it at each shell call site.

Full reference for signing off a worktree through a **pull request** instead of
direct-push finalization. See [SKILL.md](../SKILL.md) for the overview, the
two-phase `push-changes` + `finalize` flow (direct mode), and the safety
rules.

## Contents
- Check the target repo's PR flow first (profiles + verb applicability)
- Addressing a foreign repo -- no local checkout needed (the claimant contract)
- Detecting PR mode + where PR config lives (machine-local vs in-repo)
- Auto-complete/auto-merge is not a bypass -- prefer it, keep watching
- Default conduct: drive every PR through to merge (waiting policy + sanctioned deviations)
- Never assert what masked/redacted tool output literally contains
- `create-pr` (auto-open, attribution marker, labels) -- for tracing a PR you
  didn't open, see [pr-attribution.md](pr-attribution.md) instead
- Dispositions: keep-alive vs detach
- Bounding review/fix rounds (`pr rounds`: stop on plateau or round cap)
- Draft PRs (`--draft` / `pr-ready`)
- Multiple PRs from one worktree
- Recovery

---
## PR Workflow (PR mode)

Some repos opt into a **pull-request workflow** instead of direct-push
finalization. A repo is in PR mode when its config sets `pr.enabled: true`.

### Addressing a foreign repo -- no local checkout needed (the claimant contract)

`pr-watch` and `pr-merge` can act on a PR in a repo you have **no local
checkout of at all** -- the `foreign-repo-pr-operations` capability. The
contract is fixed, regardless of which repo you're addressing:

- **The owning project is always your CWD.** Run these commands from your
  own worktree -- the one responsible for the work, and the **claimant**
  that owns this operation's lifetime. Never run them from an untracked
  directory, a bare project anchor, or -- the common mistake -- from inside
  the *target* repo's own checkout.
- **The target repo is always an explicit argument** (`owner/name` or ADO
  `project/repo`), never inferred from "whichever repo's checkout I happen
  to be sitting in." Addressing a repo this way needs it **registered**
  (`<agent-worktrees catalog argv[0]> repos add <name> <path> --remote <url>`)
  so its own provider/token/policy can be resolved -- it does **not** need a
  local worktree of it.

```
<agent-worktrees catalog argv[0]> pr-watch wait owner/other-repo 42 --timeout 1
<agent-worktrees catalog argv[0]> pr-merge owner/other-repo 42 --now
```

Both commands refuse, with actionable guidance, instead of silently doing
the wrong thing, when:
- **CWD isn't a tracked worktree** -- there is no claimant to own the
  operation. The error names the fix: run it from your own worktree.
- **The target slug isn't a repo this machine can resolve a binding for** --
  it refuses rather than falling back to *your own* project's
  provider/token/policy for a *different* repo (which would silently act
  under the wrong identity/policy).

**If either refusal fires, follow the guidance in the error -- do not fall
back to `gh`/`az repos`/`git` directly for the same operation.** That skips
the provider/token/policy resolution this command exists to get right, and
defeats the point of having one coherent PR surface at all.



### Check the target repo's PR flow FIRST -- it is not the same everywhere

**Never assume a PR flow; read the target repo's config before you drive one.**
Different repos land work differently, and the `pr-*` verbs apply to different
subsets. Query the repo's **flow profile** up front:

```
<agent-worktrees catalog argv[0]> get pr-profile      # direct | pr-human-merge | pr-agent-merge | pr-self-merge
<agent-worktrees catalog argv[0]> get pr-enabled      # "true" or "false"
<agent-worktrees catalog argv[0]> get pr-required     # "true" -> direct-to-default-branch is blocked
<agent-worktrees catalog argv[0]> get pr-provider     # gitea | github | azure-devops (empty in direct mode)
```

The four configured profiles are provider-generic. A caller's effective flow
also applies its resolved role and live permissions; a configured self-merge
profile alone does not grant merge authority.

| Profile | Config shape | How work lands | Verbs that apply |
|---------|--------------|----------------|------------------|
| **`direct`** | `pr.enabled: false` | `finalize` lands to the default branch | *(none -- no PR flow)* |
| **`pr-human-merge`** | enabled, no `automerge_label`, no submitter-direct selection | PR-gated; a **human** approves + merges | `create-pr`, `pr-watch`, `pr-status`, `pr-nudge`, `pr-complete` -- **not `pr-merge`** |
| **`pr-agent-merge`** | enabled + an `automerge_label` bound | PR-gated; the author **signals merge consent** after approval; the review gate merges | the full `pr-*` family, including `pr-merge` |
| **`pr-self-merge`** | enabled, no `automerge_label`, and `self_approve` or `merge_actor: submitter-direct` | The authorized submitter merges once the repo's checks/review gates permit | the full `pr-*` family; explicit `pr-merge <repo> <PR> --now` |

**Applicability is self-describing.** `pr-status` prints the profile (`flow:`
line + a `flow` block in `--json`). Bare `pr-merge` applies consent only for
`pr-agent-merge`; `pr-self-merge` requires explicit `--now`, and
`pr-human-merge` does not support either merge action. A refusal names the
applicable process. Follow it -- do **not** hand-merge, escalate to an admin,
or invent a flow.

- On a **`pr-human-merge`** repo: open the PR (`create-pr`), address review with
  `pr-watch`, then **a human approves and merges** -- consult the repo's
  `CONTRIBUTING` / related narrative for who merges. `pr-merge` does not apply.
- On a **`pr-agent-merge`** repo (e.g. an auto-reviewer + auto-merge repo where
  review typically lands in minutes): after approval, `pr-merge` signals consent
  and the gate merges.
- On a **`pr-self-merge`** repo: satisfy its actual review and required-check
  policy, then use `pr-merge <repo> <PR> --now` if the caller's resolved role
  authorizes it. Bare `pr-merge` deliberately refuses rather than applying
  consent. A human-review or ownership veto is not waived by this profile.
  For GitHub, the verified Maintainer exception for a missing additional
  CODEOWNER approval is independent of `pr.prefer_auto_merge`: that setting
  selects direct versus native auto-completion, not review-rule authority.
  Both paths retain current-head, required-check and mutation-safety gates.
  Target-branch deletion and non-fast-forward protections do not prohibit the
  normal PR merge operation. They remain in the verified policy fingerprint;
  their presence does not waive CI, review authority or any other merge
  requirement, and a policy change before the write still refuses.
  GitHub's automatic Copilot-review request rule is not an additional merge
  requirement. A ruleset's review-thread-resolution requirement is satisfied only by a
  complete, current-head read with every thread explicitly resolved, rechecked
  before the administrative fallback. Unresolved, unreadable or reopened
  threads still block; actor-scoped review authority and required CI are never
  waived by either rule's presence.
- If `pr-merge` reports "no automerge_label" on a repo you **expected** to be
  `pr-agent-merge`, inspect its effective profile and resolved configuration.
  A stale binding is one possible cause; refresh it through the project's
  authorized synchronization flow when needed. Do not treat missing consent
  configuration as self-merge authority or fall back to a hand-merge.

### "Request auto-complete" -- the same shape across providers (incl. Azure DevOps)

For **`pr-agent-merge`**, bare `pr-merge` requests consent/auto-complete, not
direct self-merge. *How* the provider honors that is an implementation detail,
so ADO is the **same `pr-agent-merge` shape** as gitea/github -- not a special
case. The separate self-merge `pr-merge --now` path above is not this consent action:

| Provider | How `pr-merge` requests auto-complete | Consent marker in a snapshot |
|----------|----------------------------------------|-------------------------------|
| gitea / github | applies the `automerge_label` (the review gate then merges) | the real label on the PR |
| azure-devops | sets **native** auto-complete (`az repos pr update --auto-complete` with `squash` / `delete_source_branch` / `bypass_policy`) -- no label | the synthetic `auto-complete` marker, present once auto-complete is set |

So an **ADO repo** (e.g. `example-marketplace`) binds `automerge_label: auto-complete`
(the abstract consent-marker name) and uses the full family. Extra ADO knobs:

- `approval_required: false` -- **self-complete**: eligible when simply *not*
  changes-requested (we own the merge; no approval vote needed). A
  `CHANGES_REQUESTED` review still blocks -- address it, then re-run.
- `allow_stale_approval: true` -- preserve an approval as merge authority only
  for the bounded race where the same provider endpoint observed the exact
  tracked current head before the stale approval was submitted. Both timestamps
  come from the provider clock, and the observation must strictly precede the
  approval. Every mediated create, push, or manual association clears and
  reacquires the evidence, so a post-approval mediated push cannot inherit the
  approval. Same-second, missing, malformed, endpoint-mismatched, head-
  mismatched, or provider-unsupported evidence fails closed. Providers expose
  no portable head-generation watermark, so enabling this policy also requires
  branch updates to use the mediated PR flow; a generic client cannot detect an
  arbitrary out-of-band replay of a previously observed SHA. `pr-status` and
  `pr-watch` expose `approval_stale` plus `approval_stale_authorized`;
  mergeability, holds, WIP state, and changes-requested verdicts still block.
- `bypass_policy: true` -- complete **past** a branch policy that never
  auto-satisfies for our own PRs (e.g. a central governance status policy);
  otherwise ADO auto-complete would wait forever.

**Auto-complete/auto-merge is not the same thing as a bypass merge, and
arming it is not itself a self-merge/bypass act.** Requesting auto-complete
(this whole "Request auto-complete" mechanism, on any provider) is a
hands-off *completion* mechanic: the platform merges automatically **only
once every required review and status-check gate the PR would need anyway is
satisfied** -- it grants no gate, skips no approval, and a submitter cannot
use it to force their own unapproved PR through. That is categorically
different from an actual bypass/admin merge (the `bypass_policy` knob above,
or an equivalent "complete past a policy" action on another provider), which
deliberately overrides a gate rather than waiting for it -- keep those two
concepts distinct in any guidance you write or give.

**GitHub direct admin merges verify required CI before bypassing review.**
The provider enumerates the target branch's classic protection and effective
ruleset status contexts across all pages, then requires every declared context
and configured integration to report a pass. Integration-bound checks need
exact-head check-run evidence from that app; an unverifiable legacy status
cannot substitute for a bound check run.
Absent, pending, unsuccessful or unreadable evidence refuses the merge even if
all *reported* checks look green. Optional checks do not become required.
The verified PR head is pinned with `--match-head-commit`, and a head/base change
during verification requires new validation.

This preflight runs only at the explicit admin merge boundary, not on every
watcher poll. `pr-watch`'s observed check rollup is not authoritative required-gate
completeness. Server-side CI enforcement remains necessary for atomic policy
protection; this guard does not authenticate a same-app check against deliberate
spoofing or grant permission to change branch policy. Existing role-authorized
review self-merge remains available after required CI passes. A policy API that
cannot be read fails closed rather than assuming the branch has no requirements.

- **Prefer requesting auto-complete/auto-merge whenever the provider offers
  it**, on any repo, independent of whether this identity holds self-merge or
  bypass authority there. It is the normal low-friction path once a PR is
  ready for its gates to run -- not a privileged shortcut reserved for
  maintainers, and not something to disable out of caution on a
  human-review-gated repo. Arming it there is still correct and expected,
  precisely because it cannot complete until a human actually approves.
- **Requesting it is not "driving the PR to merge" by itself.** A submitter
  (and any agent reviewing the PR) must keep watching an auto-complete-armed
  PR until it actually merges -- a late conflict with the target branch, a
  needed rebase, or a reviewer (human or automated) raising a new finding
  after a later push can all reopen gates auto-complete was waiting on. Don't
  treat the PR as unattended just because the affordance is set; see
  "Default conduct" below.

The natural "wait for the auto-review, then complete" loop is
`pr-watch` (blocks until the reviewer weighs in / mergeability settles) →
`pr-merge` (requests auto-complete once eligible) → `pr-complete` (post-merge
reconcile). If the wait times out with no verdict at all (or a stale one
sitting untouched), `pr-nudge` asks this repo's bound automated reviewer
(`pr.reviewer`) to (re-)review before you re-arm the watch -- it reports
`supported: False` rather than erroring when no such binding exists.

**`pr-watch` tells you when to run `pr-merge`.** Its result payload carries a
`merge` block derived from the same verdict/consent classifier `pr-status` uses:
`merge.needs_consent` (true = the PR is approved and unblocked but the
merge-consent label is not applied yet — **you** must apply it via `pr-merge`;
it will not merge on its own), `merge.consent_action` (`apply` | `already` |
`skip`), `merge.clear_to_merge`, and `merge.reason`. So an `approved` transition
is a *review* signal, not the finish line: when `needs_consent` is true, run
`pr-merge` and re-arm the watch. On a `pr-human-merge` repo (no consent label
bound) the block degrades to a verdict/merge-state readout with no action.

For `pr-agent-merge`, a valid current-head approval with missing consent wakes
`pr-watch wait` promptly, even if the approval predates its baseline/cursor or
the caller selected `--until merged`. This is a **state update**, reported as
`updates: ["consent_required"]`, not a fabricated `approved` event; `events` and
`transitions` still contain only selected provider transitions.
`merge.consent_command` gives the exact single-PR follow-up command. The watcher
also supplies `merge.consent_argv` for shell-independent execution; the display
command uses POSIX quoting, so non-POSIX consumers should execute the argv array.
The watcher does not grant consent or authorize consenting for another author.
An unchanged update is acknowledged only by the cursor returned with that
update: re-arm with it to deduplicate; `pr-watch cursor` does not acknowledge
an update it never delivered. A new head/authoritative approval or an observed
intervening block can prompt again; ignored comments, pending/dismissed reviews,
and author reviews do not change the consent acknowledgement. Drafts, holds,
conflicts/unknown mergeability, pending/failed
checks, stale approvals, and wrong-base PRs do not receive this immediate wake.
Only a successful checks rollup or confirmed empty checks (`"none"`) permits
this wake; unreadable/unknown checks (`""`) and unknown author/reviewer identity
fail closed. GitHub requires both status and check-run reads to confirm
success or no checks, rejects unknown completed conclusions, and raises a
provider error rather than returning incomplete review pages. Check-run reads
paginate and require the reported total count before trusting success.
Approval commit
IDs must exactly match the current head for this wake. Self-merge and
human-merge profiles do not receive updates.
Azure DevOps currently omits authoritative checks and approval commit evidence
from its snapshots, so this immediate state wake is unsupported there and fails
closed. Its existing review transitions, timeout state, and explicit
`pr-merge` auto-complete path remain unchanged; do not expect a merged-only ADO
wait to substitute for explicitly requesting auto-complete after approval.

Exit **0** means a selected transition **or** an actionable consent update, not
proof of merge. Both text and `--json` expose the same result; text also prints
the follow-up command. Timeout remains **124**, provider/auth failure **3**, and
usage failure **2**. The native hibernation waiter composing this command wakes
on exit 0 without needing to reinterpret a review transition.

An interrupted or unexpectedly completed wait is a wake-up to reconcile, not
evidence that its requested condition or the PR's completion gate passed. Run
`pr-status` for that exact repository and PR, inspect any returned events and
consent update, and re-arm only if the intended condition remains unmet and the
previous wait has exited. Preserve the original review cursor after an
interruption with no usable result; use a returned cursor only after handling
its events/update. Do not fetch a new cursor merely to restart, which can skip a
transition during the interruption.

Keep the original finite deadline: a re-arm gets only its remaining timeout,
not a fresh full window. An elapsed deadline is a timeout, not a spurious wake.
Transient failures may use bounded backoff; repeated immediate exits need
diagnosis rather than a tight restart loop. Explicit cancellation, usage/auth
failures and installation-identity refusals remain visible failures, not
permission to restart a daemon or create a second subscription. This is
caller-side recovery discipline; it does not change valid transition/consent
wakes, the existing transient-provider retry, or supported unbounded waits.

`pr-merge` applies consent by default through its existing checked provider
path; `--dry-run` instead reports `merge.needs_consent`,
`merge.consent_action`, and `merge.consent_command`, without waiting or labeling.
Successful application reports consent fulfilled, not a completed merge; a
refusal/application failure remains nonzero.

### Comment threads -- first-class, every provider

Review **comment threads** are a first-class capability (`pr-status --threads`
lists them; `--resolve-threads` marks the active ones resolved). Azure DevOps
maps threads cleanly (REST; AAD or PAT auth); gitea/github carry more-irritating
details (gitea has no programmatic conversation-resolve -- read-only there;
github threads use GraphQL and resolve all active threads at once). The
feedback loop: `pr-status --threads` (read) → fix in the worktree →
`push-changes` → `pr-status --resolve-threads` → `pr-merge`.

Check before signing off:

In **direct mode** (the default), use the two-phase `push-changes` +
`finalize` flow above. In **PR mode**, the flow becomes
`create-pr -> [delegate PR creation] -> finalize`, and `push-changes` targets
the *feature* branch instead of the default branch.

### Never assert what masked/redacted tool output literally contains

A host's content-exclusion or secret-redaction layer can replace sensitive-
looking substrings (tokens, credentials, certain patterns) with a placeholder
(e.g. a run of asterisks) before the content ever reaches the agent --
invisibly, with no "denied"/error signal distinguishing it from genuinely
trivial content. This differs from an outright denied tool call (which IS
clearly signaled); here the call succeeds and returns content, just with some
substrings swapped for a mask the agent cannot see through.

**Never treat masked output as ground truth about what the real file
contains.** A real incident this guidance is drawn from: an agent read a
source line displaying a six-asterisk placeholder in an `Authorization`
header call, assumed it was literal source text, and then filed a review
reply and a GitHub issue making confident factual claims ("the code sends the
literal string `******`") and asking the operator to fix it accordingly --
when the masking was purely a display artifact of the agent's own tooling,
and the agent had no actual way to know what the underlying bytes were.

Telltale signs worth treating as a yellow flag before asserting anything
about such content:
- The same suspicious placeholder (asterisks, `[REDACTED]`, etc.) appears
  verbatim and identically across multiple, otherwise-unrelated call sites or
  files -- a real secret value would vary; a masking layer produces identical
  output for every match.
- The placeholder sits exactly where a credential, token, or secret-shaped
  string would naturally go (e.g. an `Authorization` header value, a
  `--token` argument, a connection string).

When you notice this pattern:
- Do not assert, in a commit message, PR comment, review reply, or filed
  issue, what the real characters are or what the code "does" with them --
  you only know what your own tooling displayed, not the real bytes.
- If the real content's correctness genuinely matters (e.g. a review bot
  flagged a possible bug at that exact line), say so honestly: name the
  uncertainty, and ask a human (or a path without this masking) to verify
  directly, rather than describing masked display text as if it were the
  file's real content.
- **Never edit the masked expression itself, or any syntax it depends on
  (a wrapping quote, an `f`/`r`/`b` string prefix, an escape sequence) --
  even a change that looks purely cosmetic can silently change what the
  real, unseen bytes mean.** The hidden characters determine whether
  surrounding syntax is load-bearing: an f-string prefix masked as
  `f"******"` could be hiding a real `{token}` interpolation, and dropping
  the prefix to "fix" an apparent lint complaint would silently disable
  that interpolation -- a real mistake made while drafting this very
  guidance, caught by a review bot before merge. Treat the masked span,
  and anything syntactically coupled to it, as off-limits until an
  unmasked path (a different tool, or a human with real access) confirms
  what is actually safe to change. An edit well outside and independent of
  the masked span is fine; one touching its boundary is not.
- If you already filed something (an issue, a PR comment) based on a
  masked-content assumption, correct it explicitly once you notice --
  retract the specific factual claim, keep only what you can actually verify
  (e.g. a real, unmasked CI diagnostic that independently flagged the same
  line).

### Where PR config lives (machine-local vs in-repo)

The `pr` block may come from two places:

- **Machine-local** `~/.{project}/config.yaml` under `repos.<name>.pr` --
  the default location, per-machine.
- **In-repo** `<repo-root>/.agent-worktrees.yaml` (committed) under a top-level
  `pr:` block -- **repo-level policy shared across every machine**. When this
  file provides a `pr` block it **overrides** the machine-local one entirely.

Put PR *policy* (enabled/required/provider) in the in-repo file when it should
be identical everywhere -- it then needs no per-machine replication. A
malformed or absent in-repo file safely falls back to machine-local. Either
way, query the effective values with
`<agent-worktrees catalog argv[0]> get pr-*`.

### `pr.enabled` vs `pr.required` -- available vs mandatory

These are two distinct switches:

- **`pr.enabled: true`** makes the PR path *available*. The mode is **opt-in
  per worktree**: `push-changes`/`finalize` only take the PR path once a PR
  record exists (you ran `create-pr`). A worktree that never runs `create-pr`
  still finalizes **direct-to-default-branch**.
- **`pr.required: true`** makes the PR path *mandatory* (it implies
  `enabled`). The direct-to-default-branch path is **refused**: `push-changes` will
  not push to the default branch, and `finalize` will not prune a worktree
  with unmerged work. The **only** way to land work is `create-pr` -> open PR
  -> merge. There is no local bypass — when `pr-required` is `true`, every
  worktree goes through a PR.

If `<agent-worktrees catalog argv[0]> get pr-required` returns `true`, **do not** attempt a
direct `push-changes`/`finalize` for unmerged work — it will be refused. Go
straight to the end-to-end PR loop below.

### Default conduct: drive every PR you open through to merge

This is the default for **every** PR an agent opens through `create-pr`,
regardless of whether the target repo sets `pr.required: true` — not only the
mandatory-PR case. Opening a PR and stopping (or reporting it as "landed") is
not the end state; **merged** is. Apply whichever waiting policy actually
fits the target repo's configured flow — they are not interchangeable:

- **Self-merge repos** (`merge_actor: submitter-direct` / `pr-self-merge`):
  obtain the actual current-head review required by the target repo and pass
  its required checks, rebase if needed, then use the role-authorized managed
  merge. A genuine current-head `Approve` is review evidence; do not discard
  it because of an assumption about owner-authored PRs. Some repos also accept
  a clean `Comment` verdict under their documented policy. An author's own
  review is not independent approval, and agents must not approve their own
  PRs. Read the actual reviewer, head, body and open findings rather than
  inferring a verdict from a watcher exit or a historical product limitation.
  `eligible`, `reason` and `self_merge_note` describe tooling/provider state,
  not an override of repository policy or permission to ignore a veto. When
  the only outstanding gate is a separate human/code-owner approval and the
  repo explicitly grants this caller role-scoped self-merge authority, use
  the managed `--now` boundary. Unresolved blocking findings, holds, failed or
  missing required CI, conflicts and unknown ownership remain blockers.
- **Human-review repos**: poll for review state and comments (the
  end-to-end loop below); address feedback in the same worktree and
  re-request review; repeat until approved and merged.
- **Auto-complete providers** (e.g. Azure DevOps): set the provider's
  auto-complete affordance at PR-open time so the platform lands it once its
  gate clears, rather than polling forever in-session.

**The only sanctioned deviations** from driving a PR through to merge:

1. **The operator explicitly says otherwise** for this PR/session (e.g. "just
   open it, don't merge yet").
2. **A specific alternate charter governs differently** — a task, skill, or
   recurring cycle that itself defines an async hand-off (e.g. a triage cycle
   that records status and lets a later cycle or a human pick up a stalled
   PR) supersedes the default, but only for the scope that charter actually
   covers.

Absent one of those two, do not silently settle for "PR opened" — see it
through, using the waiting policy above.

**Repo-specific instructions:** a repo's own PR quirks that don't fit any of
the structured `pr.*` config fields (why a bypass mode is shaped a
particular way, an unusual review-request step, etc.) surface as an extra
`Note:` line in every `pr_reminder()` when the repo sets `pr.notes` — read it
the same way you'd read any other `Note:` line; it is not optional
commentary. See `docs/config-reference.md`'s `pr.notes` entry.

### End-to-end PR loop (when PRs are required)

The normal, expected flow for a worktree with work to land:

1. **`create-pr`** — squash + push the feature branch (Step 1 below).
2. **Open the PR** via the provider sub-agent (Step 2). Add the provider's
   **auto-merge** affordance if the work should merge automatically once the
   review gate is satisfied.
3. **`set-pr`** — record the PR URL/number (Step 3).
4. **Wait for review.** The PR goes through the repo's review gate (e.g. the
   multi-machine system's automated reviewer). Poll the PR via the provider sub-agent for
   review state and comments.
5. **Address feedback** in the **same** worktree (keep-alive disposition):
   edit -> commit on the feature branch -> `push-changes` updates the PR
   branch (never the default branch). Note: new commits **dismiss stale approvals**, so
   re-request / await review again. After each automated review, run
   `pr rounds` first and stop on a plateau or round cap -- see
   [Bounding review/fix rounds](#bounding-reviewfix-rounds-pr-rounds).
6. **Repeat 4–5** until the PR is **approved and merged upstream**. With
   auto-merge set, merge happens automatically on approval; otherwise a human
   merges.
7. **Finalize.** Under `detach`, the feature branch being safely pushed is
   enough to `finalize` at any point, before merge (see below) — the rare,
   operator-approved opt-out. Under `keep-alive` (the safe default),
   `finalize` requires the PR to have actually **merged** first; stay on the
   PR through review, consent, and merge, then finalize.

**Rare opt-out — submit and detach without babysitting review.** Per the
sanctioned-deviations list above: an agent may, when the operator approves (or
a specific alternate charter says so), open the PR and immediately `finalize`
(detach disposition), leaving the open PR for asynchronous review + auto-merge
rather than waiting in-session. This still goes through a PR — it is **not** a
direct-to-default-branch bypass. Use it sparingly: the default is to see the PR
through to merge. Never skip the PR entirely when `pr-required` is `true`.

### Head scheme + branch topology (PR mode)

**Invariant (both schemes): a worktree is always checked out on its own
`worktree/{id}` branch.** The **initial** `create-pr` squashes the worktree's
commits in place on `worktree/{id}`, rebases onto upstream, and leaves HEAD
there at that squashed commit — it is **never reset off it** (#1804). A **later
`create-pr` update on the same still-open PR** reuses that published PR head
without re-squashing or rebasing the already-pushed tip onto newer upstream;
it publishes any newer commits incrementally on top. `worktree/{id}` therefore
lands on the squashed commit on first publish, then accumulates later feedback
commits on top while the PR stays open; a later `git sync` (or the finalize
reconcile) realigns it clean on merge.

`pr.head_scheme` selects **only how the PR head is published** — its name +
push mechanism — never the local worktree state:

**`refspec` (default, #1815/#1899).** Push `worktree/{id}`'s squashed commit
*directly* to a disposable PR head ref via a refspec — no local feature branch:

```
origin/<default>  <—  worktree/{id}  ——push——>  origin/pr/{slug}-{suffix}
  (upstream)       (the only local branch;     (the PR head; deleted on merge)
                    sits ahead while open)
```

The head ref (`pr/{slug}-{suffix}` by default for non-Azure-DevOps repos;
Azure DevOps defaults to `user/{username}/{slug}-{suffix}` regardless of
scheme; all of it is templated via `pr.head_pattern`) is ephemeral and
provider-deleted on merge. Requires the repo's pre-push hook to allow the mediated
`worktree/{id} → pr/{slug}` push (a hook that blocks `worktree/*` by ref name
must honor `AGENT_WORKTREES_PR_PUSH=1`). A parallel `--new` PR auto-falls-back
to a snapshot ref (one worktree branch hosts only one live refspec PR).

**`snapshot` (legacy/compatible).** Copies the squashed commit onto a separate
local `feature/{slug}-{suffix}` branch and pushes *that* — **no reset, no
checkout dance** (HEAD stays on `worktree/{id}`, which keeps the squashed
commit):

```
origin/<default>  <—  worktree/{id}  ——snapshot——>  feature/{slug}-{suffix}
  (upstream)       (keeps the squashed             (the pushed PR head)
                    commit, sits ahead)
```

Snapshot needs no pre-push-hook cooperation, so it is the safe opt-out
(`head_scheme: snapshot`) for a repo whose hook still blocks the refspec push.
Set `head_scheme` per repo to choose; the multi-machine system default is `refspec`.

> **`feature/` is reclaimed under refspec.** Under the refspec scheme the per-PR
> head lives in the `pr/` namespace, freeing `feature/<name>` for its other
> meaning — a **coordinated multi-agent shared branch** (see the
> `git-collaboration` skill). Don't conflate the two.

### Step 1: `create-pr`

Managed PR pushes retain a bounded deadline and run the real pre-push hook.
If legitimate validation needs longer than the default 180 seconds, configure
`pr.push_timeout_seconds: 300` in the repository's
`.copilot-extensions/agent-worktrees/config.yaml` (or the machine-local
`repos.<name>.pr` block). The same per-attempt bound covers `create-pr` reruns
and PR-mode `push-changes`; publication-lock wait and stale-lock budgets scale
with it. Only positive, finite numbers whose dependent budgets fit portable
signed 32-bit millisecond wait limits are accepted—there is no unbounded
setting. A timeout still kills the entire push/hook process tree. See the
[configuration reference](../../../docs/config-reference.md#pr-workflow--reposnamepr-machine-local-or-in-repo).

Config drop-ins validate the same deadline before activation; an invalid
fragment is withdrawn independently without blocking valid peers.

```
<agent-worktrees catalog argv[0]> create-pr --title "Concise PR title"
```

Provide a reviewable description with `--body` or `--body-file`. A repository
may configure `pr.required_body_sections` (for example `Intent`, `Changes`, and
`Validation`); `create-pr` then fails before publishing unless every named
Markdown section contains visible text. A hidden source marker never satisfies
the human-readable body requirement.

On the **initial** publish, squashes the worktree's commits into one and
rebases onto upstream, leaving HEAD on `worktree/{id}` at the squashed commit
(both schemes — it is never reset off it, #1804). A **later** `create-pr`
against the same still-open PR instead reuses that published head without
re-squashing or rebasing it onto newer upstream — see "Head scheme + branch
topology" above. Under the default **refspec** scheme it pushes
`worktree/{id}` straight to the provider-resolved PR head ref (`pr/{slug}-{suffix}` for
non-Azure-DevOps repos; `user/{username}/{slug}-{suffix}` for Azure DevOps) —
no local feature branch. Under **snapshot** it instead copies the current
PR-head commit onto a local snapshot branch (`feature/{slug}-{suffix}` by default;
Azure DevOps still defaults to `user/{username}/{slug}-{suffix}`) and pushes
that (no reset, no checkout dance). Either way HEAD never leaves
`worktree/{id}`. Records `pr.state` and prints the
branch, base/head SHAs, and provider.
Add `--json`
to capture the metadata, or `--branch NAME` to override the generated name.
Use `--repo owner/name` to target a different repo than the worktree's own,
and `--new` to force a brand-new PR even when one is already open (parallel
PRs). `create-pr` is idempotent -- safe to re-run.

**No local checkout of `--repo`?** Add `--from-branch <branch>` when that
branch was ALREADY PUSHED to the target repo by some other process (a
container, another host, ...) -- `create-pr` skips its entire local
squash/push path and opens the PR directly against the target's own
resolved provider, auto-journaling a claim on THIS (the calling) worktree
so `finalize` still knows the work is outstanding. Requires an explicit,
non-blank `--title` (there is no local commit history to derive one from)
and is incompatible with `--dry-run`/`--no-open` (there is no local step to
preview or skip). Without `--from-branch`, naming a different, registered
`--repo` is refused outright -- there is no mechanism to push into it from
a checkout that isn't its own. If the target repo isn't registered here at
all (so neither `--repo` nor `--from-branch` can resolve it), use
`<agent-pull-requests catalog argv[0]> create --repo <repo> --head
<branch>` instead -- it needs no repo registration, but its branch must
ALSO already be pushed to that repo; neither command creates or pushes a
branch for you.

A worktree can track **multiple PRs** over its life. When the active PR is
already **merged or closed**, `create-pr` automatically opens a *fresh* PR
(new branch off the current default-branch tip) instead of reusing the merged
branch -- so landing a second change from the same worktree just works. This
holds even when the prior PR was merged **externally** (e.g. via the provider
API + an auto-merge label, without `finalize`/`pr-watch` updating the local
record): `create-pr` reconciles the active PR's state against the provider
before choosing a branch, so a stale local `open` never causes a force-push
onto a merged branch. See *Multiple PRs per worktree* below.

**Auto-open (provider plugins).** When the repo config sets `pr.provider` with
credentials (`pr.api_base`, `pr.token_command`/`pr.token_env`) and
`pr.auto_open` is on, `create-pr` **opens the PR itself** right after the push
-- via the provider CLI (`curl` for Gitea, `gh` for GitHub, `az` for Azure
DevOps) -- and **auto-records** the url/number on the worktree (no manual
`set-pr`). By default (codename-attribution-by-default), `create-pr` embeds a
public-safe marker carrying the worktree's assigned codename -- plus an
optional `root=<name>` field (another worktree's own codename, never a raw
identifier) when this worktree is itself claimed as an outbound resource by a
different, calling worktree; see `docs/architecture.md`'s *PR Attribution &
Codenames* section for the full detail -- resolve the primary codename back
via `resolve --codename` (or `embody --codename`) on the same machine, or on a
*different* machine it now runs a cross-machine SSH scan
automatically (effort `pr-attribution-codenames` Phase 3): every other known,
ssh-ready machine is asked over SSH whether its own tracking store has that
codename. A match on a different machine still fails closed -- it reports the
resolving machine and worktree id rather than attempting a remote launch; SSH
there directly (or use a future agent-bridge dispatch) to actually resume it.
The marker may also carry a third, independent `enc=<token>` field --
an AES-256-GCM-encrypted full identity, decryptable only by the holder of a
shared symmetric key, emitted automatically whenever one is configured (see
`docs/architecture.md`'s *PR Attribution & Codenames* section for key
custody and the decode command). Opaque ciphertext to anyone else, so it
needs no additional config to appear -- scoped to this `"codename"` mode
specifically; it never appears under `true` (already plaintext) or `false`
(the anonymous opt-out).
(This is the *author's* path back to their own worktree; a maintainer or
reviewer tracing a PR they didn't open should instead read
[pr-attribution.md](pr-attribution.md), written from that side.)
A closed-circuit repo may instead set `pr.source_attribution: true` to embed
a hidden marker containing the raw source worktree, machine, session, and
head SHA -- this must stay off for a public repo. Setting
`pr.source_attribution: false` opts fully out of any marker (the anonymous
opt-out). Useful flags: `--no-open` (push only),
`--no-attribution` (suppress a configured marker), `--body`/`--body-file`,
`--repo owner/name`. If the provider call fails the branch is still pushed, and
the result carries `pr_open_error` so you can fall back to Steps 2-3 below. A
repo **without** provider credentials configured uses the manual flow unchanged.

**Manual PR metadata is still the author's responsibility.** If another tool
opens the PR, prepare its UTF-8 description from the managed source worktree
with `<agent-worktrees catalog argv[0]> attribution-prepare --body-file
authored-pr.md`, save the prepared output, and pass it to the existing
provider's description update flow. The command emits a marker when no body
file is supplied and does not mutate remote PRs. See
[pr-attribution.md](pr-attribution.md) for format and optional adopter tags.

When a repository's required CI rejects missing source attribution, repair the
description rather than adding a dummy commit or bypassing its PR tools again.
If the preparation command is not yet installed, append the documented source
comment using the actual assigned public codename from worktree creation or
listing. Never invent a source, expose raw identifiers, or silently weaken an
intentional opt-out. Ask the operator when no publishable source exists.
Agents must not apply or remove operator-only policy labels to bypass CI.

**Branch-name leak class.** The hidden marker above is not the only surface
that can carry a private identifier -- the PR head's *branch name* is public
too. Whenever `pr.source_attribution` isn't exactly `true`, `create-pr` hard-
blocks (never warns) publishing an effective head -- however resolved: the
scheme default, an explicit `--branch`, an existing-PR reuse, or a rendered
`pr.head_pattern` -- that contains the raw worktree id, the machine name
(case-insensitively; checked against both the live and originally-recorded
machine identity), or an unresolved `{machine}`/`{worktree_id}` template
marker. `push-changes` enforces the same block on every re-push (both the
snapshot and refspec publish paths), since it republishes a worktree's
recorded PR branch directly -- including one set via `set-pr --branch`, or
one that predates this guard. Run
`<agent-worktrees catalog argv[0]> attribution-audit` to check a repo's configured
`head_pattern` for this risk ahead of time.

`push-changes` and an idempotent `create-pr` re-run publish the final pushed
head as a dedicated hidden PR comment. Consumers use the newest source marker
across the initial body and managed comments. Mutable attribution never
replaces the authored PR description.

### Reconciling an owned PR after its base moves

Normal review updates are incremental: `push-changes` and a repeated
`create-pr` do **not** rebase or squash a live PR themselves. When reconciliation
is necessary, use `git sync`; if it aborts on conflicts, resolve in the owning
worktree with `git rebase <remote>/<default>` and `git rebase --continue`, then
publish with `push-changes` (or repeat `create-pr`). Inspect and validate the
resolution before publishing.

A non-ancestral update is authorized only for a unique, live, tracked private
`pr/`, legacy `feature/`, or contributor `user/` head carrying this worktree's
suffix/codename or freshly proving its previously attested custom-head ownership.
The current checkout must be its recorded `worktree/<id>` branch (or that
same tracked private head in the legacy checked-out-feature flow). An exact
legacy bare-id record resolves to its canonical `worktree/<id>` branch.
Synchronization selects the matching live PR when a private head is checked
out, and the live active PR on the canonical owning branch. Terminal-only
records and unrelated branches still receive local recovery refs but no PR
rewrite checkpoint, and cannot replace an existing valid PR checkpoint.

Before `git sync` changes HEAD, it preserves the committed local tip and the
recorded published tip under unique worktree-scoped
`refs/agent-worktrees/recovery/...` refs. These keep the objects alive even
after reflog expiry or garbage collection. The command prints the original
full commit id and `git switch --detach <id>` to revisit it without resetting
uncommitted work. Repeated syncs retain separate refs, not a shared stash or a
single overwritten backup. If preservation fails, nothing is rebased.

The worktree's `agent-worktrees-pr-recovery.json` binds those retained objects
and the completed supported sync to its original PR association. That explicit
operation checkpoint permits publication of its result and ordinary feedback
commits without requiring intact rebase journals. Missing legacy base/patch
metadata is reconstructed from the pinned commit graph, not manually edited.
Preparation writes a separate pending point. The last completed checkpoint is
replaced only after a successful synchronization result is durably recorded;
a conflict, abort or checkpoint-write failure preserves that last completed
authority even after its rebase journals expire.
Completed points are retained per PR identity, so synchronizing another live
PR also cannot overwrite an older PR's valid authority.
Before rebasing, the source branch and HEAD must still match the recovery
snapshot. At completion, the checkpoint captures the exact native rebase
transition with that original tip and pinned target (or an unchanged no-op),
not an arbitrary later HEAD. Native journals are needed only at this immediate
operation boundary; the durable checkpoint does not require them to survive
until publication.
The first backed sync requires the pre-sync source to contain the saved
published tip. Repeated syncs may continue a prior completed backed result;
they retain its original lineage under a separate ref. An unrelated reset
before sync preserves recovery refs but refuses before rebasing; backups are
not a way to launder arbitrary replacement history into implicit authority.
Changing the PR association or resetting to an unrelated tip invalidates the
checkpoint. A backup is recovery, not authority to change a destination or
replace somebody else's remote work.

For a manual rebase without a supported-operation checkpoint, both Git
rebase journals must bind the original source tip, completed replay, and newer
default-branch base. The published patch is reconstructed from the recorded
base/head objects, and every source patch must remain in order or already exist on the
new base. A content-changing conflict resolution additionally requires an
explicit sequencer `continue` and a one-to-one replay with unchanged original
author and full commit message; a journal alone never authorizes dropping work.
Ordinary feedback commits may follow the completed replay.

Successful `git sync` checkpoints this verified evidence in the worktree's own
Git metadata (`agent-worktrees-pr-rebase.json`). An earlier supported manual
rebase can recover the same proof at publication from intact Git journals.
The checkpoint is audit evidence, **not** a reusable rewrite token: publication
re-verifies the current source, pins its exact object, runs real pre-push hooks,
and retains the exact **last-observed remote head** lease. Fetching a reviewer
update never silently advances that lease. After a successful rewrite, tracking
refreshes the base, head and patch together.
If an earlier source push reached the remote but its acknowledgement was lost,
a later rebase can leave the saved lease behind. Recovery never adopts today's
tip blindly: the Git remote and provider must independently agree, and that tip
must descend from the saved head and be contained in the verified pre-rebase
source. The provider must still report the same live PR number, source ref,
full source repository identity and expected base; closed/merged or retargeted
PRs cannot be reconciled from their SHA alone.
The complete source replay is checked again and the recovered exact tip
fences the one bounded retry. The audit retains both the saved head and transport
lease; tracking advances only after successful publication. Unconfirmed tips,
concurrent commits outside that original source, changed associations or
destinations remain explicit refusals.
The destination is recorded only as a SHA-256 fingerprint; raw Git push URLs
can carry credentials and are never copied into the checkpoint.
Proof queries disable Git replacement objects, including ancestry, patch and
commit-message checks, so they inspect the raw history that is actually pushed.
Proof reads, push-destination authentication and the pinned push scrub inherited
Git repository/config selectors so all operate on the supplied checkout.
Patch IDs are diagnostic metadata only: they discard hunk locations. Authorization
reconstructs each source commit's three-way tree on its new parent with
`git merge-tree --write-tree --merge-base` and requires the actual replay tree.
Only a genuine conflict with an explicit sequencer continuation may change it;
an unchanged reconstructed tree proves an already-applied source commit. Exact
source commit objects already reachable from the new base also need no replay,
even when a stale recorded base includes upstream changes later edited again.
This ancestry check uses the original new base, never later replayed source work
or patch-ID equivalence. Empty upstream commits in a stale saved-base range
need no patch ID only when their raw trees match their sole parent's tree and
their exact commit objects are ancestors of that pinned new base. Source-owned
empty commits and merge commits remain unsupported, even if a rebase drops them.
A Git version without this plumbing capability refuses recovery explicitly.
Older runtimes could leave the cached patch ID behind after an incremental
push; recovery records both that cache and the reconstructed published patch,
but authorizes only the freshly verified full source replay, never the cache.

Generic pushes remain ancestry-guarded. Shared/default/protected heads,
arbitrary refspecs and untracked/custom non-private heads fail closed.
Without a completed backed sync, resets, amends, unexplained changed patches,
dropped work and missing/expired replay journals still fail closed. A backed
operation authorizes its recorded result, not a later unrelated rewrite.
Refusals identify the actual failed condition. Do not bypass the refusal:
inspect the original source and remote history, recover a verifiable replay,
and re-run validation. There is no blanket rewrite flag.

> **Trust the result -- do not open a second PR.** When `create-pr` returns
> `pr_opened: true` (or any `number`/`url`), the PR is already open and recorded
> -- **skip Steps 2-3 entirely**; opening another PR yourself produces a
> duplicate. This applies to re-runs too: a re-run on an already-pushed branch
> **surfaces the existing PR's number/url** (and opens a still-pending PR),
> rather than silently succeeding with no PR. Only fall back to Steps 2-3 when
> the result carries a `pr_open_error`, a `pr_open_skipped` (`--no-open`, or
> `pr.auto_open` is off), or no provider creds are configured. (After `--no-open`,
> re-running `create-pr` without it is the alternative where auto-open is on.) A run that pushed the branch but opened no PR
> says why in `pr_open_skipped` (`--no-open`, or `pr.auto_open is off`), and its
> reminder reads `no PR opened` (state `pushed`), never `PR created`: there is
> nothing to review or merge until the PR is opened and recorded.
> A `--dry-run` reads `dry run: nothing was pushed or opened` (state `dry-run`)
> and says it *would* push.

> **Never run `create-pr`/`push-changes` for the same worktree from two
> actors at once -- not even a delegated sub-agent "helping" with the exact
> PR you're already driving.** The initial `create-pr` squashes and rebases
> IN PLACE on `worktree/{id}`'s own checkout; later publication uses that same
> checkout, even though ordinary updates preserve its history. A second
> actor (a spawned sub-agent given the same worktree path, or a second session
> bound to it) committing, stashing, or pushing concurrently corrupts the
> other's in-flight edits invisibly -- a mid-flight multi-step edit can land
> half-applied with no error, and commits already safely pushed to an open PR
> can vanish from `git log` the moment the other actor's own `create-pr` run
> rewrites past them, with nothing to suggest why. Confirmed live: a
> background sub-agent
> mistakenly delegated the SAME repo/worktree (rather than its own,
> independently created one) ran for 3+ hours alongside the delegating
> session, pushing its own commits and `git stash`-ing the other session's
> uncommitted work out of its way -- surfacing hours later as what looked
> like a mysterious, unexplained loss of already-committed work. One worktree
> checkout, one active git actor, always: give a delegated sub-agent doing
> PR/git work its own freshly created worktree (`<catalog argv[0]> create
> --json`), never the path you're concurrently editing in the same session.
> If you suspect this already happened, `git reflog`/`git fsck --unreachable`
> recovers a dropped commit; check `git stash list` for displaced work before
> concluding anything is actually lost.

> **A bare "failed to push some refs" from `create-pr` can hide the REAL
> reason.** The error message is built from git's stderr plus (as of the
> `agent-worktrees` push-failure-stdout fix) its stdout -- but a pre-push
> hook's own check script commonly prints its failure detail (e.g. a
> module-size-cap violation, a missing changefile) to stdout, not stderr, and
> an older `agent-worktrees` build only ever surfaced stderr, silently
> dropping that detail across every retry. If a push keeps failing with no
> further detail even after one retry, don't keep guessing or retrying
> blindly: run the repo's own pre-push hook script(s) directly (see
> `tools/hooks/pre-push`, e.g. `python tools/check-module-size.py` or
> `python tools/check-changefile-presence.py`) to see the exact, un-truncated
> failure before trying again.

> **`pr_label_error` -- PR opened, but a label didn't stick.** When `create-pr`
> opens the PR but a configured label (e.g. `auto-merge` / `source:<machine>`)
> could not be applied, the result carries `pr_label_error` (the PR still
> exists -- do **not** open another). The label apply now retries transient
> failures, so this is rare; if it appears, re-apply the named label(s) via the
> provider sub-agent rather than re-creating the PR.

### Step 2: Delegate PR creation to the provider sub-agent

*(Manual fallback -- used only when `create-pr` did **not** open the PR: i.e.
the result carries a `pr_open_error` or a `pr_open_skipped` (`--no-open`, or
`pr.auto_open` is off), or no provider creds are configured. If `create-pr` already reported `pr_opened: true` /
a `number`, do not run this step -- the PR exists.)* The CLI does **not** call
any provider API in this path -- you do, via the matching sub-agent. Read the
provider and route accordingly:

| Provider | How to create the PR |
|----------|----------------------|
| `gitea` | Use the **gitea** sub-agent (Task tool, `agent_type: "gitea"`) to open a PR for the pushed feature branch into the default branch. |
| `github` | `gh pr create --head <feature-branch> --base <default-branch>` via the shell (or a GitHub sub-agent). |
| `azure-devops` | `az repos pr create --source-branch <feature-branch> --target-branch <default-branch>` via the shell. |

Enable auto-merge if the workflow calls for it -- that is a provider-side
action you request, not a CLI flag.

### Step 3: Record the PR metadata

After the sub-agent returns the PR URL and number:

```
<agent-worktrees catalog argv[0]> set-pr --url <URL> --number <N>
```

Inspect tracked PR state any time with
`<agent-worktrees catalog argv[0]> pr-status [--json]`.
Add `--all` to list every tracked PR (serial/parallel), not just the active
one. When a worktree tracks several PRs, `set-pr` updates the **active** PR by
default; target a specific one with `--pr <number>` or
`--select-branch <branch>`.

### Draft PRs (`--draft` / `pr-ready`)

Open a PR as a **draft** when you want it visible (a shareable URL, or a place to
iterate with `push-changes`) *before* inviting review:

```
<agent-worktrees catalog argv[0]> create-pr --draft --title "..."   # opens as a DRAFT
# ... iterate: edit -> commit -> push-changes ...
<agent-worktrees catalog argv[0]> pr-ready          # draft -> ready-for-review
```

`--draft` uses the provider's **native** not-ready-for-review state, and
`pr-ready` clears it. *How* a provider encodes a draft is an implementation
detail the CLI hides:

| Provider | Draft encoding | `pr-ready` |
|----------|----------------|------------|
| `github` | native draft flag (`gh pr create --draft`) | `gh pr ready` |
| `gitea` | a `WIP:` title prefix (Gitea ≤ 1.26 has no draft boolean; the API's `draft` field is derived from it) | strips the WIP prefix by editing the title |
| `azure-devops` | not supported here | reports unsupported |

**`pr-ready` is an un-draft verb, not a merge signal.** It performs exactly one
transition — **draft → ready-for-review** — and states it explicitly. It does
**not** grant merge consent; that stays with `pr-merge` (a separate,
post-approval transition). `pr-ready` **errors** when the PR is not a draft (a
no-op never reports success). Whether an auto-reviewer reviews a draft depends on
the review backend — many skip drafts until the `ready_for_review` transition
that `pr-ready` triggers, so un-drafting is what invites review.

> **Deprecated `--hold`.** `create-pr --hold` is retained as an alias for
> `--draft`. The old model opened a PR carrying a `do-not-merge` label (a
> merge-only hold); that is retired in favour of native draft state. For a legacy
> PR still carrying a `do-not-merge` label, `pr-ready` removes it as a
> backward-compat transition. Prefer `--draft`.

### Multiple PRs per worktree (serial & parallel)

One worktree can track more than one PR -- recorded as a `prs:` list in the
tracking YAML, each entry self-describing (its own `state`, `branch`, target
`repo`, timestamps). The **active** PR (what no-selector commands target) is
the most recent non-terminal (open/creating) PR, or the most recent overall
when none are live.

- **Serial (the common case):** land a PR, then start the next change in the
  same worktree. Once the first PR is merged, just run `create-pr` again --
  it appends a fresh PR with a new branch and a current base, never reusing
  the merged branch. Works even when the prior PR was merged externally:
  `create-pr` reconciles the tracked PR's state against the provider first.

  **After a PR merges, immediately pull the worktree forward -- this is the
  standard, expected post-merge move, not an optional cleanup.** The moment you
  confirm your PR landed, rebase the worktree branch onto the updated default
  branch with:

  ```
  <agent-worktrees catalog argv[0]> git sync
  ```

  It drops the just-merged (squashed) commits as already-applied and keeps any
  newer local work, so you continue *on top of* the merge rather than starting a
  fresh worktree. See the **`git-collaboration`** skill.

  **Confirming the merge is built into `pr-status`.**
  `<agent-worktrees catalog argv[0]> pr-status`
  reconciles the active PR against the provider before reporting, so a PR merged
  externally (e.g. via the `auto-merge` label, bypassing `finalize`/`pr-watch`)
  shows `state: merged` instead of a stale `open` -- this is your authoritative
  "did my PR land?" check. When it has landed **and** the worktree is not yet on
  top of the updated default branch, `pr-status` flags it for you:

  ```
  "pull_forward_recommended": true,
  "pull_forward_command": "agent-worktrees git sync",
  "next_action": "Active PR #N is merged. Pull this worktree forward: ..."
  ```

  Treat `pull_forward_recommended` as a directive: run `git sync` straight away.
  Because the PR squashes your work into a single commit, the rebase usually
  reconciles cleanly; if it does hit a conflict the rebase auto-aborts and tells
  you to resolve it by hand -- do so, then re-run. (If the worktree is dirty,
  commit or stash first, as the `next_action` note will say.)

> **The operator owns local worktrees -- an agent never creates one as a
> *continuation* of its own work.** Reusing the current worktree (sync forward,
> keep going) is the *only* way to advance serial work, the next stretch of an
> effort, a follow-up PR, or a finalized worktree. **Do NOT run
> `<agent-worktrees catalog argv[0]> create`** for any of these; from inside an agent session,
> create a worktree only when the **operator explicitly requests** it.
> - **Handoffs are in-place.** A context handoff continues in the **same
>   worktree** via a **new session** -- the handoff prompt must **never** tell
>   the next session to "create/build on a fresh worktree." (See the
>   **`context-handoff:context-handoff`** skill.)
> - **Cross-machine `agent-bridge` delegation is the exception that proves the
>   rule:** dispatching genuinely *parallel* work to another machine implicitly
>   provisions a remote worktree *bound to the host worktree* -- that is expected
>   and fine. The ban is only on new worktrees used as *continuations* of the
>   current line of work.
- **Parallel:** keep one PR open and open another from the same worktree with
  `create-pr --new`. Address a specific one with `push-changes` (from its
  feature branch) or `set-pr --pr <n>`.
- **Cleanup safety:** a worktree with any **open** PR is never reaped by
  cleanup, even if its current HEAD's content is already on the default branch.

### Iterating on review feedback (keep-alive disposition)

To address feedback in the **same** worktree: edit, commit on `worktree/{id}`,
then update the PR branch with:

```
<agent-worktrees catalog argv[0]> push-changes
```

In PR mode `push-changes` updates the PR head, never the default branch. Feedback commits
ride on `worktree/{id}` (create-pr leaves HEAD there); `push-changes` preserves
that published PR tip and then publishes the newer commits incrementally per
scheme — under **refspec** (default) it pushes `worktree/{id}` to the
provider-resolved PR head ref (`pr/{slug}-{suffix}` for non-Azure-DevOps repos;
`user/{username}/{slug}-{suffix}` for Azure DevOps); under **snapshot** it
snapshots the local publish branch (`feature/{slug}-{suffix}` by default;
Azure DevOps still defaults to `user/{username}/{slug}-{suffix}`) to the new
tip and pushes that.
Either way HEAD stays on
`worktree/{id}` — just commit there and run `push-changes`. (A worktree still
checked out on a legacy feature branch is accepted too and pushed as-is.) It
does not create a PR; it updates the existing one.

Ordinary `push-changes` accepts only incremental publication or the bounded
source-owned replay proof described above; an arbitrary squash or rewrite
does not inherit that permission. To publish an **intentional local rebase
or squash** outside that proof, run
`<agent-worktrees catalog argv[0]> push-changes --rewrite-pr`.
This is not an automatic rebase: it publishes your current committed tip
as-is, under either head scheme, using the **previously recorded full head
SHA** as an exact remote lease. It never fetches a new lease to overwrite
someone else's newer work. A moved/deleted remote head requires inspection
and reconciliation, not blind retry.

If a verified rebased publication reached the remote before its transport
acknowledgement was recorded, a later update can recover without rewriting
that remote again. The source-owned replay proof and unchanged open PR,
repository and head association must still verify. When the independently
confirmed remote head is already an ancestor of the pinned source commit,
recovery uses normal non-forcing transport, with the usual hooks and deadline.
Unknown provider evidence or an unrelated concurrent update remains a refusal.

The opt-in requires the owning `worktree/{id}` checkout, persisted successful
`create-pr` publication ownership, and an open, exclusively tracked private
head ending in that worktree's canonical creation suffix. Ownership also binds
the full destination attested by publication, for origin as well as a fork;
another destination with the same slug and tip does not inherit permission.
An explicitly named head from `create-pr --branch pr/custom-name` also qualifies
without the generated suffix when the successful publication already attested
the exact worktree/branch and destination. The existing worktree PR ledger owns
that branch-to-provider/repository/PR mapping; there is no parallel mapping
store. Follow-up `push-changes`, watch, rebase and completion reuse the recorded
branch. `set-pr --branch` records an association but does not manufacture
publication ownership. For a custom head, rebase/rewrite publication freshly
checks the same numbered open PR, head ref, expected full head SHA, destination
identity, remote tip and exclusive ledger ownership. Missing, foreign or stale
evidence fails closed; suffixes remain the default naming convenience, not the
only proof of ownership.
Suffix-free `feature/*` remains the coordinated shared-branch namespace and
never qualifies for this exception, even when its host opened an attested PR.

Legacy feature-branch checkouts, unowned `set-pr` associations, shared/default
and release branches, missing leases, changed destinations and dirty trees
are refused. Normal pre-push hooks, attribution and head tracking remain
active. Rewrite authorization strictly scans every adopted project in the
selected installation, matching the full destination and head ref rather than
project names. Malformed or unreadable registry/record state and an ambiguous
same-ref destination refuse the rewrite. Legacy destinations are resolved
against each owning project's remote configuration, not the caller's remote
name. A tracked provider/configuration mismatch is refused before credentials
are resolved. An installation-scoped authority guard
excludes reassignment and new ledger registration until the push finishes.
First/incremental pushes and provider creation acquire that same authority
before external mutation and retain it through the fresh tracking transaction.
When finalization is also needed, its lock is acquired first and released last;
authority contention therefore fails before a remote head or provider PR changes.
Manual `set-pr` corrections wait for that guard; other association writers
already holding a record lock fail immediately and must retry, never deadlock
against publication. Ordinary Picker stamps with unchanged PR authority do not
wait behind network I/O. The published source
is pinned from the validated owning branch, never a later mutable `HEAD`.
Review the rewritten diff and obtain current review before merging.

PR observation reconciliation first attempts its ordinary record transaction.
On publication-authority contention it releases the record lock, waits within
the configured deadline-derived publication lock budget, then reloads and
revalidates the exact PR association before persisting the observation. Provider
reads happen outside this wait; best-effort background reconciliation skips a
contended write rather than waiting. Fenced finalization also uses this
nonwaiting mode: its outer record lock must never wait for publication
authority. Foreground reconciliation refuses to wait while any outer record
sidecar remains held. Unchanged observations need no authority wait.

If the budget expires, `pr-status --json` returns a nonzero exit with
`error_code: pr_authority_busy`, `retryable: true`, `retry_after_seconds: 2`
and `reconciliation_pending: true`, retaining available tracked/live status.
This is local bookkeeping contention, not evidence of a failed review or a
stale lock. Agents should wait at least the indicated delay and retry the same
status command in the same worktree without asking the operator. If contention
persists, retain the obligation and defer a later retry instead of spinning.
Do not repeat a push or merge to reconcile a completed effect, delete a lock,
terminate its owner, or bypass ownership checks.

Persisted provider-native `active` PR states remain live for selection and
ownership refresh, as well as explicit rewrite admission; `completed` and
`abandoned` remain terminal. Classification does not require rewriting a legacy
ledger first. Explicit rewrites also retain the configured publication deadline
and its matching lock budgets when integrating with newer deadline support.

Authority admission revalidates the exact selected PR (including a checked-out
legacy sibling), not merely the active entry. Every admitted publication lock
uses the configured deadline-derived wait. Project registration and removal
hold installation authority across registry read/mutate/write, so concurrent
registration cannot hide another project's ledger. A legacy empty provider may
be backfilled only to the configured provider during the same verified
publication transaction; mismatches remain refused.

For an attested legacy open PR without publication ownership,
`push-changes --rewrite-pr` reconciles ownership in the same command, even
after rebasing or squashing its own commits. The flag is the host's explicit
confirmation of intentional rewriting; automatic replay proof is not required.
Compatibility requires a numbered
provider PR and URL, and its previously saved full destination identity
or an HTTPS provider PR URL identifying that same repository and PR number.
The provider and remote must both still hold the exact saved lease; the
usual owning-worktree, private-ref and installation-wide exclusivity checks
remain mandatory. Ownership is persisted only after those checks, without
switching to or re-publishing the old tip. A nonzero metadata generation alone
does not disqualify the owning host. Such a legacy record must additionally match
the provider's current numbered PR, source branch, source repository and saved
head exactly before ownership is reconciled. Partial ownership or missing
destination evidence still requires explicit reconciliation. An inconsistent
PR identity or branch correction remains refused; a matching-looking suffix is
never proof by itself.

#### Routine publication reconciliation

Normal `push-changes` or `git sync` reconciles a provider/ref-agreed head that is
an ancestor of the pinned local source, with ordinary exclusive ownership and
no override. An accepted backed-sync result or verified non-forcing
`source-lineage` continuation does not require new operator approval. A stale
saved head on an older lineage does not make the latter a history rewrite.
Missing acknowledgement or a missing old replay checkpoint alone is not a
reason to ask for "publication recovery" or "exact-head recovery" approval. See
[routine publication reconciliation](../../../docs/cli-reference.md#routine-publication-reconciliation)
for the admission, retention, non-forcing transport and refusal contract.
For an intentional rebase or squash of the host's own private PR, the existing
`push-changes --rewrite-pr` command is the host's intent confirmation under its
normal delegated task, not a request for another operator approval. Its owning
worktree, exact saved/live lease and exclusive-destination checks must still pass.

#### Operator-confirmed break glass

Prefer source-owning CWD when available. If the original context or historical
ownership/replay proof cannot be recovered, use the selected recovery worktree
and project configuration explicitly:

```text
<agent-worktrees catalog argv[0]> push-changes <recovery-worktree-id> --config <project-config> --break-glass-reason "<reason>" --json
```

The unconfirmed invocation refuses and previews the exact existing PR,
destination fingerprint, immutable source commit, original saved head and
current provider/ref-agreed expected remote commit, without changing Git refs,
tracking, audits or the remote. Saved publication authority, action and any
validated backed-sync proof are also fingerprint-bound. **Stop for real
per-PR operator sign-off** on that previewed operation. A reason, discovered
flag, broad policy approval or previous refusal does not authorize the push.
Only then repeat with the same inputs and
`--confirm-break-glass <preview-fingerprint>`. Agents must not generate consent
on their own. A changed source (including an amend), destination, reason,
saved publication, expected head, action or evidence requires a new preview
and sign-off.

The preview prefers `settle-publication` when an existing completed backed-sync
checkpoint independently proves its exact result is already the current
provider/ref-agreed head. Confirmation then settles only local bookkeeping:
no push, PR attribution edit or publication of later local work. Equal SHAs or
patches without that checkpoint are not proof. Otherwise `rewrite` requires
explicit confirmation of the exact source and agreed live CAS lease. The
original saved lease is retained as evidence, never silently replaced before
approval in this override flow; outside routine backed reconciliation, normal
publication and ordinary `--rewrite-pr` still refuse it.
Corrupt, unreadable or dangling optional checkpoints mark settlement evidence
`unusable` in the preview and audit. They disable settlement, not an explicit
rewrite preview; their bytes/available file identity and referenced-ref state
remain bound to confirmation and are never silently repaired.

The exception waives historical original-owner, suffix and replay proof, not
provider authentication, exact source/destination validation, an unchanged
full remote-head lease, protected/default/release refs, committed clean source,
serialization, real hooks or the normal new-head review/merge gates. A known
different PR sharing that destination ref still refuses. Unavailable historical
ledgers and duplicate historical associations to the same PR are reported in
the preview and private audit, never silently treated as ownership evidence.
The approved commit object is pinned through transport even if its local branch
moves later; no opportunistic lease refresh or implicit exception exists.

Before either action, the command retains the approved source and actual agreed published
tip at reported worktree-scoped `refs/agent-worktrees/recovery/...` refs. They
survive reflog expiry and garbage collection while the refs and repository
exist; do not remove them or clean up the worktree until the recovery is
reviewed, merged and no rollback is needed. Inspect without mutation using
`git show <reported-ref>` or `git diff <published-ref> <source-ref>`. To revisit
a retained commit, first preserve current work and obtain approval for checkout
changes, then use `git switch --detach <reported-ref>`. Restoring local content
does **not** restore the provider head: any remote rollback needs its own new
preview, current exact lease and per-operation approval. No plain force push.

Private `agent-worktrees-pr-break-glass-<id>.json` audits live in the selected
worktree's Git metadata, with original saved publication evidence,
confirmation basis, reason, retained refs and
available observed invoker/session attribution. Unknown provenance is recorded
honestly; the data is not proof of authenticated identity and is not
automatically published. `succeeded` means transport or the explicitly named
bookkeeping settlement succeeded, not that the PR merged. If tracking or audit
persistence fails after transport, the command
reports the published remote head and reconciliation need, not success or a
safe retry. Read provider/remote evidence before repairing bookkeeping.

Ownership and each changed publication lease are persisted through fresh,
generation-bound metadata transactions so an older unrelated save cannot erase,
resurrect, or regress them. Provider-open identity advances that generation too.
Attribution-only first-touch stamps may proceed during I/O; successful publication
merges those stamps into its fresh transaction after rechecking the unchanged
authority, rather than losing the new lease to an unrelated revision increment.
Full-record saves retain the authority snapshot captured on load, so a newer
attribution-only stamp cannot discard a concurrent lifecycle transition.
Legacy stable-ID backfill is not itself an authority change. A genuinely newer
authority correction still wins over a stale save. Public `create-pr` contention
returns the normal human or JSON error contract rather than an uncaught timeout.
An unleased provisional first publication can retry only while its destination
ref is absent, using an atomic absent-ref lease. If a failed transport may already
have created the ref, inspect and reconcile that ambiguous outcome; never adopt
its new tip as implicit rewrite authority. Short IDs, relocated
worktree paths, and JSON responses use the selected project's ledger.
Reusing a confirmed-terminal PR's legacy feature branch preserves that prior
PR's saved lease only when its recorded destination is still attested; it does
not refresh the lease from today's remote tip.

### Bounding review/fix rounds (`pr rounds`)

An automated reviewer can keep finding one new edge case per round, so an
unbounded fix -> push -> re-review loop can run for many rounds without
converging. After **each** new review from the bound reviewer lands on your
head, and before you start fixing it, run:

```
<agent-worktrees catalog argv[0]> pr rounds [<owner/name> <n> | <n> | <worktree-id>] [--reviewer <login>] --json
```

`--reviewer` defaults to GitHub Copilot's review account. When the repo's
bound reviewer is anything else (`pr.reviewer: agent:<name>` or `external`),
pass that reviewer's account login: otherwise its reviews aren't counted and
every read shows zero rounds.

Act on its exit code:

- **0** (`continue` or `done`) -- the guard doesn't stop the loop. Whether to
  run another round is still the target repo's own stopping policy (for
  example, a repo may say not to spin a round only for low-severity findings
  once its verdict condition is met): the finding count is advisory and
  carries neither severity nor whether a finding was already dismissed. On
  `done` there is nothing left to fix, so check `pr bar` and merge when it is
  met.
- **20** (`plateau`) or **21** (`round_cap`) -- **stop starting rounds.** The
  loop isn't converging; another round won't fix that. Look for the
  lower-level cause the findings share, or bring in a person. If this work
  runs as an agent-dispatch task, post a steer card on it rather than asking
  in chat, so the decision shows up wherever the operator looks:

  ```
  <agent-dispatch catalog argv[0]> card set <task-id> \
    --title "PR <n>: review loop stopped (<verdict>)" \
    --status "Needs a decision on the remaining findings" --link "<PR url>" \
    --body @rounds.md \
    --request-input "decision:choice[Rethink,Run more rounds,Stop here],notes:textarea"
  ```

  where `rounds.md` carries the guard's `reason`, its `trend` (findings per
  round) and the remaining findings. Resume only on the operator's answer.
  The answer steers the review loop only: it never waives the repo's merge
  gates (its verdict rules, required approvals, `pr bar`).
- **12** (`unknown`) -- never permission to continue, and never a stop. Read
  its `reason`: a read that moved under it (the head moved, the PR changed)
  is transient, so re-run once. Anything else (the reviews couldn't be read,
  a provider without this read, the same `unknown` again) is not going to
  clear by retrying: stop and escalate as for a plateau, with the reason.

`--max-rounds` and `--plateau-passes` override the defaults (6 and 3) when the
operator sets a different budget for this PR.

### Finalizing a PR-mode worktree

```
<agent-worktrees catalog argv[0]> finalize
```

**`finalize`'s HEAD-reset behavior depends on `pr.strategy`; it is never a
blanket "decoupled from merge."** The safe default, `keep-alive`, requires
the PR to have **merged** (or the branch's content to already sit on
`origin/<default>`) before finalize will proceed -- an open-but-unmerged PR
blocks it, by design, so a worktree can never tear itself down while it is
still the sole party positioned to drive that PR to merge. Only the
explicit, operator-approved `detach` opt-out accepts "the feature branch is
safely pushed" as sufficient on its own, without requiring a merge --
finalize tears down the worktree and local branches but **leaves the remote
feature branch intact** (it backs the open PR), for asynchronous review to
land later. If there are unpushed commits, finalize blocks either way and
tells you to run `push-changes` first.

Every code path that moves a worktree branch's HEAD off unmerged commits
(`finalize`'s own pointer-reconciliation pass, and `pr-complete`'s
post-squash-merge realignment) is independently gated on the branch's
content being **already confirmed present on upstream** before it resets or
rebases anything -- never on an assumption, and never while real unmerged
work would be discarded. `detach` is the only strategy where finalize itself
accepts an open, unmerged PR as sufficient to proceed; every other path
requires actual merge (or upstream-equivalent content) first. See the
`pr-merge-obligation-gate` effort (`efforts/active/pr-merge-obligation-gate/`)
for the audit that confirmed this and the further structural (obligation-
claim) and guidance defenses layered on top of it.

### Recovering a PR after teardown (detach disposition)

If a finalized PR later needs more work, there is **no special resume
command**. Start the normal `create` workflow for a fresh worktree, then use
your provider git-ops skill to fetch the surviving remote feature branch and
re-establish the rebase chain. The CLI stays provider-agnostic; recovery is
ordinary git owned by you.
