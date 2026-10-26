# Persistent isolated spawn reproducer

Back to [Linux role fleet](README.md) and the
[logging integration plan](logging-conventions.md). Tracker: #8377.
Complements the merged [first causal-record source](dispatch-spawn-causal-logging.md).

**Status: plan reviewed in #9069; first pure controls merged in #9071;
ordinary-volume storage amendment proposed below.**
This is an agent-recommended decomposition of the remaining isolated
failure-reproduction and logging qualification work. It does not authorize
resource creation, production fault injection, deployment or credential access.

## Outcome and scope

Retain one explicitly selected synthetic experiment across client disconnect,
service restart and container stop/start, so the actual Dispatch-to-Bridge spawn
path can be inspected and repeated without touching an existing installation.
Use actual source-built service/CLI processes with fake ACP, forge and inference
peers first. A fake peer proves the protocol interaction exercised, not native
Copilot startup, model behavior, authenticated forge operation or a real review.

This closes part of machine-fleet's
[discoverable diagnostic logging](../../../visions/machine-fleet/README.md#convention-discoverable-diagnostic-logs)
intent. It applies the clean-room vision's
[falsifying, not self-healing](../../../visions/clean-room-validation/README.md#falsifying-not-self-healing)
discipline, but a retained experiment is **not** that vision's fresh-machine
qualification. Fresh-install tests continue to start independently clean.
Keep the existing ownership, bounded-work and service-contract patterns; no
new fleet manager, resident controller, diagnostic database or message protocol.

### Operator-selected storage amendment

The operator selected the literal value:

> ordinary-volumes

The selected option was presented as:

> Use ordinary persistent Docker volumes; revise the hard quota requirement to monitored limits

This supersedes the earlier agent-recommended hard persistent-storage quota
requirement. Use ordinary experiment-exclusive Docker volumes with monitored
usage and free-space thresholds, not a privileged filesystem/mount or new volume
driver. This choice does not authorize creation, service actions or host changes.
The source controls must change through review; actual provisioning remains
separately approved. CPU/memory/PID limits and all other isolation, retention,
custody and pre-effect requirements are unchanged.

## Source-backed boundaries

| Existing source | Reuse and limit |
|-----------------|-----------------|
| `.devcontainer/test-isolation/devcontainer.json` and `tools/run_tests_in_devcontainer.py` | Reuse source-snapshot, resource and no-host-checkout principles. Its workspace is tmpfs-backed and teardown removes the container/volume; do not make that periodic test path persistent. |
| `plugins/agent-containers/src/agent_containers/fleet.py` and `lifecycle.py::restricted_policy_errors` | Restricted members use bounded tmpfs and reject persistent/image-declared mounts, even during migration. Do not relax that production invariant or label the lab a qualified restricted fleet member. |
| `tools/clean-room/ARCHITECTURE.md` | Keep scenario inputs generic, outcomes literal and results separately attributable. Do not import its optional real-login/authentication path into the credential-free lab. |
| `plugins/agent-bridge/tests/runtime_idle_fixture.py` | Reuse relevant actual runtime-graph setup knowledge. Its in-memory transfer channel and synthetic grant are not proof of native process handoff or durable guest storage. |
| Existing Dispatch diagnostics, Bridge event storage and process-telemetry owners | Discover and retain their real outputs; observation does not supply task, session, retry or service authority. Native streams and protected installer attempts remain with their owners. |

The proposed home is a small, opt-in test tool under `tools/reproducer-lab/`,
with synthetic fixtures in the owning plugins' tests where appropriate.
It uses the host's existing Docker execution boundary to manage only its
explicitly named experiment. Docker control stays outside the guest. This is
not a new `agent-containers` runtime profile, provider registration or fleet.
Extract an existing helper only where its safety/cleanup contract actually fits;
do not call a private ephemeral helper and silently suppress its teardown.

## Isolation and persistence contract

Before any service launch or injected fault, the host tool must inspect the
effective container, attached network and storage, and refuse missing,
uninspectable or mismatched boundaries:

- Immutable selected image/source inputs, a non-root guest user, read-only root,
  all capabilities dropped, no privilege escalation and positive CPU, memory,
  swap and PID ceilings. No host devices, shared host namespaces, privileged
  mode, added capabilities, host networking or published ports.
- An experiment-exclusive internal Docker network containing only its declared
  synthetic peers, or no network for an offline case. No external attachment,
  host-gateway mapping, tunnel, relay, SSH identity or Docker socket. Verify
  effective egress refusal, not just a network name.
- Explicit guest HOME, runtime/install/config roots, Dispatch and Bridge
  databases, workspace, registrar and evidence paths in exclusive persistent
  storage. Copy an admitted source snapshot into the guest; never bind a host
  checkout, home, runtime, vault, credential store or production state.
  Remove inherited credentials, proxies and service-discovery overrides from
  the minimal launch environment. Use only experiment-local synthetic identities.
- Use ordinary named local volumes, never tmpfs for durable state. Treat
  `persistent_bytes` and `output_bytes` as monitored usage thresholds, not
  enforced quotas. Check experiment usage and backing free space before
  starting a case and on a bounded cadence during an explicitly active case;
  refuse new work on threshold exhaustion or an unavailable/stale measurement.
  Reuse the native output owner's existing bounded capture/rotation where
  available. Do not change host filesystems, daemon settings or privileged
  mounts to manufacture quota support.
- Persist an exclusive experiment declaration in that owned storage with source
  and image identity, actual physical resources, fixed budgets and output paths.
  This is test-artifact metadata, not a second allocation/session ledger.
  Resume addresses those same resources and rechecks the boundary; a missing
  or different resource is not permission to create a replacement automatically.
  Concurrent host drivers must not operate the same experiment.

Guest volatile `/tmp` and `/run` remain separately bounded. Durable state must
not depend on them surviving stop/start. Database continuity uses the service's
own recovery semantics; copying an open database is not a valid snapshot.
Private logs, seeds and authoritative databases are not interchangeable:
apply each existing owner's retention/protection contract, preserve unknown
or incomplete evidence and refuse further synthetic work when capacity is
at its monitored threshold. No automatic broad cleanup, truncation or volume replacement.

Monitoring does not intercept every write: accepted work, concurrent host users
and delayed samples can exceed a threshold or consume the remaining backing
space. Report that limitation explicitly; do not advertise a hard storage bound.
Client disconnect can also end the host monitor. No new experiment case may
start while that monitor is absent, and resume must measure again before work.
Do not force-stop accepted work or fabricate a successful drain when monitoring
is lost. Retained services/evidence do not imply permission for an unattended
stream of new tasks. The approved experiment must name its active-case lifetime
and observation cadence; lifetime expiry refuses new cases, not automatic
deletion or an unauthorized process kill.

The default retains resources and evidence on failure or client exit.
Inspection/export is bounded and explicitly selected; exports allocate an
exclusive results directory and never overwrite another experiment's artifacts.
Stop, replacement and deletion require the normal operator approval and exact
resource selection, after checking active work. No global prune or host process
kill. A guest service restart is itself an explicit scenario action, not repair
performed to make a failed case pass.

## First experiment and evidence

Start with one ordinary synthetic task using the actual installed-in-guest
Dispatch/Bridge entry points, their own databases and a fake ACP peer.
The subject task runs in a synthetic harness workspace that permits hosting;
the source repository remains source-only. Preserve actual repository-policy
checks rather than disabling them to launch a fixture.
Retain the original seed, task/reservation/creation identities and actual
returned native/session identity where that backend supplies one. Unsupported
CLI-mode/native combinations report unsupported; do not rewrite production
admission, invent a session binding or send a second seed to force success.

Then inject one bounded fault at a time: blocked reconciliation before task
selection, Bridge create/send delay or error, lost reply after accepted delivery,
and a controlled guest process exit. Capture entry before the call, literal
cause, duration, any actual result and the last observed stage. A missing end
is outstanding/unknown, not a confirmed failure or permission to retry.
Exercise original-key/status reconciliation through the real existing owner;
never clear a reservation, lease or delivery fence to obtain a clean run.

General application logs retain their existing credential and prompt exclusions.
The synthetic original seed may be retained separately in the experiment's
private evidence directory. A lab-only strict mode must establish durable
pre-effect observation through the existing logging owner before allowing its
synthetic effect; an asynchronous enqueue or later readback is not that proof.
An unavailable strict capability refuses that scenario. It must not silently
change ordinary callers' degraded-log/primary-result behavior.

The report joins existing outputs by their actual available identifiers and
timestamps, with monotonic durations only within the same process. Mark absent
or truncated sources and unjoined events explicitly. Exporting a timeline is
not proof all native/workspace/first-prompt stages are instrumented. Do not add
a permanent collector, infer causality from wall-clock proximity, or claim an
observed protocol acknowledgement proves task START or review completion.

## Implementation sequence

Each step stops on its named failed prerequisite; do not self-heal the subject.

1. Land this source proposal through independent review and required CI before
   implementation. Preserve #8377, native capture and released/running gates.
2. Review this storage amendment, then extend pure configuration/boundary
   controls with explicit monitored thresholds, fresh usage/free-space refusal
   and honest unavailable-measurement outcomes. Add synthetic positive and
   negative controls without starting Docker.
3. Implement the opt-in host tool and credential-free fixtures using existing
   source-materialization, finite subprocess and service owners. Give every
   action a timeout, output cap and explicit outcome; leave unresolved native
   capture/strict-write dependencies unavailable rather than bypassed.
4. After separate resource/provisioning approval, create exactly one isolated
   experiment, inspect its effective boundary and prove persistence, monitoring
   and egress controls before the ordinary spawn and fault cases. Source review does not
   supply this approval or authorize existing-service changes.
5. Review the implementation and record only actually exercised qualification.
   Select later native CLI, Windows, real authentication and service-manager
   scenarios separately; no privileged/systemd image or production adoption is
   included in the first experiment.

## Validation plan

- [ ] Pure controls reject foreign/shared volumes, host mounts/sockets/devices,
  credential-bearing environment, external networks/ports, privileged or mutable
  image posture, missing CPU/memory/PID limits and missing or stale monitoring
  prerequisites. No schema or report may turn a monitored threshold into quota.
- [ ] Existing restricted-provider policy and ephemeral test-runner behavior
  remain unchanged; no test claims persistence from tmpfs or mocked Docker.
- [ ] On the approved real Docker volumes, inspect actual physical IDs and limits,
  prove no host-state exposure or egress, and prove two experiment names cannot
  share storage, network peers or export destinations.
- [ ] Create guest-owned state through actual services, disconnect the client,
  explicitly restart the selected guest services and stop/start the container;
  reattach to the same resources and verify database/workspace/registrar/evidence
  continuity. Missing storage refuses, without automatic replacement.
- [ ] Concurrent-driver, stale selection and interrupted host-command controls
  preserve exclusive custody; an unknown outcome does not repeat an effect.
- [ ] Use a small dedicated synthetic monitoring-test allocation and controlled
  free-space observations to cross the configured threshold. Refuse new cases,
  preserve evidence and expose missing/stale/failed measurements without
  deleting, growing or claiming a write-time quota. Cover cadence, monitor loss,
  remeasurement on resume and overshoot/accepted-work outcomes.
- [ ] Ordinary spawn records the actual invocation/returned identity and one
  seed delivery; delay, error, lost-reply and process-exit cases preserve literal
  failure/unknown semantics and existing deduplication/recovery.
- [ ] Strict pre-effect storage failure prevents the synthetic backend call;
  normal degraded logging retains its existing primary-result behavior.
- [ ] Timeline/export uses real records, marks missing sources and preserves
  useful synthetic identities/causes while excluding credentials. No model,
  native START, real review or full retention claim from a fake-peer receipt.
- [ ] Required CI remains a focused pure contract gate. Real Docker persistence,
  process/fault and platform matrices are explicitly selected bounded integration
  lanes, not new unconditional PR/promotion work.
- [ ] Independent review and required CI precede source merge. Record release,
  authorized installation and actual running/native acceptance separately;
  neither this plan nor a successful isolated experiment closes the parent.

## Immediate next action

The first [pure controls](../../../tools/reproducer-lab/README.md) parse the
declaration and identify known unsafe or missing static Docker observations.
They retain explicit unavailable backend, egress, custody and guest/runtime
requirements, never a lab-ready result. The bounded source merged in #9071;
it still carries the earlier unimplemented capacity-backend requirement.
That implementation must be revised after this amendment's review, not credited
as monitored-volume support. Synthetic tests cannot close step 2's
real boundary qualification or any persistence/fault gate.

Review this storage amendment, then implement the finite host reader and
ordinary-volume usage/free-space monitoring before a provisioning path.
Keep persistent provisioning blocked until the exact resources, thresholds,
case lifetime and actions receive separate approval.
