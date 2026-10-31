# Logger legacy command publisher adoption

Parent: [Source-Managed Runtime Installs](README.md), Phase 4b.
Coordination: #7758; same Installation execution implementer.
This is **agent-recommended integration** of the qualified Logger recipes,
not another installation authority or a new operator requirement.

## Boundary and source facts

Logger has six required-context commands: `agent-logger`, `collate-session`,
`read-session-digest`, `prepare-session-log`, `ramp-up-session` and
`session-sync`. Their generated payload shims pass the owning payload and
fixed command/module pair to Logger's native runtime gate. That gate currently
uses foreground service-less `provision` under a separate outer provisioning
lock after its healthy read-only dispatch and publication probe.

The global primary compatibility binstub has separate emitters in Logger's
`scripts/install.sh` and `scripts/install.ps1`; it is not SSH's shared primary
template. It currently invokes a selected snapshot installer directly, and the
POSIX emitter can fall back to an arbitrary installed payload. The five global
auxiliary compatibility wrappers already delegate through the selected
`payload-dir` to that snapshot's generated command shim.

The fixed Logger `provision` recipe now admits its canonical installed payload
or exact selected legacy snapshot through the same Driver queue, frozen-input
and retained lifecycle owner. Keep that **single existing action** for command
construction. Do not borrow SSH's `stamp-provision` action, broaden Logger's
`stamp`/`install` source admission or replace provision with registration-bearing
`install`. The already-qualified bootstrap remains independent.

## Implementation plan

1. Route the actual generated payload command family through the packaged
   Python-free first-use client and existing Logger `provision` recipe at the
   default legacy root. Preserve context/governance resolution, fixed command
   targets, original project/source/root, six-command lifecycle declaration,
   native staging and complete dependency closure. Ready-only healthy dispatch
   must remain before cold publication, status/trace or outer-lock writes.
2. Change Logger's own primary emitters to delegate cold invocation through the
   exact selected snapshot's qualified dispatcher. Retain the existing
   auxiliary wrappers' attributable selected-source delegation and qualify all
   five, rather than creating independent executors per command. Remove the
   arbitrary installed-payload fallback; missing, unselected or unsupported
   retained source must fail explicitly, never switch to today's payload.
3. Check the selected dispatcher's and packaged client's actual contract
   support before issuing a new invocation. Preserve strict argument parsing
   and explicit missing/older-support refusal; no foreground installer,
   recursive submission or package-version-only compatibility inference.
   Keep healthy runtime argument, environment, stderr and exit forwarding
   intact, including healthy installation-cell dispatch.
4. Replace cold outer provisioning locks/status writers and direct installers
   with waiting observation of the same caller-independent authority and
   ordinary durable request/result. Preserve exact accepted source and
   publication controls at admission and execution, concurrent request
   coalescing, failed-candidate selection preservation and unavailable-source
   outcomes. Caller loss must not cancel installation or strand an
   observer-owned second action; arbitrary command arguments remain outside
   installer execution.
5. Refuse managed publication, no-self-provision, pinned reads, nested custody,
   missing/skewed support and unsupported cold custom-root/cell requests before
   acquisition, queue or plugin-state writes. Keep already-healthy reads
   non-publishing; do not make unsupported cold cell construction a condition
   of healthy dispatch.
6. Update Logger/Driver owner documentation and an explicitly selected native
   Windows/Ubuntu command portfolio, retaining the existing recipe/bootstrap/
   snapshot regressions. Do not add the exhaustive subprocess matrix to
   required PR CI or introduce another scheduler.

## Validation plan

- Materialize the actual native installers, stamp and emitted primary/auxiliary
  wrappers in isolated homes. Exercise both primary native forms (including
  Windows `.cmd` delegation) and all six payload command targets. Use
  help/version/status or synthetic inputs only; `session-sync --help` must
  not process a real session, archive, prune or start periodic work.
- Prove a Python/uv/Manager-free payload command can construct through the
  existing single provision request, then invoke its safe installed consumer.
  Separately start from an actual stamp, make the installed payload
  unavailable, and invoke the emitted primary and five auxiliary commands
  against the exact selected snapshot. Verify installed module/distribution,
  command/lifecycle ownership, source independence and same-source reuse.
- Destroy a real primary or payload observer's Job/process group after
  positive supervisor launch acknowledgement, then verify the ordinary
  durable receipt/result, completed runtime and safe re-entry. Overlap cold
  primary/payload/auxiliary callers and verify the shared authority and
  request coalescing rather than another outer execution lock. Join all owned
  workers before fixture cleanup.
- Preserve the verified healthy selected generation and completion bytes when
  construction fails. Accepted-source loss, mutation or reselection must
  settle the same accepted request with a nonzero durable outcome, not
  substitute a newer source, erase history or dispatch a fallback generation
  as if construction succeeded.
- Exercise no-write refusals for managed publication, no-self/pinned/nested
  controls, missing/older dispatcher and client contracts, missing/malformed/
  unselected/substituted snapshots, unavailable source and unsupported cold
  cells/custom roots. Observe acquisition, queue, runtime, marker, trace/status
  and command publication state, not only the returned message.
- Prove healthy legacy and installation-cell dispatch preserves original
  arguments, environment, stderr and module exit status without installer
  work. Check cold/healthy native timings and blocking work separately; packet
  duration or a watchdog alone is not a command-latency measurement.
- Intercept native task-stop/registration/start effects using the existing
  isolated adapters. Preserve service-less provision's bounded stop behavior;
  require no task/timer registration, periodic worker start or personal
  sync/prune. Retain the qualified bootstrap's separate drift `install`
  registration contract without exercising host registrations.
- Run the focused existing Logger recipe/bootstrap/selected-snapshot,
  generator/publication and other-adopter regressions relevant to changed
  owners. Qualify the actual command packet on native Windows and Linux before
  claiming adoption; macOS and broader native acceptance remain separately
  tracked, not implied by a POSIX source edit.

## Custody and completion

Review and actually merge this proposal before implementation. Complete the
bounded slice only after native qualification, review and actual merge; then
update the ROOT and dated Journal. Preserve all #2822/#4010/#4015/#4058
acceptance and incumbent core/schema, normal/repair, source/adopter/native/
deployment/maintenance, #7030 publication and #8370 diagnostic custody.
CodeSpaces #6909, contributor command features and watchdog work remain
separate; this proposal neither incorporates nor supersedes them.

Explicit installers as execution entry points, compound actions, cold custom
roots/cells, live task/timer workers, macOS, installed adoption and rollout
remain separate. Editing an installer-owned wrapper emitter does not adopt
the explicit installer entry point. No host registration, session processing,
deployment, enrollment, repair, conversation scrub or finalization is authorized.

This closes existing Installer/Plugin Service Model execution-owner intent
through single-owner, thin-adapter, attributable-command and cross-platform
patterns; it changes no standing vision. Documentation impact is this proposal
and its ROOT/Journal links. Runtime owner documentation changes with the later
implementation, not by describing this proposal as already delivered.
