# Worktree — Fleet-Wide Stale/Backlog Sweep

Full procedure for investigating a **backlog** of worktrees stuck showing
`active` (or otherwise non-terminal) with no clear live owner -- typically
surfaced after a mux/daemon crash left cached liveness hints stale, or
discovered incidentally while auditing a machine's worktrees. This is
distinct from [cleanup-details.md](cleanup-details.md), which covers
resolving a single `dirty` worktree once you already know which one needs
attention; this reference covers finding and triaging *many* candidates at
once, safely.

## Step 0 -- Define the outcome and contain recurring allocation

Record the operator's requested repositories, retention/count target, deletion
scope, and protected owners. Resolve each repository's registration and worktree
root; keep a separate baseline for each. Count actual Git checkouts, including
system and retained finalized directories, not just visible Picker rows or
tracking statuses. Reconcile Git's worktree inventory with directories carrying
Git administrative markers under the resolved roots; a cache directory is not a
checkout, and a missing tracking row is not proof a directory is disposable.
Do not satisfy a count by hiding rows, archiving unmerged WIP, or resetting work
without exact operator approval. Drive useful work through commit/publication,
or explicitly abandon superseded/rejected work under
[cleanup-details.md](cleanup-details.md)'s approval-gated procedure. Recovery
storage is not completion; retain any remaining objective with its accountable
owner. Selected planning/prototype artifacts belong to a named canonical
effort, not a generic WIP archive.

If recurring producers are growing the backlog, contain them through their
owning subsystem **before** reaping their outputs, within the operator's
authorization. For Dispatch, use `agent-dispatch:agent-dispatch` and its exact
session command catalog. Inventory local registrations, inspect native
`supervise override list`, and disable the approved exact IDs through
`supervise override disable`. Verify each applicable registration against its
persistent local mask, including relevant logical/declaration aliases. A
registration can still say `active` while disabled; a pool summary need not
include its concrete registration mask. Neither display alone proves effective
containment. Preserve unrelated machines, existing live workers, and pending
effects; disabling future admission is not cancellation of an admitted worker.

## Step 1 -- Baseline health (read-only)

These commands are **project-scoped, not machine-wide**: `handoffs-check`,
`reap-sessions`, and the `claims` commands this procedure uses (`fleet-audit`,
`sweep`, `reconcile-at-rest`) all resolve against the current project (from
cwd, or `--project <name>`). A machine has one such project per adopted repo
(`<agent-worktrees catalog argv[0]> repos list --json`) -- repeat those
commands once per project you want covered; there is no single
all-projects invocation for them. Two exceptions: `reclaim --all` targets
**every bound Copilot on the machine** (per its own `--help`) -- run it once,
machine-wide, not per project; and `claims owner <kind> <ref>` intentionally
searches every registered project for a given claim ref, which is a
different (tracing) use case than this sweep.

Before mutating anything, confirm the tooling itself is healthy and get an
authoritative read on what's actually still running:

```
<agent-worktrees catalog argv[0]> doctor --json
<agent-worktrees catalog argv[0]> handoffs-check --all --json
<agent-worktrees catalog argv[0]> reap-sessions --dry-run --json
<agent-worktrees catalog argv[0]> reclaim --all --json
```

`reclaim --all` in particular reports genuinely-live PIDs bound to a
worktree (`homing: mux`) -- treat those as **confirmed live**, not stale, no
matter how the picker/status field renders. A daemon that crashed can
self-heal (`status-monitor-restart` reports `"a current monitor already owns
the host"` when it's already back), and mux itself can reattach previously
orphaned processes over the following minutes -- re-run these checks rather
than trusting a single snapshot if you suspect recovery is still in
progress.

Keep inventory output bounded: project the fields needed to shortlist candidates
before printing full `list --json` records, then fetch exact
`worktree-status-bundle --worktree <full-id> --json` evidence only for those
candidates. Its facts are wrapped under `facts.<name>.value`, with confirmation
and observation metadata alongside the value; do not read missing top-level
fields as empty claims or known inactivity. Counts and shortlist snapshots are
not action-time authority. A truncated report or an unavailable probe means
unknown, not zero.

Build a compact candidate manifest: full ID, repository, content/PR disposition,
objective/owner evidence, claims, and next native action. Group by shared
controller, terminal task, or identical suspected noise, but keep a per-checkout
verdict. Fetch the repository and recover shared completion evidence once per
cohort instead of repeatedly scanning whole fleets or transcripts. Reuse settled
evidence until new facts contradict it; the lifecycle command still refreshes
removal safety. Run bounded cohorts, capture each result, and avoid concurrent
removals competing for the same repository lock. Independent repositories can
progress separately. Do not repeat successful mutations after interrupted output
without inspecting their resulting state.

## Step 2 -- Release the claims-ledger backlog (mechanical, safe)

A worktree's `status: active` display and its `finalize`/`cleanup`
eligibility both consult the **claims/obligation ledger** (see
[obligations.md](obligations.md)). Crashed sessions routinely leave this
ledger cluttered with claims that are already safe to clear:

```
<agent-worktrees catalog argv[0]> claims fleet-audit          # read-only inventory
<agent-worktrees catalog argv[0]> claims sweep                # dry-run: any provably-gone-and-safe claim kind
<agent-worktrees catalog argv[0]> claims sweep --apply
<agent-worktrees catalog argv[0]> claims reconcile-at-rest           # dry-run: settled claims, excluding sessions
<agent-worktrees catalog argv[0]> claims reconcile-at-rest --apply
```

Both are purpose-built as **never-wedge** operations: `sweep` is explicitly
allowed to flip a claim it can positively verify is provably-gone-and-safe
from `active` to `abandoned` (per `docs/cli-reference.md`'s `claims` entry),
and `reconcile-at-rest` only releases claims already marked settled --
**excluding `session`-kind claims**, which have their own dedicated
lifecycle (settled on finalize, released only by session deregistration) and
are deliberately left alone by this command; a lingering session claim is
not a `reconcile-at-rest` gap. Neither force-releases an unverified `active`
or ambiguous claim, deletes a folder, or touches git content -- this is
metadata bookkeeping, not a git operation, so it's safe to run before you've
finished investigating individual worktrees.

**This only clears cleanup/finalize blockers -- it does not, by itself,
change a worktree's `active` display.** The `active` state is derived from
live session/mux liveness, a separate signal from the claims ledger; a
worktree with a genuinely live session stays `active` no matter how many of
its claims you release, and a worktree with a dead session only drops out of
`active` once *both* its liveness is confirmed gone (Step 1) *and* its
blocking claims are cleared. Re-run `cleanup` (report mode) afterward -- releasing stale claims frequently
reclassifies a worktree from `active`/blocked straight to `completed`
without touching anything else.

When `doctor` identifies stale tracking status, its non-destructive `--fix`
path can reconcile that metadata. Do not add daemon-health, session-deletion,
or process-retirement flags merely because a cleanup audit found stale status.
Session conclusion/deregistration is not part of this mechanical claims pass:
perform it only after Step 3 establishes objective completion.

### Task claims, mirrors, and inherited journals are different evidence

Resolve an opaque resource through its owner before selecting a cleanup command.
A Dispatch task-generation wait is not necessarily a spawn reservation; do not
feed every `task` claim to reservation retirement or infer its type from the
reference's punctuation. Confirm the exact task/generation's authoritative
state, preserve pending/unknown effects, and use the owner's supported terminal
settlement. Retire dormant automation only within the approved scope, never by
overriding a genuinely live owner.

After settlement, verify the local ledger **and** any discovery/lease mirror.
An already-released backend resource can still have an inherited orphan-journal
row. Preview `claims cleanup <ref-or-source> --json` selectively; if it reports
an unsupported kind, record the exact residual and track the missing native
reconciliation path. Do not hand-edit the journal, relabel the resource, or
claim that zero remaining workers means zero remaining audit obligations.

## Step 3 -- Verify session *content*, not just git + claims metadata

**This is the step that's easy to skip and the one that matters most.**
Zero uncommitted changes, zero commits ahead of the default branch, and a
clear claims ledger only prove nothing is sitting *unlanded* in that
worktree -- they don't prove the session's actual objective *resolved*.
A crashed session can be perfectly git-clean and still represent a real,
unfinished thread (a diagnosis that was never acted on, a PR that got
superseded and needs someone to notice, a handoff nobody picked up). Before
concluding a stale-`active` worktree is safe to clean, or even safe to
leave alone unremarked, read what its session actually did. First find its
session id(s) -- `<agent-worktrees catalog argv[0]> list-sessions --worktree
<full-worktree-id> --json` (see [reference.md](reference.md) § Cross-Machine
Inspection) -- then:

- **Small session (both bounded event volume and few turns):** read it
  directly -- `<agent-worktrees catalog argv[0]>
  session-transcript <session-id> --json`. (The `read-session-digest`
  command from the agent-logger catalog is *not* an alternative direct-read
  path here -- it only reads an already-collated digest and does not ingest
  `events.jsonl` or create one; it's for going deeper into a digest a
  `ramp-up-session` invocation already produced, per Step 3's "Larger
  session" case below.) Cheap enough not to need delegation; just check the
  last few user/assistant messages for how it actually ended.
- **Larger session:** delegate to the **`agent-logger:session-rampup`**
  sub-agent per the `agent-logger:ramp-up-session` skill instead of reading the raw
  transcript yourself -- that's exactly what it exists for (absorbing a
  potentially huge transcript and returning a bounded takeover briefing
  instead of flooding your own context). Ask it for an **explicit verdict**:
  did the objective land (possibly under a *different* worktree's PR after
  a handoff or supersession -- check, don't assume the local branch is the
  only place the work could have gone), is it genuinely done, or is there a
  real open thread even though git is clean (e.g. an informational/
  diagnosis-only session whose recommendation was never acted on)?

Make the delegation **audit-only**, not takeover: explicitly permit ephemeral
digest collation and bounded reads, forbid repository/lifecycle mutations and
resuming the original work, and request a cited `safe candidate` / `preserve`
/ `unknown` verdict. A report-only audit can create the tool-owned
ephemeral digest; it must not edit source, settle claims, launch successors,
or remove worktrees. A few turns can still contain thousands of tool events;
turn count alone is not a transcript-size bound.

Read a valid effort binding first. If it is missing, use the bounded session
evidence to locate the canonical effort, including its archived successor:
missing binding does not prove no effort exists. Compare old summaries with
later journals, merged PRs, and the effort's actual Plan/Validation Plan.
For several child worktrees sharing one controller/effort, recover that
parent's completion evidence once, then verify each child's own landed slice
and claims. An open parent is not an automatic hold on a completed child whose
remaining responsibility was explicitly retained by the parent; a merged child
is not evidence the parent finished.

For WIP, record which outcome is actually being driven: useful work committed
and delivered, or particular superseded/rejected work explicitly abandoned with
operator approval. Neither copying it into an archive nor approving a recovery
backup resolves that choice. A canonical-effort planning/prototype artifact must
retain its purpose, owner and remaining acceptance; completed-effort and
historical-record archival are unaffected. Follow
[cleanup-details.md](cleanup-details.md) before any approved reset/discard;
the fleet count grants no destructive or ownership authority.

Only after this step should you treat a worktree as safe to prune, safe to
leave silently, or in need of a decision from the operator. A `cleanup`
report of `completed`/`unused` plus a clear claims ledger is necessary but
**not sufficient** on its own -- it tells you nothing was lost; it doesn't
tell you the story resolved.

## Step 4 -- Respect the tool's own refusals

Only after completing Step 3 for a verified completed, dormant session,
`conclude-session --worktree <full-id> --session <session-id> --state concluded`
records completion; `deregister-session --worktree-id <full-id> --session-id
<session-id>` closes its registration and session claim. Neither substitutes for
the objective evidence. Do not conclude a live owner or an unfinished relay leg
to clear a cleanup blocker.

Preview an exact candidate list with subject, PR/base landing evidence,
objective disposition, outstanding owner, and any unknowns. Obtain the
repository/operator-required approval, then remove that subset with
`cleanup --worktree-id <full-id> --json`, never a broader `cleanup --clean`
than approved. Verify the returned outcome and the expected folder/branch
absence; report partial results honestly.

For an unexpectedly retained finalized checkout, inspect the **exact** hold,
not just its old status. Registered session locks, muxes, hosted/external
execution legs, bound-session/bridge evidence, and checkout-local guidance
workers can protect different paths. A concluded session record does not prove
all of those are inactive. Read only the candidate's attributable evidence;
unknown ownership or a live worker is still a hold.

After the owner settles, a lock clears, or a supported metadata repair succeeds,
retry that candidate's normal `finalize` or non-forced `cleanup` and inspect its
result. A previous refusal need not be permanent; do not jump from it to
deleting locks, stopping processes, force removal, or repeatedly restarting the
daemon. Conversely, a successful `finalize` can leave a protected directory in
place. Confirm physical removal rather than counting the finalized status as a
deleted checkout. Treat paired siblings independently: prove each sibling's
content and custody, then use its own repository's lifecycle rather than
assuming removal of one side removed both.

Some worktree kinds are intentionally out of scope for the standard
`cleanup` flow and will say so rather than silently no-op:

```
<worktree-id>: skipped -- agent-owned bridge worktree (use the System menu)
```

Do not work around a refusal like this with raw git commands (`git worktree
remove`, manual branch deletion, etc. -- see the entrypoint's *Never
Finalize Manually* rule). It means a different subsystem (here, agent-bridge)
owns that worktree's lifecycle; retire it through that subsystem's own
mechanism, or leave it for the operator.

## Step 5 -- Ownership ambiguity is a stop, not a guess

If a worktree's `reciprocal_relation.state` (a top-level field alongside
`controllers`/`controller_findings`, not nested inside either) reads
`ambiguous`, or a `session-rampup` briefing
reveals the real owner of the work turned out to be a *different* worktree
or even a different project (a cross-repo handoff, a superseding PR opened
from elsewhere), do not decide unilaterally which side is authoritative.
Report the finding and hand it back to the operator -- see the
`tracing-claimant-graphs` skill for walking an ownership chain across
worktrees/projects when you need to trace it further.

Separate three orphan cases: untracked on-disk directories (`gc --dry-run
--orphans-only --no-reap-shells --json`), abandoned resource claims (`claims
orphans` and a selective `claims cleanup <ref-or-source>` dry-run), and missing
historical session/controller records. An already-absent directory may require
ledger reconciliation rather than filesystem removal. Unknown locks, access
denied, missing parents, and incomplete projections remain holds; never force
delete, elevate, or replace ownership to make a sweep look complete.

Likewise, an empty `handoffs-check` result is not proof that every pending baton
has a registered successor. Use `context-handoff`'s documented `check-heads`
surface when auditing that alignment. Distinguish an unconsumed continuation
from a consumed-but-unlinked baton using exact token/session evidence. Link
only proven succession through `link-succession`; if an existing predecessor
conflicts, stop and preserve the evidence. Multi-command repairs are not a
transaction: inspect the head after each mutation and stop on the first
refusal rather than running later conclusions as though linking succeeded.

## Step 6 -- Surface real findings; don't silently discard them

A session can be entirely "at rest" from a cleanup standpoint (nothing to
land, nothing blocking) while still containing a substantive, still-relevant
answer the operator hasn't acted on (a completed root-cause diagnosis, a
flagged follow-up decision). Report those findings back explicitly instead
of treating "safe to clean" as "nothing here was worth mentioning."

## Step 7 -- Track the systemic root cause once, not per-worktree

If the same pattern recurs across many worktrees on a sweep (e.g. crashed
sessions whose recorded `state` never leaves `active` even once `live`
correctly flips to `false`), that's a defect worth filing once rather than
hand-remediating the fleet every time it recurs -- see the `file-issue`
skill/`error-response` discipline (fix or track every issue, never dismiss
it as pre-existing and move on).

## Step 8 -- Verify and stop at the agreed outcome

Recount actual remaining checkouts **per requested repository**, with the same
denominator as the baseline. Recheck the approved producer masks and any pending
admissions separately from those counts. Report the preserved live/unfinished
owners, completed removals, and exact unknown/unsupported residuals with their
tracked disposition. A metadata-only audit row is not an extra checkout; a
remaining checkout is not removed merely because its claims were released.

Once the requested target is met, preserve the justified remainder instead of
expanding into unrelated owners' active efforts. Stop owned bounded helpers and
settle or explicitly transfer the sweep's own obligations. Further runtime
repairs, automatic reclamation, or payload rollout require their own scope and
authorization; a successful manual sweep does not authorize them.
