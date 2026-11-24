# Command entry coverage inventory

Parent: [Discoverable Command Errors](README.md). Tracking: #14336.
This maps implementation owners; it is not evidence that error capture is
already implemented or installed.

## Portable source owners

| Family | Canonical producer or entry owner | Required integration |
|----------|-------------------------------|----------------------|
| Published payload shims | `libs/payload-invocation/generate.py` and its POSIX, PowerShell and CMD templates; plugin `payload-invocation.json` declarations | Extend the producer and materialization closure, not individual generated copies. The initial inventory has 13 manifests and 18 commands. |
| Required-context dispatch | The twelve context-aware manifests' plugin-owned `runtime-gate.*` or `invoke-payload-runtime.*` dispatchers | Native errors before Python require an entry adapter; a payload-template-only change is incomplete. |
| Project shortcuts | `plugins/agent-worktrees/src/agent_worktrees/project_command_templates.py`, selected by `installer.py` | Cover current project shortcuts without rewriting historical recognition-only templates or changing the receiving command grammar. |
| Legacy compatibility commands | Plugin installers' native wrapper publication and Worktrees' checked-in `bin` sources | Treat each still-supported direct entry as a real ingress, even when a newer payload path exists. |
| Versioned application commands | `libs/installer-engine/runtime_application_binstubs.py`: setup, slot and global binstubs | Record missing/invalid selection, staging, UV and launch failures through the shared application producer, including Manager. |
| Application materialization | `tools/runtime_application_project.py` and `tools/materialize_source_applications.py` consume that application producer | Generated candidates must contain the admitted diagnostic support; do not add a competing Manager-specific launcher. |
| Bootstrap/native capture | Payload Invocation runtime admission and Installer Engine `bootstrap-python.ps1`, `bootstrap-uv.ps1`, `bootstrap-capture.ps1` | Compose native early-error/completion/Job outcomes with #8370 rather than placing a second supervisor around them. |
| Other declared native/Node entries | Final bounded declaration inventory pending | Do not claim these are covered by the Python payload manifests. Resolve their actual producer or record that no published binstub exists before closing the inventory gate. |

`tools/materialize_payload_invocation.py` regenerates declared payload outputs
and checks competing destinations. Its candidate closure and each producer's
packaging inputs must include the same diagnostic implementation.

## Existing implementation seams

- `process_telemetry.diagnostic_logs.DiagnosticLog` already supplies retained,
  attributed diagnostic persistence under an explicit owner directory/custody.
  It does not discover a command's root or intercept its failures.
- `agent_procutil.diagnostics` supplies bounded/redacted text, structured error
  selection and stderr hints. Its refusal to copy opaque stdout is intentional.
- `update_telemetry.human.native_output` is an explicit known-human framing
  adapter, not protocol-safe general capture. Keep ordinary inherited streams.
- Installer Engine's native bootstrap capture already owns correlated child
  completion and Job retirement. Extend its existing error/result seam.
- Existing boot-phase traces are not terminal command-error coverage, and
  logging must not create an absent legacy runtime merely to obtain a sink.

The shared contract belongs in these existing evidence/entry owners. Native
adapters must be usable before the application's Python environment exists;
runtime adapters then capture parser, exception and reported-error context
inside the selected process.

## Adopter integration

Repository-owned generators can emit managed Python, stdlib Python, trusted
system-Python, PowerShell and Bash commands. Bootstrap/root-development/setup
entries and manually maintained exceptions may sit outside their manifest.
Each adopting repository must map these to the same diagnostic contract through
its own reviewed generator and dependency/packaging owners. Their private
inventory and deployment evidence remain local to that repository.

Do not silently count a project shortcut as an adopter-owned implementation:
the portable project-command producer above owns that entry.

## Compatibility controls

- POSIX `exec` paths retain PID/signal semantics.
- PowerShell stderr forwarding must not accidentally promote native stderr
  into a terminating ancestor-script error.
- Preserve the declared distinction between PowerShell object input and raw
  redirected native stdin.
- Leave protocol streams unchanged; do not use human progress framing as a
  generic stderr archival mechanism.
- Observe direct-child completion through its existing owner rather than wait
  indefinitely for a retained descendant to close an inherited pipe.
- Do not rewrite an executing CMD wrapper in place or change its byte-offset
  continuation semantics.

## Focused validation homes

- Payload Invocation: `tests/test_generate.py` and
  `tests/test_command_lifecycle_stdio.py`, including actual first-use failure,
  stdout/stderr, interruption and child/Job controls.
- Application binstubs: `tools/test_runtime_application_binstub_paths.py` and
  `tools/test_runtime_application_binstubs.py`.
- Plugin ingress: each changed plugin's `tests/test_payload_invocation.py`;
  Worktrees' installer/global-publication tests.
- Adopters: their generator parity, actual forwarded stdin/Unicode/exit,
  bootstrap error and setup-result protocol tests.

Add focused regressions that fail without the new behavior. Keep the finite
native acceptance packet separate from required unit CI and record any
unqualified family as an open completion gate, never as inferred coverage.
