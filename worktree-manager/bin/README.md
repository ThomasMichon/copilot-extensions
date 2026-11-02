# Worktree Manager launcher scripts

These launcher/wrapper scripts are the **canonical, single implementation** of
interactive Mux (TMux/PSMux) session launch and reattach for the copilot-extensions
harness, per the [`session-hosting`](../../visions/session-hosting/README.md)
vision and [Phase 3b Slice 2](../../efforts/active/worktree-manager-control-plane/phase-3b-mux-relocation.md)
of the `worktree-manager-control-plane` effort.

- `launch-session.ps1` / `launch-session.sh` / `launch-session.cmd` — resolve a
  JSON launch plan from `agent-worktrees resolve`, execute it natively
  (mux session creation/attach, profile selection, seed injection, handoff
  cutover argv), then call `agent-worktrees post-exit` for finalization.
- `terminal-launch.ps1` - the foreground Windows shell's finite Picker handoff.
  At an interactive native entry it offers a version-1 `CE_TERMINAL_LAUNCH`
  return channel before invoking Python. A capable Manager returns its prepared
  local mux launch, then exits. Only after preparation has returned and its
  Python-owned Jobs have closed does the outer shell consume the one-shot result
  and invoke `launch-session.ps1` in its own PowerShell process. Nested entry
  points share that outer channel rather than attaching inside another Python
  waiter. The existing launcher retains attach-return, detach/exit and terminal
  cleanup duties; neither forwarding interpreter is a session-lifetime host.
  The consumed result and request directory are removed before attachment;
  literal argv arrays are splatted without shell evaluation or flattening.
  The selected script runs in the foreground host, but its process-environment
  additions, removals and changed values are restored on return, matching the
  previous child-shell isolation when the caller reuses its PowerShell session.
  A finite callback records a journaled attempt's actual launcher return, not
  preparation success. No credentials or seed prompt bodies use this channel.
  If that observer fails, a diagnostic is emitted without replacing the
  launcher's original exit result or automatically repeating a launch.
  Manager's PowerShell entry and ordinary bare/project-only CMD entry use the
  helper; Worktrees' user and attributable PowerShell entries consume the same
  canonical asset through the existing launch-wrapper manifest.
  A CMD entry without PowerShell 7 on PATH retains its existing direct Python
  route rather than making an unavailable terminal host a new Picker dependency.
  The response itself negotiates version 1 against the outer shell's request;
  no new command-line option is sent to an older runtime. A missing helper,
  older Manager that returns no prepared result, redirected stdin, explicit
  Python invocation, no-mux and remote paths retain their existing behavior
  and cannot be credited with removing both forwarding roles. New-window launch
  remains separately owned. POSIX keeps native process replacement.
- `pane-wrapper.sh` / `pane-wrapper.ps1` — wrap the actual mux pane command for
  graceful exit-code handling, initial-prompt injection, and AHP token handoff.
  The Windows wrapper binds its kill-on-close Job Object calls directly through
  .NET P/Invoke metadata, without starting a C# compiler during pane launch.
  The wrapper still owns the native job handle for its entire lifetime, so
  exiting or terminating the pane reaps its inherited child process tree.
  Native ownership coverage in `../tests/test_pane_wrapper_argv.py` exercises
  both this canonical wrapper and the promotion-materialized Worktrees fallback.
  The existing `PSMUX_FILE_LAUNCH_E2E=1` lane in `../tests/test_pane_args_file.py`
  exercises both producers over two real PSMux cycles, including window/focus
  observation and owned-descendant cleanup.
- `pane-launch.ps1` — Windows file-based argument transport. Producers invoke
  this shipped entrypoint with `pwsh -File` and `-Manifest <JSON path>`, using
  PowerShell single-quoted paths with doubled apostrophes across PSMux's space
  join. The version-1 JSON object carries `wrapper` and a non-empty string
  `argv` array, consumed and deleted before invoking the unchanged wrapper.
  Consumption holds an exclusive delete-on-close file handle through validation,
  so concurrent consumers cannot execute the same handoff twice.
  The interactive producer additionally requests an atomic, manifest-bound
  receipt containing only consumption/rejection status, validation stage and
  consumer PID, never argv or credentials. It waits up to 30 seconds for that
  explicit acknowledgment rather than treating file disappearance as success
  or assuming cold nested PowerShell starts within five seconds. Rejection,
  invalid receipt and missing acknowledgment are logged before owned-session
  teardown, with a worktree-preserving retry / `--no-mux` recovery command.
  Each create attempt uses a fresh manifest;
  failed creation cleans up its unconsumed file after owned-session teardown.
  AHP credentials still use the separate protected token handoff, not JSON.
  Relaunching requires a fresh producer command, not replaying a consumed
  one-shot handoff. Timed-out programmatic mux calls retain their manifest
  because the pane may still be starting; its consumer owns successful cleanup.
  Prepared copied shells use a `pane-args/` directory beside their launch-owned
  wrapper, outside removable installations. Legacy commands retain the runtime's
  dedicated `pane-args/` directory.
  Unconsumed files expire after 24 hours on the next resident-monitor sweep or
  Python pane-command generation, preserving delayed startup within that grace period.
  Cleanup is limited to transport-named files in those admitted directories and
  refuses symlink/junction traversal.
  A cleanup sharing violation logs a warning and retains the file for expiry
  rather than replacing the original launch failure or interrupting retries.
- `pane-args.ps1` — Picker's file-handoff helpers. After final launch and seed
  planning, marker-qualified copied PowerShell setup commands serialize startup
  argv into a second one-shot file beside their copied command reader, under the
  launch plan's tracking root. Each
  creation retry gets fresh outer and startup files; known failure, unused join
  and rejected receipt clean up both, while publication uncertainty retains
  them for the consumer or existing 24-hour expiry. The startup transport leaves
  direct setup's normal/recovery reconciliation policy unchanged. Unsupported
  commands and older copied readers retain their original invocation. These
  helpers ship beside both Manager's launcher and the engine's packaged fallback;
  they do not change creation leases, seed/console custody or uninstall admission.
- `session-options.ps1` / `session-options.sh` — per-session status bar +
  behaviors that `launch-session.ps1`/`.sh` stamp onto each mux session.
  `launch-session.ps1` dot-sources `session-options.ps1` via a
  `$PSScriptRoot`-relative path (`session-options.sh` instead uses a fixed
  `~/.agent-worktrees/bin/` path, unaffected by relocation), so the Windows
  script must ship as a sibling of the relocated launcher — omitting it left
  Worktree Manager-launched sessions with a silently unconfigured psmux status
  bar (the failure is swallowed, not a launch error).
- `apply-mux-keybinds.ps1` / `apply-mux-keybinds.sh` — opt-in, server-global
  mux tuning (keystroke passthrough + `escape-time`), shipped alongside
  `session-options.*` for parity with agent-worktrees' own deployment; run by
  the user or a machine-restore flow, never automatically.
- `psmux-passthrough.conf` — the keystroke-passthrough fragment
  `session-options.ps1` resolves by the same `$PSScriptRoot`-relative path.
- `psmux-path.ps1` — Windows psmux binary discovery/compatibility helper,
  also dot-sourced from `launch-session.ps1` by a `$PSScriptRoot`-relative
  path; ships as a sibling for the same reason as `session-options.ps1`.

**Migrated verbatim from `plugins/agent-worktrees/bin/`,
`plugins/agent-worktrees/terminal/`, and `plugins/agent-worktrees/scripts/`**
(proven, tested implementation). Phase 3b Sub-slice 2a Step 2's cutover is
complete: this is now the **one true copy** of the interactive mux launch
scripts. `agent-worktrees`'s `cmd_launch` resolves this installed location
live and execs into it; when this Manager is absent or unusable it falls back
to a small, direct non-mux Copilot invocation instead of a second, in-plugin
implementation of the mux launcher. The in-plugin scripts and their deploy
steps were deleted from `plugins/agent-worktrees/` in the same cutover.

Deployed automatically: `self_install.py`'s `_copy_payload` copies the whole
`worktree-manager/` payload directory (this one included) into each versioned
install slot — no separate packaging step is needed for this directory.
For agent-worktrees' standalone fallback, promotion also materializes the
files in `plugins/agent-worktrees/launch-wrapper-assets.json`, including
`pane-launch.ps1`, into that plugin's packaged `bin/` directory.
Python pane generation uses that fallback when an otherwise-usable older
Worktree Manager bundle does not yet contain the dispatcher and wrapper pair.
The outer launcher retains the cmd shim's Windows PowerShell 5.1 fallback:
plan argv uses PowerShell's bundled Newtonsoft reader with date parsing
disabled. The native pane dispatcher still runs through PowerShell 7.
