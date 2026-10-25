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
portable bundle carries a dependency-free adapter which invokes the guest's
own enforcing Worktrees policy command; it does not import the frontend's
installation configuration or silently omit admission. The `session-host-agent` runner
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

## Provider admission and capability limits

Supported container and CodeSpace targets are checked, not blanket-refused.
Container admission inspects the exact running Docker instance and executes
the policy query as the provider-selected user at the actual guest checkout.
CodeSpace admission uses its existing read-only availability and SSH transport,
resolves execution checkout metadata, then asks the receiving owner. Neither
query prepares credentials, submits a seed or starts a managed agent. A stopped
or otherwise uninspectable guest refuses instead of being woken speculatively.

Known CLI namespace providers first resolve an unprepared target and perform
guest admission before `ensure_ready`; resolution is checked again before
plugin staging and at the final target. The read-only resolver protocol and
actual receiving target, not the namespace's spelling, determine support.
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

Already-running remote ACP connections remain connection-only targets, not
local authoring-process launches. Bridge does not apply its local filesystem
policy to an unrelated remote server's `/` attachment path.

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
  refusal, namespace readiness and opaque provider refusal.
- `tests/test_repository_hosting_surfaces.py`: keyed reservation retention,
  UI/CLI pre-seed gates and continued live delivery.
- `tests/test_hosting_capability.py`: actual command wiring, mixed-version
  daemon refusal, per-attempt capability/protocol composition and health support.

These selectors are intended for the repository's isolated bounded runner after
all new source/test files are included in its snapshot. They do not assert that
tests, native proofs, release or installed adoption have completed.

See also [architecture](architecture.md), [Agent Bridge](../README.md) and
[experimental remote ACP](remote-acp.md).
