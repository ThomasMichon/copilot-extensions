# Full-Harness Startup Reliability

- **Slug:** `full-harness-startup-reliability`
- **Repo:** copilot-extensions
- **Branch(es):** per-session worktree branch (initial)
- **Created:** 2026-09-22
- **Status:** Active
- **Vision:** hardens the marketplace's own reliability contract — every
  plugin's `sessionStart` hook and extension connection should load
  deterministically and cheaply at full-roster scale, not just in isolation.
  Directly informs the still-open half of copilot-agent-runtime#22266 (the
  "ready-then-exit(1)" mystery) by supplying the first reproducible, timed,
  full-harness evidence of *why* a real session can take minutes to produce
  its first response.
- **Umbrella issue:** ThomasMichon/copilot-extensions#3303
- **Related work:**
  `sessionstart-static-dynamic-conformance` (#2256, active) — audits hook
  *content shape* (static vs. dynamic emitted text); this effort audits hook
  *execution reliability and cost* (does it load every time, how long does it
  take, does it call expensive interpreters/tools unnecessarily). Distinct,
  complementary concerns over the same `sessionStart` roster —
  cross-link, don't duplicate.
  `github/copilot-agent-runtime#22266` — the upstream issue whose second,
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
| Windows operator workstation (this session) | Drives the test bench, diagnoses, fixes, lands PRs | local `copilot-extensions` worktree checkout |
| Windows dev-tunnel box | Hosts the Windows-container clean-room test bench (Hyper-V isolation) | SSH alias + `docker` (`copilot-cleanroom:windows` image) |

## Coordination

- **Topology:** single host, independent per-plugin PRs (short cycles, one
  plugin or tightly-related group per PR, per this repo's own norm).
- **Host (owns PRs):** the operator's Windows workstation.
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
    copilot-agent-runtime#22266's still-unconfirmed second mystery. This
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
- [ ] Confirm whether per-attempt container reset is required (does
  `~/.copilot` state from a prior attempt change behavior?) or whether N
  attempts can safely run in the same container back-to-back.
- [ ] Run an initial 20-attempt baseline batch and record, per attempt: total
  time, whether all 3 real extensions reached `ready` without an unexpected
  `exit code=1`, whether every hook-registering plugin's hook completed
  (success or a *fast* no-op) rather than timing out, and the `/env` output's
  own extension/hook tally. This baseline is Round 1 of the ledger below.

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
  turn — no unexpected `exit code=1 disposition=stopped-normally`.
- [ ] 20/20 clean full-harness starts: every hook-registering plugin's
  `sessionStart` hook completes (success or fast no-op) with **zero**
  `HookTimeoutError` warnings in the process log.
- [ ] `/env` in every one of the 20 attempts reports the full expected
  extension and hook/skill set loaded — no plugin silently missing.
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
  ThomasMichon/copilot-extensions#3332 (branch
  `worktree/tmichon-book2-win-20260922-201924-820e`).
- **Re-verified:** on a second genuinely fresh container/plugin-install with
  the fix applied, the same standalone invocation dropped to **9.23s** — a
  real, substantial improvement, but **not a full fix**: a *separate*,
  distinct cost remains. Direct instrumentation traced the residual time to
  the installation-context resolver's own synchronous status query
  (`$statusJson = @(& $hostExe @statusArgs)`, itself a full child
  `powershell.exe` spawn — measured standalone at ~1.4-1.7s per invocation
  in this container, steady-state, ruling out a one-time cold-JIT effect as
  the sole explanation). That resolver-status call is now Round 2's target:
  either make it async too, or cache/skip it on the common
  not-yet-provisioned path where its answer can't meaningfully change the
  outcome. Also confirmed: `Test-Path`-gated state (`~/.agent-machines`,
  `~/.copilot-extensions`) changes which code branch subsequent invocations
  take, so accurate timing requires either a fresh container/install per
  measurement or explicit removal of that state between repeated local
  tests — noted for Phase 1's test-bench methodology going forward.

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
- Test-bench substrate (Windows clean-room, cloud2, `copilot-cleanroom:windows`
  image with the marketplace roster used in this investigation installable) already exists and was
  validated working in the seeding investigation; Phase 1 here is about
  making that repeatable and instrumented for 20-in-a-row runs, not building
  it from scratch.
