# MCP legacy bootstrap adoption

Parent: [Source-Managed Runtime Installs](README.md), Phase 4b.
Coordination: #10499 and #7758; bounded adoption driven under #10377.
This is **agent-recommended integration** of the released
[MCP fixed recipes](mcp-first-use.md), not a transfer of construction,
application, publication, lifetime or recovery ownership.

## Boundary and existing behavior

Migrate only MCP's legacy session-start bootstrap: an absent deployment
manifest selects `stamp`; a drifted or unprepared runtime selects `provision`.
Both actions retain MCP's actual `init` declaration and existing options.
Do not substitute Bridge's distinct `install`/service-logon contract.
The recipe prerequisite shipped through #10574/#10626; it did not migrate
this ordinary publisher or prove production caller-Job loss.

Keep the canonical installed payload, default legacy root, originating
filesystem project and existing native source/materialization owner.
MCP's source-aware selected/completed runtime check remains the readiness
authority. Package-version equality, a present interpreter/link or an
unrelated healthy fallback cannot stand in for the selected generation.

The Windows first-use launcher currently requires setup-provided Python
3.10+ as `python` on PATH. An absent MCP runtime, UV or Manager is not a
Python-free machine. Preserve that declared prerequisite and its pre-launch
failure instead of adding a downloader or alternate launcher. Linux's
qualified native cold path remains distinct from macOS support.

## Implementation plan

1. Retain the killswitch, source/publication admission, own no-self/pinned
   controls, installation-cell boundary and independent guidance hook.
   Capture the filesystem project before changing directory. Healthy
   read-only use must not create traces, status, queue or publication state.
   Preserve actual malformed/unsupported-source diagnostics rather than
   treating parse failure as healthy or falling back to another payload.
2. Package and invoke the existing standalone foundation through the canonical
   dependency materializer. Submit only the qualified literal `stamp` or
   `provision` to its original ordinary queue, frozen-input executor and
   durable result. Keep MCP's native participant handshake under that same
   transaction; do not create a second executor or recursively submit behind
   existing installation custody.
3. Discover the packaged receiver's actual supported recipe contract before
   emitting its syntax. Missing or skewed support must be explicit before
   acquisition/publication effects, without a direct-installer fallback,
   guessed package-version capability or effectful probe-and-replay.
   Preserve the supported old-caller/new-provider and rollback behavior.
4. Remove this hook's direct foreground stamp, conhost/nohup drift installer,
   age/PID reaping and reconcile-file execution custody. Existing legacy
   records remain historical data: do not delete, overwrite, use their age as
   authority or terminate their processes. Existing kernel admission,
   request coalescing and durable outcomes own new execution.
5. Retain the actual 15-second hook deadline and exactly one `{}` stdout
   response. Validate the existing bounded native launch acknowledgement and
   attributable diagnostic location. Keep launch, durable acceptance,
   completed construction and usable installed MCP as separate outcomes.
   An unavailable prerequisite or failed launch is not a successful stamp.
6. Update MCP's owning runtime/bootstrap documentation and affected tests.
   Add focused contract checks to existing bounded coverage; keep actual
   materialized construction and lifetime qualification in an explicitly
   selected native Windows/Linux portfolio, not exhaustive required PR CI.

## Validation plan

- Materialize the actual complete MCP payload and packaged canonical
  foundation. Invoke the real hook entry for first stamp and drift provision,
  measure the unchanged deadline/output, and verify exact action/source/root/
  project in the original durable queue/result. Exercise cold MCP/UV/Manager
  with each platform's declared setup prerequisites; separately require
  Windows missing/unsupported Python to fail before launch.
- Use the existing native lifetime owner to exercise a real submitting
  Windows Job's destruction and Linux caller-group loss after launch while
  preserving every outer runner cleanup boundary. Accepted work must finish
  without another submission. Include concurrent equivalent callers, one
  ordinary request, source-independent execution and confirmed final
  supervisor/worker/descendant settlement. Contained caller-process exit,
  breakaway flags or early health alone do not prove this boundary.
- Observe headlessness from a windowless Windows parent across the existing
  two-cycle native observation contract. Retain bounded lease/custody and
  exact cleanup on assertion, timeout and launch-unknown paths. An unavailable
  lane is an unresolved acceptance item, not permission to weaken containment,
  expand host effects or replay an unknown launch.
- Qualify actual native construction, installed command/module/distribution,
  selected immutable completion, healthy no-op/repeat and incoming-health
  failure preservation. Load unchanged synthetic configuration from MCP's
  actual `bridges` directory after source removal and failure. Do not start
  an MCP server, fetch credentials or contact a backend.
- Exercise source-qualified selection, wrong/missing/incomplete selectors,
  malformed metadata and a healthy but different fallback through MCP's actual
  readiness consumers. Preserve read-only behavior and complete healthy slot
  bytes; do not copy another plugin's weaker readiness shortcut.
- Require no-write refusal for managed publication, no-self/pinned/nested
  execution, wrong/unsupported source, missing/skewed driver and unsupported
  cold cells/custom roots. Preserve killswitch no-op and independent session
  guidance/catalog output. Old PID/status files must never trigger reaping.
- Retain the fixed-recipe, publication, native-participant and source-readiness
  regressions. Require exact-candidate Windows/Linux hook and lifetime results
  before crediting this adoption. Keep process, deadline, memory and storage
  bounds intact and distinguish any synthetic adapters from real consumers.

## Custody and completion

Review and actually merge this proposal before implementation. This closes
existing single-execution-owner intent through thin native adapters; no vision
revision or new global supervision mechanism is proposed.
[Authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
and the [install contract](../../../docs/install-contract.md#authoritative-replacement-and-completion)
remain the completion target. This automatic publisher's conservative
admission does not redefine manual update or make old fences a new manual
permission requirement; that remaining recovery work retains its existing owner.

Keep #9026's Dispatch work, #9448/#9455's first-foundation work, #7758's
ordinary admission/recovery/coalescing, #4195's whole hook family and #7030's
publication/application boundaries intact. U13/#10652 fresh settlement,
#10653 forced cutover, #9716 census and #10486 full reattachment keep their
existing application/lifecycle owners; this bootstrap adapter does not replace
those acceptance items. Commands, explicit installers,
other plugin migrations, selected snapshots, cold cells/custom roots, macOS,
live fenced-queue recovery, shared installations, services, credentials,
backend connections and rollout remain separate.

After implementation review, native qualification and merge, reconcile the
ROOT Plan/Validation/Journal and #10377's acceptance matrix. Keep the parent
efforts Active and #10499/#7758/#7028 open until their own remaining gates
are resolved. A reviewed plan, recipe release or hook launch is not completion.
