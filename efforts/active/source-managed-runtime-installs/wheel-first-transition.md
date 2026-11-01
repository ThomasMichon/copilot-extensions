# Wheel-first, independently maintained suite

Back to the [canonical effort](README.md).

**Status: proposed direction; implementation and host deployment are not proven.**
The effort, vision and performance proposal is reviewed and merged upstream
before implementation. Local construction, live version-update/cutover and
recovery qualification then feed one final whole-flow implementation PR.
A proposal merge is an intent checkpoint, not installed-system acceptance.

## Requested outcome

The suite becomes a separately installed system. Copilot plugins connect users
and agents to its services and tools; replaceable plugin payloads are not the
runtime installation source or a trigger for runtime construction.

Any selected backing checkout supplies source to promotion, not directly to an
installer's source-build loop. All source-install footprints come from that
flow as a main-equivalent snapshot and matching prebuilt first-party wheels.
Promotion produces complete native application projects from one canonical
version composition, with pyproject declarations authoritative. Installation
consumes those outputs through the existing UV-native assembly path; a governed
source fallback also goes through promotion before installation.
The [performance report](wheel-first-performance.md) is evidence for this
construction strategy, not application readiness or rollout authority.

The two tracks are:

1. **Fast relief:** consume the existing canonical promotion and versioned
   artifact outputs through a Manager-only application asset and the existing
   upstream self-install entry. Source/wheel production stays with its existing
   owners; this caller does not create another composition or publishing flow.
2. **Independent maintenance:** acquire the upstream installer independently of
   Copilot, install Manager first, derive wanted local services from selected or
   registered repositories, prepare them with bounded parallelism, and activate
   through application-owned readiness and native ownership.

Reuse the mechanisms and owners of #9290, #9453, #9032, #7758 and #9623.
This proposal does not replace their branches, claim their work, or incorporate
unreviewed patches from another contributor.
The [2026-10-31 refinement](inception-transcript.md#promotion-mediated-backing-checkouts-and-app-staging)
requires this boundary and an install/activate split for every app.

## Source-input contract — proposed, not existing syntax

The self-install entry separates **runtime source** from **repository adoption**.
Existing adoption spelling remains `--repository`; an ambiguous `--repo` must
not silently select a different Manager source.

| Input | Intended semantics |
|---|---|
| No runtime-source override | Resolve trusted upstream Main once and consume its admitted promotion snapshot and matching wheel inputs. No dependency on an installed plugin payload, shell profile or old runtime command. |
| `--install-source <absolute snapshot>` | Consume a locally promoted, complete installation-source snapshot and its matching local wheels/locks. The snapshot records its selected backing source and main-equivalent version-seed association. No GitHub PR, tag or release is required for isolated validation. |
| `--source-checkout <absolute checkout>` | Supply a selected backing checkout to the existing promotion flow, producing a main-equivalent snapshot and matching prebuilt wheels before installation. Never follow live edits from a deployed environment. A last-known-good Main checkout retains that ancestor's finalized versions and locks, rather than being relabeled as the latest Main. |
| `--source-commit <full Git object ID>` | Select the admitted commit from the stated source. The Main/LKG path proves ancestry against the approved fetched Main through Git. Explicit development assembly uses the selected development authority and canonical local promotion, not a false Main/release claim. |
| Explicit source repository/ref | Override the default through the source owner; preserve Git credential ownership and reject embedded secrets, unsupported syntax and ambiguous source combinations. |
| `--repository <HTTPS repository>` | After Manager installation, acquire/register the selected repository and hand off to trusted repository-owned setup. Omission permits an explicitly selected registered-repository sweep. |

These proposed switches need provider-first support discovery and mixed-version
parser/process tests before callers use them. Retain existing supported bootstrap
overrides during migration. Unsupported old entrypoints report a precise
prerequisite or capability gap; they do not reinterpret arguments.

## Installation and activation

Every app exposes a stage-only preparation boundary and a normal-install
activation boundary. The separation is required; the following spelling remains
a proposal requiring provider-first contract support:

| Proposed operation | Required boundary |
|---|---|
| `install --stage` | Assemble the candidate venv from promoted inputs and verify app-owned cutover readiness. Do not change active version redirects, global binstubs or consumer registrations, publish serving state, or admit live work. |
| `install` | Publish the prepared selection, binstub and registration updates; launch the now-current versions and let displaced owners sunset after successor startup. Reuse staged inputs without a first-party rebuild or repeated source preparation. |

For an atomic toolset, all members stage before its coordinated activation;
per-app staging cannot publish an independent premature redirect.
_(Agent-recommended compatibility detail:)_ standalone normal installation may
compose staging when no matching prepared candidate exists, preserving ordinary
first-install usability without requiring a separate manual command.

- Use the published upstream `worktree-manager/bootstrap.sh` or
  `worktree-manager/bootstrap.ps1` front door, and the existing
  `scripts/runtime_install.py` construction input seam. Consume one complete
  promoted Manager-only asset/UV project before expanding to repo-derived services.
- Manual standalone self-install and rollback entrypoints depend only on their
  trusted promotion inputs, Git where source selection needs it, supported UV
  and configured feed—not a healthy old
  Manager, agent command, updater queue or the services being replaced.
  Repository adoption and its authentication follow completed core installation.
- Read repository configuration and plugin registrations as desired-state
  inputs. Do not make marketplace installation of every runtime the default
  selection. Keep independently installable components usable without a
  mandatory Manager or central coordinator.
- The wanted-service union respects each repository's role, locus, providers
  and installation scope. A missing or incomplete declaration does not authorize
  automatic uninstall or erase another repository's requirement.
- Materialize complete promoted application projects, libraries and build
  resources into owned version slots through the UV-native assembly owner.
  Resolve the matching wheel closure before setup; unavailable artifacts select
  the governed promotion path with a reason, not a downstream raw-source build.
  Present-but-invalid artifacts and blocked dependencies are not absence.
- Split preparation from activation for every app. Setup owns its completion
  and readiness result; activation owns stable command redirects and serving
  state. An installer does not claim success from compile-only probes.
- The operator-selected local **atomic toolset** strategy prepares every member
  and admits actual incoming readiness before one coordinated selector chooses
  the new set. Per-app setup does not publish premature independent redirects
  that destroy atomicity. Preserve the last good set and state-compatible
  rollback; unsupported or foreign ownership blocks the affected transition.
- Use existing passive/start, health, routing, drain and native retirement
  contracts. Incoming owners and displaced owners settle replacement; the
  caller need not wait for complete draining after admitted startup. Connection
  reset and partial handoff outcomes must follow explicit commit-forward or
  rollback semantics, not a success-shaped timeout.
- Keep global launchers stable across version changes. The first migration away
  from a running legacy CMD launcher must happen outside that launcher's mutable
  execution stream. Every owned consumer registration resolves through the
  stable, marker-aware entry after payload/staging removal.
- An old installed global wrapper or connector must still reach the new
  runtime, or a supported, explicitly authorized connector refresh precedes
  activation. Never patch an installed payload ad hoc to manufacture compatibility.

No new whole-source fingerprint framework or full-suite hash-attestation barrier
is proposed. Reuse native Git source/release association, uv's package/lock
validation and existing release authority. Preserve authentication, integrity,
readiness, wrong-owner protection and release gates. Any replacement of an
existing source-hash/admission contract is a reviewed, separately tested migration,
not an implicit waiver by this document.

## Manual versus automatic work

Automatic update inhibition remains operator-owned and unchanged by a manual
install. Explicit manual rescue authority applies only to its addressed scope
and enters normal native transaction/ownership admission. It does not clear a
global switch, steal another repair, replay an unsettled request or authorize
all machines.
Inhibition suppresses automatic update work, not ordinary launch/use of an
already admitted healthy runtime. Manual authority does not clear unsettled
native queues or become a waiver of future unrelated maintenance.

The automatic-update switch blocks only an explicitly classified
`<repo> update --auto` invocation. Manual `update` without `--auto`, raw
self-install, setup/install-source, doctor/list and ordinary launch are not
blocked by that switch. A launch that requests a nested `update --auto` skips
only the automatic update; it still launches admitted healthy tools.
Actual parser/request propagation must preserve that classification.

| Switch state / command | Required outcome |
|---|---|
| Active / `update --auto` | Explicit inhibited automatic outcome; no update runs |
| Inactive / `update --auto` | Normal installation-owner admission |
| Active / manual `update` | Manual owner admission; no switch veto |
| Active / raw self-install or setup/install-source | Normal addressed owner admission |
| Active / doctor/list/ordinary launch | Read/use proceeds; nested automatic update is skipped |
| Any / foreign or unsettled native queue ownership | Independent ownership refusal remains; no stealing or clearing |

Background preparation happens after Manager load, with bounded parallelism and
one admitted maintenance owner. A long cadence, such as approximately hourly,
is optional policy rather than an unconditional new scheduler. The next launch
can select an already prepared generation without foreground rebuilds or scans.
Plugin session-start hooks perform bounded guidance/discovery only; they do not
install, reconcile or stamp runtimes. Missing-backend warnings and an optional
once-per-session prepared-update notice remain honest and bounded.

Prepared plugin/instruction/hoist selections must keep fresh worktrees correct:
required current managed bodies reach model discovery before execution. Reviewed
fallbacks are used only when a current managed body is unavailable. Copying,
presence, a cached version or a background completion notification does not
assert that a running model admitted the body.

## Recovery before host risk

Use an already-ready installation owner's `doctor --json` and, when supported,
read-only request/recovery-status operations first. A diagnostic launcher that
silently bootstraps is not a read-only doctor. The trusted standalone self-install and explicit promotion-mediated source
overrides must remain usable when the old binstub is broken.

The [generic recovery cases](wheel-first-recovery-cases.md) preserve configuration,
durable data, active worktrees, SSH forwards and unrelated agents. Native owners
positively inspect stale locks, redirects, publication state and process birth
identity. Unknown, inaccessible, foreign or unsettled ownership is not permission
to unlink, kill or declare the state clean. Do not adopt unofficial workarounds,
edit installed code, add privilege, or replace other contributors' open fixes.

## Plan and acceptance

### W0 — Reviewed upstream intent first
- [ ] Capture the operator request and distinguish requested scope from recommendations.
- [ ] Review and merge the effort/vision/performance proposal upstream before implementation.
- [ ] Qualify locally, then submit one final implementation PR for the complete flow.

### W1 — Complete local promotion and Manager fast path
- [ ] Reuse canonical Main seeding/changefile composition before all source/wheel locking.
- [ ] Consume complete promoted projects and matching prebuilt first-party closures; keep wheel binaries outside Git and production with its existing owners.
- [ ] Consume backing-checkout promotion and Manager-only acquisition through existing owners, preserving main-equivalent snapshot/wheel correspondence and strict native project declarations without another builder/cache/profile system.
- [ ] Preserve promotion-mediated source fallback, configured feed, seasoning, resources and honest diagnostics.
- [ ] Require a stage-only readiness boundary and a normal-install publication/launch boundary for every app, including Manager; qualify provider support before callers send proposed flags.

### W2 — Real whole-system Windows-container acceptance
- [ ] Use local install-source snapshots for every container deployment, with no host credentials or mounts.
- [ ] Install all selected daemons through supported fixture authentication and role configuration.
- [ ] Prove real Manager and agent-command operations: repository/worktree and task/state round trips, required service health and installed new code.
- [ ] Change source and a canonical changefile; promote V2, prepare/reinstall it, prove new command behavior, owner retirement, preserved state and last-good failure behavior.
- [ ] Prove every app's stage-only operation leaves current redirects, global commands/registrations and serving ownership unchanged; normal installation publishes the selected version and starts it before the displaced owner sunsets.
- [ ] Exercise atomic selection/readiness/rollback and the generic legacy recovery cases.
- [ ] Retain failed containers and literal phase/child diagnostics; success cleanup remains scoped.

### W3 — Independent maintenance and launch integration
- [ ] Move runtime installation out of plugin first-use/session-start paths.
- [ ] Integrate repo-derived wanted services, post-load preparation and prepared activation.
- [ ] Replace foreground reconciliation/projection/hoist work with bounded prepared selection while proving correct fresh-body delivery.
- [ ] Verify Windows PowerShell and POSIX noninteractive SSH-shaped self-install without TTY, ambient PATH or profile assumptions where available.

### W4 — Final implementation review and deployment-owner handoff
- [ ] Clear required CI/review on the final whole-flow implementation PR and update actual operating documentation.
- [ ] Confirm actual merge and Main promotion before handing the released flow to its deployment owner.
- [ ] Supply the local qualification, target-specific owner/readiness and independent rollback evidence; a healthy project doctor is not all-suite clearance.
- [ ] Leave host installation, peer coordination and any declared-route remote fleet update with the separately assigned deployment owner.
- [ ] Distinguish local SSH-shaped process proof from actual remote SSH validation.

The phases and owner reuse above are implementation decomposition
_(agent-recommended)_. The operator explicitly requested whole-system proof,
local promotion inputs, proposal review before implementation, one final code
PR, scoped recovery and preservation. The exact automatic/manual truth table is
operator-required, not a generic maintenance-active veto.
If an unavoidable external credential/model/role is required, identify the
blocked service and scope; do not claim the portfolio passed.
