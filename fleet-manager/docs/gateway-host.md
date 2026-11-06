# Explicit installed Gateway hosting

The `gateway-host` optional dependency profile composes the existing Source
application constructor with Fleet's TLS Gateway lifetime. It is not a second
installer, deployment manager, SSH writer or enrollment issuer. Ordinary
`fleet-manager` installation has no hosting callbacks and remains inert.
Selecting `service-gateway` alone supplies the foreground SDK, not a resident
role.

The same installed lifetime can host one explicitly selected SSH participant
when the application selects both `gateway-host` and `ssh-client`. This does
not start a second Gateway server or install a second lifecycle owner.
`ssh-client` alone remains foreground-only. The physical installation selects
one hosted role, not simultaneous Gateway and participant lifetimes.

The selected executable reports this additive contract with
`fleet-manager gateway-host capabilities`: `fleet-manager.gateway-host-contract`,
version 1, with `selected-application-host-v1` and `native-owner-stop-v1`.
Callers must inspect their selected executable, not infer support from a newer
payload. Older executables continue to reject unknown syntax. The original
foreground `serve` and client lifecycle commands remain unchanged.

## Preparation and selection

Use the existing Source snapshot, associated wheels and native profile
preparation with component `fleet-manager` and extra `gateway-host`.
The generated application carries its actual selected closure and the owning
`__source-setup` / `__source-start` callbacks. Its ordinary `install --stage`
only constructs the environment; regular `install` validates the host inputs,
publishes through the existing selector owner, then requires actual incoming
Gateway readiness before reporting success.

An authorized adopter supplies an owner-private `gateway-host.json` in the
physical installation root (`FLEET_MANAGER_HOME` when explicitly configured):

```json
{
  "schema": "fleet-manager.gateway-host",
  "version": 1,
  "role": "gateway",
  "policy": "<absolute private enrollment-policy path>",
  "tls": "<absolute private listener path>",
  "journal": "<absolute private operation-journal path>",
  "authority": "example-authority",
  "installation": "example-installation"
}
```

These are references, not embedded credentials. Listener, policy and private-key
custody are checked before startup. The listener's certificate and key references must
also be absolute; installed hosting never resolves them against an installer's
working directory or the resident's different working directory.
The policy must use the existing
`fleet-manager.gateway-enrollment-policy` contract; the original operation
journal retains its accepted policy floor across restart. Preflight checks that
floor without publishing a new policy or enrolling a machine. An absent,
unsupported, expired or changed required input is a failure, never client-only
success. Hosting does not acquire a provider identity, issue a key or grant a
new machine permission to join.

### Selected SSH participant

Use the same owner-private `gateway-host.json`, with configuration version 2:

```json
{
  "schema": "fleet-manager.gateway-host",
  "version": 2,
  "role": "ssh-client",
  "configuration": "<absolute private fleet-manager.ssh-client JSON path>",
  "authority": "example-authority",
  "installation": "example-installation"
}
```

The referenced [SSH configuration](ssh-machine.md) keeps its existing v1
token/proof or v2 machine-key meaning and provider-owned references. Preflight
validates the existing local writer scopes and credentials without network,
lease creation or profile writes. The SSH lease and entire installation root
must be separate. No enrollment, credential acquisition or materialization occurs.
The entire installation root is excluded from all direct and derived writer
destinations, protecting configuration, selectors and immutable runtime slots
as well as lifecycle state. Selected configuration and startup input references
outside that root are protected from writer overlap too. Token-mode preflight
reads and validates the current bounded UTF-8 token without retaining it; later admissions still reread the
provider reference.
Every fingerprinted policy, identity, public-key, proof-key and CA reference is
read during setup preflight, before the incoming selection can replace a healthy
owner. The local public-key reference must also parse as canonical bounded
ed25519 during preflight; readable malformed bytes cannot defer failure until
after selection changes.
Existing profile/source lock paths must be ordinary single-link files.
Preflight does not create, acquire, clear or modify those locks.
Capability output additively advertises both host configuration versions and
each role's required extras. Older strict v1 host readers refuse v2; rollback
must not translate it into Gateway serving or foreground-only success.
Neither configuration version is retired or automatically migrated.

## Lifetime and operations

Native qualification runs the independent installed Gateway sequence, the
legacy SSH/host controls and the continuity-selected installed SSH sequence in
three disjoint groups within the same six-minute job. Each
group retains the 180-second runner bound, and neither stateful installation
sequence is split. Their union preserves the full selected case set; this is
not a claim that the combined portfolio finishes within one 180-second process.

`fleet-manager gateway-host status|start|stop` addresses the selected physical,
completed application. No resident code, working directory or lifecycle binding
depends on the source checkout or plugin payload. A runtime retention lease pins
the application through settled shutdown. The canonical attributed daemon
launcher owns headlessness, durable process creation and contained-test policy;
no Scheduled Task, system service or privileged registration is added.

The host uses the existing Gateway server for authenticated TLS serving, live
policy reconciliation and channel/callback cleanup. A separate owner-private
loopback control capability reports selected-generation/native-birth readiness
and requests exact stop. Its random capability is kept out of argv and public
Gateway routes; browser-origin control requests are rejected. Stop retains a
native process reference and reports success only after confirmed exit.
Deleting configuration is not withdrawal or uninstall.

For SSH, readiness publication follows the first authenticated snapshot and
complete synchronous profile/authorization transaction. It is not process
creation, watch startup, new enrollment or proof of an SSH route. Control
reports the last applied directory revision; it does not assert a continuously
connected remote channel. Authentication or local writer failure ends the
consumer and closes control. Stop and repeated cancellation settle that original
watch and its local lease before releasing the installed runtime retention.
Generation selection is checked while waiting for the first snapshot too;
an unconverged superseded host settles its watch and releases both holdings
without waiting indefinitely for Gateway recovery.
An unchanged install reuses the same ready owner. Key, local policy, target-login
identity, CA or configuration changes invalidate reuse; token renewal stays with
the existing per-admission reference reader and is not a host restart trigger.
Startup fingerprints bracket input preparation and are rechecked before opening
the consumer and after convergence, so a replaced configuration cannot be
published as a different consumer's readiness.
Selected-generation replacement still observes the existing exact stop and
lease exclusion. Before opening a watch, the same incoming process allows up
to the existing ten-second cold-rebind window for an outgoing lease to settle;
it neither creates duplicate consumers nor terminates a foreign incumbent.
This does not claim gap-free notification handoff.

Prelaunch intent is written before process creation and settled by that exact
child's readiness publication or confirmed launcher cleanup. If the launcher
disappears first, stop reports the unresolved launch instead of claiming native
exit from an absent readiness record. Unknown creation is not automatically
replayed, and the intent does not authorize signaling an unknown process.
Selecting a new generation does not settle or replace an earlier unresolved
launch or retirement intent; startup refuses before creating another child.
Launch admission, runtime publication and stop share a short native control
lock. Readiness waiting happens outside it; revalidation, publication and
intent settlement remain serialized so a stale child cannot overwrite an
already published successor.
Startup readiness samples take that same lock, so ordinary runtime-record
replacement does not become a fatal read/admission race.
Publication transfers the resident out of its launcher's failure-cleanup
custody: a late or interrupted launcher reconciles rather than terminating a
published host. Unpublished cleanup first revokes publication admission and
retains unresolved retirement intent until native exit is confirmed.
Status reports `launch-unresolved` rather than claiming no process exists
while either intent remains unsettled.
Unavailable control fails status rather than reporting a live native owner as
not running; predecessor startup inspection remains best-effort.
When that unresolved owner belongs to the selected generation, startup retains
its runtime record and refuses replacement, even if the listener inputs changed.
Unavailable predecessor-process permissions do not authorize a signal or block
incoming startup; explicit stop confirmation and private-input checks stay strict.

An unchanged install reuses a healthy matching host. Reuse checks the retained
listener and TLS byte snapshot, separately from live policy
reload. Same-generation listener changes or certificate/key rotation refuse
reuse instead of claiming the old endpoint is current; use an explicitly
selected new generation or exact stop/start to adopt those inputs.
Explicit replacement
requests best-effort drain from the displaced owner; that owner also observes
the current selector independently. The fixed TLS listener currently requires
a bounded cold rebind rather than seamless active/passive ingress switching.
The host closes new admission, offers a short drain opportunity, and joins the
existing serving cleanup before releasing its lifetime. A disconnected request
can have an uncertain external result; clients must reconnect and reconcile
their original operation instead of replaying it blindly.

This realizes a bounded part of
[authoritative runtime replacement](../../visions/installer/README.md#authoritative-runtime-replacement),
not the complete Fleet role lifecycle. Incoming construction/readiness and
current selection are separate from predecessor retirement. Unknown/foreign
processes are never killed to free a port; an occupied listener or unconfirmed
stop is reported literally. Provider-managed ingress cutover, protected
machine-management continuity, unattended reboot startup, role withdrawal,
complete release-selected fleet setup and actual adopter rollout remain with
the [onboarding owners](../../efforts/active/gateway-ssh-fleet-onboarding/role-host-setup.md).
Nothing here authorizes those effects or marks that parent complete.

## Qualification

Focused source controls cover optional-role selection, inert client behavior,
private references, the existing durable policy floor and the actual TLS/control
consumer. The opt-in `test_gateway_host_distribution.py` packet constructs a
noneditable application from real first-party wheels and a fixture closure of
installed external dependency bytes. It exercises the original application
installer, definite no-child and post-creation listener failures with retry,
source-independent execution, unchanged repeat, failed candidate
preflight, a distinct updated profile generation and exact native stop. That fixture is
not release-feed acquisition, provider authentication, deployment or fleet
adoption evidence.

The SSH variant uses the same actual offline application constructor and
installer against a disposable authenticated TLS Gateway with synthetic machine
keys. Its packet exercises first touch, source-independent repeat, failed
candidate preservation, selected update, unchanged local writer state and exact
native stop. Focused host controls additionally refuse readiness before directory
completion and on writer failure. This is not native ARM64, provider enrollment,
reboot startup or actual adopter rollout evidence.
