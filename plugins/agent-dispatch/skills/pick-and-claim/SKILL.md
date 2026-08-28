---
name: pick-and-claim
description: >
  Dedup-safe self-dispatch for open-ended "pick something and work on it"
  prompts. Before starting freely-chosen work, atomically claim your pick on the
  dispatch queue with a subject dedup_key, so two concurrent open-ended
  agents don't land the same item. Use this whenever you (or several parallel
  sessions) are told to choose your own work.
  Trigger phrases include:
  - 'pick something interesting'
  - 'work on something'
  - 'find something to do'
  - 'grab something to work on'
  - 'self-dispatch'
  - 'claim something to work on'
  - 'pick an issue and fix it'
  - 'choose your own work'
  - 'avoid duplicate work'
  - 'pick up an effort and drive it'
---

# Pick-and-Claim — dedup-safe open-ended self-dispatch

Use the exact `argv[0]` from the agent-dispatch session command catalog for
every dispatch operation below. Replace `<agent-dispatch catalog argv[0]>`
with that path; never search `PATH` for a same-named command. In PowerShell,
invoke it as `& "<agent-dispatch catalog argv[0]>" <args>`. If the catalog is
missing, follow the single-installed-payload fallback in the `agent-dispatch`
skill and fail on ambiguity.

When you are told to **pick your own work** ("pick something interesting and work
on it", "grab an issue", "pick up an effort and drive it") — especially when
**several sessions run the same open-ended prompt at once** — you must avoid two
agents landing the **same** subject. The queue's atomic claim is how.

**The one rule: land your selection on an atomic claim keyed by the subject.**
A sweep of what others are doing only helps you *pick better*; the **claim** is
what makes your pick *stick uniquely*. Do both — sweep, then claim.

## The protocol

1. **Honor assigned work first.** Run
   `<agent-dispatch catalog argv[0]> worktree-status` before choosing a new
   subject. Resume or claim a task explicitly targeted at this worktree before
   self-selecting unrelated work, unless the operator's current request
   conflicts with it.

2. **Sweep (advisory — pick well).** Before choosing, check what is already in
   flight so you steer off obvious overlaps:
   - A **semantic search** over your work corpus / issue tracker, if one is
     available (it catches differently-worded duplicates that a substring match
     misses).
   - `<agent-dispatch catalog argv[0]> list --status queued,claimed,started,suspended` — what is already
     grabbed on the queue.
   - Active worktree **charters** (if the coordination layer exposes them, e.g.
     `agent-worktrees list --json`) — do not pick what another agent is already <!-- marketplace-isolation: allow agent-worktrees-management -->
     driving.

3. **Prefer a structured subject.** A tracked artifact (issue / PR / effort /
   vision / doc) yields an **exact** dedup key, so dedup is *guaranteed*, not
   best-effort. Fall back to a free-form `topic:` key only when nothing
   structured fits.

4. **Commit = atomic create-and-claim (authoritative).** The instant you commit
   to subject *X*, claim it in one race-free call:

   ```bash
   <agent-dispatch catalog argv[0]> create "<what you're tackling>" \
     --dedup-key "<subject-id>" --claim
   ```

   - **`claimed_by_me: true`** in the output → the subject is **yours** → start
     working.
   - **`claimed_by_me: false`** → an existing row was returned. Inspect its
     `status` and `owner`: claim a queued task with `claim --task <id>`, resume
     an active task already owned by this worktree, or treat another live
     owner's task as a lost race and go back to step 2. A terminal row is
     history, not an active claim; a reopened subject needs an explicit,
     deterministic new episode key.

   `--claim` creates the task **already claimed by your worktree in one
   transaction**, so there is no queued-and-unclaimed gap for another worker to
   slip into. (Without `--claim`, guard the gap with `--require worktree:<self>`
   then `claim`, but `--claim` is the clean primitive.)

5. **Work it, then close the loop.**
   `<agent-dispatch catalog argv[0]> progress <id> …` at phase boundaries;
   `<agent-dispatch catalog argv[0]> complete <id> --result-ref <ref>` when done. If the
   pick is an *objective* rather than a single step, make it a **durable goal**
   (`create … --goal "<objective>" --done-criteria "<when done>"`) and **loop
   toward it** — work a unit, record a progress beat, re-check the done-criteria,
   repeat — so a replacement resumes from the recorded progress rather than
   restarting (see the **`agent-dispatch`** skill § *Goal-loop tasks*). If you
   must drop it:
   `<agent-dispatch catalog argv[0]> yield <id> --exclude-self worktree` (append a "not
   me" so you are not re-offered it), or `abandon --duplicate-of <ref>` if it turns
   out to be a duplicate.

## Canonical `dedup_key` conventions

Every open-ended agent **must key the same subject the same way**, or two picks
of one subject will not collide. Namespaced `<kind>:<identity>`, canonicalized
(lowercase host/owner/repo, kebab slugs):

| Subject kind | `dedup_key` | Example |
|--------------|-------------|---------|
| Issue        | `issue:<owner>/<repo>#<n>` | `issue:acme/widget#42` |
| Pull request | `pr:<owner>/<repo>#<n>` | `pr:acme/widget#128` |
| Effort       | `effort:<slug>` | `effort:auth-hardening` |
| Vision       | `vision:<domain>/<subject>` | `vision:platform/api-gateway` |
| Doc / plan   | `doc:<repo-rel-path>` | `doc:docs/architecture.md` |
| Fuzzy topic  | `topic:<kebab-slug>` | `topic:tidy-log-formatting` (last resort) |

**Prefer a structured kind over `topic:`** whenever the pick maps to a tracked
artifact — that is what turns "as best I can" dedup into *exact* dedup. A
deployment may extend this table with its own subject kinds; the rule is
constant: a namespaced, canonicalized `<kind>:<identity>` that every picker
computes identically.

### GitHub issue binding

For a GitHub issue, search for the canonical key first. Reuse a queued task,
resume one already owned by this worktree, and stop for another live owner.
Only when no row exists, carry the same canonical identity through the first
work episode:

```bash
<agent-dispatch catalog argv[0]> create "Fix owner/repo#42: concise title" \
  --prompt "Work https://github.com/owner/repo/issues/42 end-to-end ..." \
  --source github-issue \
  --origin-ref issue/owner/repo#42 \
  --dedup-key issue:owner/repo#42 \
  --claim
```

The dispatch row is the atomic claim wherever all workers share a coordinator.
If machines use separate coordinators, also publish the reservation through the
issue tracker's claim/discussion convention and re-check the thread before
editing. That visible marker is a cross-coordinator backstop, not a replacement
for the atomic queue claim. If a closed issue is reopened after its original
task became terminal, use a deterministic episode suffix from the reopen event,
such as `issue:owner/repo#42:reopen:<event-id>`.

## Why this works (and its limits)

- **Correctness** comes from the `dedup_key`: for a subject with no existing
  row, `create --dedup-key … --claim` is a single-writer atomic op, so of two
  simultaneous first claims **exactly one wins**. Later callers get the existing
  row and branch on its status/owner rather than assuming every collision is an
  active loss.
- **Efficiency** comes from the sweep: it just reduces how often two agents pick
  the same thing in the first place.
- **Residual gap:** genuinely *fuzzy* subjects whose keys do not collide. The
  semantic sweep catches most; anything left is caught late — an agent that
  starts, then discovers overlap, `yield`s or `abandon --duplicate-of`s.

## See also

- The **`agent-dispatch`** skill — the full CLI, the eight-state lifecycle, worker
  identity, capability/affinity routing, and selector (`--require`/`--exclude`)
  matching. Its responsibility boundary distinguishes durable task-loop state
  from live agent-bridge conversation. For generic task decomposition and the
  decision to delegate at all, use
  **`delegation-guidance:delegating-work`**.
- `<agent-dispatch catalog argv[0]> create --help` — the `--claim`, `--dedup-key`, `--require`, and
  `--exclude` flags.
