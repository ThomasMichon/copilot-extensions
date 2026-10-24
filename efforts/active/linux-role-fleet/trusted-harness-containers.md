# Trusted harness containers

Parent: [Linux role fleet](README.md), #6030.
Initial image slice: #8352. **Status: proposed plan; no image or runtime delivered.**
The campaign owner retains integration and publication. Implementation starts
only after this plan clears review and merges, followed by explicit execution
authorization. A merged plan does not authorize live provisioning or rollout.

## Guiding intent and request

Generalized request:

> Provide a generic trusted full-harness base image and specification in this
> repository. A consuming harness owns its thin devcontainer composition.
> Extend the existing agent-containers adapter for remote trusted venue session,
> authentication and management integration; retain the external manager as
> container lifecycle authority. Fleet-manager owns desired configuration.
> The selected Komodo controller on the Gateway service host owns deployment
> and updates. The Gateway builds approved source updates first; a future CI
> builder consumes the same image contract.
>
> Persist each container's whole home, workspace, machine identity and SSH keys.
> Retain existing user-login-session credential producers: any valid
> MFA-approved refreshing signed-in source sustains authentication. When all
> sources sign out, credentials eventually expire and authentication-dependent
> work stops without destroying state. Do not introduce an unattended identity,
> new issuer, or build-time authentication.

This slice supports coding and planning without selecting a model or changing
routing configuration. It closes part of
[`full-harness-projection-trusted`](../../../visions/plugins/agent-containers/README.md#full-harness-projection-trusted),
including persistent home/workspace and real self-maintenance, and the
[machine-fleet vision](../../../visions/machine-fleet/README.md)'s existing
role/service-authority separation. Implementation gaps stay in this effort;
this plan does not revise vision intent.

## Existing coverage and ownership

[Safe setup/refresh](fleet-setup-reconciliation.md) already requires a full
harness container proof but does not define a base-image recipe or build
interface. This child supplies that bounded detail, not a competing campaign.
[Repository-led preparation](repository-config-preparation.md) remains the
controller's configuration-acquisition/preparation plan.

| Boundary | Retained owner / dependency | This slice's limit |
|----------|-----------------------------|--------------------|
| Initial generic image | #8352 under #6030 | Recipe, standalone contract, focused docs/tests only |
| Desired configuration, activation and container lifecycle | Existing fleet-manager construction/lifecycle owners; Komodo deployment adapter | Consume approved inputs; no new controller, scheduler or direct worker mutation |
| Remote trusted sessions and auth reach | Existing agent-containers, agent-bridge and credential-relay owners; related #3442 and #954 | Propose interoperable image facts; separately agree adapter source changes |
| Capability-to-host placement | #2680 | Report image capabilities, not a replacement placement engine |
| Cross-venue integration proofs | [Venue parity](../venue-parity/README.md), #954 | Add a bounded trusted-image journey by agreement, not ownership of its full matrix |
| Gateway/SSH onboarding and artifacts | [Existing onboarding effort](../gateway-ssh-fleet-onboarding/README.md), #6681 | No signing, publisher, enrollment or reserved source-leaf edits |

References identify dependencies, not accepted transfers or claims on their
entire scope. In particular, Gateway/SSH work in #8000 and its source/test/docs
files remain untouched. Existing #6240/#6424 construction, selected-release and
lifecycle seams and #6241 operation admission retain their authorities.
The onboarding effort's exclusive policy/runtime leaves and publisher
integration reservations remain intact.

## Initial source reservation

Reserve **`.devcontainer/trusted-harness-base/`** for the initial implementation:

- `Dockerfile`: generic trusted image recipe, not an adopter harness checkout.
- `README.md`: supported platforms, trust, persistence, build and runtime contract.
- `build-inputs.schema.json`: versioned, non-secret input description.
- `build.py`: standalone build adapter using approved inputs, not a deployer.
- `tests/test_contract.py`: focused offline contract tests.

These filenames and the interface below are **agent-recommended realization
details**, subject to plan review. Keep image-specific tests/docs within that
directory initially; shared test-runner wiring needs the integration owner's
agreement. No initial edits to `fleet-manager/`, `services/`, credential
producers, publisher/signing workflows, Gateway/SSH files or agent-containers
runtime are reserved. A later adapter slice requires its own exact file
agreement and plugin changefile. No plugin changes or changefile are needed
for this plan-only amendment.

`.devcontainer/test-isolation/` is a hardened ephemeral test-execution boundary,
**not** this trusted base. Do not weaken it, inherit its disposable-home policy,
or treat the existing CI-repair template as a seasoned full-harness image.

## Proposed interoperable build interface

**Agent-recommended contract, not an existing API:**

Inputs are a local approved source snapshot and one versioned non-secret input
document. The builder receives explicit paths; it does not fetch arbitrary
repositories or interpret remote shell/config payloads.

| Input | Required meaning |
|-------|------------------|
| Source | Exact approved repository revision and build-context digest; no local credentials, homes or unrelated files |
| Target | Explicit supported Linux architecture/platform, non-root runtime user and stable home/workspace mount targets |
| Dependency selection | Immutable base/tool image digests; exact Copilot, Node/npm, Python/uv and OS package selections with integrity and admissibility evidence |
| Age evidence | Release/upload timestamps, source/feed attribution and evaluation instant proving the seven-day minimum for the external closure |
| Runtime capability set | Declared SSH, tmux, CLI, installer and supported service/timer capabilities; no private harness, inventory, model choice or credential configuration |
| Output | Caller-selected local image reference and artifact directory; optional publication only through a separately approved publisher |

Outputs: a local OCI image/image archive, immutable image/config digest, resolved
dependency inventory and sanitized build receipt recording source, platform,
input digest and actual validation outcomes. Mutable tags are conveniences, not
deployment identity. A receipt proves observed build facts, not publisher trust,
signing authority, deployment consent or a release signature. Use the existing
release/publisher contract if consuming deployment needs those proofs.

Proposed invocation, from repository root:

```text
python .devcontainer/trusted-harness-base/build.py --inputs <approved-inputs.json> --output <artifact-directory>
```

Both Gateway-host and future CI builders consume that same schema and emit the
same outputs. The first builder can use local OCI artifacts without a hosted CI
requirement or selecting a registry. A deployment owner must explicitly agree
how Komodo consumes an artifact on another host; do not assume a local tag is
remotely pullable or choose a registry by implication.

### Dependencies and build authentication

Apply [external dependency seasoning](../../../docs/install-contract.md#external-dependency-seasoning)
to build and runtime dependencies: at least 604800 seconds old, exact integrity
and configured-feed availability, including transitive selections. No blind
`latest`, unbounded npm install, mutable base tag or unknown-age artifact.
Reuse canonical `installer_dependency_policy.py` for supported Python installs
and the repository's dependency-age audit for declarations; the audit alone
does not prove subsequent installation admission or cover every image layer.

The existing CI-repair recipe accepts `COPILOT_VERSION` but does not establish
the complete locked, seasoned image contract. No concrete Copilot/npm, OS
snapshot or full-image lock is selected by this plan. Implementation must locate
admissible existing pins/helpers and prove their exact artifact age/integrity,
or report missing release evidence as a blocker before installation. Do not
invent a lock or weaken seasoning to make the build pass.

No interactive login, auth pump, secret build argument, token-bearing build
secret or copied credential cache enters a build. Approved source/dependency
bytes must be available without runtime operator authentication; unsupported
private inputs are outside this generic recipe.

## Persistence, updates and runtime placement

Each logical container owns persistent **whole-home** and workspace volumes,
including Copilot sessions, plugin payloads/runtimes/data, repositories and
worktrees. Its machine identity and SSH keys persist as well; generate identity
on first runtime admission, never bake shared keys into an image. Initial home
seeding is non-destructive and repeatable: a mounted existing home is not
replaced or masked by stale image defaults.

Prefer image-owned tools and independently supported `services/*` code/systemd
units under `/opt` or `/var`; config and persistent service data belong in the
declared persistent home. Existing plugin installers that retain runtime code
in home remain supported. Do not force an unimplemented code/data installer
split. Explicitly document which components can use image paths today and
which keep their existing installer layout.

Image replacement and persistent-home plugin/runtime updates are separate
generations. Record effective versions, test forward updates with an old home,
preserve sessions/config/data, and use existing installers rather than copy
source over active runtimes. Do not downgrade a newer home-installed runtime
because an older image is selected. Rollback requires actual data compatibility,
not just switching the image digest.

Real timer/service capability requires a supported container init, cgroup and
supervision contract. Document host requirements and explicit unsupported
capabilities; do not claim systemd works in every container, expose the manager
socket, or add blanket privilege as a shortcut. Trusted is not unrestricted host
filesystem or lifecycle authority.

## Phased plan and gates

1. **Reviewed contract — image participant; campaign owner integrates.**
   - [ ] Merge this bounded plan and confirm explicit implementation authorization.
   - [ ] Agree build inputs/outputs and exact image reservation with adjacent
     source owners; disagreements remain blocking for the affected seam only.
2. **Standalone image — #8352 image participant.**
   - [ ] Implement only the reserved recipe/schema/build adapter/docs/tests.
   - [ ] Prove secret-free build, dependency admissibility, fresh-home startup,
     existing-home preservation, real tooling and supported init/SSH/tmux behavior.
   - [ ] Record missing release or platform evidence explicitly. An offline
     contract pass alone does not make an image ready.
3. **Builder and deployment integration — existing controller/deployment owners.**
   - [ ] Gateway builds approved source updates using the reviewed image contract;
     fleet-manager owns desired source/config/package selection and prepared state.
   - [ ] Selected Komodo on the Gateway service host is the sole deployment/update
     writer. Building is not activation; candidate health, drain, accepted work,
     persistent storage and compatibility checks precede replacement.
   - [ ] Future CI replaces only the builder using identical inputs/outputs;
     publisher/signing and any registry require their existing approval/owners.
4. **Remote trusted venue — existing adapter/session/auth owners.**
   - [ ] Extend agent-containers without adding a second lifecycle store: bind
     logical machine/container identity, current manager instance/generation,
     lease/assignment revision and verified SSH endpoint/host key before acting.
     Stale/reassigned instances refuse launch, attach and auth delivery.
   - [ ] Preserve trusted-only admission, concrete cwd, bridge session/managed
     worktree binding, reconnect and current lease validation. Restricted venues
     never inherit this projection. Discovery/session management is not
     permission for adapter-driven create/remove/redeploy.
   - [ ] Keep existing login-session auth producers and relay/forward custody.
     Any one currently valid MFA-approved refreshing signed-in source sustains
     the auth pump; no initiating workstation or terminal must remain attached.
     All-source signout stops renewal, credentials expire under existing
     lifetimes, and auth-dependent operations fail visibly while state survives.
     No new issuer, service identity, indefinite cached token or offline bypass.
5. **Real journey — campaign integration owner with existing Picker/parity owners.**
   - [ ] Discover an admitted trusted remote container in Picker; select an
     actual container-local managed worktree and launch interactive Copilot in
     tmux with its intended plugins and exact workspace cwd.
   - [ ] Detach, close the initiating client, reconnect and attach to the same
     live tmux/session identity. Also dispatch headlessly through agent-bridge
     and inspect actual session registration and worktree/lease binding.
   - [ ] Recreate through Komodo after safe drain, retain home/workspace/keys,
     and rediscover the new instance without accepting an old-instance handle.
     Reattach to surviving sessions only where genuinely supported; do not
     promise process survival across container replacement.

Each phase's owner must accept its interface/file appointment before source
work; this plan does not record such acceptance. #8352 completion is the tested
base-image deliverable, not closure of #6030 or the other dependency issues.
Integration remains open until the actual journey and dependent proofs pass.

## Validation plan

The following implementation details are **agent-recommended**:

- [ ] Offline unit/contract: schema rejects unknown/unsafe inputs and missing
  age/integrity evidence; no credentials or consumer harness embedded; build
  outputs retain exact source/input identity without claiming deployment.
- [ ] Clean-room build/start: real approved dependency resolution and no login;
  use a fresh home/workspace and independently inspect image layers, history,
  environment and logs for secret-free construction. Exercise supported tools,
  actual SSH, tmux and timer/service execution rather than file-existence checks.
- [ ] Persistence/update: sentinel files plus real Copilot session/plugin state,
  repository worktrees and SSH identity survive restart/recreation and
  home-runtime forward update; failed candidates preserve prior data.
- [ ] Auth lifecycle: two admitted refreshing sources, either sustaining alone,
  initiating-client disconnect, one-source signout, all-source signout,
  eventual expiry/refusal and explicit reauthentication recovery. Never print
  credentials or misreport an unauthenticated process as healthy.
- [ ] Remote binding: wrong/stale lease, endpoint/key mismatch, recreation and
  reassignment refuse stale control/auth; current instance works through the
  existing adapter without a competing lifecycle writer.
- [ ] Deployment: prove Gateway-first build and Komodo-only activation/update,
  changed input invalidation, interrupted/unknown effect reconciliation, safe
  drain and failed-image preservation. No direct Docker fallback on outage.
- [ ] Native journey: execute Phase 5 with real Picker/tmux, interactive and
  headless paths; inspect actual identity/cwd/plugin/session outcomes.
- [ ] Record unit, simple integration, clean-room and available live-venue
  evidence separately. Missing approved infrastructure, artifacts or auth
  remains an explicit blocked lane, never a synthetic pass.

### Documentation validation

For this plan-only delta, from repository root:

```text
python tools/check-effort-vision-structure.py efforts/active/linux-role-fleet/README.md
git diff --check
```

Also check all relative Markdown links in this child and its parent integration
anchors. No build, package installation or live provider test belongs to this
planning stage. No behavior/as-is docs or visions require changes yet.
Future implementation uses focused offline tests first; dependency declarations
also run `python tools/check-dependency-age.py`. Image builds, clean-room/native
and live validations run only after the execution and venue gates above.

## Journal

- 2026-10-23: Drafted the #8352 bounded image plan within #6030. Source
  reservations and build interface are proposed; no implementation, build,
  deployment, new credential producer or adjacent-owner transfer is claimed.
