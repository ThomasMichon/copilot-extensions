# Manager automatic-update controls

Parent: [Source-Managed Runtime Installs](README.md#phase-4a1---manager-automatic-update-controls).
Existing owners: #7028, #8645 and the [maintenance slice](maintenance.md).
Standing intent: [Installation & Updates](../../../visions/installer/configurator/README.md#installation-and-updates).

## Request

Operator direction, 2026-11-02, verbatim:

> Ah was this work the source of the odd Toast in the Worktree Manage that says that automatic maintenance is OFF, and seems to provide no way to turn it ON?
> Right now, we have a KillSwitch which enables and disables the auto-updates available after the manager starts, and before a worktree is created or resumed. We should put this under control of the Manager's Configuration for Install/Update.
> We need to ensure that the auto-updates are run using `<repo> update --auto`, so they are handled distinct from on-demand `<repo> update` calls; `--auto` calls can be blocked by the setting, whereas direct calls from the user or agent must not be blocked (other than re-entrancy guards)

**Settled summary:** provide an actionable automatic-update control in the
existing Manager workspace. Preserve explicit automatic origin through the
canonical updater and its durable execution. Automation policy is not a veto
on manual updates and does not require the user to force a normal update.

## Baseline and scope

The existing OFF toast comes from the Picker's post-binding
`maintenance.automatic` check. That Manager-local veto also suppresses
automatic housekeeping and monitor/repair activity; it is not just an update
flag. The Installation & Updates workspace offered component details
and explicit updates but no control for that veto.

The default installation also has the existing shared bootstrap KillSwitch,
with its own persistence, compatibility readers and installation scope.
The durable execution driver already distinguishes `--auto` from manual
requests and checks that hold before executing pending automatic work.
Namespaced installations retain their separate ownership boundary.

The public automatic flag is already understood by Manager and the engine.
The launch adapters currently submit automatic phase work through
`launch-update`; the requested follow-up puts automatic software updates
through the canonical repository update operation instead.

This is not recurring-controller enrollment, the complete pending maintenance
implementation, a new installation executor, a bootstrap-policy rewrite or
authority to change installed settings. Existing application/source, native,
setup and release owners retain their unrelated work.

## Plan

- [x] Review and merge this bounded intent before code changes (#10056).
- [x] Extend the existing Installation & Updates interaction so the operator
  can inspect and change supported local automatic-update controls, with
  effective ON/OFF state, target scope and any independent blocker visible.
- [x] Reuse existing policy persistence and admission rather than a shadow
  switch. Preserve the general Manager audit veto and default-installation
  KillSwitch semantics, defaults and cell isolation.
- [x] Route actual automatic software updates through `<repo> update --auto`
  using the existing Manager/project adapter and durable execution owner.
  Keep the automatic marker through forwarding, detachment and execution.
- [x] Re-read applicable policy at automatic effect admission, including work
  queued before a disable; do not terminate an already admitted update.
- [x] Preserve direct CLI, agent and selected-component update semantics
  without adding `--auto`, requiring force, bypassing single-writer custody
  or letting the automation setting block their normal path.
- [x] Make ordinary disabled state actionable in Configuration rather than a
  dead-end toast; preserve visible configuration and execution errors.
- [ ] Add focused tests and owner documentation, publish the source with
  required changefiles, and drive independent review/CI through merge.

**Agent-recommended realization:** show the independently existing launch-time
maintenance veto and applicable update hold honestly rather than combining
them into an ambiguous boolean. Keep the existing no-update option and
non-update housekeeping separate from canonical software-update submission.
Reuse supported request forms and providers; do not add a prerequisite policy
RPC, a sibling-plugin runtime dependency or an automatic retry through a
different effectful CLI form.

## Validation plan

| Boundary | Required assertion |
|----------|--------------------|
| Configuration | The production Installation & Updates navigation reaches the control, shows effective state/scope/blockers, and offers an ON/OFF action without depending on healthy engine inventory. |
| Persistence and consent | Cancel performs no write; confirmed changes persist across a fresh read without clobbering unrelated configuration or silently clearing independent holds. Errors and stale/conflicting state remain visible. |
| Automatic callers | Startup and pre-create/pre-resume software-update paths use canonical update with `--auto`; Bash and PowerShell retain the same meaning and do not add first-paint blocking work. |
| Admission | A disabled applicable setting prevents automatic installation effects, including an already queued automatic request; active work is not retrospectively killed. |
| Explicit requests | Plain user/agent update and selected-component actions reach the same normal owner with automation disabled; no force is required and re-entrancy/exclusive execution remains intact. |
| Scope and compatibility | Existing defaults and default-installation/cell boundaries survive. Current/prior caller-provider and rollback controls preserve strict parsing, explicit incompatibility and original automatic/manual identity. |
| Other maintenance | Audit-veto housekeeping semantics and explicit no-update behavior remain intact; enabling update permission does not enroll or start the optional recurring worker. |
| Publication | Source, docs and affected changefiles pass normal review and required CI. Source merge, release, installed activation and whole-effort acceptance remain distinct. |

Use temporary test-owned configuration and synthetic update effects.
No live setting change, background enrollment, desktop/window observation,
service operation or rollout is authorized by this source refinement.

## Implementation and qualification

The source implements two independently confirmed existing controls: the
default-installation bootstrap KillSwitch and Manager's broader launch-time
maintenance veto. The first invokes the canonical shell/PowerShell controllers
and shared helper shipped in Manager's own payload; it has no Machines runtime
dependency or shadow state. Independent cells cannot edit that global hold.
The second edits only the existing TOML preference with stale-view checks and
preserved unrelated values/comments. Neither action starts an update or worker.

Automatic software updates use the existing project/Manager `update --auto`
seam and one durable execution owner, with optional detached or blocking
observation. Explicit updates retain their original admission, engine-target
identity and re-entrancy behavior. Actual queued automatic requests re-read
applicable policy before becoming running and keep a non-success receipt
when inhibited; active work is not retrospectively terminated.

Local qualification includes real private-fixture controller changes and
payload copies, production virtual Configuration confirmation/cancellation and
refresh, queue/manual/scope controls, and the available Bash argument matrix.
Initial source qualification passed the 3,674-case actor-free packet with 465
bound sources and 214 production coverage targets. The early-bootstrap
addressed-root regression then passed in the committed 3,675-case packet.
After the managed publication rebase preserved upstream admission additions,
the complete 3,685-case packet passed with 519 bound sources, 218 production
coverage targets, coverage once and no observed effects in 53.20 seconds inside
the unchanged 300-second budget. Current-head provider CI also passed, including
the full seven-day dependency-age audit with 447 declarations and no violations.
All 21 currently admitted strict typing projects
pass Linux and Windows static views; pending projects are not thereby admitted.
Independent implementation review/required CI and normal source merge remain
explicit publication gates. No source result qualifies desktop behavior, an
installed update or the complete parent effort.

The first native source packet refused before archive creation or test
collection: its preflight compared a normalized Git blob with the intentional
CRLF PowerShell checkout. No native outcomes or skips were produced. The
operator authorized one renewed attempt using exact pinned source bytes,
without changing the original limits or host-effect exclusions. The durable
correction is the existing raw-object/complete-source reader, not changing
`.gitattributes` or normalizing the checkout; `TESTING.md` and the existing
fresh-Git source-reader regression record that distinction. The refused
attempt remains refused.

The renewed attempt also refused before test collection: native Git would not
trust the source repository through its cross-OS path. No ownership exception
or Git-setting change was applied. Source preparation therefore stays with
the Linux Git owner, producing the canonical complete-tree archive and
inventory for independent native verification. The file-only source input
reconstructs the exact pinned tree and preserves checkout bytes; source
preparation alone does not authorize another native attempt or qualify
unperformed tests.

A separately authorized provided-input attempt then passed the actual Windows
source-contract packet: 33 passed and 11 explicit POSIX-only Bash skips.
All 11 launch modes passed through both `pwsh` and `powershell`, and all 11
canonical controller/scope/payload cases passed. The 49 collected cases
selected 44 and explicitly deselected the observer and four long handoff cases.
The pinned implementation's selected product, controller and test bytes matched
the then-reviewed head. The complete 13,096-entry source archive was independently
verified before effects and preserved afterward. The 184.53-second group
included source preparation and owned cleanup inside the unchanged 300-second
group and 30-second item limits; runner, group and supervisor exited zero.
Private fixture state, application bytes, provided inputs and the two earlier
refusals were preserved. No desktop observation, production setting,
installation, service operation or rollout was performed.

The subsequent implementation review identified four real gaps before merge:
queued no-update reconciliation could miss the maintenance veto, borrowed
Manager execution could re-read a worker's unrelated switch override, and both
settings controls had read/modify/publish races. Source corrections now retain
request-selected scope and serialize confirmed mutations with all existing
writers. The switch's Bash/Python and Python-free PowerShell controllers share
native byte-range locking and declare conditional-mutation support explicitly;
Manager refuses a downlevel controller rather than clearing unconditionally.
Manager source, runtime-source and maintenance writers share a bounded
per-config lock across reading and atomic publication.

Bounded private-fixture evidence includes 257 coupled Manager/queue/control
passes with one platform skip, 129 generator/footprint/publication/capability
passes, and 48 final canonical-controller/Manager passes. These overlapping
suites include real configuration-writer subprocess interleavings, replacement
of an audit hold between UI validation and actual controller execution, and
Linux Bash/PowerShell/Python writer-lock interoperability. All 24 currently
admitted strict typing projects pass Linux and Windows static views; the 44
pending projects remain pending.

The corrected source at `3a9d63c82e4a9a77c738b61bf029ec695bcd07b3`
passed its exact 3,687-case actor-free packet with the same 519 bound sources,
218 production coverage targets and 300-second limit: 58.93 seconds, coverage
once and no observed effects. Its source, manifest, proof and coverage were
independently verified. A separate read-only review of the correction delta
reported no meaningful high-confidence correctness findings.

The earlier Windows result belongs to the earlier source, not these changed
controllers. Independent current-head provider review/CI, source merge and any
separately authorized Windows execution remain pending; no native attempt is
implied or authorized by this source correction.

The next review found that a leading `~` override could be expanded by a writer
but interpreted literally by its shell reader. Both canonical controllers now
select the same current-user or relative path for `on`, `check`, `status` and
`off`; capability discovery remains independent of state-path resolution.
The actual 16-case controller-process packet passed on Linux, including Bash
and PowerShell controls, and all 13 plugin copies plus standalone Manager were
regenerated. The MCP producer hash was refreshed without changing its footprint
contract revision.

After reconciliation with the newer development base, the real handoff fixture
was updated to register the actual public `update` parser and durable engine
request path rather than its obsolete launch-only parser. Synthetic installer
effects remain gated; coalescing, a distinct project and final queue drain are
still exercised through real process boundaries. Both selected Linux handoff
controls passed; the two native Windows counterparts were explicitly skipped.
The complete selected Manager/configuration/queue/controller/generator/footprint
cohort then passed 377 cases with 26 declared platform skips. All 27 currently
admitted strict projects pass both static platform views; 41 projects remain
pending. These results do not renew or consume an earlier exact-source native
authorization, qualify Windows execution, or close independent source review,
required CI, merge, rollout or the parent acceptance gate.

The next review found two further path-selection gaps: a hold override with
an absent intermediate directory and `..` could be written canonically but
read as absent, and a relative saved Manager root could be read from a
different worker directory. Readers now select the same normalized hold
parent as mutations while retaining the unchanged leaf and linked-leaf refusal.
Automatic admission resolves a relative Manager root against its retained
absolute request cwd and visibly refuses an unusable cwd; manual admission is
unchanged. The default Bash hook path adds no normalization subprocess.

The corrected canonical controllers, Manager controls, actor-free policy
functions and durable queue cohort passed 146 private-fixture Linux cases.
A separate public Machines CLI regression passed as well. These include
fresh reads before mutation, conditional clearing, linked-leaf preservation,
and queued policy changes with a worker directory distinct from the caller.
All 27 admitted strict projects pass both static platform views; 41 pending
projects remain pending. The affected canonical copies and unchanged-revision
MCP footprint digests were regenerated. Exact committed unit qualification,
independent review/CI and any newly authorized native execution remain separate
gates; no earlier exact-source native approval is renewed by these corrections.

Independent review confirmed both path-selection findings resolved, then found
that a borrowed engine request's second Manager policy check lost the retained
caller cwd. The borrowed environment now freezes its Manager root against that
directory before any policy/configuration read and restores the worker's
original selection afterward. Direct Manager checks retain their own invocation
directory. Both Manager and engine borrowed paths cover absolute, relative and
current-user-home roots without a worker-policy fallback.

The coupled Manager request/configuration/policy cohort passed 141 cases with
one explicit Windows-only skip in private Linux fixtures. Earlier controller
and queue results remain distinct evidence; native Windows execution,
independent current-head review/CI, source merge and parent acceptance are not
implied by this correction.

The next review found that public submission froze a home-relative Manager
root before expanding `~`. Unlike the borrowed-execution fixture, a real caller
could therefore select a literal `~/manager` child under its cwd and miss the
disabled home policy. Submission context, source identity and self-install
selection now expand the current-user home before freezing the absolute root.
The regression starts at public update submission, checks actual automatic
policy admission for absolute/relative/home roots and both policy values, and
preserves manual exemption without invoking an installer.

The renewed native packet refused before any dependency preparation, collection
or test host invocation: exact selection deliberately removes ambient pytest
options, which also removed the wrapper's only per-case JUnit output. No native
pass or skip is credited. The source runner now offers explicit retained
per-group JUnit output independent of selection, with a new destination required
for every attempt. It preserves the same containment, admission and deadlines;
the refused native packet is not replayed or treated as executed.
