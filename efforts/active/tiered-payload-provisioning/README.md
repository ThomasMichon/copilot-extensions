# Tiered payload provisioning — cheap, deferred reconcile for session-start hooks

- **Slug:** `tiered-payload-provisioning`
- **Repo:** copilot-extensions
- **Branch(es):** `copilot-extensions create` per phase; never the anchor
- **Created:** 2026-09-09
- **Status:** Draft
- **Vision:** `visions/plugin-services` §Features/`self-provisioning-runtime`,
  §Behaviors/`hooks-and-callbacks-are-transient`; extends
  `docs/install-contract.md` rule 10 (stamp + deferred provision) to cover
  version-drift reconcile, not just first install.
- **Related efforts:** [`plugin-process-hygiene`](../plugin-process-hygiene/README.md)
  (process-count hygiene generally; this effort is the concrete fix for one
  named cost driver its Phase 1/#2317 only gated rather than cured),
  [`self-provisioning-runtime`](https://github.com/tmichon_microsoft/dotfiles/blob/main/efforts/active/copilot-extensions/self-provisioning-runtime/README.md)
  (the launcher-triggered `reconcile-plugins` mechanism this effort's stamp/
  provision split is consistent with, but does not replace),
  [`installer-update-robustness`](https://github.com/tmichon_microsoft/dotfiles/blob/main/efforts/active/copilot-extensions/installer-update-robustness/README.md)
  (payload-lock/singleton-dir robustness; orthogonal — that effort is about a
  install surviving contention, this one is about not attempting a full
  install from a hook at all).
- **Umbrella issue:** _to file once this design clears review_.

## Guiding Intent

A session-start hook's own cost must not scale with how often the plugin's
source changes. During active development (this repo, right now) that is
*continuously* — so any reconcile strategy whose cost is "proportional to how
stale the deployed runtime is" is proportional, in practice, to "how often a
session starts while the maintainer is iterating." The fix is not to gate the
expensive path behind an opt-in (that only changes who pays the cost, not
whether it's expensive) — it's to make the **hook's own action always cheap**,
and push every genuinely expensive step (venv build, dependency install) to
the one place that can afford to pay it exactly once per version: the
plugin's own binstub, on its first actual invocation of that version.

**Every version update goes through this same path — there is no separate
"first install" case.** A plugin with no manifest yet and a plugin whose
manifest names an older version are the same state (deployed != expected);
first-install is not special-cased, it is simply the case where "deployed"
is absent. One stamp/provision path, one set of guarantees, for every update.

That path must also be **safe under concurrency, not just cheap in
isolation**. Many sessions/worktrees can start at once against the same
installation cell; several binstub invocations can race the same not-yet-built
version. Cheapness alone doesn't stop N concurrent stampers from redundantly
copying the same snapshot, or N concurrent binstubs from redundantly building
the same venv — that's still runaway-process territory, just with a cheaper
per-process cost. The stamp and the provision steps each need **at most one
active worker at a time, per installation cell, per version** — a loser
serializes behind the winner and reuses its result, it never redoes the work
or renders/dispatches against a half-finished one.

## Request

> Gating the spawn behind config just mitigates an incident, but I need the
> reconcile to be cheap, knowing that it will get invoked a lot.

> Yes, make an effort and a design for this. In our new regime, the plugin
> payload folder (cloned over by Copilot) will need to contain the
> "binstubs". These need to resolve the version from their plugin folder, and
> then ensure the plugin's installable payload is blitted into the versioned
> user-global install folder for the plugin (should already be done by the
> hook, but will need on-demand ensurance). Then, the plugin's binstub will
> ensure the actual installation (creation of venv, install.ps1/sh) on first
> usage for that version, followed by actually invoking the installed version
> of the expected command (python -m ___/agent-*.py ___).
>
> Somewhere between when the sessionStart hook runs, and the agent
> subsequently invokes an `___/agent-*` command, we'd like the plugin payload
> to be copied over. Challenge is that sessionStart hooks might need this
> ASAP; perhaps plugins with sessionStart hooks get mini payloads so they
> don't block forcing installation of the whole service on first load.

> Oh yeah the stamp-and-provision later flow is supposed to apply to every
> version update, too. And per your guidance we need a serialized, debounced
> flow for that, to prevent contention and runaway processes.

## Context

### The existing contract already prescribes this shape — for first install only

`docs/install-contract.md` rule 10 ("Fast install + deferred self-provision")
already splits a plugin's bring-up into exactly the two tiers the Request
describes:

- **`stamp`** — snapshot the payload source into the versioned slot area
  (`~/.<name>/snapshots/<ver>/` + a `payload-dir`/`stamped-version` marker on
  Windows; a `payload-dir` pointer on POSIX) and deploy the **self-
  provisioning binstub**. No venv build. Fast enough to run inline from a
  `sessionStart` hook's own grace window.
- **`provision`** — the deferred heavy build (venv + `uv pip install` +
  versioned activate + manifest), run from the slot-local snapshot **the
  binstub invokes on first use** of a version not yet built.

`agent-worktrees.ps1` (the installed binstub, not the plugin payload) is the
existing, working reference for the *provision-on-first-use* half: it
resolves the `current-version` marker, and if no runtime slot exists yet,
self-provisions via the snapshot's `install.ps1 provision` before dispatching
to the built interpreter (`~/.local/bin/agent-worktrees.ps1`, read during this
session's own conhost investigation).

### Where reality diverges: version-drift reconcile bypasses stamp/provision entirely

Rule 10 covers **first install** (no manifest yet). It does not yet cover
**version-drift reconcile** (a manifest exists, but the deployed version no
longer matches the currently-enabled payload — the state most session starts
are actually in during active development). For that case,
`plugins/agent-bridge/scripts/bootstrap-check.ps1` (confirmed by direct read,
2026-09-09) does something categorically different from stamp/provision: on
detecting drift, it directly spawns a background `conhost --headless` ->
`pwsh` -> `install.ps1 install -NonInteractive` — a **full** re-install
(venv rebuild included), from the hook, every time drift is detected. Gating
this behind an explicit `background_reconcile: true` opt-in
([copilot-extensions#2317](https://github.com/ThomasMichon/copilot-extensions/pull/2317),
merged 2026-09-09) stopped the *silent, ungated* flooding, but does not change
what happens once an operator opts in: the same eager, expensive, per-session
action recurs at whatever rate the source drifts — which, for an actively
iterated repo, is close to "every session."

The code's own comments confirm the intended shape was always stamp/provision
— it just isn't wired for the drift case yet, and isn't even implemented for
first-install on Windows:

> `bootstrap-check.ps1` (first-install branch): "do the cheap FIRST install
> ('stamp') so the self-provisioning binstub is on PATH this session; the
> binstub then builds the venv on first use (#1393)... NOTE: agent-bridge's
> install.ps1 does not yet expose a 'stamp' action (the Windows
> self-provisioning lane is a follow-up), so on Windows this is currently a
> no-op."

So on Windows, agent-bridge's session-start hook has **zero** cheap path
today: first-install stamp is a no-op, and drift-reconcile is the expensive
eager path. This effort's Phase 2 closes both gaps together, because the fix
is the same primitive applied to both states — first-install and drift are
not two cases, they are the same case (deployed != expected).

### Every stamp and every provision must serialize — cheap is not the same as safe

Making each individual stamp/provision attempt cheap does not, by itself,
prevent many of them from running at once. A machine with several worktrees
or concurrent Copilot sessions against the same installation cell can fire
several `sessionStart` hooks within milliseconds of each other, and several
binstub invocations can race a not-yet-built version at once. Without
serialization:

- **N concurrent stampers** redundantly re-copy the same snapshot, and a
  reader mid-copy can observe a torn/partial snapshot if the copy isn't
  atomic end-to-end.
- **N concurrent provisions** redundantly build the same venv — competing
  `uv`/`pip` processes writing into the same target directory is exactly the
  "runaway process" failure mode this whole effort exists to close, just
  with a cheaper per-process cost than today's full reinstall.

The fix is **at most one active worker per installation cell per version**,
for both steps, using the primitive this suite already has for exactly this
shape: `libs/single-instance-lease` (`SingleInstance` — an OS-level,
liveness-reconciled exclusive lock; a loser's acquire raises
`AlreadyRunningError` naming the winner rather than racing it). A losing
caller does not redo the work or proceed against a possibly-stale result — it
waits for the winner to finish, then re-reads what the winner produced (the
marker file for stamp; the built slot for provision). This mirrors the
contention policy `plugin-process-hygiene`'s Phase 4c just designed for a
different shared resource (`_classify_records`, the Worktree Manager's batch
classification pass): a losing caller waits for the winner's pass and
re-reads its repaired result rather than computing its own competing answer.
Extending that same, already-reviewed policy to stamp/provision reuses a
proven pattern rather than inventing a new one for this effort.

**Debounce is the fast-exit half of the same lock**, not a separate timer.
Once a caller holds the lease, its first act is to re-check whether the work
is still needed at all (compare the snapshot's recorded source commit /
built-slot version against current) — if a previous holder already did it
moments ago, the new winner's own attempt is a near-instant no-op before it
releases the lease, rather than every queued caller redoing the copy/build in
turn. A burst of concurrent session-starts against unchanged source thus
costs one lock acquisition each, and at most one real stamp.

### The new wrinkle: some hooks need live payload content, not just a version check

agent-bridge's `write-session-guidance.ps1` and agent-worktrees'
`hook_client.py` are session-start hooks that do more than compare version
strings — they read/write real content (session guidance text, a live socket
request to a resident monitor) and must run correctly on **every** session
start regardless of whether a stamp/provision cycle has completed recently.
If "stamp" ever grows expensive enough to defer or debounce (a large payload,
a slow filesystem), those hooks must not silently start reading a half-copied
or stale snapshot. The Request's proposed answer is a **mini payload**: a
small, hook-declared fileset (e.g. `hook_client.py` + whatever it directly
imports) that is always kept current synchronously and cheaply, decoupled
from the full payload snapshot the eventual `provision` step consumes.


## Plan

### Phase 1 — Design (this PR; no code)
- [ ] Land this effort + design as the reviewed plan.
- [ ] Formalize the **unified stamp** contract: a `sessionStart` hook's *only*
      permitted action, on any drift (first-install or version-drift alike),
      is to refresh the versioned-slot snapshot + marker (file copy, no
      subprocess, no venv touch) and update the self-provisioning binstub if
      needed. It **never** spawns a background install/provision itself,
      full stop — this replaces `bootstrap-check.ps1`'s current
      drift-reconcile branch, not just gates it.
- [ ] Formalize **provision-on-first-use** as the *only* place a venv/
      dependency build happens: the installed binstub, dispatched by the
      user's own invocation of the plugin's command, resolving its own
      version from the plugin payload folder it ships beside, ensuring the
      snapshot is current (on-demand, in case the hook's own stamp hasn't
      run yet or lost a race), then provisioning if the target version's slot
      isn't already built, then dispatching to it. Cite
      `agent-worktrees.ps1` as the working reference implementation to
      generalize from.
- [ ] Formalize the **mini-payload declaration**: a new, optional
      `hookCriticalFiles` (name TBD) list in `plugin.json` (or a sibling
      manifest) naming the small fileset a plugin's own hook scripts need
      verbatim-current on every invocation. The stamp step always refreshes
      this subset synchronously first (cheap — a handful of files), then the
      rest of the snapshot on whatever cadence stamp itself runs at. A plugin
      with no `sessionStart` hook needing live content declares nothing and
      gets no special treatment.
- [ ] Formalize the **serialized, debounced stamp**: every stamp attempt
      acquires a `single_instance_lease.SingleInstance` keyed on
      `(installation cell, "stamp")` before touching the snapshot. A losing
      caller waits (bounded) for the winner, then re-reads the marker the
      winner wrote — it never runs its own competing copy. The winner's own
      first act under the lock is the debounce check: compare the snapshot's
      recorded source commit against current; if unchanged, release
      immediately as a near-no-op. Applies uniformly — first-install and
      drift are the same case under this lock, not two.
- [ ] Formalize the **serialized, debounced provision**: symmetric design for
      the binstub's provision-on-first-use step, keyed on
      `(installation cell, "provision", target version)`. A losing binstub
      invocation waits for the winner's build to complete, then dispatches to
      the now-built slot — it never starts a second concurrent venv build.
      The winner's debounce check is "is this version's slot already built
      and marked complete?" (the existing `.install-complete.json` marker
      convention from `versioned_runtime.py`) before it does any real work.
- [ ] Decide the bounded-wait budget and its failure mode for both locks
      (what a caller does if the winner appears wedged, not just slow) —
      reuse `single_instance_lease.reaper`'s reconcile-set-reap shape rather
      than inventing a new stale-holder policy.
- [ ] Write the design's acceptance criteria into the Validation Plan below
      before any implementation PR.

### Phase 2 — Reference implementation on agent-bridge
- [ ] Implement the unified stamp (drift and first-install share one code
      path) in `bootstrap-check.ps1`/`.sh`, replacing the current eager
      background-install branch entirely (not just its opt-in gate), guarded
      by the serialized/debounced lock designed in Phase 1. **The stamp lock
      must be a native PowerShell/bash primitive, not the Python
      `single_instance_lease`** — `bootstrap-check` runs pre-venv, sometimes
      pre-any-python, by design (its own docstring: "no python/venv dependency
      ... the whole point of it being pure PowerShell"). `agent-ssh`'s
      `dtssh-host-launcher.ps1` is the existing precedent to generalize from:
      a named `System.Threading.Mutex` (`Global\<Name>Stamp_<cell>`) on
      Windows, a `flock`-held file descriptor on POSIX bash — same contention
      policy (wait for winner, re-check, near-no-op debounce), native
      implementation per language.
- [ ] Implement/complete the Windows `stamp` action in `install.ps1` (closes
      the "currently a no-op on Windows" gap).
- [ ] Update the installed binstub to perform on-demand snapshot-ensure ->
      provision-if-needed -> dispatch, matching `agent-worktrees.ps1`'s
      existing shape, with the provision half guarded by the real
      `single_instance_lease` Python library — the binstub only reaches this
      step once a system/bootstrapping Python is already resolved, so the
      shared lib is safely reachable there (unlike the stamp step above).
- [ ] Declare `write-session-guidance.ps1`'s own file dependencies as the
      first real `hookCriticalFiles` set; prove the hook still runs correctly
      immediately after a version bump, before any provision has occurred.

### Phase 3 — Fan out
- [ ] Apply the same unified-stamp shape to the sibling plugins PR #2317 left
      out of scope (agent-dispatch, agent-ssh, and others still confirmed
      carrying the eager background-spawn shape) — this effort's fix
      *replaces* the need for each of them to adopt the opt-in gate
      individually, since there is no longer an expensive hook-triggered path
      to gate.
- [ ] Update `tools/check-install-contract.py` (`_declares_stamp_provision`)
      to also flag a `sessionStart` hook that spawns a subprocess-based
      install/reconcile directly, not just check for the *presence* of a
      `stamp` action.

## Validation Plan

- [ ] **Field before/after**, same technique as the `plugin-process-hygiene`
      effort: process count + spawned conhost/pwsh count across N session
      starts with the payload deliberately left drifted, before and after
      this effort's Phase 2 lands on agent-bridge. Expect zero background
      subprocess spawns from the hook in either state (first-install or
      drift), only a cheap file-copy stamp.
- [ ] **Correctness under drift**: a session-start hook that needs live
      payload content (`write-session-guidance.ps1`) produces correct output
      immediately after a version bump, with no provision having run yet —
      proving the mini-payload declaration actually decouples hook
      correctness from full provisioning.
- [ ] **Provision-on-first-use still works**: the first actual invocation of
      the plugin's command after a version bump triggers exactly one
      provision (venv build), and every subsequent invocation of the same
      version dispatches directly to the already-built slot with no rebuild.
- [ ] **Concurrency: N simultaneous stampers, one real stamp.** Spawn N (e.g.
      20) real OS processes invoking the stamp path at once against the same
      installation cell with genuine drift present. Assert: exactly one
      performs the actual copy/marker-write, the rest wait and then observe
      the winner's result, and the OS process count returns to baseline after
      all N exit (same tag-scoped census technique the
      `plugin-process-hygiene-convergence` mock proved out) — no leftover or
      wedged lock-holder.
- [ ] **Concurrency: N simultaneous provisions, one real venv build.**
      Symmetric test against the binstub's provision step: N concurrent
      invocations of a not-yet-built version result in exactly one venv build
      and N successful dispatches to it, none racing a second build.
- [ ] **Debounce: a burst against unchanged source is near-free.** Repeat the
      stamp concurrency test with **no** actual drift present; assert every
      caller's own lock-held duration is dominated by the debounce check, not
      a real copy — bounding the *cost*, not just the *count*, of a
      no-op burst.
- [ ] **No regression to `check-install-contract.py`** and the existing
      version-consistency guards.
- [ ] **Windows-specific**: the previously-documented "stamp is a no-op on
      Windows" gap is closed and covered by a test analogous to the existing
      `test_bootstrap_check_reconcile_opt_in.py` shape (file-shape assertions
      plus real executions against an isolated fake plugin/install dir).

## Proposal

_Pending — this section fills in once Phase 1's design is drafted in full and
ready for review._

## Journal

### 2026-09-09 — Kickoff

Effort created directly from an operator critique of
[copilot-extensions#2317](https://github.com/ThomasMichon/copilot-extensions/pull/2317):
gating agent-bridge's background reconcile behind an opt-in flag stops the
*silent* flooding but does not make the reconcile itself cheap, and for an
actively-iterated repo, the reconcile fires close to every session. Read
`docs/install-contract.md` rule 10 and confirmed it already specifies the
correct shape (cheap stamp, deferred provision-on-first-use) — but only for
first install, and `bootstrap-check.ps1`'s own code comments confirm the
Windows stamp action doesn't exist yet, while the drift-reconcile path bypasses
the stamp/provision split entirely with an eager background full-install.
`agent-worktrees.ps1`'s installed binstub is the working reference for
provision-on-first-use to generalize from. Captured the Request's new
"mini payload" idea as a formal `hookCriticalFiles` declaration, decoupling a
hook's own live-content needs from the cadence of the full payload snapshot.

### 2026-09-09 (later) — Serialization/debounce made explicit, not optional

Operator follow-up clarified two points that reshaped Phase 1: (1) the unified
stamp/provision path applies to **every** version update, full stop — there is
no separate "first install" case, only "deployed != expected"; (2) cheap is
not the same as safe under concurrency, and a real serialized + debounced flow
is required, not optional-if-measured-expensive as Phase 1's first draft had
hedged. Replaced the "audit whether debouncing is needed" checkbox with a
concrete design: `single_instance_lease.SingleInstance` keyed per
(installation cell, step, version), a losing caller waits for the winner and
re-reads its result rather than racing or computing its own answer — the same
contention policy `plugin-process-hygiene`'s Phase 4c just designed for
`_classify_records`, reused rather than reinvented. Debounce is framed as the
lock-winner's own fast-exit check (compare recorded state vs. current; no-op
if unchanged), not a separate timer. Caught one real design error before it
reached Phase 2: `bootstrap-check.ps1` runs pre-venv, sometimes pre-any-python
by its own documented design, so the *stamp*-side lock cannot be the Python
`single_instance_lease` — it needs a native PowerShell/bash primitive, with
`agent-ssh`'s `dtssh-host-launcher.ps1` (already using a named
`System.Threading.Mutex`) as the existing precedent to generalize from. The
*provision*-side lock (binstub-driven, python already resolved by then) can
use the real shared library directly. Added matching concurrency + debounce-
cost cases to the Validation Plan.
