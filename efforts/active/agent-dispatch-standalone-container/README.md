# agent-dispatch: standalone (non-plugin) package + container image

- **Slug:** `agent-dispatch-standalone-container`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice PRs against `dev`
- **Created:** 2026-10-11
- **Status:** Draft
- **Vision:** [machine-fleet](../../../visions/machine-fleet/README.md):
  `standalone-service-installation`, role deployments and container managers
  (role instances may run directly or in containers on explicitly admitted
  hosts); extends the `out-of-plugin-delivery` behavior the
  [installer vision](../../../visions/installer/README.md) already names for
  `worktree-manager`.
- **Umbrella issue:** [#6177](https://github.com/ThomasMichon/copilot-extensions/issues/6177)
- **Sub-issues:** None yet

## Guiding Intent

Give `agent-dispatch`'s coordinator daemon the same out-of-plugin distribution
shape `worktree-manager` already proves for `agent-worktrees`: installable and
runnable with **zero Copilot/marketplace involvement** (no `copilot plugin
install`, no session hooks), plus a reusable container image so the
coordinator can run headless on a fleet/CDE host as a first-class, reusable
daemon service. This is the first of what should become a repeatable pattern
for other agent-* daemon services; it deliberately starts with exactly one
service (agent-dispatch) rather than generalizing a framework up front.

This is explicitly **not** a rewrite of agent-dispatch's coordinator lifecycle.
`plugins/agent-dispatch/scripts/install.{sh,ps1}` already implements a mature,
well-tested versioned-venv + binstub + service-unit installer (systemd user
unit on Linux, Scheduled Task on Windows, graceful zdd cutover, WSL
coordinator-vs-client detection, embody-supervisor). This effort adds two
things beside it, reusing its runtime mechanics rather than duplicating them:

1. A **Copilot-independent fetch/bootstrap entrypoint** (`bootstrap.sh` /
   `bootstrap.ps1`, mirroring `worktree-manager/bootstrap.{sh,ps1}` byte-for-
   byte in shape) so a bare machine with no Copilot CLI and no marketplace
   registration can still install and run the coordinator.
2. A **container image** that runs the coordinator as PID 1
   (`python -m agent_dispatch serve`), reading the exact same environment
   variables the systemd `EnvironmentFile=` already documents
   (`AGENT_DISPATCH_HOST`/`PORT`/`DB`/`TOKEN`/`CONTROL_TOKEN`/
   `ENFORCE_REGISTERED_REPOS`) -- no systemd, no Scheduled Task, no supervisor
   unit inside the container; Docker (or the fleet's chosen container manager)
   owns restart/health instead.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Campaign owner | Planning, package/container packaging, validation, PR stewardship | Managed upstream worktree |

## Coordination

- **Topology:** Independent per-slice PRs against `dev`.
- **Host (owns PRs):** Campaign owner.
- **Delegates:** None assigned; do not give two actors the same slice.
- **Handoff:** Resume the first unresolved Plan item from this canonical README.
- **Adjacent ownership:** [agent-index standalone service](../agent-index-standalone-service/README.md)
  (#5768) owns the analogous standalone-package + version-slot-installer pattern
  for agent-index, and its own Phase 3 has an **open, undecided** "generic
  Dockerfile" item. This effort does not block on or preempt that decision --
  it proves the pattern independently on agent-dispatch (a materially simpler
  service: one coordinator process, no embedding engine, no durable corpus,
  no model cache) and either package can later adopt whichever shared
  convention proves out first, without either becoming a hard dependency of
  the other.
  [machine-fleet-routing-foundation](../machine-fleet-routing-foundation/README.md)
  (#5789) separately owns the Gateway-routed controller/connector runtime and
  typed service adapters (`vault-v1`, `index-v1`); this effort supplies a
  deployable artifact (agent-dispatch's own container image) that a future
  fleet role could place, but does not implement fleet routing, a connector,
  or a credential adapter itself.
- **Downstream boundary:** Actual fleet placement, CDE/container-host
  admission, and any private deployment's specific credential/network wiring
  are downstream, adopter-owned concerns (see `visions/machine-fleet`'s
  "Role deployments and container managers" -- "Adopter-specific provisioning
  remains outside the portable fleet core"). This effort ships a reusable
  package and image; it does not deploy either anywhere.

## Context

`tools/standalone_consumers.py` currently names the only two packages that
participate in standalone repository releases: `worktree-manager` and
`agent-index-service`. No existing effort or package addresses agent-dispatch.

`worktree-manager/bootstrap.sh` (and its `.ps1` twin) is the proven template:
it auto-provisions `uv` (user-local, no admin), falls back to a GitHub tarball
fetch when `git` is unavailable, version-installs the fetched payload via the
package's own `self-install --apply`, and execs the freshly-installed CLI --
all without any Copilot plugin or marketplace step. `worktree-manager`'s
`pyproject.toml` also shows the established convention for a standalone
package's sibling-lib dependencies (`[tool.uv.sources]` editable `path =
"../libs/..."` entries on `dev`, materialized to real local copies only for a
`main`-branch release build via `tools/materialize_main.py`).

`plugins/agent-dispatch/scripts/install.sh` already fully implements the
coordinator's installed lifecycle (versioned venv activation/gc, binstub,
systemd user unit with graceful zdd cutover, WSL detection, label-gated embody
supervisor). None of that needs to be reinvented; a standalone, non-plugin
package should installer-wrap the *same* mechanics (or import and drive the
coordinator module directly), not fork them. A container image needs even
less: no systemd/Scheduled Task machinery at all, since the container runtime
itself supplies process supervision -- the image installs the venv, then execs
`python -m agent_dispatch serve` as PID 1, reading the same documented
`AGENT_DISPATCH_*` environment variables the systemd `EnvironmentFile`
otherwise supplies.

Scope was deliberately narrowed to agent-dispatch only (not agent-worktrees)
after investigation found agent-worktrees' own "daemon" pieces
(`mux_daemon.py`, the resident status-monitor) are primarily about managing
*local* terminal panes and worktree state on an operator's own dev box, not
an obviously fleet-reusable headless service; its containerizable boundary
needs separate design and is out of scope here.

## Request

Operator, paraphrased (2026-10-10 session): build dedicated, reusable
containerizable installers for agent-* daemon services, starting with
agent-dispatch and agent-worktrees. After investigation surfaced that
agent-worktrees' containerizable boundary is unclear while agent-dispatch
already has a clean, headless, systemd-unit-based coordinator service, the
operator confirmed: start with agent-dispatch only, and write this effort doc
for review before any implementation.

## Plan

### Phase 0 -- Drafting and review
- [x] Capture the literal request, confirmed scope (agent-dispatch only, with
  agent-worktrees deliberately deferred), and existing-pattern survey
  (`worktree-manager`, `agent-index-standalone-service`, `tools/
  standalone_consumers.py`).
- [x] Open the umbrella issue (#6177).
- [ ] Review this draft with the operator before implementation starts.

### Phase 1 -- Standalone (non-plugin) package
- [ ] Decide the package name/location (e.g. a new top-level
  `agent-dispatch-manager/` sibling to `worktree-manager/`, or another name
  that avoids confusion with the existing `plugins/agent-dispatch` plugin
  wrapper) and register it in `tools/standalone_consumers.py`.
- [ ] Build `bootstrap.sh`/`bootstrap.ps1` mirroring `worktree-manager`'s shape
  exactly (uv auto-provision, git-or-tarball fetch, versioned self-install,
  exec passthrough).
- [ ] Reuse (not fork) the existing coordinator lifecycle from
  `plugins/agent-dispatch`: either vendor-point at its installer mechanics or
  factor the shared venv/binstub/service-unit logic so both the plugin-
  delivered and standalone paths call the same code.
- [ ] Preserve exact runtime compatibility: the same `~/.agent-dispatch` DB/
  config layout, the same `AGENT_DISPATCH_*` environment contract, so a host
  can move between plugin-delivered and standalone installation without data
  migration.

### Phase 2 -- Container image
- [ ] Add a `Dockerfile` (standalone package, or a sibling `docker/` directory)
  that builds the coordinator's venv from the same sources the standalone
  package installs, with no systemd/Scheduled Task layer -- the container
  entrypoint execs `python -m agent_dispatch serve` directly.
- [ ] Document the exact environment-variable contract the image reads
  (`AGENT_DISPATCH_HOST`/`PORT`/`DB`/`TOKEN`/`CONTROL_TOKEN`/
  `ENFORCE_REGISTERED_REPOS`), a volume mount for the durable task DB, and
  expected restart/health behavior left to the container runtime.
- [ ] Prove a clean build + run + restart-persistence cycle on a throwaway
  container host (mirroring the validation rigor the parallel
  `harness-gateway-fleet` dotfiles-private effort already applied to its own
  Gateway container: build, run, push/read persistence across `docker
  restart`, teardown).

### Phase 3 -- Validation and documentation
- [ ] Prove plugin-delivered and standalone installations are interoperable:
  a coordinator installed one way is indistinguishable (DB format, wire
  protocol, CLI) from one installed the other way.
- [ ] Document the reusable pattern (package layout, bootstrap shape,
  container conventions) so a later agent-worktrees or other agent-*
  containerization effort can follow it without rediscovering these decisions.
- [ ] Mark Done only after every Plan and Validation Plan item is resolved or
  explicitly transferred to a named follow-on objective.

## Validation Plan

- [ ] Standalone bootstrap: fresh-machine install (no Copilot, no marketplace,
  no `git`/`uv` preinstalled) succeeds via `bootstrap.sh`/`bootstrap.ps1`;
  re-running is idempotent/version-gated like `worktree-manager`'s.
- [ ] Container image: clean build, run, and a real `docker restart` with DB
  persistence verified (not merely "the process starts").
- [ ] Plugin-delivered and standalone coordinators interoperate: same DB
  schema/location contract, same CLI/API behavior, no silent divergence.
- [ ] Existing plugin-delivered install/update/status/start/stop/uninstall
  flows remain unaffected; this effort adds a parallel path, not a
  replacement.
- [ ] Touched-code lint, relevant suites, changefiles, documentation-impact
  review, actual automated review and required CI before each merge.

## Proposal

_Pending._

## Journal

### 2026-10-11 -- Kickoff
- Captured the operator's request and the investigation that narrowed scope
  to agent-dispatch only (confirmed via `ask_user`), deferring agent-worktrees
  pending clearer design of its own containerizable boundary.
- Surveyed existing precedent: `worktree-manager` (standalone twin of
  agent-worktrees, with `bootstrap.{sh,ps1}`), the actively in-flight
  `agent-index-standalone-service` effort (#5768, same-day commits; its own
  Phase 3 Dockerfile decision is open and not blocking this effort), and
  `tools/standalone_consumers.py` (the authoritative registry this effort
  will extend). Confirmed via GitHub issue/PR search that no existing effort
  or issue covers agent-dispatch standalone/container packaging.
- Opened umbrella issue #6177. No code changed in this slice; Phase 1
  implementation has not started pending review of this draft.
