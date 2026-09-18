---
visions:
  - visions/session-hosting
---

> **Migrated from a private facility repo** (`efforts/active/handoff-cutover-reload-robustness`)
> on 2026-09-13, as part of reconciling handoff/cutover effort tracking into
> the repo that actually owns the mechanism. Original frontmatter referenced
> that repo's vision `visions/change-pipeline`; updated above to this
> repo's `visions/session-hosting`, which covers the same intent. Content
> below was verbatim at time of migration, and updated on 2026-09-17 to
> reconcile with Sub D landing directly against the private repo's copy
> between the migration and this PR's merge (see the aperture-labs migration
> PR's rebase). **A trailing `†` marks a reference to that private repo's own
> issue tracker — not resolvable here.**

# handoff-cutover-reload-robustness — Stop the Cutover Successor Hanging on `consume_handoff`

- **Slug:** `handoff-cutover-reload-robustness`
- **Repo:** copilot-extensions (migrated 2026-09-13 from a private facility repo, which
  originally hosted this as effort home + tracking) — the `agent-worktrees` +
  `context-handoff` plugins and the Copilot CLI runtime.
- **Branch(es):** _historical source branch omitted (private identifier)_
- **Created:** 2026-08-20
- **Status:** Active — **Sub B (bash-first seed) shipped & deployed** (context-handoff
  `0.1.0-dev36`, the primary facility dev host), the effective fix, now **covered by unit tests + a
  clean-room scenario** (`context-handoff-cutover`, 11/0 on a fresh box). **Sub A
  closed as not-viable plugin-side** (see Phase 2) — deferred to runtime **Sub C**
  (#5253† / upstream #13494). **Sub D (env-var-free candidate confirmation)
  merged 2026-09-16** — copilot-extensions PR
  [#2757](https://github.com/ThomasMichon/copilot-extensions/pull/2757) (squash-merged,
  agent-worktrees `1.5.5-dev122`), tracked as #7072†.
  Effort essentially complete pending the upstream runtime fix + confirming Sub D
  on the next live Windows cutover.
- **Umbrella issue:** #5250†
- **Sub-issues:**
  #7072† (D — agent-worktrees psmux env-var-free candidate confirmation) ·
  #5251† (A — agent-worktrees bare-resume spawn) ·
  #5252† (B — context-handoff seed hardening) ·
  #5253† (C — CLI runtime orphaned external-tool call)
- **Sibling effort:** [`handoff-live-cutover`](../handoff-live-cutover/README.md)
  (#2249†) — builds the interactive/mux live cutover this effort *hardens*.
- **Vision:** `visions/session-hosting` — a handoff should **continue by
  itself**. An indefinite hang on the successor's first action violates that
  stated behavior, so this effort is **vision-closing**. (Originally recorded
  against that private repo's `visions/change-pipeline`; see the migration
  note above.)

## Guiding Intent

The live-cutover handoff is supposed to be hands-off: the predecessor spawns a
seeded successor in the same mux session, the successor consumes the stored
handoff and retires the predecessor, and work continues with a clean context
window. In practice the successor's **very first action** — `consume_handoff` —
can hang **indefinitely**, stranding the whole handoff until a human notices and
manually re-drives the session. This effort makes the cutover survive the Copilot
CLI's startup **extension-reload race** so the successor always reaches a consumed
handoff on its own.

## Context

`consume_handoff` is an **extension-provided (external) tool** from the
`context-handoff` plugin. When the successor Copilot starts, the CLI can reload
its extensions several times in the first ~15 seconds (a known runtime
generation race — upstream github/copilot-agent-runtime **#13492**, fix
**#13494**; also described in the facility's managed "Loading…/Resuming… hang"
note). If the `consume_handoff` request is routed to an extension instance that
is then **torn down** during that reload storm, the runtime emits **no**
`external_tool.completed`, does **not** re-route to the surviving instance, and
applies **no** timeout — so the model's tool call blocks forever.

Two design choices in the cutover make the race near-certain to fire:

1. **`agent-worktrees handoff-cutover` spawns the successor with cwd = the
   worktree path.** Starting in the repo cwd is exactly the condition the
   documented **Bare resume** workaround (cwd=`$HOME`) exists to avoid — it opens
   the reload/resume race window.
2. **The seed makes `consume_handoff` the successor's first action**, so the call
   fires squarely inside that window.

### Diagnostic findings (what the tool is actually waiting on)

- The tool call is **not** stuck in the retire step's "poll until the old pane is
  gone" loop. That poll is bounded (~6–16 s) and is the **last** step of the
  handler — and in both incidents it **never ran** (no `handoff_predecessor_retire`
  event; the task stayed `proposed`; the old pane stayed alive).
- The call is waiting on the `external_tool.completed` **IPC reply** from a
  **dead extension instance**. Killing the old pane does not help; **re-driving
  the successor** (so the model re-issues the still-`proposed`, idempotent call on
  a live extension instance) is the whole recovery.

### Reproductions (both on the primary facility dev host's WSL, 2026-08-20)

| Worktree | Hung | Recovery |
|----------|------|----------|
| incident 1 | ~8 h | operator killed the old pane; the successor session was re-driven, and the still-`proposed`, idempotent handoff call completed in under a second once a live extension instance picked it up |
| incident 2 | ~32 m | operator retried the handoff after the successor appeared stuck; the retry completed and the predecessor pane was retired cleanly shortly after |

_(Raw session/pane/task identifiers from the private incident record are
omitted here as operational noise with no public value; both are on file
in the private tracker.)_

## Request

> An agent on a private facility host was stuck for 8 hours on a
> `consume_handoff` tool call, followed shortly by a second worktree stuck the
> same way. We need to make this more robust. File issues, then drive fixes
> for them, using an effort to catalog the issues.

## Plan

### Phase 1 — Catalog (this effort + issues)
- [x] Diagnose both incidents from `events.jsonl`, the agent-worktrees
      `activity.jsonl`, and the extension host logs (read-only; no touching the
      live sessions).
- [x] File umbrella #5250† + sub-issues #5251† / #5252† / #5253†.
- [x] Author this effort and submit it for review (reviewed intent before code).

### Phase 2 — Sub A: bare-resume the successor spawn (#5251†) — **DEFERRED (not viable plugin-side)**
Investigated in depth (2026-08-21) and **closed as a plugin-side fix** — deferred
to the runtime fix (#5253† / upstream #13494). Findings:
- A cutover successor is a **fresh** session, so launching it with cwd=`$HOME`
  degrades the **whole session** (not just startup): the Copilot bash tool does
  **not persist `cd`** between calls, so every later command would run from
  `$HOME`. The two-step "Bare resume" only escapes this because it immediately
  `/resume`s (Copilot's resume-auto-cd drops back into the worktree) — a fresh
  successor has no resume to auto-cd.
- The `/cwd` command *does* move the session cwd persistently, so "launch in
  `$HOME`, then `/cwd` into the worktree once ready" looked promising — **but the
  operator confirmed extensions re-register (reload) after a runtime `/cwd`.** So
  the reload race simply **follows you into the worktree**; there is no plugin-side
  way to be in the worktree without triggering the reload.
- Secondary blockers (would have needed solving anyway): a fresh successor needs a
  **new unscoped-bind** path (the existing bind is session-id-scoped and cwd
  inference dies at `$HOME`), and `handoff-cutover --retire-pane` **fails from a
  `$HOME` cwd** ("could not resolve a project").
- **Conclusion:** the correct layer is the runtime (#13494 — the reload must not
  orphan in-flight external-tool calls). **Sub B already makes the successor's
  critical actions immune regardless of *when* the reload fires** (core `bash`
  tool, not an extension tool), so it holds even across a `/cwd`-triggered reload.
  No plugin change beats that without the whole-session cwd degradation.

### Phase 3 — Sub B: don't make `consume_handoff` the first action (#5252†) — **DONE**
- [x] In `context-handoff/extensions/context-handoff/extension.mjs`, make the
      task-backed cutover seed **bash-first**: the successor's first action is a
      single shell chain (`agent-dispatch consume <id> --defer-complete &&
      agent-worktrees conclude-session … && agent-worktrees handoff-cutover
      --retire-pane …`) — exactly what `consume_handoff` shells to — so **no
      extension tool sits in the reload-window critical path** (the `bash` tool is
      core, not extension-provided, so it can't be orphaned). File-backed handoffs
      and unknown-pane cases keep the tool-based seed + retry clause.
- Shipped in **context-handoff `0.1.0-dev35`** (copilot-extensions PR
  [#854](https://github.com/ThomasMichon/copilot-extensions/pull/854), public
  issue [#853](https://github.com/ThomasMichon/copilot-extensions/issues/853));
  **deployed on the primary facility dev host** (`agent-worktrees update`, dev32→dev35).

### Phase 4 — Sub C: runtime orphaned-call fix (upstream track, #5253†)
- [ ] Verify whether deployed CLI `1.0.81-5` carries runtime #13494.
- [ ] Track the upstream fix: fail in-flight external-tool requests with a
      retryable error on extension-generation teardown, and/or a client-side
      watchdog. (Mitigations A/B keep the race from firing in the meantime.)

### Phase 6 — Sub D: env-var-free candidate confirmation (#7072†) — **shipped & merged**
- [x] Diagnose the cd0e stacking-panes incident (5 stacked sessions,
      `pending_handoffs[0].candidate: null`).
- [x] Add `associate_pane_matched_candidate` (pane/process-ancestry match, no
      env var) to `sessions_pane_retire.py`; wire it into
      `_wait_for_handoff_candidate` alongside the existing token self-report.
- [x] Regression tests + fix a pre-existing unrelated test bug found while
      validating (`tests/test_handoff_cutover.py`, 84 passed).
- [x] Open, iterate through Copilot review (race-loser return, TOCTOU
      revalidation, pane-scoped trust of a stale record-wide candidate,
      throttled scanning, neutral PR metadata, doc-impact statement), and
      squash-merge copilot-extensions PR #2757 (agent-worktrees `1.5.5-dev122`).
      Along the way, fixed two pre-existing, unrelated `main`-branch CI breaks
      hit mid-flight (a missing skill-table row blocking the shared
      docs-consistency guard, and a module-size baseline race from a
      concurrently-merged PR) — confirmed pre-existing via a clean
      `origin/main` checkout before touching either.
- [ ] Confirm on the next live Windows/psmux cutover in the field.
- [x] Deployed on lambda-core (`agent-worktrees update` picks up dev122 on
      next launch); close #7072† once field-confirmed.

### Phase 5 — Land + verify
- [x] Land Sub B in copilot-extensions via `working-cross-repo` (PR #854, deployed).
- [ ] Confirm Sub B on the next real facility cutover (interactive; can't be driven
      headlessly).
- [x] Close Sub B (#5252†); update umbrella #5250†. Sub A (#5251†) deferred to #5253†.

## Validation Plan

- [x] **Clean-room robustness scenario** (`context-handoff-cutover`, Tier-P F1 in
      copilot-extensions): on a fresh Docker box, proves (3) the shipped
      `cutover-seed.mjs` builds a **bash-first** task-cutover seed (successor's
      first action is a core `bash` chain, not the `consume_handoff` extension
      tool), (4) the seed's three CLI verbs are real, (5) the retire verb kills a
      live tmux pane. **Result: 11 passed / 0 failed.** Plus a `node --test` unit
      suite (`cutover-seed.test.mjs`) wired into CI (8 pass).
- [x] **Retire path intact:** exercised live in the clean room (Phase 5) and in
      the field (the 9a22 recovery: `handoff_predecessor_retire` `outcome=gone`).
- [ ] **Full interactive cutover on the next real facility handoff** (needs a TTY;
      can't be driven headlessly) — confirm the successor reaches a consumed
      handoff with no manual re-drive.
- [ ] **No regression on Windows/psmux** (the sibling effort's substrate).

## Journal

### 2026-09-15 — Sub D: env-var-free candidate confirmation (psmux stacking-panes fix)
- **Live incident:** worktree `lambda-core-win-20260725-193449-cd0e` accumulated
  **5 stacked live Copilot sessions** on one pending handoff (opened
  2026-09-15T22:07:49Z); `agent-worktrees head-session --json` showed
  `pending_handoffs[0].candidate: null` throughout. Each successor also failed to
  reliably discover `/consume-handoff`/the context-handoff skill on its first
  turn, floundered, then a fresh pane spawned on top of it.
- **Root cause (distinct from Sub A/B/C):** the mux spawn path's
  `_wait_for_handoff_candidate` waited up to 30s for the successor's own
  sessionStart hook to self-report `AGENT_WORKTREES_HANDOFF_TOKEN`, propagated
  into the new pane via `psmux new-window -e KEY=VAL`. **psmux does not reliably
  propagate `-e` custom env values into the actual Copilot child process's
  environment on Windows** the way tmux does — so the token was silently never
  observed, the wait always timed out, the whole spawn was reported failed even
  though a real live successor pane existed, and the caller spawned *another*
  successor on top. Operator directive: "let's not expect windows to have *any*
  relevant environment variables defined; the Manager and Mux system need to
  walk the Mux list or subscribe to Mux, and use process hierarchies if
  possible."
- **Fix (agent-worktrees `1.5.5-dev121`):** added
  `associate_pane_matched_candidate` — an env-var-free confirmation path that
  reuses `mux_binding_for_session`'s existing exact-session, no-sweep,
  process-ancestry machinery (walks the mux's own pane list + the live process
  tree, never `~/.copilot/session-state` itself, honoring
  `docs/patterns/session-state-access.md`) to check whether a session already
  registered on this worktree (ordinary `register-session` on sessionStart,
  unconditional on any handoff token) is running under the exact pane the
  cutover just opened. On a match, agent-worktrees associates the candidate
  itself — the successor never needs to self-report anything. Races alongside,
  never replaces, the existing token self-report (tmux/Linux unaffected).
- Moved the new logic into `sessions_pane_retire.py` (not `__main__.py`) after
  CI's module-size guard caught the grandfathered `__main__.py` baseline being
  pushed 20 lines over its shrink-only ceiling — `__main__.py` now keeps only a
  thin backward-compatible shim.
- Two new regression tests in `tests/test_handoff_cutover.py`; also fixed a
  pre-existing test bug found while validating (`mux_retire_pane` was
  monkeypatched on the wrong module after an earlier extraction). Full suite:
  81 passed.
- Shipped: copilot-extensions PR
  [#2757](https://github.com/ThomasMichon/copilot-extensions/pull/2757).
  Tracked as sub-issue #7072†. The
  `/consume-handoff` discovery flakiness on a fresh successor's first turn is
  filed as a follow-up in the same issue — likely the same extension-reload
  race as Sub C (#5253† / upstream #13492/#13494), not yet independently fixed.

### 2026-09-16 — Sub D squash-merged after review + two unrelated main-branch CI fixes
- Copilot's automated review caught real issues across four rounds, each
  fixed and re-pushed: (1) the pane-match path could return a race *loser* as
  the confirmed candidate when `associate_handoff_candidate` raised because
  another session already won the token; (2) the pane/process scan ran on
  every 50ms poll tick (throttled to 1/s); (3) a record-wide `handoff.candidate`
  was trusted without confirming it belonged to *this* wait's pane (a
  racing/earlier attempt could set it for a different pane); (4) the same
  race-loser association needed to revalidate liveness under the lock
  (TOCTOU). Added tests for the race-loser and stale-different-pane cases.
- Mid-flight, the branch hit **two pre-existing, unrelated breaks already
  present on `origin/main` itself** (confirmed via a clean checkout before
  touching either, per error-response discipline — not assumed): a
  module-size-baseline race from a concurrently-merged PR (resolved by
  rebasing once main's own follow-up fix landed), and a missing skill-table
  row (`hoisting-plugin-agents`) failing the repo-wide `check-docs-consistency`
  guard for every PR regardless of what it touches (fixed with a one-line
  doc addition + the plugin's own required version bump). A third,
  deeper pre-existing failure (`agent-codespaces`' session-start-stack
  declaration) was investigated and confirmed unrelated + non-blocking (the
  PR's `mergeable`/`merge_state` were clean throughout; only `not yet approved`
  gated it) — left untouched as out of scope.
- Squash-merged via `pr-merge --now` (agent-worktrees `1.5.5-dev122`); worktree
  finalized.

### 2026-08-21 — Clean-room + unit tests for the bash-first fix
- Made the seed's bash-first invariant provable without a live cutover. Extracted
  the pure seed builders (`leadFrom` + `buildCutoverSeed`) from `extension.mjs`
  into a sibling SDK-free module `cutover-seed.mjs` (verified the extension still
  loads — reaches `ready`, no module error), so the seed SHAPE is importable by
  a test and by the clean room.
- **Unit tests** (`plugins/context-handoff/tests/cutover-seed.test.mjs`, `node
  --test`, 8 pass): task+known pane/wt/sid ⇒ bash-first (three shell verbs, no
  `consume_handoff` tool); file/unknown-pane ⇒ tool-based fallback; single-line
  ASCII. Wired a JS-test step into copilot-extensions CI (`checks` job) so
  `plugins/*/tests/*.test.mjs` actually run (pytest-only CI would have orphaned
  them) — confirmed green in CI.
- **Clean-room scenario** `context-handoff-cutover` (Tier-P F1, `tools/clean-room`):
  on a fresh Docker box, install the live-cutover trio and assert (3) the SHIPPED
  `cutover-seed.mjs` builds a bash-first task seed (imported via `seed-probe.mjs`),
  (4) the seed's three CLI verbs are real, (5) the retire verb kills a live tmux
  pane. First run proved the fix (Phase 3) but hit the known agent-worktrees
  self-provision gap + a PATH issue; a follow-up made the scenario self-contained
  (installer-provision the runtimes, export `~/.local/bin`, context-independent
  verb recognition). **Final: 11 passed / 0 failed on a fresh box.**
- The hang itself is a runtime timing race (not deterministically reproducible),
  so the tests target the fix's **invariant** (bash-first seed) and **mechanism**
  (the verbs + live retire), which is what makes a regression catchable.
- Shipped: copilot-extensions PR #889 (seam + tests + scenario; context-handoff
  dev35→dev36, harness dev19→dev20) and #890 (scenario determinism); deployed on
  the primary facility dev host.

### 2026-08-20 — Effort carved from two live incidents
- Diagnosed ef44 (~8 h) and 9a22 (~32 m) stuck `consume_handoff` calls. Root
  cause: Copilot CLI extension-reload generation race orphans the in-flight
  `external_tool.requested` when the servicing `context-handoff` instance is torn
  down mid-startup; no completion, no re-route, no timeout. Made near-certain by
  the cutover spawning the successor in the worktree cwd and by `consume_handoff`
  being the first action.
- Confirmed it is **not** the retire pane-poll: no `handoff_predecessor_retire`
  until the successful retry; task stayed `proposed`; old pane outlived the hang.
- 9a22 recovered when the operator spammed Escape and asked it to retry — the
  model re-issued the idempotent call on the live extension instance (`796599`),
  which completed in ~1 s and then retired `%20` (`method=hard`, `gone`).
- Filed umbrella #5250† + subs #5251† (agent-worktrees bare-resume), #5252†
  (context-handoff seed), #5253† (runtime orphaned call). Effort submitted for
  review before any code lands (reviewed intent first).

### 2026-08-20 — Sub B shipped & deployed (bash-first cutover seed)
- Found the source already carried a *retry-on-not-ready* clause for this race —
  but it only helps when the bad call fails **fast** (a 400 / tool-not-found the
  model can retry); it does nothing for the **silent hang** both incidents hit.
- Fix (context-handoff `0.1.0-dev35`): the task-backed cutover seed is now
  **bash-first** — the successor's first action is a single shell chain
  (`agent-dispatch consume --defer-complete && agent-worktrees conclude-session …
  && agent-worktrees handoff-cutover --retire-pane …`), the exact verbs
  `consume_handoff` shells to. The `bash` tool is core (not extension-provided),
  so it can't be orphaned by the reload storm. File-backed + unknown-pane cases
  keep the tool-based seed.
- Landed via copilot-extensions PR
  [#854](https://github.com/ThomasMichon/copilot-extensions/pull/854) (public
  issue [#853](https://github.com/ThomasMichon/copilot-extensions/issues/853),
  now closed); self-merged; **deployed on the primary facility dev host** (dev32→dev35).
- Validation: seed rendered single-line/ASCII with the exact verbs; the retire
  verb was exercised against a throwaway tmux pane (`gone`, graceful); `consume`
  / `conclude` are production-proven (the 9a22 recovery used them). A full
  **interactive** cutover needs a TTY session and will be confirmed on the next
  real facility cutover.
- **Sub A** (bare-resume spawn) — see the 2026-08-21 journal entry: investigated
  and **closed as not viable plugin-side**; deferred to runtime **Sub C** (#5253† /
  upstream #13494). Deployed CLI here is `1.0.81-5`.

### 2026-08-21 — Sub A investigated and closed (plugin-side not viable)
- Explored bare-resuming the successor spawn (cwd=`$HOME`) to dodge the startup
  reload storm. Two viable-looking designs, both dead-ended:
  - **cwd=`$HOME` for the fresh successor** degrades the *whole* session — the
    Copilot bash tool doesn't persist `cd`, so every later command runs from
    `$HOME`. Bare-resume only escapes this via `/resume` auto-cd, which a fresh
    successor doesn't have.
  - **Launch in `$HOME`, then `/cwd` into the worktree once ready** — the operator
    confirmed **extensions re-register (reload) after a runtime `/cwd`**, so the
    race just follows you into the worktree. No plugin-side way to be in the
    worktree without triggering the reload.
- Secondary blockers found & verified: fresh successors need a new **unscoped-bind**
  (existing bind is session-id-scoped; cwd inference dies at `$HOME`), and
  `handoff-cutover --retire-pane` **fails from `$HOME`** ("could not resolve a
  project").
- **Decision:** defer Sub A (#5251†) to the runtime fix (#5253† / upstream #13494).
  Sub B is the right layer — it makes the successor's critical actions immune to
  the reload *whenever* it fires (core `bash`, not an extension tool). Scratch
  worktrees created during the investigation were removed; no code shipped for A.

## See Also

- [`handoff-live-cutover`](../handoff-live-cutover/README.md) — the cutover this
  effort hardens (#2249†).
- Umbrella #5250†.
