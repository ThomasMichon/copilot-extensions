# Worktree — Fleet-Wide Stale/Backlog Sweep

Full procedure for investigating a **backlog** of worktrees stuck showing
`active` (or otherwise non-terminal) with no clear live owner -- typically
surfaced after a mux/daemon crash left cached liveness hints stale, or
discovered incidentally while auditing a machine's worktrees. This is
distinct from [cleanup-details.md](cleanup-details.md), which covers
resolving a single `dirty` worktree once you already know which one needs
attention; this reference covers finding and triaging *many* candidates at
once, safely.

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

Before touching anything, confirm the tooling itself is healthy and get an
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

## Step 2 -- Release the claims-ledger backlog (mechanical, safe)

A worktree's `status: active` display and its `finalize`/`cleanup`
eligibility both consult the **claims/obligation ledger** (see
[obligations.md](obligations.md)). Crashed sessions routinely leave this
ledger cluttered with claims that are already safe to clear:

```
<agent-worktrees catalog argv[0]> claims fleet-audit          # read-only inventory
<agent-worktrees catalog argv[0]> claims sweep                # dry-run: any provably-gone-and-safe claim kind
<agent-worktrees catalog argv[0]> claims sweep --apply
<agent-worktrees catalog argv[0]> claims reconcile-at-rest --apply   # any lingering at-rest claim, EXCEPT session claims (their own lifecycle -- see below)
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

- **Small session (roughly under a few hundred events / ~10 turns or
  fewer):** read it directly -- `<agent-worktrees catalog argv[0]>
  session-transcript <session-id> --json`. (The `read-session-digest`
  command from the agent-logger catalog is *not* an alternative direct-read
  path here -- it only reads an already-collated digest and does not ingest
  `events.jsonl` or create one; it's for going deeper into a digest a
  `ramp-up-session` invocation already produced, per Step 3's "Larger
  session" case below.) Cheap enough not to need delegation; just check the
  last few user/assistant messages for how it actually ended.
- **Larger session:** delegate to the **`agent-logger:session-rampup`**
  sub-agent per the `ramp-up-session` skill instead of reading the raw
  transcript yourself -- that's exactly what it exists for (absorbing a
  potentially huge transcript and returning a bounded takeover briefing
  instead of flooding your own context). Ask it for an **explicit verdict**:
  did the objective land (possibly under a *different* worktree's PR after
  a handoff or supersession -- check, don't assume the local branch is the
  only place the work could have gone), is it genuinely done, or is there a
  real open thread even though git is clean (e.g. an informational/
  diagnosis-only session whose recommendation was never acted on)?

Only after this step should you treat a worktree as safe to prune, safe to
leave silently, or in need of a decision from the operator. A `cleanup`
report of `completed`/`unused` plus a clear claims ledger is necessary but
**not sufficient** on its own -- it tells you nothing was lost; it doesn't
tell you the story resolved.

## Step 4 -- Respect the tool's own refusals

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
