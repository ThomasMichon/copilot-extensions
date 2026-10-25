# Bridge legacy bootstrap adoption

Parent: [Source-Managed Runtime Installs](README.md), Phase 4b.
Coordination: #7758; same Installation execution implementer.
This is **agent-recommended integration** of the qualified
[fixed Bridge actions](bridge-install-action.md), not another operator
requirement or a transfer of service/lifetime/publication custody.

## Boundary and existing behavior

Migrate only Bridge's legacy session bootstrap: absent deployment manifest
selects `stamp`; drift/unprepared runtime selects its existing `install`,
not Windows' prepared task-only `provision`. Preserve the required-context
direct declaration, complete dependency closure, native staging, immutable
construction/selection and Bridge's own service effects.

Windows' existing drift bootstrap supplies both a noninteractive PowerShell
host and Bridge's `-NonInteractive` installer choice, and sets
`AGENT_BRIDGE_NONINTERACTIVE=1`. These are different contracts: the latter
chooses the native S4U/AtStartup task, not the default interactive AtLogOn
task. The existing fixed context already carries that Bridge-owned choice.
Preserve it for this bootstrap rather than silently reverting the service
mode or changing ordinary installer defaults. POSIX retains its native
systemd/install semantics.

Bridge does not yet package the standalone foundation in its ordinary payload.
Use the existing canonical dependency/source materializer, including native
snapshot assets; recipe fixtures alone are not production packaging.

## Implementation plan

1. Capture the originating filesystem project before changing working
   directory. Preserve canonical installed payload/default legacy root,
   killswitch, publication/no-self/pinned controls and precise unsupported
   context/custom-root outcomes. Do not turn a healthy non-publication path
   into provisioning or suppress independent exact-session guidance.
2. Preserve Bridge's actual selected/completed readiness and source-aware
   checks, including source-qualified runtime identities. Do not substitute
   package-version equality, an existing interpreter/link, an unrelated
   healthy fallback or another plugin's weaker readiness test. Keep read-only
   checks free of trace/status/publication writes. Fresh callers may lack
   Python, UV, Manager and Bridge; retain native cold admission rather than
   silently skipping first stamp because Python is absent.
3. Submit only the qualified literal `stamp` or `install` through the packaged
   native foundation and existing ordinary queue/results/custody. Keep exact
   source/root/project, Bridge context and Windows drift logon choice. Use
   the existing capability mechanism to refuse missing/older support; do not
   send unsupported syntax, recursively submit behind existing custody,
   invent service options or fall back to foreground installation.
4. Remove the bootstrap's direct native installer launch, age/PID reaping,
   reconcile mutex/status writer and secondary supervision ownership.
   Ordinary queue progress and durable results become execution truth.
   Preserve explicit diagnostics for admission/recovery/failure; legacy
   reconcile files are historical data, not authorization to reap or a second
   execution fence. Do not delete them or unrelated pending/failed history.
5. Preserve the real 15-second hook deadline and exactly one `{}` stdout
   response. Validate the bounded native launch acknowledgement and its
   attributable diagnostics without confusing launch, durable acceptance,
   construction, native registration or actual serving success.
6. Update owning bootstrap/runtime documentation, obsolete reconcile-specific
   tests and the explicit manual native portfolio. Preserve independent
   guidance hooks and all unrelated runtime/service behavior. Keep exhaustive
   subprocess qualification outside required PR CI.

## Validation plan

- Materialize the actual complete Bridge payload and canonical foundation;
  qualify raw-checkout source mapping and self-contained materialization.
  Run actual native hooks with Python absent from caller PATH, checking
  first stamp, drift `install`, exact output and measured 15-second deadline.
- Exercise actual cold construction and safe installed CLI/module/
  distribution/selected-generation consumers, caller Job/group loss,
  concurrent sessions, ordinary receipt/result and source independence.
  Join every acknowledged supervisor/worker and settle kernel custody before
  fixture cleanup, including assertion or foreground-timeout paths.
- Qualify healthy selected no-op and malformed/mismatched/incomplete or
  source-qualified selection behavior through the actual Bridge readiness
  consumers. Preserve immutable completion and healthy-prior selection on
  failed construction; do not infer serving readiness from a completed slot.
- Intercept native registration/start/repair effects only. On Windows assert
  the actual bootstrap drift S4U principal, AtStartup trigger, stable launcher/
  config root and start call; distinguish it from ordinary default AtLogOn.
  On Linux inspect the actual unit/launcher and systemctl operations. Preserve
  fatal/tolerated diagnostics and explicit unattended repair refusal.
  Synthetic health is not real native service/serving evidence; prevent
  fallback from launching an actual bridge in the fixtures.
- Refuse managed publication, no-self/pinned/nested execution, missing/skewed
  driver, substituted/unsupported source and cold cell/custom-root publication
  before acquisition, queue or plugin mutation. Retain killswitch and
  non-publication behavior; a legacy PID/status record must never cause reaping.
- Exercise the independent exact-session guidance hook, own command catalog
  and attributable payload path against its separate deadline/output contract.
  Do not start a bridge, create a session or use a credential.
- Require complete exact-candidate native Windows/Linux qualification plus
  applicable fixed-recipe, publication, lifecycle and bootstrap regressions.
  Partition retained materialized fixtures within the existing finite runner
  resource bounds rather than weakening them or omitting cases.

## Custody and completion

Review and actually merge this proposal before implementation. Complete only
after native qualification, passing review and actual implementation merge,
then reconcile the ROOT Plan/Validation/Journal. Keep the ROOT and
#7028/#7758 Active/open.

Preserve all #2822/#4010/#4015/#4058 acceptance, core/schema/normal/repair
reservations, source/adopter/native/deployment/maintenance custodians,
#7030 publication, #8370 diagnostics, #7833/#8720 lifetime, Bridge
#4477/#5750/#4022/#5225 and CodeSpaces #6909. Do not supersede an open
contributor's implementation, wait on unrelated work or claim another
owner's newly delivered Manager/source integration.

Selected snapshots, primary/payload command and explicit-installer callers,
compound actions, cells/custom roots, macOS qualification, live bridge
sessions/service deployment, host repair, rollout, enrollment, conversation
scrubbing and worktree finalization remain outside this slice.

This closes existing Installer/Plugin Service Model execution-owner intent
through single-owner, thin-adapter, attributable-command and cross-platform
patterns; no standing vision changes. Documentation impact is this plan,
its direct ROOT row and the dated Journal. As-is runtime documentation
changes with implementation, not this proposal.
