# Launch-Time Model/Effort/Context Preference Flags

- **Slug:** `launch-time-model-preference-flags`
- **Repo:** ThomasMichon/copilot-extensions (agent-worktrees)
- **Branch(es):** `worktree/lambda-core-win-20260930-205959-4edd`
- **Created:** 2026-09-30
- **Status:** Done
- **Umbrella issue:** #4776

## Guiding Intent

A machine's enforced Copilot defaults (model, reasoning effort, context tier)
must be a **guarantee** at every worktree launch, not merely a persisted
preference Copilot CLI may or may not honor. This narrow effort closes that
specific gap in `agent-worktrees`' launch path; the broader thread (the
Intelligence Dampener dispatch-reviewer launch API, which has its own,
larger, separately-tracked gap) is explicitly out of scope here.

## Context

- Downstream facility (aperture-labs) session log, 2026-08-31, "The Model
  Policy the Launcher Couldn't Carry": documented that the Intelligence
  Dampener's dispatch-reviewer launch path cannot carry model/effort/context
  to the worker it starts, and that the same class of gap applies to the
  ordinary interactive worktree create/resume launch path owned by this
  repo's `agent-worktrees` plugin.
- Separately, Copilot CLI's own `/settings` documentation confirms
  `model`/`effortLevel`/`contextTier` are plain persisted `settings.json`
  keys that can also be set via `--model`/`--reasoning-effort`/`--context`
  CLI flags — the live behavior report is that the persisted values alone
  are not reliably honored at startup.
- `agent-machines` (a separate plugin) is this facility's sole writer of
  `~/.copilot/settings.json`'s `copilot.settings` keys; this effort does not
  change that ownership, it only adds a read-only consumer in the launch
  path.

## Request

Operator ask (aperture-labs session, paraphrased from the originating
request there): "whenever we launch `copilot`, ensure we pass the `--model`,
`--effort`, and `--context` flags to our preferences from agent-machines
copilot-settings, because Copilot has started ignoring the user-global
settings and this can currently only be set via command-line or mid-session."
Routed here (rather than authored in the downstream facility repo) because
the actual launch mechanics live in this repo's `agent-worktrees` plugin —
see `working-cross-repo`'s capability-aware placement rule.

## Plan

### Phase 1 — Launch-time flag injection
- [x] Add `agent_worktrees/copilot_launch_prefs.py`: best-effort reader of
      `~/.copilot/settings.json`, translating `model` / `effortLevel` /
      `contextTier` into `--model` / `--reasoning-effort` / `--context`,
      never overriding a flag the caller already supplied (bare or
      `=`-form).
- [x] Wire it into `_build_launch_cmd` (`__main__.py`), appended alongside
      the existing `--allow-all` auto-approval logic so every launch path
      (config-driven `launch` template, normalized setup-hook launcher,
      legacy `tools/setup/setup.*`, and the plugin default-setup launcher)
      carries it uniformly.
- [x] Document the behavior in `docs/config-reference.md`.
- [x] Changefile: `agent-worktrees` patch.

## Validation Plan

- [x] New unit suite `tests/test_copilot_launch_prefs.py`: missing file,
      malformed file, full/partial preference, non-string/empty values
      skipped, explicit bare and `=`-form caller overrides never duplicated.
- [x] New integration tests in `tests/test_launch_cmd.py`:
      `test_launch_injects_persisted_model_effort_context_flags`,
      `test_launch_never_overrides_explicit_model_flag`. Added an autouse
      fixture isolating the whole suite from the *real* host machine's
      `~/.copilot/settings.json` so pre-existing tests stay deterministic
      regardless of what the running machine has persisted.
- [x] Full `agent-worktrees` suite run via `tools/run-plugin-tests.py
      agent-worktrees` (630+ passed). One unrelated flaky test
      (`test_git_ops.py::TestPinGitCredential::test_concurrent_pins_never_interleave`,
      a pre-existing concurrency race) failed only under parallel
      sub-suite sharding and passed cleanly in isolation — confirmed
      unrelated to this change (no file this effort touches is anywhere
      near `test_git_ops.py`).

## Journal

### 2026-09-30 — Implemented and validated
- Implemented Phase 1 in full within the same session that opened this
  effort: new `copilot_launch_prefs` module, `_build_launch_cmd` wiring,
  docs, changefile, and both new test files. Full suite run clean apart
  from the pre-existing unrelated flake noted above.
- Landing as a single PR (plan + implementation together) rather than a
  separate plan-only review gate first: the whole stretch is one small,
  already-validated, single-slice change, not a multi-phase campaign where
  pre-review would save rework.
