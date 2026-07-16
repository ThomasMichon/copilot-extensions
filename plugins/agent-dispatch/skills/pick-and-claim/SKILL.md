---
name: pick-and-claim
description: >
  Dedup-safe self-dispatch for open-ended "pick something and work on it"
  prompts. Before starting freely-chosen work, atomically claim your pick on the
  agent-dispatch queue with a subject dedup_key, so two concurrent open-ended
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

When you are told to **pick your own work** ("pick something interesting and work
on it", "grab an issue", "pick up an effort and drive it") — especially when
**several sessions run the same open-ended prompt at once** — you must avoid two
agents landing the **same** subject. The queue's atomic claim is how.

**The one rule: land your selection on an atomic claim keyed by the subject.**
A sweep of what others are doing only helps you *pick better*; the **claim** is
what makes your pick *stick uniquely*. Do both — sweep, then claim.

## The protocol

1. **Sweep (advisory — pick well).** Before choosing, check what is already in
   flight so you steer off obvious overlaps:
   - A **semantic search** over your work corpus / issue tracker, if one is
     available (it catches differently-worded duplicates that a substring match
     misses).
   - `agent-dispatch list --status queued,claimed,started` — what is already
     grabbed on the queue.
   - Active worktree **charters** (if the coordination layer exposes them, e.g.
     `agent-worktrees list --json`) — do not pick what another agent is already
     driving.

2. **Prefer a structured subject.** A tracked artifact (issue / PR / effort /
   vision / doc) yields an **exact** dedup key, so dedup is *guaranteed*, not
   best-effort. Fall back to a free-form `topic:` key only when nothing
   structured fits.

3. **Commit = atomic create-and-claim (authoritative).** The instant you commit
   to subject *X*, claim it in one race-free call:

   ```bash
   agent-dispatch create "<what you're tackling>" \
     --dedup-key "<subject-id>" --claim
   ```

   - **`claimed_by_me: true`** in the output → the subject is **yours** → start
     working.
   - **`claimed_by_me: false`** → a `dedup_key` collision returned someone else's
     already-claimed row → **you lost the race** → go back to step 1 and pick
     something else.

   `--claim` creates the task **already claimed by your worktree in one
   transaction**, so there is no queued-and-unclaimed gap for another worker to
   slip into. (Without `--claim`, guard the gap with `--require worktree:<self>`
   then `claim`, but `--claim` is the clean primitive.)

4. **Work it, then close the loop.** `agent-dispatch progress <id> …` at phase
   boundaries; `agent-dispatch complete <id> --result-ref <ref>` when done. If you
   must drop it: `agent-dispatch yield <id> --exclude-self worktree` (append a "not me"
   so you are not re-offered it), or `abandon --duplicate-of <ref>` if it turns
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

## Why this works (and its limits)

- **Correctness** comes from the `dedup_key`: `create --dedup-key … --claim` is a
  single-writer atomic op, so of two simultaneous claims on one subject **exactly
  one wins**; the loser gets the winner's row back and re-picks.
- **Efficiency** comes from the sweep: it just reduces how often two agents pick
  the same thing in the first place.
- **Residual gap:** genuinely *fuzzy* subjects whose keys do not collide. The
  semantic sweep catches most; anything left is caught late — an agent that
  starts, then discovers overlap, `yield`s or `abandon --duplicate-of`s.

## See also

- The **`agent-dispatch`** skill — the full CLI, the six-state lifecycle, worker
  identity, capability/affinity routing, and selector (`--require`/`--exclude`)
  matching.
- `agent-dispatch create --help` — the `--claim`, `--dedup-key`, `--require`, and
  `--exclude` flags.
