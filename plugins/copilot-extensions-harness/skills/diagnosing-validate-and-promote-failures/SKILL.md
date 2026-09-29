---
name: diagnosing-validate-and-promote-failures
description: >
  Diagnose a red `validate-and-promote` GitHub Actions run
  (`.github/workflows/validate-and-promote.yml`) -- the automated dev-to-main
  release-pipeline gate for copilot-extensions. Walks the exact `gh run`/`gh
  run view --log` sequence to isolate the one failing per-plugin fan-out job,
  read the pytest failure literally, and tell a CI-environment-specific test
  bug (e.g. a Windows-only `subprocess` constant referenced on a Linux
  runner) apart from a genuine regression -- both owed a real fix-forward PR,
  never a "pre-existing, not my concern" shrug. Use when
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

`validate-and-promote` (triggered by `workflow_run`/`repository_dispatch` as
`dev-advanced`, per the dev-branch-release-pipeline effort,
`ThomasMichon/copilot-extensions#3336`) is the sole automated gate between
`dev` and `main`. It fans out one full-suite job per runtime plugin
(`full - agent-worktrees`, `full - agent-bridge`, ...); promotion proceeds
only when **every** fan-out job is green. A red run blocks **all** pending
contributors' already-merged work, not just whoever's change happened to
break it — treat it with the same urgency as a production incident, not a
personal test failure.

## Procedure

1. **List recent runs** — find the failing run(s) and confirm the pattern
   (one bad run vs. a persistent block):

   ```bash
   agent-worktrees repos gh ThomasMichon/copilot-extensions -- \
     run list -R ThomasMichon/copilot-extensions \
     --workflow=validate-and-promote.yml --limit 20
   ```

   (Route every `gh` call through `agent-worktrees repos gh <owner/repo> --
   <gh args>` — never a bare `gh`; see `contributing-to-copilot-extensions`
   and the repo's own account-routing warning from `related resolve`.)

2. **Open the failing run** and read its job list — the workflow's own
   per-plugin fan-out means exactly one (or a small subset) of the `full -
   <plugin>` jobs is red while the rest are green:

   ```bash
   agent-worktrees repos gh ThomasMichon/copilot-extensions -- \
     run view <run-id> -R ThomasMichon/copilot-extensions
   ```

   Note the failing job's numeric ID from the output (e.g. `full -
   agent-worktrees in 2m37s (ID 109338891734)`).

3. **Pull that job's full log** and grep for the pytest summary — the log is
   large (tens of KB), so save it to a file and grep rather than reading it
   inline:

   ```bash
   agent-worktrees repos gh ThomasMichon/copilot-extensions -- \
     run view <run-id> -R ThomasMichon/copilot-extensions \
     --job <job-id> --log > /tmp/run-<run-id>-job-<job-id>.log
   grep -n "FAILED\|short test summary\|AttributeError\|Traceback" \
     /tmp/run-<run-id>-job-<job-id>.log
   ```

   Read the literal `FAILED <test-node-id> - <ExceptionType>: <message>`
   line and the traceback above it before forming a hypothesis.

4. **Classify the failure — test bug vs. real regression:**
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
   worktree:

   ```bash
   cd <writable checkout>   # from `related resolve copilot-extensions`
   git log --oneline -3 -- <path/to/the/failing/test-or-source>
   agent-worktrees repos gh ThomasMichon/copilot-extensions -- \
     pr list -R ThomasMichon/copilot-extensions --search "<test name>"
   ```

   If `dev`'s tip already contains a fix, don't duplicate it — clean up any
   worktree you opened (`copilot-extensions finalize`) and just watch for the
   next `dev-advanced` run to go green.

6. **Land the real fix** through the normal flow — `copilot-extensions
   create` a worktree off `dev`, fix the test or the source, add a
   changefile, run the plugin's own suite, and land it via
   `contributing-to-copilot-extensions`'s PR flow. Never patch `main`
   directly, and never treat a retry/force-deploy of the pipeline itself as a
   substitute for a real fix (see the `error-response` discipline: an error
   names a symptom, not a license to force past it).

## Worked example

`test_windows_falls_back_to_new_console_without_wt` faked `platform.system()`
to `"Windows"` to exercise `headed_launch._windows_spawn`'s
no-Windows-Terminal fallback, then asserted against
`subprocess.CREATE_NEW_CONSOLE` directly. That attribute is real only on an
actual Windows Python build; on the Linux `ubuntu-latest` runner it raised
`AttributeError: module 'subprocess' has no attribute 'CREATE_NEW_CONSOLE'`
before the faked `Popen` was ever reached. The production code was already
correctly platform-guarded (`spawn_headed_attach` only calls
`_windows_spawn` when `platform.system() == "Windows"`); the fix was
module-level `_CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE",
<fallback flag>)`, used by both the production code and the test, so faking
the platform branch on any OS no longer requires the constant to genuinely
exist there.

## Reference

`contributing-to-copilot-extensions` (the full PR flow, the fix-forward
obligation, the mandatory changefile/version bump);
`diagnosing-copilot-extensions` (the sibling skill for deployed-plugin/runtime
symptoms, as opposed to this repo's own CI); the
dev-branch-release-pipeline effort
(`ThomasMichon/copilot-extensions#3336`) for the pipeline's own design.
