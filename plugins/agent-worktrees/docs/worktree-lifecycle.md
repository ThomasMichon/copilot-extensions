# Worktree Lifecycle & Change Management

The single, browsable walkthrough of a worktree's life — from creation through
landing its change to cleanup — and how the two landing paths (direct-push and
pull-request) work. This is the **map**. For the exact operator steps inside a
Copilot session, use the **`worktree` skill**; for the deep PR reference (head
schemes, provider delegation, recovery), see
[`skills/worktree/references/pr-workflow.md`](../skills/worktree/references/pr-workflow.md);
for every config key, see [config-reference.md](config-reference.md); for the
full verb catalog, see [cli-reference.md](cli-reference.md).

> All commands use the `agent-worktrees` binstub (or a project binstub /
> `--project <name>`). Never call the Python modules or raw `git worktree`
> directly — the binstub owns squash, rebase, push, and prune. Context resolves
> **from the current directory**, the way git does.

## The lifecycle at a glance

```
                      ┌─────────────────────────────────────────────┐
   create / resolve   │                                             │
  ───────────────────▶│  ACTIVE  ──commit──▶  WIP  ──────────────┐  │
   (picker or          │  (live session)      (ahead, not landed) │  │
    programmatic)      └───────────────────────────────────────┐  │  │
                                                                │  │  │
                                   ┌────────────────────────────┘  │  │
                                   ▼                                │  │
                         ┌──────── landing ────────┐                │  │
                         │                          │               │  │
              direct-push│                          │PR mode        │  │
                         ▼                          ▼               │  │
                 push-changes              create-pr → review        │  │
                      │                    → pr-merge (consent)       │  │
                      │                    → merged                   │  │
                      ▼                          │                    │  │
                   MERGED  ◀──────────────────────┘                   │  │
                      │                                               │  │
                      ▼                                               │  │
              finalize / pr-complete ──▶ COMPLETED ──cleanup──▶ FINALIZED/pruned
                      │                                               ▲  ▲
                      └────────── resume until pruned ────────────────┘  │
                                   (detached PR? recover) ───────────────┘
```

**Committed ≠ merged ≠ deployed.** A commit lives only on the worktree branch;
**merged** (via the default-branch landing) makes the change shared and *primes*
deployment; **deployed** means a running system reflects it (a separate install
step for runtime plugins). Don't call a merged change "deployed."

### Creation starts from freshly resolved repository state

Before creating an ordinary or paired worktree, agent-worktrees fetches the
configured remote and chooses the fetched remote default branch as the start
point when available. A clean anchor checked out on its default branch is also
fast-forwarded opportunistically. Dirty, detached, non-default, ahead, and
diverged anchors are never rewritten; creation still uses the fetched remote
ref, or a last-known local fallback when the fetch is unavailable.

Registered repositories used as local marketplace sources receive the same
safe refresh before a directory override is materialized during explicit creation,
adoption, or reconciliation. The bounded session-start hook remains
refresh-free. Fetch failure is non-fatal and is reported as degraded freshness
rather than as a successful refresh. The existing `auto_fast_forward` policy
disables opportunistic anchor movement during creation without preventing
remote-ref-based worktree creation.

### Folder trust follows admitted checkout entry

Creation (including paired knowledge and retained-creation repair), non-preview
resume planning, and successful `register-session` / `bind-session` entry ensure
the native checkout is in Copilot's `config.json` `trustedFolders`. Resume
planning does this before local guidance rendering even with
`--defer-projections`; bare resume trusts the recorded checkout, not HOME.
Restore establishes required trust before reclaiming an existing owner;
projection preparation remains separate from that destructive boundary.
Legacy labels must also resolve to a local record on the actual native
platform; a coincidentally existing path is not authority. Declared aliases
and record-scoped legacy WSL mappings remain supported.
Read-only inspection, dry runs and rejected ownership/lifecycle admission do
not grant trust. Folder trust neither clones tool permissions nor approves
extensions; those remain separate creation-time operations.

The update is idempotent and shares the user-wide configuration sidecar lock
with existing trust mutations. A missing configuration is initialized. Checkout
existence is checked again after acquiring the lock so a waiting
ensure cannot restore trust after confirmed removal. Existing
trust entries, unrelated settings and the managed leading comment header
survive. Malformed settings, a failed write or lock contention are explicit
entry errors, not “already trusted.” Session registration may already be
recorded when a trust error is reported; after fixing the cause, repeat the
same entry command rather than creating a replacement session.

Finalization preserves trust and checkout-specific permissions when it retains
the directory, including an unsuccessful removal attempt. The existing cleanup
flow removes them only after the checkout is actually absent. This does not
enable automatic cleanup or change its eligibility and custody gates.

### Paired knowledge is an owned obligation

A harness paired with a knowledge **worktree** holds a durable `worktree` claim
on that exact sibling. Pair creation journals it with the reciprocal link.
Finalization reconciles missing or stale legacy claims before checking the
ordinary obligation gate, reopening a falsely finalized harness when needed.
An anchor-kind pair does not allocate a sibling worktree claim.

The claim remains active until the reciprocal knowledge worktree is explicitly
finalized. A clean or unused checkout, published commits, a stopped session,
or an absent checkout does not substitute for that conclusion. Settle, release,
remove, sweep, overwrite, handoff, and `finalize --abandon` cannot clear an
unfinished pair's responsibility; missing, foreign, or unreadable sibling
records preserve the claim.

Land the knowledge changes through that repository's configured publication
flow, finalize the knowledge sibling, then finalize the harness. The sibling's
successful finalization settles the parent claim bottom-up. Even an unused
sibling requires that explicit conclusion; no cascade infers it complete.
Both records remain subject to the existing BOTH-finalized removal gate and
fresh liveness/cleanup checks. Finalization does not remove the knowledge half
while the harness half is still unfinished.

The final sibling probe and physical removal share a bounded sibling-record
lock through the terminal save. Contention refuses cleanup rather than trusting
a stale finalized state. Both pairing directions require reciprocal identity;
a foreign finalized record is not permission to delete. Legacy pairs without
a claim cannot erase their links or become terminal to bypass this check.
Dispatch cleanup likewise waits for explicit sibling finalization, retaining
its exact reciprocal creator link and interrupted-unlink receipt checks.
It releases only the exact settled paired claim under both lifecycle locks;
unrelated held resources still block conclusion. Reconciliation of a newly
settled legacy claim uses the same finalized-proof fence. A refused paired
tombstone write preserves its ledger instead of falling back to deletion.
Repeated sibling finalization leaves an already released parent pair claim
released. Pair-only reconciliation can repair completed managed owners without
admitting unrelated allocations or overwriting handoff/producer custody.
The first paired save fences and validates a proposed settled claim even
before the harness's pairing metadata has been committed.
Retargeting a settled pair verifies and fences both the old sibling's discharge
and the replacement sibling's finalized state through that same save.
If a sibling reopens during a repeated finalize, failed rollback of the old
finalized state restores an active harness and an active paired obligation.
The checkout and reopened work remain intact, and reconciliation can retry.

### Session worktree names

Seeded and unseeded session creation use the same timestamp and four-character
hexadecimal suffix. The platform is appended only when the logical machine name
does not already end in that environment (`example-wsl`, not
`example-wsl-wsl`). Windows accepts either `-win` or `-windows` as the existing
environment suffix.
Platform labels are matched case-insensitively without rewriting the logical
machine name.

Remote prompt admission keeps its request and seed identities in a durable
receipt, separate from the generated worktree name. Replaying the same request
recovers that receipt's original allocation. Short-allocation receipts must
agree with their recorded worktree ID, timestamp and suffix; inconsistent
receipts are refused without rewriting the worktree record or staged prompt.
Completed receipts with a complete allocation also prove the original record,
creation nonce, owner/pair evidence, checkout and receiving project namespace
before applying naming compatibility. New admissions retain the observed
execution origin, so a later optional machine-map customization does not
invalidate an otherwise identical completed replay. Older receipts are not
backfilled: an original OS-derived identity can be independently reconstructed,
but an unprovable former custom owner still requires ordinary ownership checks.
The same-request replay and status read preserve the original record and seed;
they do not grant mutation or seed-replacement authority. Fresh/unstarted
allocations, first completion, Resume and actual seed writes keep their ordinary
execution-authority checks. Consumed or superseded intent cannot be restored by
replaying its admission.
Existing worktrees and historical
UUID-suffixed admissions retain their exact names, including during creation
repair and paired-knowledge recovery.
When a caller's record is absent, delegate host inference accepts both complete
allocation formats, not partial machine/environment prefixes. Matches for
different host identities remain unresolved rather than authorizing finalization
from an ambiguous host.

### Replay-safe programmatic creation

`create --creation-key <opaque-key> --json` binds one logical request to its
allocation facts and generated worktree identity. It creates space only:
the keyed result contains `worktree` plus versioned `creation` metadata,
not an executable session launch plan. Existing unkeyed creation is unchanged.
The key is not a worktree ID. Callers choose a different logical revision
only for a deliberate replacement, never for a transport retry or dispatch
attempt.

The per-project, installation-qualified owner journal records intent before
allocation effects and completion before returning. An exact completed replay
returns the original result after checking the owner record, path, branch, and
creation nonce. Changed allocation facts conflict; removed or retired results
refuse rather than allocating again. Concurrent or interrupted creation returns
an explicit pending/recovery error (exit 3), including the reserved identity
when known. It never treats a missing response as permission to repeat create.
Pending effects require reconciliation; no automatic replacement is authorized
by an absent local receipt.
Profile, readiness, pairing and codename validation (including the under-lock
recheck) precede reservation of the allocation identity. Source/root preparation
may already have occurred, but no checkout, tracking record or reciprocal child
claim exists yet. A positively refused validation before that workspace-effects
boundary preserves the bound facts and permits same-key retry after repair.
Once an identity is reserved, even a later failed call stays pending and cannot
reset the key or authorize another allocation.

## Worktree states

The tracking state (seen in `list` / the picker) and its status-bar block:

| Tracking state | Bar block | Meaning |
|----------------|-----------|---------|
| `active` | — | Live Copilot session detected |
| `dirty` | `DIRTY` (red) | Uncommitted changes in the working tree |
| `wip` | `WIP` (amber) | Clean; ahead with commits not yet on upstream |
| `unused` | `UNUSED` (grey) | Clean; no commits **and** no conversation since the fork point |
| `convo` | `CONVO` (teal) | Clean; no commits, but the session held conversation turns (`💬N`) |
| `pushed` | — | Changes pushed to the default branch, awaiting finalization |
| `completed` | `FINAL` (green) or `MERGED` (orange) | All content landed on the default branch -- see below for which label shows |
| `finalized` | — | Landed and the worktree removed |
| `gone` | — | Worktree directory missing |
| `orphan` | `ORPHAN` (magenta) | No merge base with upstream |

`unused` vs `convo` is why cleanup never auto-purges a commit-less worktree: it
may hold planning or conversation. See
[cli-reference.md § status-segment](cli-reference.md) for the bar detail.

Git classification observes the current branch, dirtiness and local refs on
each pass. After patch-equivalence checks, its content fallback reads a raw
branch diff and batches exact upstream tree-entry lookups instead of launching
two blob lookups per changed path. It considers only paths changed by the branch since its merge
base; unrelated upstream changes do not make the branch WIP. Full blob/gitlink
object IDs, not file modes, determine content equality. Rename detection and
submodule hiding are disabled for this fallback so an outstanding old-path
deletion or gitlink change cannot disappear from the evidence. Literal
NUL-delimited paths support whitespace and Unicode without Git's display
quoting. Literal, nonrecursive `ls-tree` requests are split under a conservative
24-K argument budget, with ancestors and descendants in separate batches:
they never enumerate unrelated upstream files. Oversized individual path
arguments, failed commands and malformed tree evidence report `UNKNOWN`, never a clean
or completed state. This is per-pass batching, not a persistent fact cache or
a change to daemon authority, fetch freshness, or session/tracking refinements.

#### `FINAL` vs `MERGED` -- the closure descriptor split

A `completed` worktree (its content is fully on the default branch) does not
always render as `FINAL`. The canonical closure descriptor
(`prune.assemble_closure_descriptor`, worktree-finality-and-obligations Phase
5) splits it into two distinct labels, shared by the status bar, `list --json
--classify`'s additive `closure` field, and the Picker/Worktree Manager table:

- **`FINAL`** (green) -- a `completed` worktree that is genuinely, currently
  *proven* safe to clean: the evidence came from a **refreshed** (fetched)
  classification, it has **zero held claims**, **zero open follow-ups**, and
  no other blocker (e.g. `rec.status == "finalizing"`).
- **`MERGED`** (orange) -- `completed`, but not (yet) provably settled. Any of
  the following forces `MERGED` instead of `FINAL`: no tracking record at all
  (there is no evidence to prove `FINAL` with), a fetch-free/cached poll (the
  default `status-interval` tick never fetches), a requested `--fetch` that
  itself failed, one or more held claims, or one or more open follow-ups. A
  `MERGED` block may carry a compact `C<N>`/`F<N>` marker suffix for held
  claims / open follow-ups, plus an `XM<N>` marker (see below) when some of
  those held claims are purely cross-machine.

Successful explicit `finalize` is also a durable **display** assertion. The
descriptor's additive `display` projection reports `FINAL` while the record
remains finalized and its recorded HEAD/branch identity still matches, without
requiring every normal poll to fetch again. Legacy records without a checkout
stamp remain conservative until explicitly finalized again. An
attached concluding shell does not erase that assertion; liveness remains
visible independently and continues to prevent removal. New claims/session
activation or follow-ups reopen responsibility through their owning writers;
observed dirtiness, checkout drift, open PRs, and pending handoffs suppress
the finalized display.

This does not change destructive authorization: the existing `closure.final`
and `action` fields still require fresh evidence. A cached descriptor can
therefore display `FINAL` while its cleanup action remains blocked, including
when a paired sibling is not yet removable. Older consumers that do not
understand `display` retain the conservative legacy label.

##### Decomposed sub-state facts (Phase 9)

Internally, `prune.assemble_closure_descriptor` (schema `version: 2`)
decomposes the descriptor into five named, independently freshness-tracked
`facts` rather than one all-or-nothing `evidence_mode`/`evidence_complete`
pair: `checkpoint_activity`, `upstream_containment`, `local_dirtiness`,
`open_claims`, and `pending_handoff`. Each carries its own `confirmed` flag.
`checkpoint_activity`, `local_dirtiness`, and `pending_handoff` are always
locally computed (`pending_handoff` reads `rec.pending_handoffs` --
agent-worktrees' own already-tracked opened-but-unlinked session
handoffs), so always `confirmed` and purely informational (never gate
`FINAL`); `upstream_containment` and `open_claims` require fresh evidence
(a fetch, a provider PR lookup) to be `confirmed`, and `FINAL` requires
BOTH to be independently confirmed. This is additive plumbing: the
`FINAL`/`MERGED` label rules above are unchanged; the Picker does not yet
render each fact's own freshness marker (still a separate, unstarted
slice) the way `list --json` and this status segment already do (see
below).

##### Per-fact freshness markers (Phase 9)

`compact` additionally carries an `U*`/`OC*` marker whenever
`upstream_containment`/`open_claims` (respectively) is unconfirmed --
independent of each other and of the `C<N>`/`F<N>` held-claim/follow-up
markers above, and independent of the base label (a `DIRTY`/`WIP`/etc.
worktree can carry either marker too, not just `MERGED`). This is the
general marker convention the Plan calls for: an unconfirmed fact is marked
**in place**, never spawning a separate whole state. `FINAL` never carries
either marker (both facts are confirmed by construction whenever `final` is
true). The mux/PSMux status segment (below) renders `compact` directly, so
it picks up both markers automatically; the Picker does not yet consume
`compact` (still a separate, unstarted slice -- see the 2026-09-15 Journal
entry on Picker label parity).

##### Cross-machine held claims (worktree-claims-transitive-finalization, Phase 4)

An outbound `worktree`-kind claim whose ref names a **different** machine
targets a worktree hosted remotely -- it isn't a LOCAL blocker, since this
worktree holds the claim but the claimed resource lives elsewhere. This
proves only that the target is remote, nothing about its state: a
cross-machine claim may target a genuinely busy remote worktree, not an
idle/settled one, and nothing today actually sweeps/settles this kind of
claim either way (`sweep.py`'s `gone_of`/`safe_of` both spare an
unjudgeable cross-machine ref, and `worktree` isn't in
`sweep._LEASEABLE_KINDS`). This bucket names WHERE the target lives, not
that the claim needs no further look or clears itself. When **every** held
claim on a worktree is one of these, `cleanup_disposition` reports the
`held-claims-cross-machine` bucket instead of the generic `held-claims` --
same safety posture (still not cleanable, still `blocked`), but a distinct
reason ("claim(s) on a worktree hosted remotely") so an operator isn't left
thinking an otherwise-clean worktree is blocked on something local. A
single same-machine or non-`worktree`-kind claim in the mix keeps the
generic bucket -- never collapsed into the cross-machine reading when
something might genuinely need local attention. The sub-count rides the
`open_claims` fact as `cross_machine_held` and renders its own `XM<N>`
`compact` marker (alongside the ordinary `C<N>`) -- both additive: the
wire-safe `held-claims` blocker code and `DESCRIPTOR_VERSION` are
unchanged, so an older consumer degrades
gracefully to the generic reading.

### The status core — an orthogonal disposition layer

The tracking state above is **git-derived** (what the tree/branch look like). Over
it rides a second, independent layer — the **status core** — that answers *"does
this worktree still need a human?"* even when git says it's clean. It has two
parts:

- **Asserted disposition** (written by the agent/operator via
  `agent-worktrees status`): a **`follow_up`** flag plus a one-line **`summary`**.
  This is the durable "I'm done" / "there's more to do" signal. `--follow-up`
  raises it, `--resolved` clears it, `--summary "…"` sets the line
  (`agent-dispatch focus "…"` writes the same field).
- **Live pulse** (derived, never asserted): `live_intent` / `live_pulse` /
  `live_rest` — a dim, self-refreshing line from the session's intent/rest
  stream (or a bounded event-tail fallback for coarse busy/idle). It is
  presentation only and **never** sets `follow_up`.

An optional **active-effort focus** is identity on the same worktree record, not
a third status register. `effort-focus bind` stores one contained,
repository-relative effort README plus its declared participant/slice. While
that effort remains open, reads derive `follow_up=true` and an effort summary
through the existing status core; `status --resolved` refuses to bypass any
binding. `effort-focus release --completed` verifies `Status: Done` and resolved
Plan/Validation Plan checkboxes at the active or standard dated archive path,
while `--transfer "<tracked objective>"` records an explicit transfer. Missing
or stale pointers are never presented as active.
Bind writes `follow_up=true` and an effort summary; release clears that flag and
replaces the summary. Re-assert any follow-up unrelated to the effort after
release.

The crucial rule: **`follow_up` is orthogonal to state, and it gates cleanup.** A
worktree can be `finalized`/`completed` (git says "done, prune me") yet still
carry `follow_up=true` — its cleanup bucket is then downgraded from the auto-prune
**`clean`** verdict to the review-class **`follow-up`** bucket, so cleanup/gc leave
it alone. Two common cases:

- **Genuinely more to do** — the flag is correct; resume and land it (or file an
  issue and clear the flag).
- **Stale flag** — the work is actually done but the agent finalized *without*
  running `status --resolved`. The worktree looks done but won't reap. The fix is
  a "landing pass": confirm nothing remains, then clear the flag.

For an effort-bound worktree, use `effort-focus release` rather than treating
the derived flag as stale.

**Title sigils.** The picker prefixes scannable glyphs onto a worktree's title,
independent of the state column:

| Sigil | Meaning |
|-------|---------|
| `✚` | `follow_up` flag set (needs attention / not auto-prunable) |
| `⏳` | live session parked **awaiting the operator** ("this needs me") |
| `⚠` | bound-but-un-muxed Copilot (a **bare orphan** session) |
| `⚭` | one half of a carved **pair** (harness/knowledge) |
| `[system]` / `[delegate]` / `[acp]` | origin/interface tag (see [picker.md](picker.md)) |

## 1. Create

| Way | Command | Use when |
|-----|---------|----------|
| Interactive | `my-project` (bare binstub) or `agent-worktrees resolve` | A human at a terminal picks/creates a worktree and launches a session (the **Picker**). |
| Muxed launch | `agent-worktrees resolve --new` | Create **and** launch a multiplexed interactive session (refused without a TTY). |
| Programmatic | `agent-worktrees create [--json]` | An agent or daemon needs a worktree path with **no launch and no mux** — prints id + directory. |

A fresh worktree branches from the up-to-date default branch into a
`<anchor>.worktrees/<id>` sibling folder. Until it has commits or a live
session it lists as `unused`.

For an interactive muxed launch, worktree creation and session creation are
separate recoverable steps. The launcher retries mux creation three times and,
after interactive exhaustion, offers another retry. If launch is cancelled or
still fails, the worktree remains registered and the error names both its path
and the command for reopening that same worktree; recovery does not create a
replacement worktree.

## 2. Active — work and commit

Commit freely on the worktree branch; commits are cheap and isolated, and the
branch is never shared until you land it. Idle worktrees are kept aligned with
the default branch **fast-forward only** (never rebased or discarded) — a clean,
strictly-behind worktree auto-fast-forwards on resume (`--no-fast-forward` or
`auto_fast_forward: false` to disable). A worktree that is ahead or diverged is
left untouched.

## 3. Landing the change

There are two landing paths. Which one applies is a **repo config choice**
(`pr.enabled` / `pr.required`), not a per-session decision. Check it first:

```bash
agent-worktrees get pr-profile     # direct | pr-human-merge | pr-agent-merge
agent-worktrees get pr-required    # "true" ⇒ direct-to-default-branch is blocked
```

### 3a. Direct-push (no PR) — two-phase sign-off

```bash
agent-worktrees push-changes --title "concise description"   # squash → rebase → push to default branch
agent-worktrees finalize                                      # validate content is upstream, then prune when idle
```

`push-changes` squashes the worktree's commits, rebases onto the current default
branch, and pushes. `finalize` verifies the content actually landed before
removing the worktree/branch — and defers the prune while a session is still
live. Never hand-run `git merge`/`push`/`worktree remove`.

### Pausing a worktree instead of finalizing (a runbook, not a subcommand)

`finalize` is all-or-nothing on its resource-obligation-settlement gate: any
unsettled outbound legacy claim either blocks it outright, or `--abandon
--handoff-to <recipient>` re-homes the *entire* unsettled set elsewhere.
Disposable-owned producer claims are stricter: verified close or an explicitly
accepted bound-claim handoff is required, and abandon, sweep, disconnect, generic
release and gate-mode overrides cannot discharge them. Pending/allocating/unknown producer
claims always block. See [the producer ABI](resource-producer.md).
Neither fits "sync and tidy everything that's actually done, but leave this
one claim open on purpose" — e.g. a deliberate pending `context-handoff` task
meant to resume in this exact worktree, or any other genuinely-still-open
piece of work. There is no dedicated `pause` subcommand for this — compose
the existing primitives instead:

```bash
agent-worktrees git sync                       # pull the branch forward onto the latest default branch
agent-worktrees claims sweep --apply            # auto-settle whatever the never-wedge sweep can PROVE is resolved
agent-worktrees claims                          # see what's still genuinely open
# settle/release anything you've independently confirmed is safe:
agent-worktrees claims settle <ref> [--released]
# mark the worktree as intentionally idle, with a note on what's left:
agent-worktrees status --paused --summary "<why it's paused / what's still open>"
```

1. **Sync first.** `git sync` rebases the branch forward (never force-pushes,
   never prunes) so the worktree builds on the latest default branch before
   you report anything.
2. **Auto-settle only what's provably safe.** `claims sweep --apply` is the
   repo's own never-wedge reclaim sweep — it flips a claim to `released`
   (a provably-merged hand-back) or `abandoned` (every other case where its
   holder is provably gone *and* its resource is provably safe, e.g. an
   off-box CodeSpace). It never guesses. Settle anything else you've
   independently verified via `claims settle <ref>` / `claims release <ref>`.
3. **Report what remains — don't force it.** `claims` (no args) prints the
   full outbound ledger. Whatever is left open after the sweep is exactly
   what the operator needs to see; do not release, abandon, or force a
   disposition on a claim you can't prove is already safe.
4. **Mark the worktree `--paused`.** This never affects `finalize`'s
   obligation gate or `cleanup`'s prune eligibility, but it lets a human (or
   Picker) immediately see "this worktree has work left open on purpose, not
   abandoned" -- a scannable glyph in the Picker title and the
   `status`/`list` JSON payload (the plain-table view is unmarked, same as
   any other JSON-only field), and it does stamp `status_note_at` like any
   other disposition write. Pair it with `--summary` naming what's still
   open and why. Clear it later with `agent-worktrees status --unpaused`
   once the worktree is active again (or genuinely done — run `finalize`
   instead).

Settling a specific claim, then re-running `finalize`, is how a paused
worktree eventually becomes finalizable — `pause` itself never settles
anything `claims sweep` couldn't already prove safe.

### 3b. PR mode — the `pr-*` command family

When the repo is PR-gated, sign-off becomes **create-pr → review → merge →
reconcile**, and `push-changes` targets the *feature* branch, never the default
branch. Three profiles decide which verbs apply:

- **`pr-human-merge`** — PR-gated, a human approves + merges. Use `create-pr`,
  `pr-watch`, `pr-status`, `pr-complete`. `pr-merge` **does not apply** (no
  consent label bound).
- **`pr-agent-merge`** — an auto-merge consent label/native auto-complete marker
  is bound: after approval the author runs `pr-merge` to signal consent and the
  review gate merges. The full family applies.
- **`pr-self-merge`** — the submitter is authorized to merge directly after
  provider-required checks/reviews. On GitHub this never means self-approval. Bare
  `pr-merge` refuses; use `pr-merge <pr> --now`, which prefers native
  CI-gated auto-merge when the provider supports it and otherwise performs the
  configured merge.

The base config has a pure, offline **configured profile** (`get pr-profile`).
Networked actor-specific verbs resolve an **effective actor profile** from that
base + live provider permission + a matching GitHub `pr.roles` override.
`pr-status` prints the effective `flow:` and its configured source;
`pr-status --no-live` remains configured-only. `pr-merge` uses the same
resolver before deciding whether `--now` applies, and `pr-watch wait` uses the
same effective review posture. Believe those live surfaces; never hand-merge
past a verb that says it doesn't apply.

| Verb | Role in the loop |
|------|------------------|
| `create-pr [--title][--body/--body-file][--draft][--new]` | Squash + publish the PR head branch and open the PR. `--draft` opens it not-ready-for-review. |
| `pr-ready` | Move a draft PR **out of draft** (request review). |
| `set-pr --url URL --number N` | Record PR metadata when a sub-agent/provider opened the PR out of band. |
| `pr-status` | Tracked PR metadata + live verdict / conflict / merge state; flags pull-forward when merged. |
| `pr-watch wait <repo> <pr>` | Block until the PR moves (approved / changes_requested / conflict / mergeable / merged / closed) and wake the caller with a race-proof cursor. |
| `pr-merge <repo> <pr>` | Signal **merge consent** on an approved PR (applies the bound `automerge_label`); the gate merges when satisfied. |
| `pr-complete` | Reconcile the worktree after its PR merged — fast-forward past the squash-merge (or rebase), dropping the local commits the squash already absorbed. |

**Merge consent is a deliberate, post-approval act.** Opening a PR invites
*review*; only after an approval does the author run `pr-merge` to authorize the
*merge*. "Please review" is never silently "ship it."

**An opened PR is final by default.** Land everything *before* `create-pr` — a
late push races the merge. If you need to keep iterating, open it as a
`--draft` and run `pr-ready` when it's genuinely ready for review.

A typical PR-mode loop:

```bash
agent-worktrees create-pr --title "add the thing"      # squash + open PR
agent-worktrees pr-watch wait owner/name 123 --json     # sleep until a review lands
# ...on approval...
agent-worktrees pr-merge owner/name 123                  # signal consent → gate merges
agent-worktrees pr-watch wait owner/name 123 --until merged --json
agent-worktrees pr-complete                              # reconcile the worktree forward
```

See [`references/pr-workflow.md`](../skills/worktree/references/pr-workflow.md)
for head-scheme/branch topology, provider delegation, comment-thread handling,
and the disposition modes.

### Held and follow-up PRs

- **Held / draft.** A PR you're not ready to have reviewed is a **draft**
  (`create-pr --draft`); `pr-ready` releases it. A repo may also bind
  `hold_labels` (e.g. a "needs-rebase" or explicit block) that keep consent from
  applying — `pr-status` surfaces them.
- **Follow-up PRs.** A merged PR that missed a piece doesn't get reopened —
  land a **follow-up** PR. Short review cycles + a cheap second PR beat one giant
  anxiety-preserved PR.

### Serial vs parallel PRs

- **Serial (default, recommended).** From one worktree, open a PR, see it
  through, then open the next. Sequential PRs avoid tangled local branch state.
- **Parallel.** Multiple in-flight PRs are supported (`create-pr --new` forces a
  fresh head branch), but only when each PR leaves the default branch green on
  its own. Prefer independent worktrees for genuinely parallel work.

The details of both — including how a `--new` PR picks its head ref — are in
[`references/pr-workflow.md` § Multiple PRs per worktree](../skills/worktree/references/pr-workflow.md).

## 4. Finalize, prune, and resume

`finalize` marks a landed worktree **prune-eligible**; the actual removal
happens once the session is idle, or explicitly via `cleanup`:

```bash
agent-worktrees cleanup --clean                    # remove completed + gone worktrees
agent-worktrees cleanup --clean --include-unused   # also purge commit-less worktrees (asks first)
```

Cleanup never auto-purges an `unused`/`convo` worktree — a commit-less worktree
may still hold planning or conversation. For a `gone` worktree the branch is
deleted only when its content is verified on the default branch.

**Finalized is not terminal.** Until it is pruned, a finalized worktree still
appears in the picker and can be resumed to carry follow-up work — open a fresh
PR for the new change. It can also still journal a fresh outbound resource
claim (`claims add`) or take part in a claim handoff: `finalized` only stamps
"no obligations as of this validation," not "frozen." (Only the in-flight
`finalizing` RMW window and a genuinely broken `orphaned` record refuse new
claims.) If a PR-mode worktree was already torn down (the `detach`
disposition), recover it via
[`references/pr-workflow.md` § Recovering a PR after teardown](../skills/worktree/references/pr-workflow.md).
When in doubt, just `create` a fresh worktree and continue there.

Historical WSL claim-bundle actors may retain native-host labels. Local
offer/accept routing requires each complete qualified reference to resolve under
its receiving project's record, platform and namespace authority; host-label
similarity alone never authorizes a transfer. Stored bundle/claim references and
machine/platform fields are preserved while the accepted ownership changes.
Anchor actors are checked against their receiving repository's configured anchor,
not a synthetic worktree-root child. Equivalence uses the referenced project's
topology; an unregistered foreign source uses the fenced remote leg, while invalid
local authority still refuses the transfer.

## 5. What's cleanable, and what's never reaped

"Can this worktree be removed?" is **not** the same question as its tracking
state. The authoritative answer is the **maintenance disposition** — a bucket
each worktree falls into, graded SAFE / REVIEW / UNSAFE (neutral buckets carry no
chip). `list --json --classify` emits it, and the picker uses the same
disposition in its Cleanup/Sync dialogs on the Worktrees row (the old standalone
Maintenance view has been folded into those actions).

| Disposition | Buckets | Cleanup behavior |
|-------------|---------|------------------|
| **SAFE** | `clean` (landed on default branch) | Auto-prunable — `cleanup --clean` removes it. |
| **REVIEW** | `unused`, `conversation`, `follow-up`, `closed-unmerged`, `gone` | Cleanable but **confirmed first** (the flag/opt-in gate) — never silently purged. |
| **UNSAFE** | `dirty`, `wip`, `unmerged`, `orphan`, `active` | **Never** auto-pruned — it holds un-landed work or is in use. |
| neutral | `open-pr` (healthy, in review), `unknown` (remote too old to classify) | No chip; not offered for cleanup. |

Two things move a worktree *out* of SAFE even when git says it's clean: an
**`open-pr`** (a healthy end state — leave it), and the **`follow_up`** flag,
which downgrades `clean` → the REVIEW `follow-up` bucket (see § The status core).

### The reaping surface — which command clears what

`cleanup` is only one of several reapers; each targets a different kind of
residue, and each **spares anything in use**:

| Command | Reaps | Spares |
|---------|-------|--------|
| `cleanup --clean` | `completed`/`gone` (SAFE); `--include-unused` / `--include-conversations` opt into those REVIEW buckets | live-session worktrees, and any UNSAFE bucket |
| `gc` | tracked reap (the cleanup verdict) **+ leaked system/bridge worktrees + on-disk orphan dirs + git prune** | see the managed-reap invariant below |
| `reap-sessions` | leaked `wt-<id>` mux sessions whose worktree is finalized/gone/untracked **and** idle past grace | **attached, active, or recently-busy** sessions |
| `reap-shells` | orphaned launcher shells (pwsh/python scaffolding stranded by a force-closed terminal) | anything with a live descendant; reports-only unless `--yes` |
| `remove-system <id>` | one **system worktree** by id (the manual escape hatch) | dirty working tree, unmerged/unpushed branch content, branch drift or a detached HEAD, an unclassifiable git state, a live PR (open/creating/unpopulated), a live outbound resource claim, or (for a resource this worktree itself is, via `owner_ref`) a live/unconfirmed inbound claimant -- refused by default; an unadvertised `--force` overrides |

### System worktrees (`sys-*`, `[system]`/`[delegate]`)

Worktrees whose `kind` is **`system`** or **`bridge`** — Neuron Forge / agent-bridge
sessions, and background/scheduled agents (e.g. per-task writer worktrees) — are
**deliberately exempt from routine `cleanup`**: they're owned and torn down by
their service, and hidden behind the picker's Toggle-hidden (see
[picker.md § Worktree types & visibility](picker.md)). When a service crashes or
finalizes without tearing its worktree down, they **leak**, and because their
tracking status stays `active` (never marked complete) `gc`'s managed sweep
records them as `not-final-or-unused` and keeps them — so they can accumulate.
Clear a *provably dead* one with **`remove-system <id>`** (verify it isn't a live
session first) -- it also independently guards against discarding real content:
it refuses (by default) a worktree with uncommitted changes, unmerged/unpushed
branch content (squash-merge-aware), checkout branch drift or a detached HEAD,
an unclassifiable git state (a zombie checkout with no `.git`, an
orphaned/unrelated-history checkout, or a timed-out probe), a live PR record, a
live outbound resource claim, or -- when this worktree is itself another
worktree's outbound resource (`owner_ref`) -- a live or liveness-unconfirmed
inbound claimant (unless this resource has itself finished: `finalized` status,
or its branch content is already merged upstream), naming the blocker so the
caller can resolve it; an unadvertised `--force` exists for a caller that has
already confirmed discarding is correct. The durable fix is for the owning
service to `remove-system` on task
completion.

### The managed-reap invariant — what `gc` will never touch

A **system/bridge** worktree is reaped only when **all** hold: it is **FINAL or
UNUSED** (work done or never happened), has **no live process** (no live mux, no
**attached** client, no live Copilot session), carries **no `follow_up` flag**,
and has been **idle past the grace window** (≈1h, so a just-created worktree
isn't yanked out from under a daemon). Anything else is spared, with an
inspectable reason: `follow-up`, `attached`, `live-mux`, `live-session`,
`not-final-or-unused`, `activity-unknown`, or `idle-grace`. In short — **`gc`
never reaps un-landed work, a flagged follow-up, or anything in use**, which is
why running it after resolving follow-ups only removes what genuinely landed.

> **Never reap a "literally active" worktree.** A live session you did not spawn
> — an operator's **attached** terminal, or an autonomous session mid-task — is
> hands-off. The reapers already spare it (the `attached` / `live-session`
> skips); don't defeat that by hand. `cleanup` prints `⚠️ Skipping … active
> Copilot session in use` for exactly these — believe it.

## Command map

| Stage | Direct-push | PR mode |
|-------|-------------|---------|
| Create | `resolve` / `create [--json]` / `resolve --new` | same |
| Work | commit on the worktree branch | same |
| Land | `push-changes --title` | `create-pr` → `pr-watch` → `pr-merge` → `pr-complete` |
| Draft/hold | — | `create-pr --draft` → `pr-ready` |
| Inspect | `status` | `pr-status` |
| Clean up | `finalize` → `cleanup` / `gc` | `finalize` → `pr-complete` → `cleanup` / `gc` |
| Reap residue | `reap-sessions` (mux) · `reap-shells` (launchers) · `remove-system <id>` (system worktree) | same |

## See also

- [Getting Started](getting-started.md) — install, register, first session.
- [The Worktree Picker](picker.md) — the interactive launcher the lifecycle
  starts from (screen, navigation, resume/create/clean/sync).
- [Multiplexed Sessions](mux.md) — why a launched session runs in tmux/psmux
  (persistence, detach/rejoin) and when to skip the mux.
- [CLI Reference](cli-reference.md) — every subcommand and flag.
- [Configuration Reference](config-reference.md) — the `pr:` block
  (`enabled` / `required` / `provider` / `strategy` / `automerge_label` /
  `hold_labels` / …) and where it resolves (machine-local vs in-repo).
- [`worktree` skill](../skills/worktree/SKILL.md) — the in-session operator flow
  and its [PR-workflow reference](../skills/worktree/references/pr-workflow.md).
- [Architecture](architecture.md) — worktree internals and the picker.
