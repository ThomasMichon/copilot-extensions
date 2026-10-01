# Launch-Time Model/Effort/Context Preference Flags

- **Slug:** `launch-time-model-preference-flags`
- **Repo:** ThomasMichon/copilot-extensions (agent-worktrees)
- **Branch(es):** a single topic branch, merged via its PR
- **Created:** 2026-09-30
- **Status:** Done
- **Umbrella issue:** #4776

## Guiding Intent

A machine's enforced Copilot defaults (model, reasoning effort, context tier)
must be a **guarantee** at every worktree launch, not merely a persisted
preference Copilot CLI may or may not honor. This narrow effort closes that
specific gap in `agent-worktrees`' launch path; other launch paths that start
a Copilot process on an operator's behalf (e.g. automated review dispatch)
have their own, larger, separately-tracked gaps and are explicitly out of
scope here.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent | Authored, implemented, and validated the whole effort solo | This repo's `agent-worktrees create` worktree flow |

## Coordination

Single-agent, single-PR effort — no branch topology or handoff to record.

## Context

- Copilot CLI's own `/settings` documentation confirms `model` /
  `effortLevel` / `contextTier` are plain persisted `settings.json` keys
  that can also be set via `--model` / `--reasoning-effort` / `--context`
  CLI flags — the reported live behavior is that the persisted values alone
  are not reliably honored at startup, only an explicit flag or a
  mid-session `/model` change is.
- `agent-machines` (a separate plugin) is typically the sole writer of
  `~/.copilot/settings.json`'s `copilot.settings` keys on a managed machine;
  this effort does not change that ownership, it only adds a read-only
  consumer in the launch path.

## Request

Paraphrased operator ask, generalized from its originating context: "whenever
a worktree launches `copilot`, ensure the launch passes `--model`,
`--reasoning-effort`, and `--context` flags carrying the machine's managed
preference, because Copilot has started ignoring the persisted user-global
settings and the preference can currently only be set via command line or
mid-session." Authored in this repo (rather than a consuming control repo)
because the actual launch mechanics live in this repo's `agent-worktrees`
plugin — see the `working-cross-repo` skill's capability-aware placement
rule.

## Plan

### Phase 1 — Launch-time flag injection
- [x] Add `agent_worktrees/copilot_launch_prefs.py`: best-effort,
      comment-tolerant reader of `~/.copilot/settings.json`, translating
      `model` / `effortLevel` / `contextTier` into `--model` /
      `--reasoning-effort` / `--context`, never overriding a flag already
      present anywhere in the assembled launch command (bare or `=`-form).
- [x] Wire it into `_build_launch_cmd` (`__main__.py`), appended alongside
      the existing `--allow-all` auto-approval logic so every launch path
      (config-driven `launch` template, normalized setup-hook launcher,
      legacy `tools/setup/setup.*`, and the plugin default-setup launcher)
      carries it uniformly. Skipped for ACP sessions (Copilot ignores these
      flags in ACP mode; `agent-bridge`'s ACP client carries model/effort
      through its own configuration path there instead).
- [x] Document the behavior in `docs/config-reference.md`.
- [x] Changefile: `agent-worktrees` patch.

## Validation Plan

- [x] Unit suite `tests/test_copilot_launch_prefs.py`: missing file,
      malformed file, `//`-commented file, a literal `//` inside a string
      value, full/partial preference, non-string/empty values skipped,
      explicit bare and `=`-form caller overrides never duplicated.
- [x] Integration tests in `tests/test_launch_cmd.py`: persisted preference
      injected, an explicit caller `--model` flag never overridden
      (`copilot_args`-supplied and template-embedded forms both covered),
      and ACP sessions never receive the injected flags.
- [x] Full `agent-worktrees` suite run via `tools/run-plugin-tests.py
      agent-worktrees` — green, including the full `test_profile_assignment`
      module's exact-command assertions, on a machine with a real persisted
      preference: this repo's existing plugin-wide `_isolate_agent_worktrees_home`
      autouse fixture (`tests/conftest.py`) already redirects every test's
      `Path.home()`, so no test in the suite observes the real settings file.
- [x] One unrelated flaky test
      (`test_git_ops.py::TestPinGitCredential::test_concurrent_pins_never_interleave`,
      a pre-existing concurrency race) failed only under parallel
      sub-suite sharding and passed cleanly in isolation — confirmed
      unrelated to this change (no file this effort touches is anywhere
      near `test_git_ops.py`).

## Proposal

Phase 1's design *is* the proposal: a small, standalone module
(`copilot_launch_prefs`) that reads the persisted settings file and
translates it into CLI flags, wired into the one existing launch-command
builder (`_build_launch_cmd`) rather than duplicated per launch-path branch.
No further design iteration was needed — the plan above was implemented as
originally scoped, with precedence/ACP/whitespace edge cases folded in
during automated-review iteration rather than requiring a redesign.

## Journal

### 2026-09-30 — Implemented and validated
- Implemented Phase 1 in full within the same session that opened this
  effort: new `copilot_launch_prefs` module, `_build_launch_cmd` wiring,
  docs, changefile, and new/updated test files. Full suite run clean apart
  from the pre-existing unrelated flake noted above.
- Landing as a single PR (plan + implementation together) rather than a
  separate plan-only review gate first: the whole stretch is one small,
  already-validated, single-slice change, not a multi-phase campaign where
  pre-review would save rework.
- Addressed automated review feedback on the first PR revision: fixed
  duplicate-detection to cover template-embedded flags (not just
  `copilot_args`), excluded ACP sessions (Copilot ignores these flags
  there), added comment-tolerant settings parsing, and removed private/
  internal identifiers from this document.
- Addressed a second review round: ACP detection now also inspects a
  template-embedded `--acp` (applied uniformly to the pre-existing
  `--allow-all` suppression too, same class of gap); persisted values are
  trimmed before validation/emission so a whitespace-only or padded value
  can't become an invalid CLI argument; added the required `## Proposal`
  section. Attempted a plugin-wide conftest fixture for the
  "isolate across all test modules" finding, but caught in validation that
  it *conflicted* with the pre-existing `_isolate_agent_worktrees_home`
  fixture (both patch the same global `pathlib.Path.home` classmethod,
  each overwriting the other's fake home) — reverted to documenting and
  regression-testing that the existing fixture already covers this for
  free, since `copilot_launch_prefs`'s `Path` is the identical class object.
