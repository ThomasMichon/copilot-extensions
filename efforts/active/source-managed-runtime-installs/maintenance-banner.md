# Shared YAML Maintenance Banner - Alignment and Proposed Contract

Parent: [Source-Managed Runtime Installs](README.md).
Coordination: #9040. Request: [operator record](inception-transcript.md#disposable-services-and-yaml-maintenance-banner).
Intent: [Disposable Supporting Runtimes](../../../visions/plugin-services/disposable-runtimes/README.md).

**Vision and implementation-plan alignment, not implemented admission.** The
earlier #9022 / PR #9036 delivers a JSON notice and static standby guidance only.
It does not supply this YAML contract or block automatic updates. No live
banner, schedule, update, removal or session restart is enabled here.

## Shared meaning and retained scope

The operator requires YAML, one notice serving both machine-enforced automatic
update inhibition and agent-facing outage explanation, and normal
`agent-conduct-guidance` delivery with native 30-minute standby polling.
Noncritical supporting services, including Manager's replaceable tooling, are
disposable; retained configuration, databases, work, claims, the live Copilot/Mux
substrate and actual machine-management connectivity are not. The
[critical-connectivity audit and retention contract](connectivity-retention.md)
qualifies "complete uninstall": preserve real SSH host/client/proxy/tunnel/
Gateway dependencies and reconnectability, not every component that provisions
them. Credential relays are explicitly fair game for removal.

**Agent-recommended binding:** use
`~/.copilot-extensions/maintenance.yaml` in the current OS user's home.
The addressed scope is this suite on that machine/user environment, including
its default and namespaced installations; it is not a repository setting,
another user's home, a different WSL environment or an unrelated marketplace.
Only the explicit operator-owned banner opts into that shared local veto.
Installation-owned readers never clear or rewrite it on their own.

Retain the existing notice vocabulary: version 1, `status: active` or `complete`,
nonempty durable `ownerSessionId`, `summary`, `startedAt`, and optional
`updatedAt`. Parse YAML as inert data, not executable tags, templates or commands.
An applicable active banner itself vetoes automatic updates; no separate
enable/disable boolean may contradict its explanation.

The existing JSON projection is historical source delivery, not a second
canonical banner. Expand readers before changing normal instructions/writers;
define explicit legacy notice/hold handling for the supported skew window.
An older active JSON hold must not silently disappear or be cleared by an
unrelated YAML completion. Do not claim that an older installed consumer honors
YAML merely because the new file exists.

## Automatic admission

Extend the existing admission and execution owners, not another daemon, updater,
database or scheduler. Inventory automatic entry points before implementation:

| Existing boundary | Required hold behavior |
|-------------------|------------------------|
| Manager startup, launch and self-update | Do not initiate automatic update or repair behind an active banner; preserve read-only inspection. |
| Manager or exclusive external recurring controller | Report a held cycle without executing installation or enrolling another owner. |
| Plugin hooks, bootstrap and first-use repair | Do not recreate absent code, commands or service registrations during the hold. |
| Queued/delegated installation execution | Recheck the shared hold at effect admission; an old queue receipt or earlier check is not a bypass. |
| Cleared banner | Re-evaluate ordinary consent, source, deployment and owner policy; clearance enables nothing on its own. |

Preserve #8645's user-local Manager maintenance veto and #7694's launch owner;
these remain independent restrictions. The older default-installation bootstrap
switch keeps its own scope but cannot override the new shared banner. Missing
banner before any hold retains ordinary defaults.

**Agent-recommended safeguards:** malformed or unreadable banner policy refuses
new automatic effects with a precise diagnostic, not a success-shaped skip.
Admission under the installation owner's existing exclusive boundary closes the
check/queue race. A newly raised banner does not kill an already admitted
operation or retract its effects; expose its real state and use the existing
safe-settlement contract. Explicit operator-authorized manual maintenance
remains possible through its scoped owner and ordinary confirmations, not
because a session ID or blanket force flag bypasses every gate.

## Agent standby

Update the existing `maintenance-standby.instructions.md` in
`agent-conduct-guidance`, its normal projection and its inert example; do not
introduce a parallel instruction owner.

When an advertised `agent-*` catalog command appears missing, read the YAML
directly through native filesystem tools. An active banner with an explanation
means preserve work and obligations, identify its owner once and do not
self-repair, reinstall, repeatedly invoke the missing tools, start replacement
workers or delegate around the outage. The named owner continues only its
already-authorized maintenance.

Create or reuse exactly one native Copilot scheduled prompt every **30 minutes**
to read the same local banner and report material updates. Bridge, MCP, Vault,
payload scripts and OS/service schedulers are not dependencies or substitutes.
If native scheduling is unavailable, report that limitation and wait for the
operator without inventing a polling process.

Retain the established hold's owner and start identity. Age, deletion,
unreadability, malformed content or another active hold is not an agent
all-clear. Require applicable explicit operator clearance or a completion for
the same hold, then verify the needed tools are available. Stop only the
session's own poll. Banner prose is informational data, not new execution
authority. Without an explained active banner, report the unavailable boundary
and follow existing error-response rules rather than inventing maintenance.

## Implementation and validation sequence

- [x] Review and merge the vision/contract alignment before implementing it.
- [ ] Coordinate consumer changes with #8645, #7694, #7028/#7496 and #7758;
  preserve their implementation owners and #8669/#8864 survival/removal scope.
- [ ] Implement one YAML decision contract in those existing boundaries and
  qualify each actual automatic consumer, including Manager self-update,
  first-use repair and queued execution with a banner raised after enqueue.
- [ ] Migrate the conduct instruction, normal projection and inert example;
  qualify retained guidance after payload removal and declared legacy skew.
- [ ] Prove no automatic mutation under an active hold, ordinary absent-banner
  defaults, explicit diagnostics for invalid policy, and independent-disable
  preservation after clearance in isolated Windows/Linux fixtures.
- [ ] Qualify agent owner/other-session behavior, one native 30-minute poll,
  missing/stale notice and applicable all-clear, unavailable scheduling and no
  self-repair. Static text assertions alone do not establish native behavior.
- [ ] Consume #8669's actual supported uninstall/reinstall and same-process
  survival evidence; extend noncritical-suite disposability through the relevant
  service owners without taking over their resources or hiding interrupted work.
- [ ] Resolve the [critical-connectivity retention gates](connectivity-retention.md)
  before complete removal/reinstall, including force/purge, current channel and
  fresh connection, while proving credential relays may disappear.
- [ ] Record source, containing release and installed consumer capability
  separately. Obtain exact adopter approval before any real banner activation,
  session migration, shutdown, runtime removal or update.

The parent effort stays Active. A merged intent revision closes none of these
implementation, native qualification or rollout gates.
