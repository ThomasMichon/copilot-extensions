# Manager automatic-update worker qualification

This Tier-P fixture uses actual OS processes, authenticated production worker
health, canonical scheduling/update leases and actual immutable runtime
construction with retained slot-local launchers.
It never changes the production Worker, policy or process modules. Both copied
runtime generations are checked byte-for-byte against the candidate's
`automatic_updates_*.py` files.
The qualification owner supplies the existing shared disposable-daemon
lifecycle declaration to actual fixture children. Startup and detached launches
use the same instance-admission substrate as production, with explicit test
expiry rather than a competing fixture registry. Production has no expiry.

## Evidence boundary

The copied Manager main substitutes its update-step implementation and imports
fixture-only telemetry, private-home and native disposable-lifecycle bindings
(in the negative case it fails worker startup while leaving `--version` usable).
Its public update guard, telemetry implementation and Worker are unchanged.
The finite command waits on a fixture-owned
file, holds the genuine update transaction, and writes per-version witnesses.
Telemetry uses the supported private update-home override.
Native supervision retains the actual user home only for its owned startup
registration. Every fixture `update` process switches to the fixture's private
execution home before queue admission; its driver descendants inherit that home.
Caller-provided update-driver custody is not forwarded. The native fixture never
borrows or drains the production user's durable installation queue.

The Manager policy names the nonempty synthetic project `fixture`; the finite
update-step body never calls an engine or resolves a real repository. Actual
project registration, CLI enrollment and register-driven adoption are not
qualified here. Plain mode launches foreground `automatic-updates run` in an
owned child, not `ensure` with an unverified startup registration.

Every report declares `cycle_kind=finite-guarded-command-fixture` and
`real_installation_cycles=false`. These tests are not real software installation,
legacy-controller retirement or source-managed deployment acceptance.

The fast mode proves one guarded child can finish while its old controller
remains responsive, followed by actual standby readiness, exclusive handover,
old-process retirement and failed-candidate preservation in a separate root.
An independent process verifies duplicate-worker refusal and public update
submission through the canonical durable queue. A foreign-controller request
must remain pending while the first installation runs, then fail execution-time
ownership admission without starting another installation. Ordinary moving-target
requests are not required to coalesce with already-running work.
Cleanup disables only fixture policy, waits on pinned
kernel process handles and verifies both canonical leases become available.
It never kills by PID/name or substitutes another shutdown implementation.

## Prepare an exact self-contained scenario

Run from a fresh Python process. Both destinations must be caller-provided,
fresh private directories outside checkouts and session-state folders:

```bash
python tools/clean-room/scenarios/manager-automatic-updates/probe.py prepare \
  --source <candidate-repository-or-Manager-payload> \
  --output <private-scratch>/prepared-scenario
```

Preparation calls the actual `self_install._copy_payload` to materialize the
candidate dependency closure. The output contains a self-contained `payload/`,
the probe and its helpers. The rig does not mount the source checkout.

```bash
tools/clean-room/run.sh --no-token --name-suffix worker-probe \
  --scenario <private-scratch>/prepared-scenario run
```

No account/token injection is required. Normal UV dependency aging, feeds and
resolution remain in effect; preparation never uses pip or disables governance.

## Portable probe and native venue steps

```bash
python <prepared-scenario>/probe.py qualify \
  --source <prepared-scenario>/payload \
  --workdir <private-scratch>/worker-qualification --cycles 1
```

`--cycles 2` observes two actual background fixture commands without changing
the policy's 300-second minimum, clocks or production poll constants. Budget at
least six minutes; the report checks that cycle two starts at least 300 seconds
after cycle one's end. Even this is not real installation-cycle proof.

The actual canonical installer constructs each fixture slot and its frozen
payload before invocation. The selected slot's retained interpreter/bootstrap
runs the worker without resolving dependencies or rebuilding at startup.
Dependency declarations and the existing seven-day aging policy remain
unchanged; the exact target is recorded in each case report.
The disconnected devcontainer regression uses UV's normal offline mode and the
package-manager cache populated by the runner's network-enabled dependency
preparation. It does not copy an existing venv or disable package aging.

`--native-startup` is explicit authority for this disposable fixture only.
On an unelevated Linux/WSL venue with an available user manager, it registers and
activates the exact production-generated unit, verifies MainPID is the direct
health-verified Python worker in the unit's cgroup, and observes native restart
across the source advance. The healthy predecessor retains scheduling custody
while a non-owning standby proves worker startup and exits; only then does
systemd restart the native MainPID. Failed candidate startup leaves that MainPID,
restart count and lease unchanged. A separate native case sends SIGTERM through a
pinned pidfd while a guarded command is active, then proves cooperative drain,
successful exit and no failure restart. Retirement removes the exact-owned unit only after
the worker drains and exits. No linger, elevated service or live Manager root
is used. Plain mode has an isolated HOME, config and unavailable session bus;
native mode necessarily addresses the current user's fixture-only unit.
Because systemd does not inherit the client's environment, the immutable fixture
loads its exact qualification-owned disposable declaration from a private
fixture binding before Worker admission. The real MainPID's shared instance
record must match this qualification's owner/run and finite lease. Production
unit bytes and ownership receipts are unchanged; no drop-in or user-manager
environment mutation is used.

### Windows native packet from a normal unelevated console

From the candidate repository, a human can run this one PowerShell command:

```powershell
py -3 tools/clean-room/scenarios/manager-automatic-updates/probe.py qualify --source . --workdir "$env:TEMP\mq-$([Guid]::NewGuid().ToString('N').Substring(0,8))" --cycles 2 --native-startup
```

Do not elevate the console. The adapter checks actual TokenElevation,
GetShellWindow/GetForegroundWindow, visible WinSta0, matching accessible input
desktop and user session. Elevated, SSH, service/session-0, locked-desktop or
inaccessible-desktop venues are blocked before registry writes. Fresh fixture
roots receive a protected inheritable current-user/Administrators DACL; volumes
without persistent ACLs are blocked.
The adapter also checks the real Run command's 260-UTF-16-unit limit. If the
Python install or temporary-folder path is unusually long, use a shorter
existing user-writable scratch parent; it never substitutes a junction,
drive mapping, shell shim or different startup command.

It registers only the fixture's unique HKCU Run value and executes that exact
REG_SZ command through the genuine GUI bootstrap inside a strict canonical
kill-on-close Job. A canonical `spawn_daemon(breakaway=False)` control stays
contained. After authenticated worker readiness, closing the inner Job must
kill that pinned control while the pinned worker and its authenticated health
remain live. Production `ensure` and `spawn_daemon` own the worker launch;
the probe does not replace their lifetime implementation.
The production Windows value contains only the real GUI interpreter, `-I -S`
and the durable bootstrap path. That installed bootstrap infers its root from
its fixed location and exact ownership receipt; the root is not duplicated in
the Run command. Explicit `--root` remains supported for legacy invocations,
and source-module execution still requires it. Regenerate prepared payloads
after this runtime change; older native packets do not certify the new hashes.

An EnumWindows/foreground observer runs continuously across both genuine
300-second-cadence fixture cycles, startup, handover and cleanup. It retains
creation-identified process handles and a baseline of already-visible windows,
so the existing terminal is not classified as a newly surfaced window.
Keep the desktop quiet: any new visible window or foreground transition is
classified as fixture-attributable or ambiguous external desktop activity,
never silently ignored as a passing observation. Polling is 20 ms; observer
failure or an observed coverage gap over 500 ms blocks the packet. Window
titles, raw environment and executable paths are not copied into the observation
packet.
An initially absent foreground window blocks observation startup. A later NULL
or disappeared foreground identity is retained as an ambiguous violation while
observation continues; Win32 permits NULL during activation transitions and
does not define a last-error failure contract for `GetForegroundWindow`.
Toolhelp process-entry types and function signatures are configured once, so
continuous polling and concurrent child identity checks cannot replace each
other's ctypes pointer types. Case results retain the original failure as well
as any later cleanup failure.

Success requires actual two-cycle timing, exact registered-command execution,
the contained-control Job-closure witness and surviving authenticated worker
health, plus zero window/focus observations. Neither a one-cycle run nor Linux
stub tests can mint a Windows-native pass. The output is `qualification.json`
in the supplied scratch root; raw fixture logs/specs remain private and should
not be published wholesale. Cleanup disables/drains only this fixture and
removes only its exact-owned Run value; no Task Scheduler, UAC, deployed process
or other startup entry is touched.

Reports and per-case logs remain in the caller's scratch directory. On any
cleanup deadline failure, retain that directory and diagnose it; the probe
does not force-stop an unverified process.

The isolated devcontainer regression is selected explicitly with:

```bash
python tools/run_tests_in_devcontainer.py worktree-manager \
  --selection-file tools/clean-room/scenarios/manager-automatic-updates/test-selection.json \
  --allow-explicit-tiers
```

If its pre-seeded pip cache lacks UV's canonical project-resolution metadata,
the process qualification reports an explicit skip, not a passing lifecycle
witness. Run the Tier-P scenario with normal dependency resolution (or provide a
properly warmed UV cache); do not copy an installed environment around the check.

See the [clean-room operator guide](../../README.md) and the
[Manager-owned maintenance matrix](../../../../efforts/active/source-managed-runtime-installs/maintenance.md).
