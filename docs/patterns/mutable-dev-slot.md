# Pattern: mutable-dev-slot

**Serves:** *Vision plugin-services* §Behaviors/`immutable-versioned-runtime`
(this pattern is its deliberate, narrow exception) and the day-to-day
contributor need this repo's own `CONTRIBUTING.md`/`TESTING.md` gotchas
surfaced repeatedly: validating a real, unmerged code change against the
actual deployed CLI a human/agent would invoke, without a slow
commit-push-merge-deploy loop, and without leaving the machine on
unmergeable code afterward.
**Exemplars:** `libs/versioned-runtime` (the shared primitive); pilot
rollout tracked in `efforts/active/mutable-dev-slot/README.md`.

## Problem

Every plugin's runtime is **immutable and versioned**
(`docs/patterns/README.md` §Design invariants): `versions/<version>/` is
built once and never edited in place, and `current-version` selects which
immutable build is active. This is exactly right for real deployments — but
it makes iterating on a live bug from a **worktree** slow and easy to get
wrong, as this repo's own contributors keep rediscovering:

- Editing a worktree's checkout and running the unified `<repo> update` does
  **not** validate the edit — `update` follows the machine's installed
  `source.kind` (marketplace vs local), which for an already-onboarded
  machine is `marketplace`, resolving `main`. A worktree's uncommitted or
  unmerged commits are invisible to it.
- Running that plugin's own installer directly from the worktree (switching
  `source.kind` to `local`) DOES validate the edit — but it builds a brand
  new **immutable, numbered** version slot each time, so the fast inner loop
  of "edit -> see the effect -> edit again" still pays a full venv (re)build
  every iteration, and it silently leaves the machine's real deployment
  pointed at throwaway code until the operator remembers to run the unified
  `update` again.
- The alternative contributors have reached for under time pressure —
  hot-patching an already-deployed version slot's files directly — works for
  a fast inner loop, but is **explicitly throwaway**: it's invisible to
  version control, silently overwritten by the next real install/update, and
  provides zero protection against two concurrent worktrees clobbering each
  other's hot-patch on the same machine.

None of these three options gives a worktree a **first-class, protected,
genuinely mutable** place to iterate against the real deployed CLI.

## Standard approach

Add exactly **one** additional, well-known version slot per plugin:
`versions/dev/`. Unlike every numbered slot, an installer is allowed to
**rebuild `dev` in place** — a fresh `uv pip install -e .` against the
worktree's own checkout, or an ordinary rebuild reusing the existing venv,
rather than always creating a new directory. Everything else about the
runtime-slot machinery (the `current-version` marker, the completion marker,
`activate()`'s marker-then-binstub-pin cutover) stays exactly as it is for
every other version — `dev` is a normal slot name to that machinery, not a
special code path bolted on top of it.

Two invariants make this safe to leave lying around a shared machine:

### 1. A claim gates who may (re)build/activate it

Because `dev` is a **single shared mutable slot per plugin per host**, at
most one owner may hold it at a time — otherwise a second worktree's rebuild
would silently clobber the first's, or two owners would disagree about what
`current-version` should be restored to on release. `libs/versioned-runtime`
implements the claim as a plain, dependency-free JSON sidecar,
`<root>/dev-claim.json`, schema `copilot-extensions.dev-slot-claim`:

```json
{
  "schema": "copilot-extensions.dev-slot-claim",
  "version": 1,
  "owner": "<absolute worktree checkout path>",
  "host": "<hostname>",
  "pid": 12345,
  "claimed_at": "2026-01-01T00:00:00Z",
  "previous_version": "1.2.3"
}
```

- `owner` is the **absolute worktree checkout path** — the same value
  `agent-codespaces`' cross-machine CodeSpace claims already use as their
  `owner`, obtained the same way (`agent-worktrees get worktree-dir`). Using
  the same shape lets a future generic "what does this worktree still hold"
  query treat every claim kind uniformly.
- `previous_version` is captured **once**, at first claim, and is the
  version `current-version` should be restored to on release — not
  whatever happened to be current at the moment of a second, idempotent
  re-claim by the same owner.
- Unlike the CodeSpace claim (a live-process advisory lease with its own
  liveness reconciliation), this is **not** liveness-reconciled and carries
  no TTL. It is meant to persist across many process lifetimes — a worktree
  may claim dev mode for hours or days across many sessions — and is only
  ever removed by an explicit release (or an operator's `--force`), never by
  a timeout. This is a deliberate difference from `single-instance-lease`:
  that library asserts "one live daemon," which is inherently
  process-lifetime-scoped; a dev-mode claim asserts "one owner's mutable
  workspace," which must outlive any single process.

`libs/versioned_runtime.py` exposes the primitive directly:

```
claim_dev(root, owner, *, host=None, previous_version=None, force=False) -> dict
release_dev(root, owner, *, force=False) -> dict | None
read_dev_claim(root) -> dict | None
```

and the matching CLI verbs (`--root <dir>` as usual):

```
dev-claim   --owner <path> [--previous-version V] [--host H] [--force]
dev-release --owner <path> [--force]
dev-status
```

A conflicting claim (different owner, no `--force`) raises
`DevClaimConflict` / exits 2 — distinct from every other error path (exit 1)
so a caller can special-case "someone else has it" from "something broke."

### 2. GC never reclaims a claimed `dev` slot — but reclaims it the moment it's released

`gc()` already never removes `current`; it now also never removes `dev`
while `read_dev_claim()` reports a live claim, **regardless of whether `dev`
is current** — an operator routinely running GC on a machine must never
silently destroy someone else's in-progress mutable install. The instant the
claim is released (by the owner, or forced), `dev` becomes an ordinary,
GC-eligible slot again like any other unclaimed, non-current version. There
is no separate "is dev special" check anywhere else in the versioned-runtime
machinery — claimedness is the *only* thing that protects it.

## Runtime accessibility (the open design question this pattern flags)

`versioned_runtime.py` is deliberately **not** packaged into any plugin's
venv (`libs/versioned-runtime/README.md` §Why this is vendored, not a
package) — it exists only in the payload's `scripts/` dir, run by the
bootstrapping installer. That is fine for `dev-claim`/`slot`/`activate`
(always installer-time operations), but a **release** needs to be callable
even when the operator isn't standing in a source checkout — e.g. an
`agent-worktrees finalize` warning telling them to run it. The two options:

1. **Copy `versioned_runtime.py` into the plugin's own root** (a plain file
   sibling of `versions/`, e.g. `~/.<plugin>/versioned_runtime.py`) at
   install time, and add a first-class `dev-release`/`dev-status` verb to
   the plugin's OWN deployed CLI that shells out to it. This keeps the
   module itself out of the venv (unchanged posture) while making release
   reachable without a checkout.
2. Require the release to always happen from a checkout (`cd
   plugins/<p>; ./scripts/install.ps1 dev-release`), matching the existing
   "Local Testing" installer-verb convention exactly, accepting that a
   worktree that's already been pruned needs the operator to manually delete
   the stale `dev-claim.json` and reactivate `previous_version` by hand.

Option 1 is the intended target shape (referenced by `agent-worktrees
finalize`'s dev-slot claim warning); it is not yet implemented for any
plugin — see the tracked effort below for rollout status.

## `agent-worktrees finalize`'s obligation

`finalize` already warns (never silently releases) about a live CodeSpace
claim a worktree still holds, with the exact release command
(`_warn_of_codespace_claims_for_worktree`). The dev-slot claim gets the
identical posture: `_warn_of_dev_slot_claims_for_worktree` scans every
`~/.*/dev-claim.json` (no hardcoded plugin list — any plugin that adopts
this pattern is picked up automatically), matches `owner` against the
finalizing worktree's checkout path, and warns with the release command per
plugin still held. Reading the plain JSON sidecar directly (rather than
shelling out to each plugin's own binstub) keeps this consistent with
marketplace isolation: agent-worktrees never imports or calls into a
sibling plugin's runtime, it only reads a small, stable, dependency-free
file format every adopting plugin commits to.

### Gotchas this pattern encodes

- **Never let a plugin installer default to dev mode implicitly.** Claiming
  and activating `dev` must be an explicit act (a `dev` verb / `--dev` flag)
  — silently routing an ordinary `install`/`update` into the mutable slot
  the moment it detects a worktree-shaped path would make an innocent
  contributor's normal install accidentally mutable and unclaimed.
- **`claim_dev`'s idempotent re-claim must preserve the ORIGINAL
  `previous_version`.** A naive re-implementation that always overwrites
  `previous_version` on every claim call would silently corrupt the restore
  target the moment a worktree calls `dev-claim` a second time (e.g. on
  every rebuild) — release would then restore to whatever was active
  *during* dev mode, not before it, defeating the point of recording it at
  all. Regression-tested (`test_dev_claim_by_same_owner_is_idempotent_and_keeps_previous_version`).
- **A `--force` takeover is a genuinely different owner claiming fresh, not
  a same-owner re-claim.** `previous_version` on a forced takeover must come
  from the NEW caller's own `--previous-version`, not the evicted owner's —
  otherwise release restores to the wrong plugin state. Regression-tested
  (`test_dev_claim_force_overrides_a_different_owner`).
- **GC's dev-slot protection must be keyed on the claim, not on `current`.**
  A dev slot is very often deliberately left NOT current (the machine keeps
  serving a real numbered version while a worktree separately builds/tests
  `dev` without ever activating it) — protecting it only via the existing
  `current`-protection logic would let a routine GC destroy an in-progress,
  claimed, but not-yet-activated dev build.

## Ordinary installers: refuse, never silently mutate a numbered slot

`dev` existing as a protected, claim-gated mutable home does not by itself
stop a plugin's ordinary `install`/`update` path from still rebuilding a
numbered slot in place when it happens to target the *same version string*
with changed content (the exact anti-pattern `dev` exists to replace) — the
two are independent unless an installer explicitly wires them together. An
audit across this repo's own vendoring plugins
(`ThomasMichon/copilot-extensions#5472`, `#5468`) found most installers
still silently rewrite a completed numbered slot in this case — some of
them racing a live daemon's open file handles in the process, which is a
correctness bug on top of the immutability violation.

**The required behavior for every plugin's ordinary installer path:**

- If the target version's slot does not exist yet, build it — ordinary,
  unremarkable first install.
- If the target version's slot exists, is marked complete, and its recorded
  payload hash matches the current source tree, this is a true no-op **for
  the slot's own contents**: activate it (or leave it active) and skip the
  venv/package (re)install — never re-run that step "just in case." Normal
  out-of-slot reconciliation (binstubs, the deploy manifest, hook/service
  registration) still runs exactly as it would on any other invocation;
  this no-op narrows only the slot-rebuild step, not the whole installer.
- If the target version's slot exists, is marked complete, but its payload
  hash does **not** match the current source tree (the dev-iteration
  anti-pattern: same version string, genuinely different content), **refuse**
  — exit non-zero with a message naming the mismatch and pointing at `dev`/
  `dev-release` as the correct path for iterating without a version bump.
  The hash **must cover every runtime install input** (the full source
  tree an install actually consumes — `src/`, vendored libs, not just
  `pyproject.toml` metadata), or a source-only change with unchanged
  metadata silently takes the already-matches no-op path instead of this
  refusal — a stale-slot bug, not a safe one. `agent-pull-requests`'
  installer (`install.ps1`/`install.sh`) already implements the refusal
  **control flow**, but its own `Get-PayloadHash`/`_payload_hash` only
  hashes `pyproject.toml` files, not `src/` — copy the control flow, not
  that narrower hash scope; `agent-pull-requests` itself needs the wider
  digest and stays in Phase 3's rollout list for that fix, not held up as
  already-complete. This refusal is the **target state for a plugin that
  adopts this contract** — it is not yet universal, and today's operator
  guidance (`docs/install-contract.md`, the
  `diagnosing-copilot-extensions` skill) still directs a `--force`
  rebuild as the recovery step for a stale/corrupt runtime on a
  not-yet-adopting plugin. Once a plugin adopts the refusal, `--force`
  must not bypass it for a content mismatch — `dev`/`dev-release` and the
  generation-based repair path below are its intended replacements for
  what that operator guidance currently reaches for `--force` to
  accomplish, and that guidance needs reconciling (naming the adopting
  plugins explicitly, or superseding the `--force` recommendation
  entirely) as part of each plugin's own adoption, not asserted as already
  true repo-wide by this doc alone.

This refusal is what actually closes the loop `dev` opened: without it, a
contributor (or an impatient automation) can still reach for "just run
install.ps1 again" and silently re-trigger the old hot-patch-equivalent
hazard against a numbered slot, bypassing `dev` entirely.

## Repairing a broken numbered slot is a cutover, not a delete-and-rebuild

A numbered slot can end up broken at runtime despite having a seemingly
valid completion marker — a partial write survived a crash in a way
`is_complete()`'s marker+hash check didn't catch, a dependency's own cache
corrupted independently of this plugin's payload, or a Windows file-lock
timeout left a half-updated interpreter. This is a **distinct case** from
the ordinary refuse-on-mismatch above: the payload hash may still match (so
the ordinary no-op/refuse logic sees nothing wrong), yet the slot is
provably unhealthy by a direct check (an import smoke-test, a health
endpoint, a corrupted trampoline).

**This is not a license to delete and rebuild the same slot directory.**
`immutable-versioned-runtime`
(`visions/plugin-services/README.md`'s *Features*) requires that "a new
version is installed **beside** the old one... switching versions... is a
selection, not a rewrite." Deleting `versions/<v>` and recreating a build
under that same identity still rewrites what that identity refers to — it
destroys the one build `last-known-good`/an operator's rollback intent
might still be pointing at, exactly as if the files had been edited in
place one at a time. Treat recovery as an ordinary generation cutover whose
trigger is a failed health check instead of a version bump, reusing the
exact serialization and promote-before-retire discipline
[`graceful-daemon-cutover`](graceful-daemon-cutover.md) already requires
for every other cutover/repair path in this repo:

1. **Confirm the slot is genuinely broken** via a direct check (import
   smoke-test, signature/trampoline validation, a declared health probe) —
   never assume brokenness from an absent marker alone. This case is
   explicitly **not** what `toss_incomplete()` already covers: that helper
   skips whichever slot `current-version` currently selects, so an active,
   live slot with a missing or invalid completion marker is exactly the
   gap this repair path exists for, not a case already handled elsewhere.
2. **Acquire the same exclusive cutover lease an ordinary update/promotion
   holds** before touching any generation state — a repair racing a
   concurrent installer run (or a second repair attempt) must not be
   possible, per *graceful-daemon-cutover*'s serialization rule.
3. **Build the replacement under a distinct generation identity**, never
   back into the broken slot's own directory, so the broken slot's
   identity is left completely untouched and `last-known-good` still means
   what it said before the repair started. Health-gate the new build in
   isolation, exactly like an ordinary install. **That distinct identity
   has two further obligations, not just "pick an unused name":**
   - It must **sort correctly against every existing recovery resolver**.
     A naive suffix like `<version>+repair1` is treated as an unsupported
     string by `versioned_runtime._version_key`
     (`libs/versioned-runtime/versioned_runtime.py:135-149`) and sorts
     *after* every real release — a later genuine release (`9.0.0`) could
     then lose to a stale repair generation during marker-loss recovery,
     and plugin-local/shell resolvers may each order it differently still.
     Whatever scheme a plugin picks must integrate with one shared,
     already-existing ordering rather than inventing a representation
     `_version_key` doesn't recognize.
   - It must **propagate everywhere a plain package version is otherwise
     compared or recorded**, not just the directory name.
     `agent-dispatch`'s `self_update` module compares a running daemon's
     self-reported `__version__`/`running-version.json` directly against
     `current-version` to decide whether it should act, from both its
     coordinator and supervisor call paths
     (`plugins/agent-dispatch/src/agent_dispatch/runtime_version.py`,
     `self_update.py`): activating a mismatched generation identity while
     the process still reports the plain package version makes that
     comparison mismatch forever, so self-update keeps treating an
     already-repaired daemon as stale, and ownership/ops records lose
     track of which slot is actually live. The generation identity needs
     its own first-class field in whatever record keeps that comparison
     (keeping the user-facing package version unchanged), not an ad hoc
     string appended only to the directory name.

   The exact representation that satisfies both obligations above is
   implementation detail for whichever plugin needs this first — this
   section fixes the *contract* (distinct identity, correctly ordered,
   propagated through every comparison, promote-before-retire, same lease
   as every other cutover), not the wiring.
4. **Promote before you retire, never the reverse**: once the replacement's
   health gate passes, atomically flip `current-version` (and any routing)
   to it. Only after that promotion is *confirmed* — not merely
   attempted — does the broken slot become eligible for retirement.
5. **Drain and retire the broken slot through the ordinary cutover path**:
   closed admission + any already-admitted work finished before removal,
   same as a routine cutover. Re-validate the broken slot's own identity
   immediately before removing it (its owner/lock/routing agreement may
   have changed between step 1's detection and this action) rather than
   trusting a stale snapshot — if nothing was ever live on it, this
   collapses to an immediate, uneventful removal. **The target is
   confirmed unhealthy, so its control endpoint may not be able to close
   admission or complete an ordinary drain at all.** When the broken slot
   is unreachable for a clean drain, follow *graceful-daemon-cutover*'s own
   fallback exactly: commit forward on the confirmed promotion (already
   satisfied by step 4) within a bounded, logged timeout rather than
   blocking repair indefinitely on a drain that may never complete, and
   never route anything back to the known-broken slot regardless of how
   its retirement concludes.

Because the replacement is built under its own distinct identity and
promoted before the broken slot is ever touched, this satisfies
`immutable-versioned-runtime` and `zero-downtime-cutover` by construction —
no identity is ever rewritten, and the broken slot's own (non-)rollback
value is lost only because it was never a valid rollback target to begin
with. Treat this as a rare, logged, operator-visible event (infrastructure
self-repair, not silent-and-routine), distinct from both the ordinary
install/update path above and from `dev`'s deliberately-mutable,
claim-gated exception.

## Rationale

The instinct this pattern satisfies is exactly the durable-vs-versioned
split in [`durable-vs-versioned-runtime`](durable-vs-versioned-runtime.md),
turned inward: most of a plugin's runtime lifecycle wants strict
immutability (safe rollback, no concurrent-mutation races, a
never-edited-underneath-a-running-daemon guarantee) — but a **worktree's own
iteration loop** is a legitimate, recurring exception that deserves a
first-class, protected home instead of an ad hoc hot-patch every session
re-invents from scratch. Making the exception a single well-known slot, with
an explicit claim gating who may touch it, keeps the exception auditable and
self-cleaning (GC reclaims it the moment nobody has it claimed) rather than
adding a second, uncontrolled mutability posture to the runtime.

## See Also

- [`durable-vs-versioned-runtime`](durable-vs-versioned-runtime.md) — the
  general immutable/durable split this pattern narrows for one specific,
  worktree-scoped exception.
- [`graceful-daemon-cutover`](graceful-daemon-cutover.md) — the
  serialization, promote-before-retire, and drain discipline *Repairing a
  broken numbered slot* reuses rather than inventing a parallel contract.
- `libs/versioned-runtime/versioned_runtime.py` — `claim_dev` / `release_dev`
  / `read_dev_claim`, and `gc()`'s dev-slot protection.
- `plugins/agent-worktrees/src/agent_worktrees/finalize.py` --
  `_warn_of_dev_slot_claims_for_worktree`.
- `efforts/active/mutable-dev-slot/README.md` — rollout status per plugin.
- `ThomasMichon/copilot-extensions#5472` / `#5468` — the audit that found
  most vendoring plugins' ordinary install/update path still mutates a
  numbered slot in place for a same-version, changed-content update,
  motivating this doc's *Ordinary installers* and *Repairing a broken
  numbered slot* sections.
- `CONTRIBUTING.md`'s *Hot-patching a deployed venv for fast pre-merge
  iteration* gotcha — the ad hoc technique this pattern is meant to
  eventually supersede once a plugin adopts `dev-claim`/`dev-release`.
- Hub: [`docs/patterns/`](README.md)
