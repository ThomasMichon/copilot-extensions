# Live Copilot CLI update controls

`agent-machines copilot-cli` exposes explicit local operator operations while
leaving settings writes, downloads, package activation and authentication with
Copilot's native owners. It requires no adopted repository and changes no
committed requirement package, launcher profile, service or schedule.

Use the exact Agent Machines executable from the current session's command
catalog. The commands below abbreviate that executable as `agent-machines`.
`--copilot PATH`, before the operation, selects a particular native front door.
It otherwise uses the current execution environment's `copilot`.

## Inspect before changing

```text
agent-machines copilot-cli status --json
agent-machines copilot-cli versions --json
```

The schema is `agent-machines.copilot-cli.v1`. Status distinguishes the live
`autoUpdate` preference, explicitly configured channel, inherited update/package
overrides, Windows user overrides, CI markers and platform-specific cached
packages. `version_in_current_environment` is the native `--version` answer,
not proof that an existing session or a differently inherited launcher changed.
An absent channel is not guessed: the native updater resolves its effective
default using its own configuration and identity.

Cache inspection includes the current OS's native architecture variants, rather
than guessing Copilot's architecture from the Python interpreter (which may be
emulated). It recognizes the native `.extraction-complete` marker,
ordinary `index.js` and matching `package.json` metadata. Incomplete/linked
versions remain visible as not ready. An unsupported platform is explicit.

## Change the live preference

```text
agent-machines copilot-cli auto-update on --json
agent-machines copilot-cli auto-update on --apply --clear-overrides --json
agent-machines copilot-cli auto-update off --apply --json
```

Without `--apply`, mutating operations only preview their native command and
intended scope. Application delegates to
`copilot config autoUpdate true|false --global`, then checks the native settings
file. Other settings and the native cross-process configuration lock remain
under Copilot's control. `COPILOT_HOME` is honored.

These controls are **not interchangeable**:

| Control | Effect |
|---------|--------|
| Live `autoUpdate=false` | Stops background downloads; already cached newer packages can still activate. |
| `--no-auto-update` / `COPILOT_AUTO_UPDATE=false` | Can also suppress cached activation and select the older bundled version. |
| `--prefer-version VERSION` | Explicit one-launch package selection. |
| `COPILOT_CLI_DIST_DIR` | An explicit or inherited package directory; inspect its source before treating it as stale. |

`--clear-overrides` is explicit. On Windows it removes only
`COPILOT_AUTO_UPDATE` and `COPILOT_CLI_DIST_DIR` from the current user's registry
environment, after successful settings read-back. Empty values are deleted,
not replaced with another empty value. Observed concurrent changes refuse
further removal and report the partial outcome.

On every platform, that option clears those names only in the native
configuration invocation's environment. It cannot change existing parent
processes, machine-wide environment, shell profiles, tmux servers or service
environments. Those are reported, not silently rewritten or restarted.
Use a refreshed launch environment and inspect again.

A declared requirement that manages the same setting or reinstalls an
environment override can reassert its policy later. This command does not
raise package authority or change that declaration. Native settings writes
require a config-capable CLI; an older CLI gets actionable update guidance,
never a direct JSON-write fallback or a lock bypass.

## Update, roll back or pull forward

```text
agent-machines copilot-cli update --json
agent-machines copilot-cli update --apply --json
agent-machines copilot-cli update --channel stable --apply --json
agent-machines copilot-cli launch 1.0.100-1 --json
agent-machines copilot-cli launch 1.0.100-1 --apply --arg=--help
```

Update invokes the existing native updater, on its effective current channel
unless `stable` or `prerelease` is explicitly selected. Native serialization,
package staging and in-use retention remain native. A completed download does
not mean an existing session changed version. A failed/interrupted native
operation reports an unknown outcome rather than replaying it automatically.

Launch requires a standalone native binary, not a batch/npm launcher, and
previews a cached-version invocation by default. `--apply` verifies the
native version answer and rechecks cache readiness. POSIX replaces the current
process; Windows waits for the foreground child with inherited stdio and
propagates its exit status. Neither path detaches the session.
The native front door selects its architecture from
the reported cache candidates. `--arg` forwards one argument at a time; it cannot
override the selected version. Only the inherited package-directory selector
is removed for that invocation. No persistent pin, binary replacement, cache
deletion or new process supervisor is introduced. Native admission remains the
final authority if cache state changes concurrently.

For an uncached exact version, use native `/downgrade <version>` interactively;
despite its name, it is the native exact-version acquisition/restart operation.
Do not silently substitute another version or download/repackage it here.
For managed worktrees, retain the normal launch/resume owner and pass the
explicit version through its supported one-launch arguments rather than
starting a second owner for the same session.

## Propagation and compatibility

Run the same preview/application on each explicitly selected execution
environment through its existing local or SSH transport. Windows and WSL are
separate targets. Resolve the target's actual Agent Machines invocation; a
local catalog path is not a portable remote path.

First use read-only `copilot-cli status` on each target. Older Agent Machines
versions reject this additive command family; update through that target's
normal owner before issuing a new mutating invocation. There is no remote
mutation-and-fallback replay, new fleet registry, automatic enrollment or
background propagation claim. Preserve per-target successes and failures.

Ordinary Bridge ACP launches now inherit native update policy instead of
adding an unconditional opt-out. Explicit caller opt-outs still work.
Isolated acquisition, test and sandbox pins remain independent.

See [the operator skill](../skills/controlling-copilot-updates/SKILL.md) and
[requirement configuration](configuration.md) for the other ownership boundary.
