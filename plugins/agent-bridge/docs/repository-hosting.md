# Repository-owned agent hosting eligibility

Bridge consumes the independent
[repository policy library](../../../libs/repository-policy/README.md).
Repository `expose_agent: false` is a veto, not a discovery preference. An
explicit agent name, stale registration, caller environment, retained launch
plan or review-like agent name cannot override it. Repository policy is opt-out:
a direct check of a repository with no explicit veto remains eligible, without
requiring catalog registration. Missing policy and `true` grant no additional
adoption or launch authority.

Catalog discovery is a separate opt-in. A `repos.yaml` row must explicitly
declare `agent: true`; a source relation in `related.yaml` does not become an
agent merely because it names a checkout or machine. Related-agent synthesis
requires the existing explicit Bridge delegation declaration. `projects.yaml`
is the launchable Copilot/Manager harness inventory, not a source-repository
catalog. Worktree-class source-only relations remain source-only.
The local topology-derived roster re-reads policy at an available registered
checkout, including its local control-plane alias; it does not rewrite the
stored opt-in. Remote roster candidates are checked by their receiving owner,
not against a similarly named checkout on the caller.

The library owns canonical/legacy configuration precedence, current-checkout
selection, strict boolean parsing and marketplace composition. Bridge selects
the receiving installation's marketplace from the existing owner-validated
installation context, including its implicit canonical receipt when the context
variable is absent. Unvalidated JSON fields are not authority. A missing or
invalid namespaced context refuses instead of dropping marketplace scope;
only the legacy installation can have no selected marketplace. Request
environment overrides do not select policy. A selected overlay cannot erase a
distribution-neutral denial.
Malformed or unreadable policy fails visibly rather than falling back to an
allow.

## Independently upgraded callers and receivers

Managed producers pass `--require-agent-hosting` to Bridge `create`, `resume`
and potentially cold-resuming `send`. A legacy CLI refuses the unknown flag;
a current CLI requires the running daemon's `/health` response to advertise
`capabilities.repository_agent_hosting: 1` (a literal integer). A current CLI
paired with an older daemon therefore refuses too. This is an additive named
capability, not a claim that an installed payload has updated a running daemon
or a change to HTTP protocol 47.

The capability composes with existing protocol floors. It is checked afresh
before every request attempt, including safe retries after endpoint
re-resolution; underlying transparent retries cannot bypass the check.
Missing, malformed or lower capability versions fail before the request is
sent. As with protocol floors, the probe-to-request listener-replacement race
is not an atomic receiver negotiation. A capability is a compatibility fence,
not cached policy admission: the receiving daemon still checks the actual
execution checkout. `--cli` forwarding does not implement this daemon contract
and refuses that flag combination.

Worktrees `embody` enforces hosting policy internally; it has no hosting-policy
flag. Before invoking that ordinary launch command, Dispatch also checks the
selected receiver's existing read-only policy operation. A missing or invalid
owner response refuses before embodiment; caller-side policy alone is not
proof that the independently selected receiver supports enforcement.
Historical receipt-only reads do not require new launch admission.

## Admission boundaries

`hosting_policy.py` separates fresh admission from roster visibility. Checks run
before local preparation, keyed creation journals/allocation, session
registration, recovery state changes, resync teardown and handoff brief
generation. Resume admission also precedes startup-progress events and their
waiting ticker; observation begins only after admission. The final resolved
execution directory is checked again before
plugin preparation. Local launch plans are prepared before session registration;
consuming a retained plan re-reads policy at its concrete directory.
Resume, resync and handoff capture the existing stop generation and durable stop
token before asynchronous policy reads. A stop arriving during admission or
while waiting for lifecycle custody still wins; it is not absorbed as a new
manual-resume authorization. Repository denial acquires no recovery custody or
ledger mutation.
Plain local preflight preserves existing request provenance. Controller origin
is attached only when plugin preparation or keyed creation needs that context.

The receiving Session Host checks before survival setup, child spawn, state
publication or readiness. Its child-spawn boundary checks again. The
portable bundle carries the canonical evaluator, portable PyYAML source and
license notices. An explicitly selected portable receiving context supplies
the host's validated marketplace scope; it is not read from request environment
or the guest's installed suite. The `session-host-agent` runner
applies an explicit cwd override before resolving
the local launch. Plan cwd is authoritative for both the child process and
ACP session creation; a configured anchor is not substituted for it.
Far-side instance admission also spans the original startup operation and
provider enumeration/native sampling retirement. Cancellation waits for that
original work without replaying it or launching a Session Host afterward.
Removed providers retain their original native holders; cancelled holders
with unknown native completion keep admission rather than claiming retirement.

Native worktree resume, singleton initialization, UI task start/resume and CLI
embodiment check before import, ownership/reservation gates, mux creation or seed
submission. The native owner remains responsible for rechecking at execution.
Carrier session creation enters the same HTTP/session-manager receiver, so a
hosting refusal prevents the carrier's subsequent prompt submission.

SSH admission asks the receiving owner for its independent read-only
`agent-policy check --path <actual-cwd> --operation <operation> --json` answer.
It requires a version-1 check result with literal `allowed: true`, no error
fields, and a successful exit; a bare policy object is not admission. Missing commands,
malformed answers and denied policy refuse without an agent channel. The
receiving shell runs the check again before either binstub or direct launch.
A project-bound target without a concrete directory asks that owner for
`agent-policy check --project-root`, or `--worktree-id` for an exact retained
workspace. This metadata-only read precedes allocation; the final execution
checkout is still checked again. Neither a guessed remote home nor a similarly
named local repository is proof.

The SSH policy read establishes the shared manager's minimal profile-based
connection before executing the query, including on a cold daemon. It preserves
existing connection forwards rather than clearing another consumer's tunnels,
and does not prepare auth hooks, plugins, worktrees or an agent channel.
Connection establishment and the policy command share a 30-second deadline;
connection failure, timeout and invalid or refused receiver results remain
explicit hosting refusals. Successful connectivity is not policy admission.
Expected OpenSSH profile-resolution failures and bounded `ssh -G` timeouts
also become hosting refusals. The shared library distinguishes operational
resolution errors from unrelated programming `RuntimeError`s; Bridge does not
catch all runtime errors or infer permission from a failed profile read.

## Provider admission and capability limits

Supported container and CodeSpace targets are checked, not blanket-refused.
Container admission inspects the exact running Docker instance and executes
the policy query as the provider-selected user at the actual guest checkout.
CodeSpace admission uses its existing read-only availability and SSH transport,
resolves execution checkout metadata, then runs the advertised portable reader
or the prior provider's enforcing guest-owner baseline. Neither
query prepares credentials, submits a seed or starts a managed agent. A stopped
or otherwise uninspectable guest refuses instead of being woken speculatively.

Known CLI namespace providers first resolve an unprepared target. Ordinary
resolution performs guest admission before `ensure_ready` and cannot wake an
uninspectable target. The HTTP start entry and initial stopped-session admission
explicitly admit activation through the existing venue owner. They retain the
CodeSpace launch-policy gate, then check fresh guest policy after readiness,
before plugin staging and again at the final target. An operation's descriptive
name is not activation authority. The read-only resolver protocol and
actual receiving target, not the namespace's spelling, determine support.
CodeSpace readiness alone does not wake a stopped target. After the typed
no-wake refusal, an explicitly activating launch may consume the provider's
version-1 `activation: start` declaration and call that same owner's
`start` operation for the exact resolved name. It then repeats fresh admission;
an available but denied repository never enters the activation branch.
This activating namespace shortcut is CodeSpace-only. Containers retain fresh
hosting admission before readiness and plugin staging; a stopped container
cannot use the CodeSpace handoff to wake before its policy can be inspected.
Container activation requires its own negotiated contract.
Explicit resume can make this same typed transition during initial admission,
before provider refresh. It retains the original stop fence, resolves the exact
retained venue through its current provider and checks that fence before and
after activation. Fresh policy at the retained execution checkout must then
pass before recovery begins. A newer stop or a changed venue still refuses.
Initial activation holds recovery admission, so an already-draining daemon
refuses before waking the venue and a later drain waits for the admitted work.
Cancellation settles the original bounded provider invocation before releasing
that admission; it never replays activation. A retained legacy session may
adopt the current provider's receiving contract without replacing its venue,
execution checkout or conversation.
HTTP start likewise holds manager-owned admission before resolving a provider;
drain cannot report completion while that request can still activate a venue.
Cross-repository resume supplies the retained repository and remote to that
provider. Adopting a different leading `cd` cannot silently retarget the retained
checkout; a changed execution path refuses before recovery.
Explicit checkout selections receive that same check. Remote-host preparation
also verifies every known retained checkout before updating its target or
preparing a replacement child, so a later provider-default change cannot
replace the directory that admission validated.
Legacy retained CodeSpace metadata negotiates an advertising current provider's
receiving declaration before choosing the first policy consumer, including when
the venue is already Available. This is not fallback after a guest-owner failure.
A provider without the declaration retains the prior enforcing adapter; an
unsupported or denying guest owner still refuses. A failed current-provider
negotiation cannot select another adapter. With no current namespace owner,
retained portable inspection or reattachment does not acquire a new provider
prerequisite.
Later full-target refresh is non-activating and preserves the retained explicit
checkout flag. If the venue stops after initial admission, that refresh refuses
rather than issuing another unfenced activation. A newer durable stop therefore
cannot be bypassed while resolution waits.
Opaque commands and unknown resolver protocols still refuse before effects;
invalid cross-repo requests are rejected before admission work.

Known privileged relays check the actual local repository before starting the
sub-daemon. The privileged receiver must then advertise exact
`repository_hosting_version: 1` before native session creation/load; preparation
capability alone is not sufficient. Older receivers refuse without a native
conversation. Opaque raw elevation commands remain unsupported. A caller
capability flag or cached allow never substitutes for receiver enforcement.

Local project targets with no cwd use the same owner's read-only policy and
source-metadata operation. Missing owner metadata is an actionable refusal, not
a fallback to the daemon's working directory or effectful launch resolution.

Synchronous native conversation import accepts exact local targets only.
Remote ACP-mode worktree import therefore refuses rather than persisting a
conversation whose receiving policy cannot be checked by that synchronous
boundary. Remote native import needs a receiver-aware asynchronous protocol;
ordinary SSH creation with an exact path retains the checked route above.

## Host-selected portable policy contract

The private launch option `--repository-policy-context` selects
`agent-bridge.receiving-policy`, version `1`, with a `marketplaceId` string.
The host constructs it only after validating its selected Bridge installation.
An empty marketplace is valid only when that host resolves the supported legacy
installation. The selected context travels as a literal argument on the existing
authenticated transport, outside child argv and request environment. Missing
or unsupported context never silently selects a different marketplace.

Provider preflight sends the same canonical evaluator and YAML sources in a
bounded stdin archive to an isolated `python3 -I -S` receiving process. This
is a deliberate Bridge-owned receiver, not fallback after invoking a failing
guest command. It ignores unrelated guest installation selection and freshly
reads the actual execution checkout and selected overlay. The original
repository-policy library's legacy probe keeps its narrower preflight-only
meaning and explicit-context refusal.

The shared `repository_policy.receiving` entry supplies this explicit receiver;
the original legacy probe is unchanged. Binary and text-only provider transports
carry the same archive, with bounded base64 encoding for the latter. Direct
Docker uses binary stdin; driver-backed containers use base64 text through
`runtime-proxy exec -i`, which forwards stdin through the driver's existing
`input_text` contract without changing the selected instance or user.

Provider declarations are additive and selected before use. A CodeSpace's
`portable_receiving_policy` version `1` includes its unguarded `acp_command`
for the enforcing bundled Host; the ordinary guarded command stays available
to prior callers. Bridge never strips policy text from a shell command.
A trusted container advertises version `1` before Bridge passes the optional
`--repository-policy-context` to `session-host-prepare`. That provider freshly
executes the canonical reader as the pinned instance/user before credentials
or environment projection, and returns the command in its existing response.
It omits only the redundant guest-suite wrapper; the bundled Host still
checks at actual execution. Without the declaration, new callers retain the
prior provider contract and its guest-owner requirement. Unsupported or malformed
declarations refuse before new syntax; an invocation failure is never replayed
through another contract.
The prior provider's preflight also uses that guest owner, rather than passing
a portable check only to fail a retained guest-owner guard after preparation.
Failure of that baseline never triggers a portable fallback.

For actual Session Host launch, the same source closure is included in the
content-addressed bundle. It rechecks before survival setup and child spawn,
including the attested shell's pre-exec gate. It carries enforcement code,
not an allow receipt. A changed policy can therefore refuse after a successful
preflight without publishing a running child or new host authority.

| Receiving path | Policy and workspace ownership |
|---|---|
| Local ACP | Current installation's canonical check; managed IDs stay with Worktrees |
| CodeSpace workspace ACP | Selected portable preflight and bundled launch checks over existing transport |
| Trusted-container workspace ACP | Exact instance/user inspection, stdin read over the runtime proxy, then bundled launch checks |
| SSH/project ACP | Existing receiving owner and shell checks remain; no new SSH Session Host is introduced |
| Managed remote worktree ID | Existing allocation/resolution owner remains required; an unavailable operation refuses rather than treating the ID as a path |
| Muxed/standalone remote-driver | Existing owner and mode remain unchanged by this policy slice |

This option is internal to the selected host bundle, not new syntax sent to an
arbitrary installed executable. A new caller ships and names its own
content-addressed closure. An older caller omitting the option retains the
previous bundled guest-owner adapter; that path still refuses unavailable or
incompatible guest commands. There is no effectful probe-and-replay fallback,
package-version equality requirement or automatic guest upgrade. Reattachment
to a retained host remains on its original protocol/lifecycle boundary;
fresh recovery uses the newly selected launch checks.

Already-running remote ACP connections remain connection-only targets, not
local authoring-process launches. Bridge does not apply its local filesystem
policy to an unrelated remote server's `/` attachment path.

## Explicit portable mux receiving

`agent-bridge create codespace:<name> --cli --detach --receiving-support portable`
selects Bridge's launch-scoped mux support through the CodeSpace provider.
`agent-codespaces copilot <name> --detach --receiving-support portable` is the
same provider entry point. Omitting the selection preserves the legacy guest
launch; an already-retained portable endpoint cannot downgrade to that path.

This source contract requires an activated, inspectable CodeSpace with an
ordinary execution workspace. Managed worktree allocation/resolution, other
venue providers and Windows receiving-side mux creation are not implemented
by this option; they refuse instead of inventing a workspace or falling back.
Windows callers use the owning PowerShell process boundary, preserving literal
plugin metadata and seed stdin. A legacy batch shim without that boundary
refuses before launch.

The selected Bridge process advertises `mux` version 1, command/stdin transport,
optional prerequisite and driver-label support, and launch-scoped registration.
`receiving-support capabilities --owner` additionally checks the actual running
Bridge's `portable_mux: 1`, `portable_inbox: 1` and
`readonly_plugin_controls: 1` health capabilities without starting it. The actual
Connection Owner must independently advertise its endpoint-custody support.
Package versions or installed guest-suite commands are not these capabilities.

The provider preserves claims, target locking, relay/auth checks and compiled
plugin preparation, but does not run dotfiles, harness or repository setup.
Canonical policy checks precede plugin preparation, prerequisite setup, native
creation and execution inside the pane. The archive composes the existing
Manager pane shell, standalone remote-driver, canonical policy and Worktrees
prerequisite helper; it does not install a guest coordination suite.

Registration credentials travel only in the receiving stdin envelope and
private attempt files. The pane's Bridge configuration and remote-driver
configuration are scoped to that attempt; shared guest credentials and an
existing native mux server are not rewritten.

The durable provider journal precedes native launch and retains the original
attempt/session IDs, reservation, request, prepared plugin context and payload
digest. Unknown creation or registration retains custody. Recovery rebuilds
only identical support and inputs; it never repeats a legacy launch, kills a
default-server session or allocates a replacement. Confirmed rejoin addresses
the same socket and native pane/incarnation, including after a human rename.
Seed admission uses the existing keyed, Bridge-owned delivery lane, never TTY
typing. Driver display labels remain separate from exclusive driver ownership.

The portable consumer includes its `portable_launch` observation in
`retained-inbox-v1`. Current receiving additionally requires the actual daemon's
`compatible_native_inbox: 1` and selects `receiving_api: "native-sdk-v1"`.
Ordinary delivery uses the already-joined public SDK and fresh exact native
registration, not a launch reservation or private controller as message
authority. Launch allocation/rejoin retain their original reservation and native
endpoint checks. Older consumers omitting the selector retain their original
reservation-bound enrollment. A portable target cannot admit a prompt before
enrollment or expose its retained prompt through the legacy inbox.

Readonly plugin observations use the capability-qualified
`controls?readiness_only=true` filter. It neither claims nor expires mutation
controls. Typed startup admission binds the original operation, selection and
message to its successful observation; retained delivery rechecks the loadout
before and after its one-shot final grant. Mode, interrupt and history-replay
effects remain unsupported. The standalone driver separately checks the unique
selected extension roots before acquiring native control.

These are source contracts, not claims of release, installed, native Copilot,
native TMux or live external qualification. The focused provider/receiving
controls, joined fake-SDK consumer and inert Windows process witnesses keep
those evidence tiers separate.

## Preserved operations

The historical machine-local `projects.yaml expose_agent: false` still only
filters discovery. Repository policy is stronger and is checked on explicit
launches too. Discovery suppresses denied/invalid repositories with diagnostics;
ordinary source/worktree inventory remains available.

Introducing a denial never kills an existing process, deletes history,
unregisters a project or releases custody. Existing live message delivery and
inspection do not become new-launch operations. A send that would implicitly
cold-resume is checked before changing session state or writing a turn.
Source-only allocation, editing, tests and source-control operations stay with
their existing owners. Externally hosted Code Review is unchanged; naming a
managed authoring session after a reviewer is not an exemption.

## Focused regression selectors

The isolated effect-ledger coverage is in:

- `tests/test_repository_hosting_policy.py`: discovery, explicit targets, cwd
  overrides, late denial, resume/resync/handoff/implicit cold-start custody.
- `tests/test_repository_hosting_receivers.py`: final resolved cwd, prepared and
  retained plans, receiver override, Session Host backstop, SSH capability
  refusal, a cold real SSH manager through the HTTP start handler, namespace
  readiness and opaque provider refusal.
- `tests/test_repository_hosting_surfaces.py`: keyed reservation retention,
  UI/CLI pre-seed gates and continued live delivery.
- `tests/test_hosting_capability.py`: actual command wiring, mixed-version
  daemon refusal, per-attempt capability/protocol composition and health support.
- `tests/test_portable_receiving_policy.py`: isolated receiving processes,
  selected overlays, malformed/unreadable policy, late denial in the actual
  shipped bundle, no-wake versus launch activation, and a real bundled Host
  with a mock ACP peer through one turn and same-conversation reattachment.
  The peer is not native Copilot or installed/external acceptance.

The required unit packet also admits
`tools/promotion_unit_tests/test_ssh_hosting_policy.py`: cold connection ordering,
preserved warm forwards, connection/receiver refusal, real profile-resolution
failures and the shared deadline.
Its stateful SSH boundary refuses execution before connection; no native
process, network or OS event loop runs in these units.

These selectors are intended for the repository's isolated bounded runner after
all new source/test files are included in its snapshot. They do not assert that
tests, native proofs, release or installed adoption have completed.

See also [architecture](architecture.md), [Agent Bridge](../README.md) and
[experimental remote ACP](remote-acp.md).
