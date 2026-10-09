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
<agent-worktrees catalog argv[0]> get pr-profile      # direct | pr-human-merge | pr-agent-merge
<agent-worktrees catalog argv[0]> get pr-enabled      # "true" or "false"
<agent-worktrees catalog argv[0]> get pr-required     # "true" -> direct-to-default-branch is blocked
<agent-worktrees catalog argv[0]> get pr-provider     # gitea | github | azure-devops (empty in direct mode)
```

The three profiles (derived purely from config -- provider-generic, no network):

| Profile | Config shape | How work lands | Verbs that apply |
|---------|--------------|----------------|------------------|
| **`direct`** | `pr.enabled: false` | `finalize` lands to the default branch | *(none -- no PR flow)* |
| **`pr-human-merge`** | enabled, **no** `automerge_label` | PR-gated; a **human** approves + merges | `create-pr`, `pr-watch`, `pr-status`, `pr-nudge`, `pr-complete` -- **not `pr-merge`** |
| **`pr-agent-merge`** | enabled + an `automerge_label` bound | PR-gated; the author **signals merge consent** after approval; the review gate merges | the full `pr-*` family, including `pr-merge` |

**Applicability is self-describing.** `pr-status` prints the profile (`flow:`
line + a `flow` block in `--json`), and `pr-merge` **refuses** on a repo whose
profile is not `pr-agent-merge`, naming the reason and pointing at the right
process (human merge vs a stale anchor). So when a verb reports it does not
apply, believe it and follow its pointer -- do **not** hand-merge, escalate to
an admin, or invent a flow.

- On a **`pr-human-merge`** repo: open the PR (`create-pr`), address review with
  `pr-watch`, then **a human approves and merges** -- consult the repo's
  `CONTRIBUTING` / related narrative for who merges. `pr-merge` does not apply.
- On a **`pr-agent-merge`** repo (e.g. an auto-reviewer + auto-merge repo where
  review typically lands in minutes): after approval, `pr-merge` signals consent
  and the gate merges. This is the flow the sections below assume.
- If `pr-merge` reports "no automerge_label" on a repo you **expected** to be
  `pr-agent-merge`, suspect a **stale anchor** first (the binding landed on the
  default branch but this checkout hasn't pulled it): refresh the anchor
  (`git sync` on the anchor / the project's update command) and retry -- do not
  fall back to a hand-merge.

### "Request auto-complete" -- the same shape across providers (incl. Azure DevOps)

`pr-merge` is **"request auto-complete of this PR"**. *How* the provider honors
that is an implementation detail, so ADO is the **same `pr-agent-merge` shape**
as gitea/github -- not a special case:

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
  wait briefly for CI and any non-blocking automated review, check real
  status rather than assuming, rebase if the head goes stale, then merge
  once the repo's own gates allow it. **`pr-status`/`pr-watch`'s raw
  `eligible: false` / `reason: "not yet approved"` pair is not necessarily
  a live blocker on this profile** — it often reflects only a
  human-approval/codeowner gate that is a documented, separate concern from
  Copilot's own (non-blocking) review verdict, and the acting identity may
  hold a live bypass right on that gate the raw fields don't represent
  (`self_merge_note`, when present in the same JSON, surfaces exactly this
  — read it before treating `eligible`/`reason` as authoritative). **Before
  trusting either field at face value, check the target repo's own
  CONTRIBUTING-equivalent doc for its documented verdict-shape and merge
  rules** — many self-merge repos (e.g. a repo whose Copilot review
  structurally never renders `Approve` on the owner's own PRs) define a
  different passing condition than "wait for Approve," and a generic
  `pr-watch`/`pr-status` field can misreport (see
  `ThomasMichon/copilot-extensions#3638`, filed after exactly this
  confusion drove a 12-round review-fix loop before an agent noticed the
  target repo's own docs already said not to wait. A later, separate
  25-round loop on a different PR was actually caused by carried-over
  review findings not clearing after being fixed — see #5183 — but this
  same verdict/bypass confusion is what then stalled *merging* that PR for
  roughly 90 minutes once the findings themselves were resolved; keep the
  two causes distinct when reasoning about either).
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
with it. Only positive, finite numbers are accepted—there is no unbounded
setting. A timeout still kills the entire push/hook process tree. See the
[configuration reference](../../../docs/config-reference.md#pr-workflow--reposnamepr-machine-local-or-in-repo).

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
`pr/` or legacy `feature/` head carrying this worktree's suffix or codename.
The current checkout must be its recorded `worktree/<id>` branch (or that
same tracked private head in the legacy checked-out-feature flow). Both Git
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
an unchanged reconstructed tree proves an already-applied source commit. A Git
version without this plumbing capability refuses recovery explicitly.
Older runtimes could leave the cached patch ID behind after an incremental
push; recovery records both that cache and the reconstructed published patch,
but authorizes only the freshly verified full source replay, never the cache.

Generic pushes remain ancestry-guarded. Shared/default/protected heads,
arbitrary refspecs, resets (even to the same tree), amends, unexplained changed
patches, dropped work, merge-heavy or missing/expired replay evidence, and
untracked/custom non-private heads fail closed. Do not bypass the refusal:
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
