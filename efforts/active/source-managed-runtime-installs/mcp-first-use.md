# MCP fixed legacy installation recipes

Parent: [Source-Managed Runtime Installs](README.md), Phase 4b.
Coordination: #7758 and #10499; bounded prerequisite driven under #10377.
This is **agent-recommended integration**, not a transfer of MCP construction,
application, publication or lifecycle ownership.

## Boundary and existing semantics

MCP declares one required-context direct `agent-mcp` command targeting
`agent_mcp`, with the native runtime gate, installer lifecycle and Windows CMD
catalog shim. Its installer is `init`, not the fixed roster's current `install`
front. Both native initializers expose `stamp` and `provision`; full
`install`/`init`/`provision` share the existing construction path.

Stamp publishes deferred command/source state without constructing a runtime.
Provision retains the initializer's normal immutable construction, instance
reconciliation, command publication and verification. MCP does not need another
plugin's client-only service flag. Preserve the actual defaults and
platform-specific persistent-environment behavior through the owning installer.

This prerequisite admits only the canonical installed MCP payload and default
legacy root through the existing foundation and ordinary queue. It does not
adopt any ordinary command, bootstrap or explicit-installer publisher.
In particular, qualifying the recipe does not by itself fix #10499's legacy
Windows bootstrap launch or complete its other four plugin migrations.

## Implementation plan

1. Extend the existing Python and Python-free native fixed-recipe admission for
   MCP's literal `stamp` and `provision` actions. Require its actual `init`
   declaration, command/module, required context, direct mode, own controls and
   complete materialized native/lifecycle dependencies. Preserve the exact
   canonical source, default legacy root and originating filesystem project.
   Refuse arbitrary installer argv, unsupported actions, ordinary copies,
   selected snapshots and unsupported cold cells/custom roots.
2. Carry the admitted source through the canonical freezer, retained foundation
   and existing durable queue/result. Execute the actual native initializer
   under the same exclusive transaction and publication authority, not a second
   execution owner. Preserve selected source/build identity, native staging,
   immutable candidate health/completion/activation and command ownership.
   Source mutation/loss must not silently select another payload or checkout.
3. Retain the initializer's real options and failure behavior. Do not expose
   force, uninstall or broker removal through this finite recipe, bypass nested
   custody, or change constructor/retirement behavior to make tests pass.
   Unavailable permission, acquisition, build or incoming readiness remains an
   honest failure; do not prompt unattended or fabricate success.
4. Reuse the qualified Python-free acquisition, native caller-independent
   supervision, ordinary coalescing, observed wait and durable progress/result.
   The original caller may lack Python, UV, Manager and MCP. Its loss must not
   cancel accepted work or require another submission. Do not add a conhost,
   WMI, scheduled-task or service-based alternate launcher.
5. Add bounded recipe controls and an explicitly selected native Windows/Linux
   portfolio, retaining existing process, runner, storage and Job bounds.
   Keep required PR CI change-scoped; update the Driver's actual recipe
   documentation and the dated Journal with delivered, not proposed, coverage.

## Validation plan

- Materialize the actual complete MCP payload through its canonical owner.
  Exercise deferred stamp, cold provision, healthy reuse and safe installed
  help/version consumers. Verify the selected interpreter, module/distribution,
  immutable completion and unchanged reuse bytes, not just installer exit.
- Keep actual native acquisition/construction and installed-consumer paths.
  Intercept only persistent-environment and other externally owned integration
  effects through explicit isolated adapters, recording what was synthetic.
  Preserve configuration and existing command/source ownership. Do not start an
  MCP server, fetch credentials, connect to a backend, register a host service
  or use a shared runtime as a fixture.
- Exercise Python-free native Windows/Linux cold callers, launch acknowledgement,
  original caller Job/group loss before a follower, concurrent equivalent
  requests and later safe installed use. Require the ordinary durable
  receipt/result and confirmed worker cleanup. Verify headless launch through
  the existing native launch portfolio rather than infer it from flags.
- Exercise malformed declaration, missing/skewed helpers, source mutation/loss,
  wrong root, managed deployment, no-self/pinned controls, nested custody and
  unsupported recipe inputs before new acquisition/queue/publication writes.
  Preserve accepted/failed/uncertain history and selected-generation bytes on
  candidate failure; do not substitute another source or replay unknown work.
- Re-run focused existing foundation/fixed-recipe and MCP installer/publication
  controls affected by the implementation. Require actual materialized native
  Windows and Linux qualification before crediting this prerequisite. Keep
  macOS, ordinary publisher adoption and affected-host recovery separate.

## Custody and completion

Review and actually merge this proposal before implementation. This closes
already-reviewed single-execution-owner intent through a thin MCP adapter; it
does not extend the standing vision. The
[authoritative replacement contract](../../../docs/install-contract.md#authoritative-replacement-and-completion)
governs explicit/manual update and preservation. This finite prerequisite does
not implement that whole-update convergence or replace its existing owner;
historical queue/foundation metadata must not become a new manual-update veto.
Automatic-only inhibition remains automatic-only.

Preserve #9026's Dispatch prerequisite, #9448's first-foundation work, #7758's
ordinary admission/recovery/coalescing follow-up, #4195's whole hook-spawn work,
#7030 publication and existing MCP construction/application/lifecycle custody.
No live queue recovery, host integration, rollout or source choice is authorized
by this plan. A later reviewed bootstrap slice must retain the actual hook
deadline/output, healthy no-op, source-aware readiness, automatic controls and
independent guidance before removing legacy reconcile custody.

The Python-free Budget hook and the other reported plugin migrations remain
separate. Recipe qualification alone leaves #10499, #10377, #7028 and #7758
open, with their remaining Plan and Validation gates intact.
