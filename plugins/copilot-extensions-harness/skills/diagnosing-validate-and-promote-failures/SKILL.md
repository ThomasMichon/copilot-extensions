---
name: diagnosing-validate-and-promote-failures
description: >
  Diagnose a red `validate-and-promote` GitHub Actions run
  (`.github/workflows/validate-and-promote.yml`) -- the automated dev-to-main
  release-pipeline gate for copilot-extensions. Walks the `gh run`/`gh run
  view --log` sequence to identify every failed job (a per-plugin fan-out
  job or one of the other required gate jobs), read a fan-out job's failure
  literally, and classify it as a known accepted flake, a
  CI-environment-specific test bug, or a genuine regression -- the latter two
  owed a real fix-forward PR, never a "pre-existing" shrug. Use when
  `validate-and-promote`/`dev-advanced` is failing, promotion is stuck, or a
  contributor asks "why won't dev promote to main".
  Trigger phrases include:
  - 'validate-and-promote failing'
  - 'dev to main promotion stuck'
  - 'release pipeline red'
  - 'dev-advanced workflow failed'
  - 'why is the promotion blocked'
  - 'diagnose the release pipeline'
  - 'full suite failed in CI'
---

# Diagnosing `validate-and-promote` failures

`validate-and-promote` is the sole automated gate between `dev` and `main`
(dev-branch-release-pipeline effort, `ThomasMichon/copilot-extensions#3336`).
It is entered only via `repository_dispatch` (event type `dev-advanced`,
fired by the separate, cheap `promote-trigger.yml` on a `workflow_run` of
`CI`) or a human `workflow_dispatch` -- never directly by `workflow_run`
itself. Its promotion gate (the `promote` job) needs **all** of: `gate`,
`full` (the per-plugin fan-out matrix, one job per plugin listed in the
current full matrix, `fail-fast: false` so more than one can be red at once),
`worktree-manager` (out-of-plugin worktree-manager suite), and
`guards-full-sweep` (full-tree, non-PR-scoped guard checks) to succeed. A red
run blocks **all** pending contributors' already-merged work, not just
whoever's change happened to break it — treat it with the same urgency as a
production incident, not a personal test failure.

Route every `gh` call in this runbook through the repository-scoped account
wrapper -- resolve the current session's actual catalog invocation name
(`<agent-worktrees catalog argv[0]>`) rather than assuming a bare
`agent-worktrees` on PATH resolves to the right runtime/account mapping; see
`contributing-to-copilot-extensions`'s own account-routing rule. The
commands below use `<agent-worktrees catalog argv[0]>` as that placeholder.

## Procedure

1. **List recent runs** — find the failing run(s) and confirm the pattern
   (one bad run vs. a persistent block):

   ```bash
   <agent-worktrees catalog argv[0]> repos gh ThomasMichon/copilot-extensions -- run list -R ThomasMichon/copilot-extensions --workflow=validate-and-promote.yml --limit 20  # marketplace-isolation: allow diagnostic-example
   ```

2. **Open the failing run** and read its full job list — because
   `fail-fast: false`, more than one job can be red, and the failure is not
   necessarily a `full - <plugin>` fan-out job at all; `worktree-manager` and
   `guards-full-sweep` gate promotion just as hard and need different
   diagnosis than the fan-out guidance in steps 3-4 below:

   ```bash
   <agent-worktrees catalog argv[0]> repos gh ThomasMichon/copilot-extensions -- run view <run-id> -R ThomasMichon/copilot-extensions  # marketplace-isolation: allow diagnostic-example
   ```

   Note every failing job's exact name and numeric ID from the output.

3. **Pull each failing job's full log** and grep for the failure summary —
   the log is large (tens of KB), so save it to a file and grep rather than
   reading it inline:

   ```bash
   <agent-worktrees catalog argv[0]> repos gh ThomasMichon/copilot-extensions -- run view <run-id> -R ThomasMichon/copilot-extensions --job <job-id> --log > /tmp/run-<run-id>-job-<job-id>.log  # marketplace-isolation: allow diagnostic-example
   grep -n "FAILED\|short test summary\|AttributeError\|Traceback\|Error" /tmp/run-<run-id>-job-<job-id>.log
   ```

   For a `full - <plugin>` fan-out job, read the literal `FAILED
   <test-node-id> - <ExceptionType>: <message>` line and the traceback above
   it before forming a hypothesis. For `worktree-manager` or
   `guards-full-sweep` (or any other job), read that job's own failure
   output the same way -- literally, before hypothesizing.

4. **Classify a fan-out (pytest) failure — three ways, not two:**
   - **Known, accepted flake or transient failure** — a genuinely
     environmental hiccup (a transient checkout/setup/runner failure, a
     flaky external dependency) already tracked by its own open issue.
     Confirm via same-SHA rerun evidence before assuming this; link the
     tracked issue in your PR description rather than forcing an unrelated
     code change (see `contributing-to-copilot-extensions`'s flake
     exception -- the *only* sanctioned exception to the fix-forward
     obligation below). An *undocumented* one-off still needs a fix or a
     newly filed tracking issue, not a silent skip.
   - **CI-environment-specific test bug** — the test's own assumption
     doesn't hold on the runner (classic case: referencing a platform-only
     `subprocess`/`os` constant, like `CREATE_NEW_CONSOLE`, that only exists
     on Windows, while `validate-and-promote`'s full suite runs on
     `ubuntu-latest`). Confirm by reading the production code path the test
     exercises: if it's correctly guarded (e.g. `platform.system() ==
     "Windows"`) and only the *test* fakes that branch on a non-Windows
     runner, the code is fine and the test needs hardening (fake the
     platform-only constant too, e.g. `getattr(subprocess,
     "CREATE_NEW_CONSOLE", <fallback>)`, not just the branch condition).
   - **Genuine regression** — the failing assertion reflects real changed
     behavior in the plugin's own source. Fix the source, not the test.

5. **Check whether it's already fixed** — another contributor or a
   concurrent agent may have landed the fix already (this pipeline blocks
   *everyone*, so it draws fast, parallel attention). Before opening a
   worktree, fetch and inspect `origin/dev`'s tip explicitly, and search both
   open **and merged** PRs -- a local checkout's bare `HEAD` may sit on a
   different branch or a stale fetch, and a default `gh pr list` only
   searches open PRs, either of which would miss an already-landed fix and
   risk a duplicate PR:

   ```bash
   cd <writable checkout>   # from a `related resolve copilot-extensions` lookup
   git fetch origin dev
   git log --oneline -3 origin/dev -- <path/to/the/failing/test-or-source>
   <agent-worktrees catalog argv[0]> repos gh ThomasMichon/copilot-extensions -- pr list -R ThomasMichon/copilot-extensions --state all --search "<test name>"  # marketplace-isolation: allow diagnostic-example
   ```

   If `origin/dev`'s tip already contains a fix, don't duplicate it —
   finalize any worktree you opened as unused and just watch for the next
   `dev-advanced` run to go green.

6. **Land the real fix** through the normal flow — create a worktree off
   `dev`, fix the test or the source, add a changefile, run the plugin's own
   suite, and land it via `contributing-to-copilot-extensions`'s PR flow.
   Never patch `main` directly, and never treat a retry/force-deploy of the
   pipeline itself as a substitute for a real fix (see the `error-response`
   discipline: an error names a symptom, not a license to force past it).

## Worked example

`test_windows_falls_back_to_new_console_without_wt` faked `platform.system()`
to `"Windows"` to exercise the module's `_windows_spawn`'s no-Windows-Terminal
fallback (at the time, `headed_launch._windows_spawn` in agent-worktrees;
relocated to `worktree_manager.new_window_spawn._windows_spawn` by Phase 9 of
the `worktree-manager-control-plane` effort, #5210 -- the lesson below is
unchanged by the move), then asserted against
`subprocess.CREATE_NEW_CONSOLE` directly. That attribute is real only on an
actual Windows Python build; on the Linux `ubuntu-latest` runner it raised
`AttributeError: module 'subprocess' has no attribute 'CREATE_NEW_CONSOLE'`
before the faked `Popen` was ever reached. The production code was already
correctly platform-guarded (`spawn_detached_new_window` only calls
`_windows_spawn` when `platform.system() == "Windows"`); the fix was
module-level `_CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE",
<fallback flag>)`, used by both the production code and the test, so faking
the platform branch on any OS no longer requires the constant to genuinely
exist there.

## Reference

`contributing-to-copilot-extensions` (the full PR flow, the fix-forward
obligation and its known-flake exception, the mandatory changefile/version
bump, and the `<agent-worktrees catalog argv[0]>` account-routing
convention); `diagnosing-copilot-extensions` (the sibling skill for
deployed-plugin/runtime symptoms, as opposed to this repo's own CI); the
dev-branch-release-pipeline effort (`ThomasMichon/copilot-extensions#3336`)
for the pipeline's own design.
