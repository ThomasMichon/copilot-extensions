# Fresh bootstrap source recovery

The canonical Windows entry is `worktree-manager/bootstrap.ps1`. Consumers
download the current upstream Main entry first, independently of the requested
installation commit and existing Manager transport configuration. Before invoking
discovery, require this exact passive line once:

```text
# copilot-extensions:recovery-source-contract=1
```

`--describe-recovery-source` is pure JSON capability discovery. Never probe an
older bootstrap with this flag: older entries can interpret unknown arguments
as application commands.

`--recover-source <absolute-request-json-path>` accepts exactly:

```json
{
  "schema": "copilot-extensions.recovery-request",
  "version": 1,
  "operation": "preview",
  "selection": {"mode": "main", "commit": null, "checkout": null},
  "manager_root": null,
  "project": null
}
```

Use `install` for the native installation journey. Selection modes are `main`,
`commit` (a full lowercase Git object ID), and `configured-main-pin` (an existing
exact `[runtime_source]` policy). Only `commit` accepts a non-null commit.
`checkout` and `manager_root`, when supplied, must be absolute local paths.
The checkout supplies Git objects, not writable source files or authoritative
Main refs. Native acquisition fetches Main ancestry independently and retains
one commit/tree. Main selection refuses to silently override a configured
non-Main policy. No selection rewrites that policy.

The entry requires existing Git and physical Python 3.11+, fetches a fresh
upstream Main toolkit, and never invokes an installed dispatcher on acquisition
failure. Preview may clone and export disposable source data; it does not build
or publish a runtime, install prerequisites, change registrations, or select,
start or stop a generation. Repair decisions remain with the authorized
native apply path. Preview obtains read-only footprint inventory
through the fresh native owner when its ordinary tool dependencies are available;
otherwise it reports inspection as unavailable, never as healthy or repaired.

## Native composition

The fresh toolkit composes the existing immutable acquisition, local source
promotion, wheel/profile preparation, Manager installation participant, engine
repair, inventory and suite execution owners. It is not a dependency planner,
installer or supervisor. Manager installation alone is not suite completion.
Apply borrows one request-local foreground operation from the native execution
driver. An occupied or fenced queue refuses admission; recovery neither drains
another request nor reconciles a foreign foundation operation. The same live
custody covers Manager construction, engine repair and suite execution.
Every fenced foundation record, including an owner-lost `running` record,
refuses request-local admission without alteration. An older supported queue
schema is migrated by the native owner only after custody and idle/unfenced
checks, before accepting or executing the new operation.
Captured engine attribution uses the existing repair owner. Missing or
unrecognized historical attribution instead selects the native install or
independent-repair entry under its registered source; it is not retried as a
legacy flow after an advertised prepared-input failure.
The inventory and suite control code come from the fresh toolkit, so an older
selected engine dispatcher cannot discard the new input protocol. Installed
application code still comes from the one selected commit.
Payload acquisition and the runtime portfolio use the native isolated phases.
Recovery does not run repository anchor synchronization, pivot pruning, or an
integration-time Manager recovery that could replace the explicit selection.
The selected source's `source-install.json` declares its prepared-input
capability. Currently Manager and Worktrees supply it; undeclared packages keep
their native legacy source flow unless they already expose the released native
`--install-source`/`install_source` protocol. That existing protocol is inspected
without running an unknown discovery flag, so released prepared-input adapters
do not require a declaration retroactively. A later package opts in through the same
declaration, without changing external consumers or a package/version matrix.
Unknown declarations fail rather than falling back.

The native suite's V1 prepared-source packet remains supported. V2 additionally
carries per-target `install_source` and the complete native selection returned
by `install_source.native_selection`. The installed execution owner revalidates
these inputs and uses the constructor's profile generation and expected
construction hash, not the raw source base version. An independent package
source-target policy cannot be bypassed by attaching wheel inputs.
Windows application launchers also carry the profile's explicit Python platform
to UV, so a supported emulated interpreter does not inherit the host UV binary's
different architecture when checking the closed lock.

Older selected sources must support native transaction pinning and the bounded
Manager installation participant. Unsupported historical sources fail
explicitly; they are not replaced with a newer source. A selected older engine
without V2 support can still execute an entirely legacy V1 portfolio.

Apply retains its exact inputs under the selected Manager root's
`runtime-sources/recovery/` attempt directory, including on failure. The final
`copilot-extensions.recovery-result` JSON reports the immutable source, actual
per-target flows, native stage exits, and retained input path. Native output is
forwarded separately; a partial result is not rollback or cleanup evidence.
No teardown, queue recovery, policy-pin activation or foreign-resource cleanup
is authorized by this request. Native ownership, execution custody and
publication gates still apply.
The downloaded toolkit is retained for install operations too; its checkout is
reported separately from the target source. This preserves code and evidence
needed by native custody after an uncertain or interrupted outcome.
