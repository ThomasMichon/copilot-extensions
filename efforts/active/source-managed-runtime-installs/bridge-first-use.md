# Bridge fixed legacy installation recipes

Parent: [Source-Managed Runtime Installs](README.md), Phase 4b.
Coordination: #7758; same Installation execution implementer.
This is **agent-recommended integration**, not another operator requirement
or a transfer of Bridge's service/lifetime/publication custody.

## Boundary and existing semantics

Bridge declares one required-context, direct `agent-bridge` command targeting
`agent_bridge`. The existing Driver fixed-recipe roster does not admit it.
Both native installers already expose `stamp` and `provision`.

Stamp publishes deferred source/command state without constructing a runtime.
Windows cold provision invokes the full install, including its normal task
reconciliation and service start. With an existing runtime, Windows provision
instead reconciles task state, including its existing explicit repair behavior.
POSIX provision follows the existing install path, whose immutable healthy
reuse and service effects remain owned there. Do not replace these semantics
with Logger's service-less provision, invent a service-disable switch, or
make the two native installers perform new effects merely to look identical.

This prerequisite admits only Bridge's canonical installed payload and default
legacy root to fixed actions through the qualified standalone foundation and
existing queue. It does not route an ordinary publisher yet.

## Implementation plan

1. Extend the existing fixed recipe's Python and Python-free native admission
   for Bridge's literal `stamp` and `provision` actions. Require its actual
   command/module, required context, direct mode, own controls and complete
   materialized installation/lifecycle dependencies. Preserve the originating
   project, exact canonical source and default legacy root. Refuse unsupported
   actions, arbitrary installer argv, ordinary copies and selected snapshots.
2. Carry the admitted source through the existing canonical freezer and queued
   execution owner. Retain native staging, immutable construction, candidate
   health/completion/activation, command/lifecycle ownership and package
   dependency policy. Do not rewrite or substitute another source when the
   accepted source disappears or changes, including during/after freezing.
3. Execute the actual owning native action under the existing transaction and
   publication custody. Preserve Bridge's literal defaults, prepared-runtime
   behavior and registration/start/repair outcomes. A nested invocation must
   not recursively enqueue behind its own authority; preserve precise refusal.
   Keep internal generation and service lifecycle owners, not another updater.
   Unavailable interactive elevation or credentials must produce an explicit
   readiness/failure outcome, never an unattended prompt or successful repair.
4. Use the existing foundation bootstrap, upgrade, queue, coalescing, durable
   request/result, interruption and observed-wait mechanisms. Python-free
   callers may lack Python, UV, Manager and Bridge. Caller loss must not turn
   the waiting process into an installer or cancel the owned accepted work.
5. Add bounded recipe tests and an explicitly selected manual native portfolio.
   Keep required PR CI fast. Update Driver owner documentation and the dated
   Journal with actual recipe coverage and unadopted boundaries.

## Validation plan

- Materialize Bridge's actual complete payload with the canonical owner, not a
  hand-authored installer stand-in. Exercise first deferred stamp, cold
  provision, healthy repeated provision and safe installed `--help`/version or
  other read-only consumers. Verify selected interpreter/module/distribution,
  immutable generation, unchanged completion on reuse and canonical lifecycle
  owner, not only a source manifest or successful exit.
- Keep real installation/construction and installed-consumer paths. Intercept
  only native registration/start/repair effects through isolated adapters and
  explicit synthetic state. Prove the owning action invokes its correct
  effects and preserves fatal/tolerated outcomes. Record what was intercepted;
  do not credit a fake service response as actual serving or native deployment.
  Never register a host task/unit, start a production bridge, process a session,
  use credentials or connect to a remote agent.
- Exercise Python-free native Windows and Linux cold callers, positive launch
  acknowledgement, actual caller Job/group loss, concurrent equivalent
  requests and subsequent safe installed use. Require the ordinary durable
  receipt/result and join every owned worker before cleanup.
- Exercise source mutation/loss, wrong/default root, missing or skewed support,
  malformed declaration, managed deployment, no-self/pinned controls, nested
  custody and unsupported cold cell/custom-root inputs before acquisition,
  queue or plugin publication writes. Keep accepted-request failures attributable
  without substituting another payload or erasing pending/failed history.
- Preserve an actual healthy selected generation and completion/redirect bytes
  when candidate construction or activation fails. Source provenance,
  publication permission, construction, installed CLI and service readiness
  remain distinct evidence tiers.
- Re-run the focused existing fixed-recipe/Foundation and Bridge installer/
  publication regressions touched by the implementation. Require actual native
  Windows and Linux qualification before claiming this bounded prerequisite;
  retain macOS and broader installed-service acceptance separately.

## Custody and completion

Review and actually merge this proposal before implementation. A qualified
recipe is not command/bootstrap adoption: those publishers still need their
own reviewed boundary and native proof. Preserve #7030 publication, #8370
diagnostics, #7833/#8720 lifetime, #4477/#5750/#4022/#5225 Bridge acceptance,
CodeSpaces #6909, and every incumbent core/schema, normal/repair, source/
adopter/native/deployment/maintenance claim. Do not supersede another
contributor's open implementation or wait for its unrelated work.

Explicit installers as ordinary callers, selected snapshots, cells/custom
roots, compound actions, live service deployment, maintenance enrollment,
rollout and finalization remain outside this prerequisite. The ROOT and
#7028/#7758 stay Active/open after the bounded recipe eventually merges.

This closes already-reviewed Installer/Plugin Service Model execution-owner
intent through the single-owner, thin-adapter, attributable-command and
cross-platform patterns; it changes no standing vision. Documentation impact
is this plan, its direct ROOT row and the existing dated Journal. Implementation
documentation changes with the later implementation, not by presenting this
proposal as a delivered runtime capability.
