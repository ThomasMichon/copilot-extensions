# Retained test environments and source lifetime

The repository test runner retains source-bound environments outside the
checkout. An editable installation can still import checkout files after its
test process exits. An idle process list or a released test lease does not prove
that deleting the checkout is safe.

Use `finalize --retain-checkout` when the selected Worktrees runtime supports it.
Keep the source until every dependent environment has an explicit disposition.
This is separate from logical worktree finalization and from physical cleanup.

## Retire one environment

`tools/plugin_test_retirement.py` owns terminal retirement of one selected test
environment. Preview and apply use the same environment-identity lease as test
preparation and execution. They do not create a missing environment or scan
arbitrary directories.

For example, from a checkout on Windows:

```powershell
python tools\plugin_test_retirement.py preview --source "<source-checkout>" --target agent-worktrees
python tools\plugin_test_retirement.py apply --preview-file "<saved-preview.json>"
```

Save the complete preview JSON in an owner-claimed scratch location before the
second command. On other platforms use their path separator. The optional
`--platform` must name the current native Python platform; this tool does not
retire another operating system's environment.

Apply recomputes the selected source/cache binding and checks both directory
identities and the preview's cache generation under the lease. A busy environment returns
exit code 3 without changing its contents. A replaced, stale, linked,
unqualified or unreadable selection refuses before removal.

Retirement first writes a terminal marker, then removes only the selected
environment's contents. It preserves the directory inode and marker. Normal
runner preparation, including `--reinstall`, refuses a retired environment.
This prevents an admitted waiter from recreating usable imports while source
cleanup proceeds. Use a new managed source checkout for later work.
Use ordinary `--reinstall`, not terminal retirement, when an active checkout
only needs its environment rebuilt.

A partial failure returns `source_retirement_safe: false`. Keep the source and
remaining cache contents. The marker continues to block preparation. After the
blocking condition is resolved, obtain a new preview before retrying. An
unreadable canonical marker remains a refusal, not permission to delete
or rebuild it. Marker writes use unique temporary files and clean their own
failed publication when possible. A stopped writer can leave an orphan temporary
file; a fresh preview can resume a valid `retiring` marker and retire that
selected environment, including stale temporary publication files.
Do not stop another process or change permissions to force
retirement.

## Interpret the result narrowly

`source_retirement_safe: true` has scope `selected-environment-only`. It means
that this environment no longer provides usable source-dependent imports.
It is not permission to remove the source checkout. Other test targets,
platforms, type-checker environments, external consumers, execution history,
open work and live sessions retain their own owners and cleanup checks.

The tool leaves sibling environments, shared UV/interpreter caches, reports,
source files and source history unchanged. Its terminal marker is lifecycle
state in the existing cache, not a second allocation index or a background
sweeper. The small directory is deliberately retained so existing waiters
cannot cross into a newly allocated inode.

Run source cleanup only through its normal owner after all applicable
dependencies and preservation requirements are resolved. Unknown ignored files
and checkout-local agendas are not disposable test-cache data.
