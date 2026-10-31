# Dispatch fixed legacy installation recipes

Parent: [Source-Managed Runtime Installs](README.md), Phase 4b.
Coordination: #7758; same Installation execution implementer.
This is **agent-recommended integration**, not another operator requirement
or a transfer of Dispatch's publication, coordinator or task ownership.

## Boundary and existing semantics

Dispatch declares one required-context direct `agent-dispatch` command targeting
`agent_dispatch`, with a Windows CMD catalog shim and native installer lifecycle.
The existing fixed first-use roster does not admit Dispatch. Both native
installers already expose deferred `stamp` and full `provision` actions;
`provision` invokes the actual install path on both platforms.

That install may reconcile coordinator and supervisor services and their
existing configuration. Preserve the native owner and its actual options;
do not silently turn ordinary provisioning into a client-only recipe or
borrow Bridge's or Logger's platform semantics. Tests may use the existing
client-only options as separately labeled controls, not as proof of defaults.

This prerequisite admits only Dispatch's canonical installed payload and
default legacy root through the existing foundation and ordinary queue.
It does not adopt a command, bootstrap or explicit installer publisher.
Selected snapshots and the independent scoped-publication capability remain
separate; a default-root prerequisite must not depend on that capability.

## Implementation plan

1. Extend the existing fixed recipe's Python and Python-free native admission
   for Dispatch's literal `stamp` and `provision` actions. Require its actual
   declaration, own controls and complete materialized native/lifecycle
   dependencies. Preserve the exact canonical source, default root and
   originating filesystem project. Refuse arbitrary installer argv, unsupported
   actions, ordinary copies, selected snapshots and cold cells/custom roots.
2. Retain that source through the existing canonical freezer, foundation and
   ordinary durable request/result. Preserve queue identity, pending-request
   coalescing, immutable construction, candidate health and native publication
   custody. Source mutation or loss must fail explicitly rather than select
   another installed payload, ambient checkout or release.
3. Execute the actual owning native action under the same exclusive transaction
   and publication authority. Keep coordinator/supervisor registration, start,
   platform configuration and failure behavior owned by the installer. Reject
   nested submission behind existing custody; waiting observes the owner,
   never becomes another installer. Unavailable elevation, interactive input
   or native integration must yield explicit failure/readiness, not a prompt
   or success-shaped fallback.
4. Reuse native Python-free acquisition, construction, observed wait, durable
   progress and caller-independent lifetime. A caller may lack Python, UV,
   Manager and Dispatch. Losing the original caller must not cancel accepted
   work or require a replacement submission.
5. Add bounded recipe controls and an explicitly selected native Windows/Linux
   portfolio, retaining existing process, runner, storage and job bounds.
   Update the Driver's actual recipe documentation and the dated Journal.
   Keep required PR CI fast; no second queue, scheduler, arbitrary-argv recipe,
   foreground exemption or new service-lifetime mechanism.

## Validation plan

- Materialize the actual complete Dispatch payload through the canonical
  owner. Exercise deferred stamp, cold provision, healthy reuse and safe
  installed help/version consumers. Verify selected interpreter, module and
  distribution, immutable completion and unchanged reuse bytes, not just
  installer exit or source provenance.
- Keep actual native construction and installed-consumer paths. Intercept only
  registration/start/configuration effects with explicit isolated adapters,
  covering the correct ordinary coordinator/supervisor defaults and fatal or
  tolerated outcomes separately. Prevent host tasks/units, firewall or
  persistent-environment writes, live coordinator/supervisor startup, network
  connections and task processing. Synthetic effects do not prove serving,
  native deployment or an existing production database migration.
- Exercise Python-free Windows and Linux launch, original caller Job/group
  loss and separately concurrent equivalent callers. Pin the acknowledged
  owner and require the original sole ordinary receipt/result to complete
  before any follower submission. Read the queue only after its committed
  receipt; malformed state and premature owner exit remain explicit failures.
  Join every owned worker before cleanup.
- Verify source/root/context/declaration/helper failures, managed publication,
  no-self/pinned controls, nested custody and unsupported sources or scoped
  roots refuse before acquisition, queue or plugin writes. Preserve unrelated
  pending/failed history. A valid selected context is not publication consent.
- Preserve an actual healthy selected generation and completion/redirect bytes
  across failed construction or activation. Verify source-independent safe
  installed use after the accepted payload disappears without rewriting a
  completed slot or trusting another publisher.
- Run directly affected fixed-recipe, native admission, installer and publication
  regressions. Require exact-candidate native Windows/Linux, passing review and
  required CI before claiming this prerequisite. Keep macOS, live service
  interaction, task-data compatibility and production adoption uncredited.

## Custody and completion

Review and actually merge this proposal before implementation. Qualifying a
fixed recipe does not adopt ordinary publishers; their command, hook deadline,
transport, selected-source and publication boundaries need separate proof.
Reconcile only this delivered ROOT row and journal after an implementation merge.
ROOT and #7028/#7758 remain Active/open.

Preserve #7030 publication and its unsupported scoped Dispatch boundary,
#8370 diagnostics, CodeSpaces #6909, Bridge lifetime custody, every retained
#2822/#4010/#4015/#4058 acceptance clause and all core/schema, normal/repair,
source/adopter/native/deployment/maintenance reservations. Do not supersede
or wait on another contributor's independent implementation.

Explicit installers as ordinary callers, snapshots, compound actions, cold
cells/custom roots, live coordinator/task operation, host repair, enrollment,
rollout, conversation scrubbing and finalization remain outside this slice.

This closes existing Installer/Plugin Service Model execution-owner intent
through the existing thin-adapter, attributable-command, single-owner and
cross-platform patterns. No standing vision change. Documentation impact is
this plan, its direct ROOT row and the dated Journal; implementation docs
must describe delivered behavior only after implementation.
