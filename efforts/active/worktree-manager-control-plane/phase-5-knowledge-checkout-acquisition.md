# Phase 5 - Remote knowledge checkout acquisition

- **Status:** Acquisition source merged in #11976; unattended desktop qualification
  design below precedes implementation. Installed and receiving acceptance remain open.
- **Parent:** [Configuration workbench](phase-5-configuration-workbench.md).
- **Coordination:** #1419 Track 3; bounded consumer slice `shiny-actuator`.
  The shared #356/#8545 acquisition owner retains its implementation boundary.
- **Vision:** closes part of
  [knowledge selection](../../../visions/installer/configurator/README.md#knowledge-selection-and-portable-defaults)
  and existing first-repository adoption, without a second clone or login engine.

## Outcome and existing boundary

Let an unbound harness user acquire an existing remote knowledge repository
from Repositories Configuration, then use the existing separately confirmed
[local registration](phase-5-local-knowledge-registration.md) and binding
actions. The acquisition result is a qualified local checkout, not a registry
entry, a usable knowledge pair or permission to write its anchor.

The shared native `repository_bootstrap.acquire` currently acquires and
reference-registers a source; `repos setup` then inspects and may execute its
explicit repository setup. Calling that command unchanged would create a
registration that the new-only local knowledge-registration action correctly
refuses to overwrite. Neither reclassifying that entry nor executing downloaded
setup code is an acceptable shortcut.

Extend that acquisition owner with an explicit acquisition-only mode which
omits registration and repository setup. Ordinary `repos setup`, its trust,
attended authentication, immutable-commit support and registration behavior
remain unchanged. Do not copy its Git, account, URL or source-qualification
implementation into a Configuration adapter.

This is not hosted repository creation, initial publication/protection,
standalone discovery, a register-URL replacement, or a transfer of those
campaigns. Existing #7611 forced-registration and #11926 remote-free identity
work remain independent.

## Native consumer

The optional `repos configuration knowledge-acquire-preview` and
`knowledge-acquire-apply` operations, with an explicit context project,
credential-free HTTPS repository URL and absolute destination path, use the
shared acquisition-only mode. Registration and binding remain separate actions.

The selected native catalog supplies a dedicated optional row and action marker
only for an eligible, canonically registered worktree-class stateless harness
which needs knowledge and is unbound. Preserve valid local/inherited choices;
malformed or unreadable binding/configuration is a refusal, not unbound.
Reuse that eligibility owner and the existing version-1 Configuration
preview/token, confirmation, result and originating-scope refresh contracts.

### Preview

Preview is a bounded read: no network request, credential inspection, Git
configuration write, target/lock directory creation, registration or setup.
It must:

1. Validate the existing owner's HTTPS grammar, canonical remote identity and
   explicit absolute destination. Reject embedded credentials, URL parameters,
   unsupported transports and redirects through configured Git URL rewrites.
2. Require an absent destination and an existing plain parent outside another
   checkout. Reject existing files, empty directories, links/reparse paths,
   task worktrees and registered name/path/provider-identity collisions.
   Existing sources use their current inspection/registration owners.
3. Resolve the intended nonsecret account selection through the existing
   native repository/owner/default mapping. Account metadata is neither a
   credential observation nor a requirement to sign in.
4. Bind the proposal to the selected installation/machine/project, normalized
   URL/path, parent identity, current registry, relevant harness/binding
   inputs and account selection. Revalidate under the existing owner's
   serialization before acquisition; a stale proposal requires fresh consent.
5. Show the exact source, destination and effects: download a new checkout
   only; no registration, binding, credential pin, repository setup, pair,
   worktree/session allocation or launch. Default confirmation remains Cancel.

The intended destination is reserved only at confirmed apply through the
acquisition owner. Preview creates no reservation. Concurrent applies must not
both acquire into, replace or clean the same target; fail explicitly if another
operation wins the destination.

### Apply and preservation

Require a valid fresh preview token before effects, including when an older
Manager substitutes an empty token. Recheck all relevant inputs before starting.
Use the shared acquisition implementation, existing configured credential
owner and native source qualification. Do not call ordinary registration or
its credential-pinning follow-up. A successful result leaves the source
unregistered, with unchanged native registry/configuration and provider policy.

The Configuration process is noninteractive. Disable terminal/device/browser
authentication through the existing acquisition environment, retaining only
the configured noninteractive credential route. An explicit selected account
must retain the shared owner's qualified filtering; it cannot degrade to
ambient credentials. Missing/denied/unsupported credentials refuse visibly
without sign-in, token copying, global account switching or an alternate remote.
Do not make personal authentication compulsory for an accessible public source.
The existing attended setup/authentication flow retains its own behavior.

Do not execute the acquired repository's hooks, setup declaration, scripts or
other code. Check out only through the owner's existing hooks-disabled Git
path, then qualify actual origin and revision. Treat changed registry/account,
harness/binding or source inputs during acquisition as incomplete/refused
completion, retaining the actual checkout and ownership for inspection.

The initial Configuration lane uses its existing finite one-shot deadline.
Preview and apply each have one aggregate native budget, including nested
Git/credential work and a cleanup reserve which fits inside that host deadline.
Use caller-owned, headless process-tree cleanup; do not extend clocks after
timeouts or leave a credential/Git descendant running. Large/slow acquisitions
may exceed this bounded lane: report that limitation, not setup success.
No new daemon, operation queue, timer, global process setting or lifecycle owner
is introduced. Keep the existing source/bootstrap defaults unchanged.

A nonzero Git result, timeout, cancellation, lost receipt or failed final
qualification never permits deleting a partial target, resetting a checkout,
replaying into it or reporting rollback. Report whether a target exists or its
state is unknown, the unfinished stage and the supported read-only inspection
next step. A subsequent existing target refuses rather than cloning over it.
Preserve unconfirmed cleanup as a failure with its exact owner.

After success, show the canonical checkout and acquired commit, refresh
Repositories, and direct the user to the existing local-registration action
with explicit branch/account metadata, followed by separate binding confirmation.
Do not generate a binding token, infer a remote default branch or claim New-ready.

## Compatibility, delivery and validation

The executing catalog's dedicated row/marker establishes optional support, not
payload version, action spelling or a guessed installed path. Older providers
omit it; newer manifests must not emit the new syntax without it. Older
manifests retain their current registration/binding behavior. Missing or
incompatible acquisition support blocks only this optional action.

- [x] Review this owner contract before adding executable caller syntax.
- [x] Extend the shared acquisition owner without changing ordinary setup,
  attended credential handling, immutable-commit or registration behavior.
- [x] Implement native read-only preview, fresh confirmed apply and the
  conditional existing Manager form/result/refresh path.
- [x] Prove the actual production Manager flow through preview, default Cancel,
  confirm, native acquisition, receipt and refresh, then the existing
  separately confirmed registration/binding boundary. No setup code may run.
- [x] Exercise a disposable real HTTPS Git consumer with a synthetic configured
  credential owner, no-credential/public behavior, explicit-account refusal,
  native timeout/descendant cleanup and preserved partial targets.
- [x] Cover existing/indirect/nested/registered destinations, malformed sources,
  URL rewrites, concurrent target creation and registry/account/binding drift.
  Compare complete fixture source/configuration trees and preserve every
  unrelated value; only the explicitly acquired new checkout may appear.
- [x] Cover older executing provider/manifest/Manager and supported rollback;
  verify ordinary setup and local registration/binding keep their promises.
- [ ] Run required source checks, review and CI. Record contained Windows/POSIX,
  native first-touch, installed UI, real provider and receiving evidence
  independently, with explicit skipped-tier reasons.

This is a source contract, not execution authority or a completed #1419
KO/KOV outcome. Hosted private creation/protection, actual first-restarted-model
and decline, provider-policy adoption/rejected-push recovery, general retrofit,
overlays, aligned state writes/cleanup and full receiving remain open under
their existing owners. No installation, authentication, deployment or live
repository operation follows from this source contract.

## Unattended native Windows qualification follow-up

This agent-recommended source plan addresses the follow-up recorded under
#1419 without changing acquisition, credential or first-task admission semantics.
It closes part of the existing knowledge-selection intent and follows
[the test portfolio](../../../TESTING.md#select-the-evidence-first) and
[version-skew-safe contracts](../../../docs/patterns/service-contract-evolution.md):
reuse the current test/execution owners, and review any new independently
deployed controller packet before a caller selects it.

The current
[`test_native_acquisition_from_windowless_parent_has_no_windows_or_focus_changes`](../../../plugins/agent-worktrees/tests/test_repos_knowledge_acquisition.py)
uses a real `pythonw.exe` parent, console Python/Git descendants, two loopback
HTTPS acquisitions, the existing Win32 observer and a kill-on-close Job.
It correctly skips without an observable foreground window. That is not an
unattended qualification lane: a person keeping their own desktop available
must not be a prerequisite for repeatable source validation.

### Execution boundary

Keep ordinary acquisition/credential/timeout controls runnable through the
existing contained runner without a desktop. Keep native visibility and focus
as an independently scored packet; a headless functional pass does not replace
it. Do not remove the foreground requirement, substitute process creation flags,
accept an empty observation, or report a skipped case as native acceptance.

First qualify an already-authorized disposable Windows execution venue through
its existing owner. The
[owner-only hosted Windows lane](../../../TESTING.md#owner-only-hosted-windows-escape-hatch)
is an existing submission boundary, not a promise of an interactive desktop.
Its protected controller, source selection, eligibility, budgets, containment
and artifact handling remain authoritative. Do not add a competing workflow,
runner service, scheduler or resource allocator.

Before the acquisition case can qualify, prove the actual Windows session,
window station and input desktop are usable, and that the observer can detect
an intentionally visible, foreground fixture owned by that packet. Retire that
positive control before measuring acquisition. The control must run only on
the dedicated test desktop, never on a shared operator desktop. Require valid
observation throughout both acquisition cycles; desktop/session loss is an
unqualified result, not a successful absence of windows.

A non-input private desktop, service session or successful Win32 API call is
not sufficient evidence. Microsoft documents
[interactive versus service window stations](https://learn.microsoft.com/en-us/windows/win32/winstation/window-station-and-desktop-creation);
its [pipeline UI guidance](https://learn.microsoft.com/en-us/azure/devops/pipelines/test/ui-testing-considerations)
also distinguishes visible desktop testing from headless browser testing.
Those are constraints, not proof about a particular GitHub-hosted run.
If the existing venue cannot pass the control, retain an explicit unsupported
venue outcome and prepare a separately approved dedicated interactive venue
through its owner. Do not unlock/switch the current session, enable autologon,
alter screen-lock policy or ask a person to hold their desktop open.

### Delivery and validation

- [ ] Review this amendment before changing the test or controller selection.
- [ ] Qualify the existing execution owner's desktop capability with a bounded
  positive control, under separately applicable execution authority. Record
  source revision, actual session/desktop suitability and the result; neither
  a runner label nor a source-plan approval supplies that authority.
- [ ] Adapt the acquisition packet to that qualified venue without changing
  the two real HTTPS acquisitions, windowless parent, real console descendants
  or current observation assertions. Reuse the current observer and containment;
  coordinate any shared-helper change with its owner rather than copy it.
- [ ] Prove the positive control detects both visibility and foreground
  ownership, then prove neither occurs for acquisition descendants. Missing
  controls, zero collected cases, skips, observer failure and mid-run desktop
  loss cannot qualify. Keep unrelated desktop activity distinct from a fixture
  regression, without turning a lost observation into success.
- [ ] Preserve the existing finite case/packet and cleanup budgets. On success,
  failure, cancellation or observer loss, settle the exact Job and descendants,
  HTTPS server and observer; retain unconfirmed cleanup custody as failure.
  Add no durable daemon or privileged cleanup fallback.
- [ ] Publish the reproducible source flow and its separately scored functional,
  native desktop, cleanup and installed/receiving outcomes through the existing
  result owner. Do not capture an operator desktop, real credentials or private
  repository content in public logs or artifacts.

This amendment changes no runtime, test, workflow or controller behavior.
No execution venue has yet been qualified by this follow-up. It grants no
installation, workflow dispatch, runner enrollment, resource creation,
authentication, session manipulation or rollout. Existing first-run, provider,
shared observer, controller and physical-resource owners retain their scope.
Required review/CI and the complete #1419/#8545 receiving gates remain independent.

## Journal

### 2026-11-10 - Acquisition-only source and native consumer

The owner contract cleared review in #11971 before executable syntax was added.
The source extension keeps ordinary setup's default registration and attended
credential behavior. Its explicit acquisition-only mode reserves an absent
destination, uses the same account/clone/source owners and leaves registration
and setup untouched. Configuration shares the local-registration owner's
unbound-harness eligibility and input revisions, rather than inventing another
binding rule. Its single caller-owned worker bounds Git and credential
descendants within the existing finite action deadline.

The acquisition packet passed 31 controls with one desktop-observation skip;
five additional focused negative controls passed after adding real indirect
parents, registered names, URL parameters, malformed binding and late source
changes. The latest real HTTPS configured-helper/public/denied packet passed
with observable setup entrypoints which did not execute. Existing setup's 54
controls and four default/pinned/unavailable/blob HTTPS cases passed across
bounded selections. Completed local-registration controls remained passing;
two native FIFO cases were skipped on Windows.

The actual production Manager packet passed five cases, including remote
HTTPS acquisition, default Cancel, receipt/refresh, separately confirmed
registration, separately confirmed binding and real New admission. The remote
flow took 66.32 seconds end to end; the five-case packet took 173.16 seconds
(299.39 including preparation and cleanup). This is a finite functional
sequence, not an installed-UI latency or tail-performance qualification.
The prior-provider process fixture and both shipped-manifest contract guards
also passed: a current Manager consumes a real legacy catalog process without
emitting unsupported acquisition syntax, while prior manifest actions remain
available. Empty-token native controls cover hosts without provider previews.

Two combined native selections exceeded their unchanged 300-second group
limit. Named completed independent cases were retained, and unfinished setup
and HTTPS cases ran in separate bounded invocations. No larger timeout or
monolithic pass was substituted. Windows window/focus observation was skipped
because no foreground desktop window was observable. POSIX, installed
interactive UI, live provider authentication and receiving-machine/relay
validation remain unqualified; no runtime installation or productive
registration/binding was performed.

### 2026-11-10 - Nonsecret preview and native failure ownership

The registry's nonempty account strings are not necessarily safe display
identities. Selection validates the resolved value through the existing
nonsecret-login owner before hashing or rendering a proposal; malformed or
secret-shaped values refuse without echoing their content.

Native cleanup failure is not a serializable PID observation. A process, Job,
retained stream/descriptor and retry operation belong to the original
`OwnedCleanupError`; reducing it to JSON discards the exact objects required
for safe settlement, especially when failure occurred before a process existed.
Worker, invocation and failure-reporting boundaries therefore propagate that
owner intact. In-process callers retain and settle it before another attempt.
No replacement cleanup owner, daemon or command replay is introduced.

A missing or malformed native completion frame can instead raise
`RuntimeError`. Preview returns a sanitized refusal; apply returns an
incomplete result with preserved-target guidance and unknown cleanup, not
a traceback or a claim that the interrupted mutation failed.
The focused native packet passed 12 controls, including process and Job-only
ownership through Configuration reporting, retained resources/retry/callback,
nested cleanup propagation, lost preview/apply completion, real secret-shaped
account refusal, HTTPS access and timeout descendant cleanup.
