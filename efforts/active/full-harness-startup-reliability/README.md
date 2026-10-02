# Full-Harness Startup Reliability

- **Slug:** `full-harness-startup-reliability`
- **Repo:** copilot-extensions
- **Branch(es):** per-session worktree branch (initial)
- **Created:** 2026-09-22
- **Status:** Active
- **Vision:** hardens the marketplace's own reliability contract — every
  plugin's `sessionStart` hook and extension connection should load
  deterministically and cheaply at full-roster scale, not just in isolation.
  Ties directly to `visions/harness-guidance`'s `ambient-delivery-fails-open`
  principle (ambient delivery must fail open and never block session
  startup) and the non-blocking startup contract described in
  `docs/patterns/session-scoped-dynamic-guidance.md`: a slow or
  timing-out `sessionStart` hook is exactly the failure mode those
  documents already require plugins to avoid, and this effort's fixes must
  stay reconciled with that existing contract rather than introduce a new
  one. Also directly informs the still-open half of
  `a private Copilot CLI runtime issue #22266` (the "ready-then-exit(1)" mystery) by
  reproducing, with reliable timing, the delay and timeout symptom a real
  session shows before its first response — not yet the confirmed root
  cause; see Context below (the watchdog-timeout theory remains unproven).
- **Umbrella issue:** ThomasMichon/copilot-extensions#3303
- **Related work:**
  `sessionstart-static-dynamic-conformance` (#2256, active) — audits hook
  *content shape* (static vs. dynamic emitted text); this effort audits hook
  *execution reliability and cost* (does it load every time, how long does it
  take, does it call expensive interpreters/tools unnecessarily). Distinct,
  complementary concerns over the same `sessionStart` roster —
  cross-link, don't duplicate.
  `a private Copilot CLI runtime issue #22266` — the upstream issue whose second,
  unconfirmed half ("ready-then-exit(1)") this effort's test bench targets.

## Guiding Intent

Every plugin in the marketplace roster should load its extension (if any) and
run its `sessionStart` hook (if any) **reliably, every time**, in a bounded
and cheap amount of wall-clock time — whether that plugin is loaded alone or
alongside every other installed plugin. Today we have direct, timed proof that is not true
at full-roster scale: a clean single harness launch took 2m28s, with a
hook from `agent-machines@copilot-extensions` consuming its entire 15-second
timeout ceiling, and ~120s of unaccounted silence before that. This effort
drives that number down to "fast and 20/20 reliable," root-causing and fixing
each blocking/failing/slow hook or extension in turn, while preserving every
plugin's intended behavior (a hook that injects config-derived instructions,
registers something, or reaches a daemon must keep doing so — just cheaply
and reliably).

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| PR-owning lane | Drives the round-by-round diagnosis, writes fixes, opens/lands each PR | a `copilot-extensions` worktree checkout |
| Clean-room validation lane | Hosts the Windows-container clean-room test bench (Hyper-V isolation) used to reproduce and time each round's findings | a Windows-container-capable Docker host, reachable from the PR-owning lane |

## Coordination

- **Topology:** two lanes, independent per-plugin PRs (short cycles, one
  plugin or tightly-related group per PR, per this repo's own norm). The
  PR-owning lane authors and lands every change; the clean-room validation
  lane is a separate execution venue it reaches to build/run the test
  bench, not a second PR owner.
- **Host (owns PRs):** the PR-owning lane.
- **Delegates:** none yet.
- **Handoff:** none yet; single-session effort so far.

## Context

This effort is seeded directly from a live investigation this session (see
the umbrella issue for the full raw log excerpt). Summary of what's already
proven, so a fresh participant doesn't have to re-derive it:

- The Windows clean-room test bench (`tools/clean-room`, PR #3262, merged)
  can build a fresh Windows container with Node + the `copilot` CLI + psmux +
  git baked in (`Dockerfile.windows`), install the real marketplace roster
  used in this investigation (`agent-bridge`, `agent-codespaces`, `agent-containers`,
  `agent-dispatch`, `agent-index`, `agent-logger`, `agent-machines`,
  `agent-mcp`, `agent-ssh`, `agent-vault`, `agent-worktrees`,
  `context-handoff`, `copilot-extensions-harness`, `customizing-copilot`,
  `efforts`, `harness-knowledge`, `visions`, `wsl-setup`) with
  `--experimental` enabled, and drive a real **headed** `copilot -i` session
  via `psmux` (`lib/psmux-drive.ps1`) — headless `-p` never loads extensions
  at all, so a headed session is mandatory for this class of investigation.
- On a completely clean container (zero prior invocations, ruling out lock
  contention from earlier force-killed test runs), a single
  `copilot -i "/env" --experimental --allow-all-tools` launch took **2m28s**
  wall-clock to its first response (`AI Credits 6.86 (2m 28s)`, the CLI's own
  self-reported turn duration). The raw process log
  (`~/.copilot/logs/process-<ts>-<pid>.log`) shows:
  - `21:57:51.298Z` — `Installed 3 native extension(s) for session ...`
    (extensions themselves load fine and fast).
  - `21:57:54.543Z` — `Successfully updated binary` (the CLI silently
    self-updated 1.0.87 → 1.0.88 mid-turn — itself worth confirming is
    intended/expected behavior, not a bug, but noted as a confound).
  - **136 seconds of complete silence** in the log.
  - `22:00:11.019Z` — `[WARNING] [rust:hooks] Hook from
    "agent-machines@copilot-extensions" timed out; allowing processing to
    proceed: HookTimeoutError: Hook command timed out after 15 seconds:
    $r = $env:COPILOT_PLUGIN_ROOT; if (-not $r) { $r = (Get-Location).Path };
    $s = …` — i.e. a hook whose own script is a trivial-looking PowerShell
    one-liner burned its **entire allotted 15-second timeout** before the
    runtime gave up on it and proceeded anyway.
  - `22:00:14.579Z` onward — graceful shutdown.
  - The per-extension logs (`~/.copilot/logs/extensions/plugin-*.log`) show
    all 3 real extensions (`context-handoff`, `agent-worktrees`,
    `agent-bridge`) reaching `=== ready ===` then `=== exit code=1
    disposition=stopped-normally ===` — the exact signature from
    `a private Copilot CLI runtime issue #22266`'s still-unconfirmed second mystery. This
    reproduced spontaneously (unprompted) in an early full-harness run,
    though not yet on every run — reliability rate not yet measured
    rigorously (see Plan Phase 1).
- **Working theory (not yet proven):** the "ready-then-exit(1)" mystery may
  be a **watchdog/timeout killing a connection that's still legitimately
  starting up**, not a genuine extension-side crash — full-harness cold start
  is slow enough (2+ minutes) that *something* (the runtime's own health
  check, or an external driver's timeout) may be reaping a connection before
  it's actually unhealthy. This effort's Phase 2+ investigation should either
  confirm or falsify this.
- We know from experience that booting a Python interpreter, shelling out to
  `git`, and parsing large JSON/YAML documents are all comparatively
  expensive per-hook operations. Any hook found to be slow should be
  evaluated for whether it's doing this unnecessarily and can be rewritten as
  a cheaper native-shell (PowerShell/bash) equivalent without losing its
  actual function (instruction injection from config, live registration,
  reaching/starting a daemon, etc. — see the Round Ledger below for which
  category each hook falls into once diagnosed).

## Request

Operator's verbatim ask (this session):

> Yes, let's do that. Huzzah, a working test-bench platform we can use to
> get to the bottom of this. Your mission is to drill into each thing that
> blocks, fails, or times out during startup, run `/env` in loaded sessions,
> and ensure that all extensions we expect, and all hooks we expect, are
> able to load every time in 20-in-a-row harness start attempts. After each
> has an issue, go back through, identify the problem, and work to fix it.
> Ensure that we can preserve the intended functionality of the associated
> plugin. Many of these are just injecting upfront instruction prompts based
> on some config; others are actually adding registrations or reaching out
> to or starting daemons. We know booting python, calling git, and reading
> lots of JSON (or YAML) are expensive operations, so we'll need to keep
> that low, or write optimized shell-based versions of these, to ensure
> performance.

## Plan

### Phase 1 — Instrumented test-bench loop
- [ ] Build a repeatable, scripted "N-in-a-row full-harness start" driver on
  top of the existing clean-room tooling: fresh (or reliably-reset) container
  state per attempt, headed `copilot -i "/env" --experimental --allow-all-tools`
  launch via psmux, full capture of: wall-clock time to first response, the
  main process log, every per-extension log, and the `/env` command's own
  reported extension/hook counts.
- [ ] Derive the installed/expected plugin roster for every baseline and
  regression run **from `.github/plugin/marketplace.json` directly**, not
  from a hand-copied list — the manifest currently declares 23 plugins
  (including `agent-pull-requests`, `ai-attribution`, and `budget-guidance`,
  each with their own `sessionStart` hook, beyond the roster this effort's
  Context section describes from its original investigation session). A
  fixed, hand-maintained list can silently drift stale and let the 20/20 run
  report success while never exercising a hook the manifest actually ships.
  The manifest also marks some entries `defaultEnabled: false` (e.g.
  `agent-pull-requests`) — deriving from every manifest entry regardless of
  that flag would let a normal full-roster install pass without ever
  exercising a not-default-enabled plugin's hook, so track the two rosters
  separately: the *default-enabled* set for the standard 20/20 run, plus one
  explicit all-plugins run (forcing every manifest entry on) so the
  not-default-enabled hooks are still genuinely tested at least once.
- [ ] Pin the Copilot CLI version before each timed attempt (disable/complete
  any self-update prior to starting the timer, e.g. an explicit update step
  in the container image build rather than relying on the timed run itself)
  and record the pinned version alongside each attempt's results -- the
  Context section already observed the CLI updating mid-turn during the
  seeding investigation, and recording the version *after the fact* would
  not by itself stop that same update from landing inside a future timed
  attempt. If an attempt's own log still shows an update occurring despite
  the pin, mark that attempt invalid and re-run it rather than counting its
  latency toward the baseline.
- [ ] Confirm whether per-attempt container reset is required (does
  `~/.copilot` state from a prior attempt change behavior?) or whether N
  attempts can safely run in the same container back-to-back.
- [ ] Run an initial 20-attempt baseline batch and record, per attempt: total
  time, whether all 3 real extensions reached `ready` without an unexpected
  `exit code=1`, whether every hook-registering plugin's hook completed
  (success or a *fast* no-op) rather than timing out, and the `/env` output's
  own extension/hook tally. This baseline becomes the next Round Ledger entry
  below (Round Ledger numbering is independent of Phase numbering — see the
  ledger's own entries for the current round number).

### Phase 2 — Round-by-round diagnose-and-fix loop (the ledger)
- [ ] For each distinct blocking/failing/timing-out thing the baseline (or
  any subsequent round) surfaces, add a row to the **Round Ledger** below:
  what broke, which plugin/hook/extension, root cause, the fix, and the
  re-verification result. One round = one pass through the current set of
  known issues; re-run the 20-attempt loop after each round's fixes land to
  see what's newly clean and what (if anything) remains or regressed.
- [ ] For each hook found to be slow: read its actual source, classify it as
  (a) static instruction-injection from config, (b) live registration/RPC,
  or (c) daemon start/reach, and fix accordingly — cheapest fix that
  preserves behavior wins (e.g. replace a `python`/`git` shell-out with a
  native PowerShell/bash equivalent; cache a value that doesn't need
  recomputing every session; parallelize independent hooks if the runtime
  supports it; shorten an unreachable-daemon retry/timeout).
- [ ] For the `context-handoff`/`agent-worktrees`/`agent-bridge`
  ready-then-exit(1) signature specifically: determine whether it still
  reproduces once Phase 2's hook-timing fixes land (supporting or refuting
  the watchdog-timeout theory in Context), and if it still reproduces
  independent of timing, continue root-causing it as its own ledger line.
- [ ] Land each fix as its own short PR (one plugin/hook, or a tightly
  related group) per this repo's short-PR-cycle norm; do not batch unrelated
  plugin fixes into one diff.

### Phase 3 — Regression guard
- [ ] Once the roster is clean, decide whether to promote the N-in-a-row
  harness-start loop into a repeatable CI/guard fixture (e.g. a new
  clean-room scenario) so a future hook regression is caught automatically,
  or whether the existing per-plugin test suites are sufficient — record the
  decision and, if a new guard is warranted, build it.

## Validation Plan

- [ ] 20/20 clean full-harness starts: all 3 real extensions (and any others
  added to the roster meanwhile) reach `ready` and stay alive for the whole
  turn — no unexpected `exit code=1 disposition=stopped-normally` **before
  the turn completes**. The Context section's own reproduction shows this
  exact signature is also the *expected* shutdown disposition once a turn
  legitimately finishes; only an exit with this signature observed **during**
  a still-in-progress turn counts as a failure for this criterion — record
  each attempt's turn-completion timestamp alongside the exit timestamp so
  the two can be distinguished, rather than pattern-matching the log line
  alone.
- [ ] 20/20 clean full-harness starts: every hook-registering plugin's
  `sessionStart` hook completes (success or fast no-op) with **zero**
  `HookTimeoutError` warnings in the process log.
- [ ] `/env` in every one of the 20 attempts reports the full expected
  **extension** set loaded — no extension silently missing. `/env` is not a
  complete oracle for hooks or skills: existing investigation evidence shows
  its Skills panel omits plugin-sourced skills, so it cannot by itself
  establish that every `sessionStart` hook ran or every skill loaded.
  Validate the hook side from the process log (success/no-op, no
  `HookTimeoutError`, per the criterion above) and the skill side against
  each plugin's own manifest, rather than treating `/env` as sufficient for
  either.
- [ ] Median and worst-case time-to-first-response across the 20 attempts is
  recorded and is dramatically lower than the 2m28s baseline (target: low
  single-digit seconds, matching the isolated single/dual-extension repro's
  observed ~2-8s).
- [ ] Every plugin whose hook was rewritten/optimized still passes its own
  existing test suite, and (where the hook's job is instruction injection,
  registration, or reaching a daemon) still visibly performs that job — not
  just "runs fast," but "still does its job, fast."
- [ ] No regression introduced in `scan-customizations.py --strict` /
  `manage-instruction-projections.py scan` (this repo's existing hook-output
  guards) as a side effect of any hook rewrite.

## Proposal

_Pending — first 20-attempt baseline batch (Phase 1) will inform whether this
needs a design doc beyond the Round Ledger itself._

## Round Ledger

_One entry per diagnose-fix-reverify round. Each entry: what the round found,
root cause, the fix, and the re-verification result. Append new rounds below;
do not edit past rounds except to correct a factual error (note the
correction inline, per the effort's own journal discipline)._

### Round 1 — `agent-machines` first-install stamp step ran synchronously

- **Found:** `bootstrap-check.ps1`/`.sh`'s legacy (non-context-selected)
  first-install branch ran `init.{ps1,sh} stamp` **synchronously**
  (`& $exe ... stamp *> $null` / `bash "$_init" stamp >/dev/null 2>&1`),
  blocking the whole `sessionStart` hook until the first-install stamp step
  finished — unlike its own sibling `$contextSelected` branch (and, in the
  `.sh` file, the final fallback branch), which already correctly
  backgrounded this class of call via `Start-Process`/`nohup ... &`.
- **Root cause:** confirmed directly (not inferred) by invoking
  `bootstrap-check.ps1` standalone in a genuinely fresh Windows clean-room
  container/plugin-install: **22.47s** wall-clock, well past the hook's own
  15s timeout — matching the exact `HookTimeoutError` seen in the seeding
  full-harness session.
- **Fix:** made the legacy stamp launch async (`Start-Process`/conhost
  `--headless` on Windows, `nohup ... &` on POSIX), matching the pattern
  already used by the file's own sibling branches and by agent-bridge's
  reference `bootstrap-check.ps1`. `stamp` only needs to land before the
  binstub is next invoked, not before the session's first turn. PR:
  ThomasMichon/copilot-extensions#3332 (dedicated per-fix worktree branch) —
  **correction:** #3332's branch/worktree was later reused for unrelated
  manual work (see Journal, 2026-09-23), contaminating its diff with 4
  unrelated commits. #3332 was closed and superseded by a clean cherry-pick
  of the same fix onto current `main`: PR ThomasMichon/copilot-extensions#3501,
  which carried this round's replacement fix (not yet merged at that point).
  **Second correction (Journal, 2026-09-26):** #3501's own review caught a
  real regression the fix introduced (backgrounding the WHOLE `stamp` action
  left a window where a session's first turn could invoke `agent-machines`
  before the binstub existed) plus atomicity/locking gaps. #3501 was closed
  in turn and superseded again by PR ThomasMichon/copilot-extensions#4129,
  which redesigns the fix (a dedicated fast, synchronous, lock-serialized,
  atomically-published binstub-only path, backgrounding only the genuinely
  slow snapshot copy) and is this round's current, still-unmerged candidate.
- **Re-verified:** on a second genuinely fresh container/plugin-install with
  the fix applied, the same standalone invocation dropped to **9.23s** — a
  real, substantial improvement, but **not a full fix**: a *separate*,
  distinct cost remained. This round's own working theory attributed that
  residual time to the installation-context resolver's synchronous status
  query. **Correction (Round 2, 2026-09-26):** that theory was wrong — direct
  instrumentation of the real (non-copied) script found the residual time
  was dominated by two other things instead: (1) Windows' `Invoke-Stamp`
  genuinely copies the whole plugin payload into a snapshot slot on every
  call (not the "cheap" operation its own docstring claims), and (2) a fully
  redundant duplicate legacy-mutation-probe spawn. See Round 2 below for the
  actual root cause and fix. Also confirmed: `Test-Path`-gated state
  (`~/.agent-machines`, `~/.copilot-extensions`) changes which code branch
  subsequent invocations take, so accurate timing requires either a fresh
  container/install per measurement or explicit removal of that state
  between repeated local tests — noted for Phase 1's test-bench methodology
  going forward.

### Round 2 — corrected root cause: snapshot copy + a redundant probe spawn

- **Found:** Round 1's own working theory (a slow resolver-status query) was
  wrong. Direct instrumentation of the real, in-place `init.ps1` (a prior
  attempt to instrument a *copied* script gave a false fast reading, because
  `$PSScriptRoot`-relative lookups inside the copy resolved to the wrong
  directory — corrected by instrumenting in place instead) found two real,
  independent costs: (1) Windows' `Invoke-Stamp` copies the ENTIRE plugin
  payload tree into a `snapshots/<version>/` slot on **every** call — a
  genuinely expensive operation its own docstring mischaracterizes as
  "cheap"; POSIX's equivalent `stamp` never does this and was already fast.
  (2) `bootstrap-check.ps1`'s own `Test-LegacyMutationAllowed` pre-check
  calls the exact same `legacy-entrypoint-probe.ps1` script, with equivalent
  arguments, that `init.ps1`'s own top-level dispatch already re-runs for
  every non-cell/-slot action — a fully redundant duplicate child-process
  spawn, confirmed by reading both call sites side by side.
- **Root cause:** confirmed via real, non-simulated timing in a sandboxed
  `HOME`/`USERPROFILE`: a single synchronous `stamp-binstub`-equivalent call
  (the pre-fix `stamp` path plus the redundant probe) took ~27-32s
  standalone; removing just the redundant probe spawn alone cut that to
  ~15.5s; the full corrected two-stage hook (below) returns in ~11.4s end to
  end — still comfortably under the 15s hook timeout, with the binstub
  already present the instant the hook returns (verified by listing the
  target directory immediately after the process exited).
- **Fix:** (a) split a new, fast `stamp-binstub` action (deploys ONLY the
  self-provisioning binstub, no snapshot copy) that `bootstrap-check.ps1`
  now calls **synchronously**, backgrounding only the full, genuinely-slow
  `stamp` (snapshot copy) afterward — closing the real command-not-found
  race window Round 1's all-or-nothing backgrounding left open; (b) removed
  the redundant `Test-LegacyMutationAllowed` pre-check for this call site,
  since `init.ps1` already re-enforces the identical gate internally; (c)
  reverted POSIX's `stamp` back to synchronous (it was never actually slow —
  Round 1 backgrounded it by unverified analogy with Windows, introducing an
  unnecessary race for no benefit) and added the lock/atomic-publish
  protection the review asked for on both platforms instead. PR:
  ThomasMichon/copilot-extensions#4129 (open at the time of this Round 2
  entry; merged much later -- see the 2026-09-27 Journal entry recording
  that outcome for the full 12-round history in between).
- **Re-verified:** real subprocess invocations against a sandboxed
  `HOME`/`USERPROFILE` (not simulated): full hook returns in ~11.4s
  (previously 22.47s, over its own 15s timeout); 3 concurrent
  `stamp-binstub`/`stamp` invocations against the same install dir, both
  platforms, all exit 0 with a complete, correctly-formed final binstub (no
  torn write observed).
- **New finding, deliberately deferred to a future round:** the
  legacy-mutation-probe chain is actually **four** levels of nested
  PowerShell process spawns end to end (`bootstrap-check.ps1` →
  `init.ps1`'s own gate → `legacy-entrypoint-probe.ps1` →
  `installation-context.ps1`), each measured at multiple seconds on the
  machine used for validation. That machinery is shared
  `installation-context` infrastructure used by other plugins too, not
  agent-machines-specific — redesigning it is real, valuable future work,
  but out of this round's scope (and this PR's) given the short-PR-cycle
  norm and the blast radius of touching shared infrastructure. Logged here
  so a future round picks it up deliberately rather than it being lost.
- **Round 2 follow-up (2026-09-27):** PR #4129's own review caught two real
  bugs in the fix above, both since corrected in the same PR (still open,
  not yet merged): (1) HIGH — `stamp-binstub` deployed the launcher but
  never wrote a usable `payload-dir` marker (only the slower backgrounded
  `stamp` wrote it, after its snapshot copy finished), so a first-turn
  command run in the gap between the two would hit the generated binstub's
  `:_noinst` path and exit 127 even though the binstub itself existed —
  fixed by having `stamp-binstub` write the marker immediately, pointed at
  the already-self-staged plugin dir (the exact same source the full
  `stamp`'s snapshot copy reads from) as a correct immediate fallback. (2)
  MEDIUM — POSIX's lock was a no-op whenever `flock` was unavailable
  (silently discarding a nonzero `flock` result), which is exactly the
  situation on macOS (no `flock` by default) — fixed by reusing the same
  PID-symlink lock fallback `cell_provision` already uses elsewhere in
  `init.sh`, verified directly via `COPILOT_EXT_NO_FLOCK=1`. Also added
  real subprocess regression tests (`test_stamp_binstub_two_stage.py`)
  covering both the payload-dir-marker race and concurrent-invocation
  atomicity on both platforms, addressing the review's third finding
  (missing test coverage for this exact code path).
- **Round 2, second follow-up (2026-09-27):** a further review pass on the
  same PR (still open) surfaced 2 new findings on top of the 3 above (which
  it re-listed as still-open threads even though already fixed in code —
  resolved as stale threads, not re-fixed): (1) MEDIUM —
  `bootstrap-check.ps1`'s synchronous `stamp-binstub` call discarded its own
  exit code (`*> $null`), so a probe denial, lock timeout, or write failure
  still backgrounded the full `stamp` and returned success with no launcher
  actually published, silently breaking the very guarantee the two-stage
  split exists for — fixed by checking `$LASTEXITCODE` immediately after
  the call and skipping the background `stamp` when the fast stage failed.
  (2) MEDIUM — the POSIX stamp lock (added in the immediately-prior
  follow-up) serialized writers against each other but not against the
  generated binstub's *reader*, which reads `payload-dir` without taking
  the lock; a plain `>` redirection truncates the file before `printf`
  writes it, so a reader racing a later/concurrent stamp could observe an
  empty path and exit 127 despite the binstub itself being atomically
  replaced — fixed by publishing the marker through a same-directory temp
  file + `mv -f`, the same pattern `deploy_binstub` already uses for the
  binstub. Verified via `bash -n`/PowerShell AST parse and the `stamp`-
  tagged regression suite (2 passed, 2 skipped — POSIX cases skip on the
  Windows validation host), pushed, and all 5 open review threads (3 stale
  + 2 new) resolved with a summary comment citing the fixing commit.

### Round 0 — Kickoff (pre-effort evidence)

- **Found:** a clean single full-harness launch took 2m28s; `agent-machines`
  hook burned its full 15s timeout; ~120s of otherwise-unaccounted silence
  before it; the 3 real extensions showed the ready-then-exit(1) signature in
  an earlier (less controlled) run.
- **Root cause:** not yet diagnosed — this is the seed evidence, not a fix.
- **Fix:** none yet.
- **Re-verified:** n/a — Round 1 (Phase 1 baseline) is the first real ledger
  round.

## Journal

### 2026-09-22 — Kickoff

- Effort created directly off a live investigation this session (see
  Context). Umbrella issue filed:
  ThomasMichon/copilot-extensions#3303.
- Scoped as a round-by-round diagnose-fix-reverify effort (the operator's
  own framing: "after each has an issue, go back through, identify the
  problem, and work to fix it") rather than a single big-bang phase — the
  Round Ledger section exists specifically to carry that iteration record
  forward across sessions.
- Test-bench substrate (Windows clean-room, the clean-room validation lane's
  Windows-container Docker host, `copilot-cleanroom:windows`
  image with the marketplace roster used in this investigation installable) already exists and was
  validated working in the seeding investigation; Phase 1 here is about
  making that repeatable and instrumented for 20-in-a-row runs, not building
  it from scratch.

### 2026-09-23 — PR #3332 contamination found and remediated

- On resuming via handoff for Round 2, found PR #3332 (Round 1's fix) had 5
  commits instead of 1: the legitimate async-stamp fix (`bb9e05ecc`) plus 4
  unrelated commits authored directly (no Copilot co-author, no session-store
  trace) for a separate `context-handoff-overhaul` effort (PRs #3440/#3459/
  #3460). Root cause: PR #3332's own head branch/worktree was reused for
  manual, unrelated git work after the fix commit landed, without checking
  out a fresh branch first, dragging PR #3332's diff along.
- Swept all other open `worktree/*`-headed PRs on this repo (#3305, #3310,
  #3348) for the same pattern — all clean, single-topic. #3332 was the only
  one affected.
- Remediation: cherry-picked `bb9e05ecc` cleanly onto current `main` in a
  fresh worktree (`fix/agent-machines-async-stamp-clean`), resolved the
  now-stale version-bump conflict (`main` had since moved `agent-machines` to
  `0.1.0-dev135`; re-bumped to `0.1.0-dev136` across all four required
  version surfaces), pushed, and opened PR #3501 as the replacement. Closed
  #3332 with a comment explaining the contamination and linking #3501.
- Takeaway for future rounds: never reuse a fix/effort worktree for unrelated
  manual work — create a fresh worktree per unrelated task instead, even for
  quick one-off git operations.

### 2026-09-26 — Round 2 executed; PR #3501 review findings addressed; effort README revised per review

- Resumed with `agent-worktrees` itself broken (a stale runtime slot,
  `ImportError: MARKETPLACE_MANIFEST_RELS`) — filed
  ThomasMichon/copilot-extensions#3999 and worked around it by invoking an
  older, still-present runtime slot directly, then created a fresh worktree
  through it once confirmed working.
- Read PR #3501's real review findings (1 high, 3 medium, 2 low severity)
  and re-diagnosed the residual delay from scratch rather than assuming the
  prior round's own theory was correct — it wasn't (see Round 2 above for
  the corrected root cause: a genuine Windows-only snapshot-copy cost plus
  a fully redundant duplicate probe spawn, not a slow resolver-status
  query).
- Opened the corrected fix as PR #4129 (still open, not yet merged at time
  of writing), closed #3501 with a comment linking it as superseded.
- Separately addressed this effort README's OWN review findings on PR #3305
  (Vision unlinked from `visions/harness-guidance`/
  `docs/patterns/session-scoped-dynamic-guidance.md`; unqualified
  `a private Copilot CLI runtime issue #22266` references; private participant/topology
  details; a Round/Phase numbering collision; a stray private machine
  alias; the premature "landed" ledger language) — all revised in this same
  pass, see the current README content rather than restating each fix here.

### 2026-09-27 — PR #4129's own review findings addressed

- Resumed via handoff; PR #4129 had picked up its own fresh review (1 high,
  1 medium, 1 medium/low severity) since the prior session ended. Fixed all
  three: the payload-dir-marker race (HIGH — a real command-not-found
  window), the POSIX flock-unavailable no-op lock (MEDIUM), and missing
  test coverage for this exact two-stage path (added
  `test_stamp_binstub_two_stage.py`, 4 tests covering both the marker race
  and concurrent-invocation atomicity on both platforms). Verified the
  POSIX no-flock fallback path directly via WSL with
  `COPILOT_EXT_NO_FLOCK=1` since the automated test host always has a real
  `flock`. Pushed as a follow-up commit on the same PR #4129 branch. PR
  #3305 has no new findings as of this check; both remain open, awaiting
  review.

### 2026-09-27 — PR #4129's third review round addressed; both PRs mergeable as of this check, awaiting review decision

- Resumed via handoff; both PRs' merge status, snapshotted at the moment of
  this check (2026-09-27 ~08:00Z), read `MERGEABLE` with no review decision
  yet — a live, continuously-recomputed GitHub status that had already
  changed by the time later entries below were written, not a durable
  claim. PR #4129 had picked up a further review pass (5 open
  threads: 3 re-listing the already-fixed findings from the prior journal
  entry as still-unresolved threads, plus 2 genuinely new medium-severity
  findings). Fixed the 2 new ones: `bootstrap-check.ps1` now checks
  `$LASTEXITCODE` after the synchronous `stamp-binstub` call and skips
  backgrounding the full `stamp` on failure (previously discarded via
  `*> $null`, silently breaking the launcher-before-return guarantee on any
  probe/lock/write failure); POSIX `init.sh` now publishes `payload-dir`
  through a same-directory temp file + `mv -f` instead of a direct `>`
  redirection, closing a truncation window a concurrent binstub-reader
  could observe (matching `deploy_binstub`'s own atomic-replace pattern).
  Verified via `bash -n`/PowerShell AST parse and the `stamp`-tagged
  regression suite (2 passed, 2 skipped). Rebased onto latest `dev`,
  force-pushed with-lease, then resolved all 5 open review threads via the
  GraphQL API and posted a summary comment on the PR citing the fixing
  commit and the verification performed, since the 3 stale threads needed
  explicit resolution rather than a code re-fix. Immediately afterward,
  PR #3305 (the effort plan itself) also picked up its own fresh review
  pass (6 open threads: 2 stale "Documentation impact statement" threads
  already satisfied by the PR description, plus 4 genuinely new/unresolved
  findings). Fixed the 4: corrected the still-open PR #4129's Journal entry
  from "Landed the corrected fix" to "Opened the corrected fix"; Phase 1's
  plan now derives the benchmark/expected plugin roster from
  `.github/plugin/marketplace.json` directly (the hand-copied list was
  already missing 3 plugins with their own `sessionStart` hooks); added an
  explicit CLI-version pin/record step to Phase 1 to keep the 20-attempt
  baseline reproducible against the CLI's own noted self-update confound;
  and the Validation Plan's exit-code=1 criterion now distinguishes a
  during-turn failure from the identical post-turn shutdown signature this
  effort's own Context evidence already reproduces. Rebased, force-pushed
  with-lease, resolved all 6 threads, and posted a summary comment citing
  the fixing commit — same discipline as #4129 above. PR #3305 then picked
  up one more review pass with 2 new findings on the round-3 fix itself:
  deriving the roster from every manifest entry regardless of
  `defaultEnabled` would let a normal install pass without ever exercising
  a not-default-enabled plugin's hook (e.g. `agent-pull-requests`), and
  `/env` cannot serve as a complete oracle for hooks or skills (its Skills
  panel is known to omit plugin-sourced skills). Fixed both: Phase 1 now
  tracks a default-enabled roster for the standard 20/20 run plus one
  explicit all-plugins run; the Validation Plan's `/env` criterion is now
  scoped to extensions only, with hooks validated via process-log evidence
  and skills against each plugin's own manifest. Rebased, force-pushed,
  resolved both threads, posted a summary comment. PR #3305 then picked up
  one more pass (1 new low finding + 1 previously-missed low finding on
  unchanged code): the Journal's own PR-mergeability mentions were
  themselves stale snapshots by the time later entries were written, and
  the Vision overclaimed causal evidence the Context section itself still
  marks unproven. Fixed both (see this and the prior paragraph's own
  now-corrected wording), rebased, force-pushed, resolved, commented. Both
  PRs remain open, awaiting a review decision (their live `mergeable`/checks
  status fluctuates by the minute during active review passes -- see each
  PR's current state directly rather than trusting any snapshot recorded
  here); watching both via `pr-watch` in the background rather than
  polling, per standard discipline.

### 2026-09-27 — Operator-directed re-review cycle on #4129 (rounds 6-8); #3305's CLI-pin finding also fixed

- Operator directed re-requesting Copilot review via the GitHub API directly
  (`POST .../requested_reviewers` with `copilot-pull-request-reviewer[bot]`,
  confirmed 201) rather than continuing the passive `pr-watch` loop, with a
  bounded 5-minute wait per cycle. This surfaced three more real rounds on
  #4129 in quick succession, each fixed the same way as before (read, fix
  root cause, verify, push, resolve threads, comment) rather than merging
  through them:
  - **Round 6**: Windows markers (`init.ps1`) were still written via direct
    truncating `WriteAllText`, unlike the POSIX side's round-3 fix -- fixed
    with the same temp-file+rename pattern. A stale doc comment overclaimed
    that removing a redundant pre-check spawn cut probe-process overhead
    per hook invocation; corrected once the two-stage split's own second
    `init.ps1` invocation (each running its own probe) was accounted for.
    Added a real subprocess test that invokes the generated binstub itself
    and asserts it reaches genuine provisioning rather than the fast
    `:_noinst` exit-127 path, killing the resulting process tree via
    `taskkill /T /F` (bare `terminate()`/timeout only signals the top-level
    `cmd.exe`). Also fixed a bug in the tests themselves: they passed
    `-InstallDir` to a directory the generated `.cmd`'s hardcoded
    `_ROOT=%USERPROFILE%\.agent-machines` could never see.
  - **Round 7**: two HIGH-severity marker-durability bugs the round-6 fix
    hadn't caught -- `Invoke-StampBinstubOnly` pointed `payload-dir` at
    `$PluginDir`, which on a marketplace install is a per-invocation
    `.install-stage/<ts>-<pid>` copy a LATER invocation's own dead-stage
    reaper can delete out from under it (now points at `$probePayload`, the
    durable original payload root instead); and `Invoke-Stamp` used to
    `Remove-Item` both markers immediately, before its own several-second
    snapshot copy even started, leaving NONE at all for the whole copy
    duration (worse than a torn write) -- removed the premature clear;
    the existing atomic replace already handles the swap once the new
    snapshot is ready. Also fixed the new test's own timeout enforcement:
    `proc.stderr.readline()` blocks, so polling it against a wall-clock
    deadline didn't actually bound it -- moved to a daemon reader thread
    feeding a queue instead.
  - **Round 8**: the SAME atomicity theme, one layer deeper --
    `resolve-runtime.ps1`/`.sh` were still copied via a direct
    `Copy-Item`/`cp -f` (truncating before copy); and `Move-Item -Force`
    itself does not guarantee an atomic replace on every supported
    PowerShell runtime (Windows PowerShell 5.1's `-Force` can delete the
    destination before moving the new one in). Added a shared
    `Publish-FileAtomically`/`Copy-FileAtomically` helper using
    `[System.IO.File]::Replace()` (true atomic NTFS replace) with a
    Move-Item fallback for first-ever publishes, applied to every Windows
    write site. Getting `Replace()` to actually work in practice needed two
    more fixes discovered by hand: its backup-path argument throws "The
    path is empty" for both `$null` and `""` on this codebase's real
    runtimes despite null being the documented no-backup form (give it a
    real, discarded backup path instead), and it can throw a transient
    "used by another process" IOException if a reader briefly has the file
    open without `FILE_SHARE_DELETE` (retried briefly rather than treated
    as fatal). A remaining finding -- the two marker files are each atomic
    individually but not atomic as a PAIR -- was deliberately documented as
    deferred rather than fixed: correctly solving it means redesigning the
    on-disk marker format several OTHER call sites across this plugin
    depend on (`invoke-payload-runtime.ps1/.sh`, `installation-context`,
    receipts, the CLI), real future work well beyond this round's scope.
    Strengthened the concurrency tests to poll the file on a background
    thread WHILE the writer processes are still racing (not just after they
    all exit), which is what surfaced both `Replace()` runtime quirks above.
- Separately, PR #3305 picked up one more finding in the same window:
  pinning/recording the CLI version alone does not prevent the mid-turn
  self-update confound the Context section already observed -- fixed by
  requiring the update be disabled/completed BEFORE the timer starts (not
  merely recorded after), and any attempt that still updates mid-run marked
  invalid and re-run rather than counted.
- Each round: verified via PowerShell AST parse / `bash -n` / Python
  `ast.parse`, the `stamp`-tagged regression suite, and (round 8) the
  broader binstub/resolver/provision selection; rebased onto latest `dev`,
  force-pushed with-lease, resolved every addressed thread via the GraphQL
  API, and posted a summary comment citing the fixing commit -- same
  discipline as every prior round.
- **Round 9** (same re-review cycle): two more real races. `Publish-
  FileAtomically`/`Copy-FileAtomically` are also called from UNLOCKED call
  sites (the regular, non-stamp install path), so two racing first-time
  installs could both pass a Test-Path check and both take the
  `Move-Item -Force` branch -- recreating the round-8 no-file window on
  Windows PowerShell. Fixed by dropping `-Force` on that branch (a
  concurrent writer now throws instead of silently clobbering, caught and
  retried). Separately, re-stamping the SAME version used to unconditionally
  delete the existing snapshot directory before copying a fresh one --
  deleting a payload-dir that was STILL advertised as live. Fixed by
  skipping the whole remove+recopy dance when a valid snapshot for that
  exact version already exists (idempotent fast path).
- **Round 10** (same cycle): the single most consequential finding of this
  whole chain. `stamp-binstub` exists specifically so bootstrap-check.ps1's
  synchronous hook call stays sub-second -- but the install-contract
  self-stage block unconditionally copies the WHOLE plugin payload before
  ANY action dispatches on a real marketplace install, silently
  reintroducing the exact cost (and race) this entire two-stage split was
  built to eliminate. Every prior round's own subprocess tests never caught
  this because they invoke `init.ps1` directly from the repo checkout,
  which never matches the self-stage guard's `/.copilot/installed-plugins/`
  path check -- a real marketplace install is the only place this bug
  actually bites. Fixed by skipping self-stage for `stamp-binstub`
  specifically, the same way cell-/slot- actions already do. Also fixed:
  both stamp-family actions now reject a custom `-InstallDir` outright
  (the generated launcher's marker root was never `-InstallDir`-aware, so a
  custom value silently produced a broken launcher); and the PID-symlink
  stale-lock fallback's TOCTOU race, fixed with an atomic `mkdir`-based
  reaper mutex applied to both occurrences of the pattern in the file.
  Verified against the FULL agent-machines suite (641 passed, 33 skipped,
  excluding the two machine-pre-existing flaky tests), not just the
  targeted selection, given how deep this round's self-stage change reaches.
- Ten review rounds on one PR is unusual even by this effort's own
  standard, but each one caught a genuinely real, previously-undetected bug
  -- the finding count has trended down round over round (this journal's
  own round-by-round entries carry the exact count each round found),
  suggesting the remaining surface is shrinking, not that the process is
  stuck; continuing the same discipline rather than merging through open
  findings.
- **Round 11** (same cycle): 4 more findings, none as consequential as
  round 10's self-stage discovery but all real. The fast `stamp-binstub`
  path shared its mutex with the SLOW `Invoke-Stamp` snapshot copy, so a
  concurrent slow stamp could make the "fast" path wait up to 20s behind
  it -- fixed by dropping the lock from the fast path entirely, since round
  9 had already made its individual operations safe unlocked. Round 10's
  own `mkdir`-based reap-mutex fix turned out to be itself buggy: an
  unowned mutex that could wedge forever if its holder died mid-reap --
  replaced with a self-healing PID-symlink (matching the main lock's own
  pattern) on both occurrences. POSIX's `stamp` action had the same
  custom-install-dir/mismatched-launcher bug round 10 fixed on Windows,
  unfixed -- now rejects it too. Added the first test in this whole chain
  that drives the REAL `bootstrap-check.ps1` hook end-to-end (every prior
  test called `init.ps1` actions directly), catching exactly the class of
  regression (`$LASTEXITCODE` handling, quoting, `Start-Process` launch)
  none of the other tests could. Verified against the full suite again
  (642 passed).
- **Round 12**: one finding, a direct consequence of round 11's own fix.
  Removing the lock from `Invoke-StampBinstubOnly` let it run concurrently
  with (or even after) a full `stamp` that already published a real
  snapshot marker, unconditionally overwriting `payload-dir` back to the
  fast path's fallback and regressing an already-valid marker to a weaker
  one. Fixed by making that write create-only: skip it if the marker
  already resolves to a real `scripts\init.ps1`, so the fast path can only
  ever fill in a missing/broken marker, never regress a valid one.
- Operator directed re-requesting Copilot review directly via the GitHub
  API (bypassing the passive `pr-watch` wait) with a bounded 5-minute wait
  per cycle, for 7 rounds total this session (rounds 6-12). Every round
  caught a real, previously-undetected bug -- including round 10's
  discovery that the entire two-stage split's core premise didn't hold on
  a real marketplace install -- until round 12's re-request cycle finally
  returned no new review within the 5-minute window. With all 27 review
  threads resolved and every real CI check green (the one failing check,
  `identifier leak guard`, is the pre-tracked #4247 misconfiguration, not
  this PR's content), admin-merged PR #4129 per the operator's standing
  authorization for exactly this outcome. **PR #4129 is merged** as of this
  entry -- the effort's Journal above describing it as still open predates
  this merge; do not re-derive its status from earlier entries.
