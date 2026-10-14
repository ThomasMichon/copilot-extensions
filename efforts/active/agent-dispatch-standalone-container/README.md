# agent-dispatch: standalone (non-plugin) package + container image

- **Slug:** `agent-dispatch-standalone-container`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice PRs against `dev`
- **Created:** 2026-10-11
- **Status:** Active; Phase 1 + Phase 2 landed; release-promotion regression
  fixed; Phase 3 interoperability proof done; Phase 4 (plugin consumes
  upstream installer on demand) implemented; package relocated to
  `services/dispatch/` under the `standalone-service-layout` convention;
  public-install-surface indirection landed and closed #6419; the on-demand
  fetch path (both the direct upstream fetch and the new proxy indirection
  on top of it) is now validated for real via clean-room network fetches --
  dropping the plugin's own offline-fallback installer copy remains open
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
- [x] Decide the package name/location: new top-level `agent-dispatch-service/`
  (mirroring `agent-index-service`'s `<name>-service` naming, since both are
  headless daemon services, not TUI/manager tools like `worktree-manager`).
  Registered in `tools/standalone_consumers.py`.
- [x] Build `bootstrap.sh`/`bootstrap.ps1` mirroring `worktree-manager`'s fetch
  shape (git-or-tarball fetch, no Copilot/marketplace dependency) -- simpler
  than worktree-manager's own Python `self-install --apply` step, since
  agent-dispatch's lifecycle is already a mature bash/PowerShell installer:
  the bootstrap scripts fetch the repo, then `exec` straight into the
  existing `plugins/agent-dispatch/scripts/install.{sh,ps1}`.
- [x] Reuse (not fork) the existing coordinator lifecycle from
  `plugins/agent-dispatch`: `agent-dispatch-service` depends on the
  `agent-dispatch` package itself (`[tool.uv.sources]` editable path on
  `dev`, same convention as every other standalone/plugin pointer) and the
  bootstrap scripts exec its unmodified `install.{sh,ps1}` directly -- zero
  lines of installer logic duplicated.
- [x] Preserve exact runtime compatibility: since the standalone path calls
  the identical `install.sh`/`install.ps1`, the `~/.agent-dispatch` DB/config
  layout and `AGENT_DISPATCH_*` environment contract are unchanged by
  construction -- there is no second implementation to drift.

### Phase 2 -- Container image
- [x] Add a `Dockerfile` (in `agent-dispatch-service/`, built from the
  repository root so it can reach the handful of shared `libs/*` its
  dependency chain needs in their real relative positions) that builds the
  coordinator's venv from the same sources the standalone package installs,
  with no systemd/Scheduled Task layer -- the container entrypoint execs
  `python -m agent_dispatch serve` directly, with self-update disabled
  (`AGENT_DISPATCH_SELF_UPDATE=0` -- an image rebuild+redeploy is how this
  service updates in a container, not an in-place self-update).
- [x] Document the exact environment-variable contract the image reads
  (`AGENT_DISPATCH_HOST`/`PORT`/`DB`/`TOKEN`/`CONTROL_TOKEN`/
  `ENFORCE_REGISTERED_REPOS`), a volume mount for the durable task DB, and
  expected restart/health behavior left to the container runtime. The image
  deliberately does **not** default `AGENT_DISPATCH_HOST=0.0.0.0` -- the
  coordinator itself refuses an unauthenticated LAN bind, and reaching it
  from another container requires explicitly pairing `AGENT_DISPATCH_HOST=
  0.0.0.0` with `AGENT_DISPATCH_TOKEN`, documented in the package README.
- [x] Prove a clean build + run + restart-persistence cycle on a throwaway
  container host (mirroring the validation rigor the parallel
  `harness-gateway-fleet` dotfiles-private effort already applied to its own
  Gateway container: build, run, push/read persistence across `docker
  restart`, teardown). Done for real locally: built the image, ran it with
  an explicit bearer token, created a real task via the `agent-dispatch` CLI
  (`docker exec`), `docker restart`ed the container, and confirmed the same
  task was still present afterward -- the task database under the named
  volume survives a container recreate. Also found and fixed a real safety
  gap live: the coordinator's own guard correctly refused to start when the
  image's first draft hardcoded `AGENT_DISPATCH_HOST=0.0.0.0` with no token
  ("the task-control API must not be exposed on the LAN unauthenticated") --
  removed that unsafe default rather than working around the guard.

### Phase 3 -- Validation and documentation
- [x] Prove plugin-delivered and standalone installations are interoperable:
  a coordinator installed one way is indistinguishable (DB format, wire
  protocol, CLI) from one installed the other way -- done 2026-10-12, see
  the Journal entry below for the isolated cross-version CLI round-trip
  (scratch `0.1.2.dev205` venv <-> plugin-delivered `0.16.7-dev1`) that
  proves it.
- [x] Document the reusable pattern (package layout, bootstrap shape,
  container conventions) in `agent-dispatch-service/README.md` so a later
  agent-worktrees or other agent-* containerization effort can follow it
  without rediscovering these decisions (repo-root build context for
  reaching shared `libs/*`, `--no-editable` to keep the final image
  self-contained, the safe-default-bind + explicit-opt-in-with-token
  pattern, and the public-PyPI-default-with-build-arg-override pattern for
  managed-network package-index policy).
- [ ] Mark Done only after every Plan and Validation Plan item is resolved or
  explicitly transferred to a named follow-on objective.

### Phase 4 -- Single installer source of truth (plugin consumes upstream)
- [x] Flip the plugin's first-use (self-)provisioning flow to fetch and run
  the UPSTREAM installer (`agent-dispatch-service/bootstrap.{sh,ps1}` on
  `main`) rather than relying on the plugin's own already-staged payload
  snapshot -- done 2026-10-12. The locally stamped snapshot remains a
  fallback for offline/network-restricted hosts
  (`AGENT_DISPATCH_PROVISION_OFFLINE=1` skips straight to it) during this
  transition; per operator direction it is expected to be dropped once the
  on-demand path is validated in the wild, at which point `install.ps1`/
  `install.sh` would no longer need to ship inside the plugin's own
  marketplace payload at all. Scoped to agent-dispatch only for now, with
  an explicit expectation this becomes a reusable pattern other plugins
  adopt later (a separate, larger effort).
- [x] Validate the on-demand fetch path for real (a live first-use
  provisioning run against the actual `raw.githubusercontent.com` URL, not
  just static/syntax checks) and confirm the offline fallback still
  activates correctly when the fetch is unreachable -- done 2026-10-13, see
  Journal (both `bash`/`PowerShell`, real network fetch, `dev` ref since
  `main` is not yet promoted past the pre-migration layout).
- [ ] Once validated, remove `install.ps1`/`install.sh` from the plugin's
  own payload entirely (thin trigger only) -- the actual "drop the
  fallback" step named above. Still open: today's validation proved the
  fetch+delegate mechanism works, not that it's safe to remove the offline
  fallback (that needs its own confirmation that offline/network-
  restricted hosts are rare enough in practice, or an explicit operator
  call to accept that tradeoff).
- [x] **Naming/layout note (2026-10-12):** `agent-dispatch-service/` moved to
  `services/dispatch/` under `docs/patterns/standalone-service-layout.md`'s
  convention (`agent-<topic>` reserved for plugins; standalone packages live
  at `services/<topic>`), including the hardcoded upstream-installer URL
  this phase added (now `.../main/services/dispatch/bootstrap.{ps1,sh}`).
  Distribution name, console-script name, and Python package name are
  unchanged -- only the top-level directory moved. See the Journal entry
  below for the full reference sweep. [#6419](https://github.com/ThomasMichon/copilot-extensions/issues/6419)
  tracks the still-open public-install-surface indirection (so a future
  move doesn't require touching this hardcoded URL again).
- [x] **Public-install-surface indirection landed 2026-10-13** (PR #6559,
  closes #6419): added the `install/dispatch/bootstrap.{ps1,sh}` stable
  proxy (resolves+execs the current real `services/dispatch/bootstrap.
  {ps1,sh}`, local-sibling-first then network fetch) and repointed the
  plugin's upstream-installer URL at it. See Journal for the design
  (dedicated `install/` proxy dir, chosen over a unified dispatcher command)
  and the same-day clean-room validation.

## Validation Plan

- [ ] Standalone bootstrap: fresh-machine install (no Copilot, no marketplace,
  no `git`/`uv` preinstalled) succeeds via `bootstrap.sh`/`bootstrap.ps1`;
  re-running is idempotent/version-gated like `worktree-manager`'s.
- [x] Container image: clean build, run, and a real `docker restart` with DB
  persistence verified (not merely "the process starts") -- done locally
  2026-10-11 (see Phase 2 above and the Journal entry below for the exact
  commands and outcomes).
- [x] Plugin-delivered and standalone coordinators interoperate: same DB
  schema/location contract, same CLI/API behavior, no silent divergence --
  done 2026-10-12 (isolated real coordinator + cross-version CLI
  round-trip; see Journal).
- [x] Plugin's first-use provisioning fetches and runs the upstream
  installer (agent-dispatch-service/bootstrap.{sh,ps1}) on demand instead
  of using its own staged payload copy, with the staged copy retained only
  as an offline fallback during the transition -- done 2026-10-12 (see
  Phase 4 and the Journal). The real (non-mocked) end-to-end exercise of
  the network fetch path (and, as of 2026-10-13, the
  `install/dispatch/bootstrap.{ps1,sh}` stable-proxy indirection on top of
  it) is now done -- see the 2026-10-13 Journal entries.
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

### 2026-10-11 -- Phase 1: standalone package landed
- Operator reviewed and approved the draft (merged via #6180); proceeded with
  implementation in the same session.
- Also found a third sibling pattern landed since the draft: `fleet-manager`
  (PR #6133, merged), confirming top-level-directory + `STANDALONE_CONSUMERS`
  is the repo's settled convention for a non-plugin distribution, not just a
  worktree-manager/agent-index-service pair.
- Built `agent-dispatch-service/`: `bootstrap.sh`/`bootstrap.ps1` (fetch +
  exec straight into the existing, unmodified `plugins/agent-dispatch`
  installer -- no new install logic), a thin `pyproject.toml` depending on
  `agent-dispatch` via the standard editable `[tool.uv.sources]` path
  pointer, a marker package, and tests (bootstrap script shape/executable-bit
  guards + a package import smoke test). Registered in
  `tools/standalone_consumers.py` and wired a dedicated
  `agent-dispatch-service (out-of-plugin)` CI job mirroring
  `worktree-manager`'s, added to the required `PR gate` job's `needs:` list.
- Validated locally end-to-end before opening a PR: `uv run --extra dev
  pytest -q` (the exact CI invocation) passes 5/5; `python tools/run-
  standalone-tests.py agent-dispatch-service --prepare` (the registered-
  component test harness) also passes, confirming the nested
  `[tool.uv.sources]` path resolution (agent-dispatch-service ->
  `../plugins/agent-dispatch` -> its own further `../../libs/*` pointers)
  works through both `uv pip install` and `uv run` invocation shapes; `ruff
  check --select F,E9` (the exact CI selector) and
  `check-version-consistency.py`/`check-changefile-presence.py` are clean.
- Phase 2 (container image) and Phase 3 (validation/documentation) remain
  open; not started in this slice.
- Hit and fixed a real CI gap after opening the PR: `check-changefile-
  presence.py` (which diffs the PR's actual base...head commits, not local
  uncommitted state) correctly failed on a missing changefile for the new
  `agent-dispatch-service` package; added one and pushed the fix. PR #6245
  merged after all checks (including the new out-of-plugin job) passed.

### 2026-10-11 -- Phase 2: container image landed
- Operator said "proceed"; continued directly into Phase 2 per the reviewed
  effort doc rather than re-confirming scope.
- Built `agent-dispatch-service/Dockerfile` (+ a repo-root `.dockerignore`):
  a two-stage build, repo-root context (to reach `plugins/agent-dispatch`
  and its `libs/*` dependency chain in their real relative positions),
  `uv pip install --no-editable` so the final stage is self-contained
  without needing to carry `/src` along, non-root `dispatch` user, a
  `VOLUME` for the durable task DB/rendezvous file, and
  `ENTRYPOINT ["python", "-m", "agent_dispatch", "serve"]` directly --
  no systemd/Scheduled Task layer, matching the effort's original intent.
- Hit and fixed two real bugs live during local validation (this machine has
  Docker Desktop with a real Linux engine):
  1. **Editable-install trap**: `agent-dispatch`'s `[tool.uv.sources]` marks
     it editable, so a plain `uv pip install <path>` left the final image's
     venv pointing back at `/src/plugins/agent-dispatch/src` -- which only
     exists in the discarded `builder` stage, so the shipped image failed
     at startup with `No module named agent_dispatch`. Fixed with
     `uv pip install --no-editable`, which bakes a real, self-contained
     install into the final stage instead of carrying `/src` along.
  2. **Unsafe default bind**: the first draft defaulted
     `AGENT_DISPATCH_HOST=0.0.0.0` in the image. The coordinator's own
     startup guard correctly refused to run ("the task-control API must not
     be exposed on the LAN unauthenticated"), exiting with code 2. Removed
     that default rather than working around the guard -- the image now
     keeps the coordinator's own safe loopback default, and reaching it from
     another container requires explicitly pairing
     `AGENT_DISPATCH_HOST=0.0.0.0` with `AGENT_DISPATCH_TOKEN`, documented
     in the README.
- Also hit a real environment constraint, not a Dockerfile bug: this is a
  Microsoft-managed developer machine with Central Feed Services PyPI
  enforcement, so the container's own (unconfigured) network egress to
  `files.pythonhosted.org` was network-blocked (`SSLV3_ALERT_HANDSHAKE_
  FAILURE`) -- the exact symptom the `working-on-managed-machines` skill
  documents for uv on an enforced box. Per that skill: never bake a
  Microsoft-internal feed URL into this **public** repository's committed
  default (external consumers couldn't reach it, and it doesn't belong in a
  personal OSS repo); instead added a `PYTHON_INDEX_URL` build `ARG`
  defaulting to public PyPI (the correct, portable committed default) and
  overrode it only for local validation via `--build-arg`, never committed.
- Full local validation performed for real (not simulated): clean image
  build; ran the container with an explicit bearer token and
  `AGENT_DISPATCH_HOST=0.0.0.0`/`AGENT_DISPATCH_PORT` pinned; confirmed
  `agent-dispatch health` (via `docker exec`) reports a real running
  coordinator (PID 1, correct bind/port, active slot); created a real task
  via `agent-dispatch create` and captured its ID; `docker restart`ed the
  container; confirmed via `agent-dispatch show <id>` that the identical
  task was still present with its original `created_at`/`id` -- the task
  database under the named volume survives a container recreate, the same
  persistence guarantee this effort's parent `harness-gateway-fleet`
  dotfiles-private effort already proved for its own Gateway container.
  Test container, volume, and image removed after validation.
- Added `tests/test_dockerfile_shape.py` (static content assertions -- no
  Docker dependency in ordinary CI): entrypoint shape, no hardcoded
  unauthenticated `0.0.0.0` default, self-update disabled, `--no-editable`
  present, public-PyPI-default build arg. The real build+run+restart
  validation above was performed locally, not wired into per-PR CI (Docker
  is not otherwise used in this repo's CI; documented as a manually
  validated gap, the same honest-gap convention the dotfiles-private
  `agent-index-container` README already uses for its own untested tiers).
- Documented the full container usage contract (build command, run
  examples, the safe-bind/token-pairing requirement, the dynamic-port
  default, the package-index build-arg override) in
  `agent-dispatch-service/README.md`.

### 2026-10-11 -- Release-promotion regression found and fixed
- Another session (umbrella #6177 comment 6115340519, via agent-bridge)
  reported that release promotion (`dev` -> `main`, run 38184375719) was
  **hard-refusing every release** -- including an unrelated already-merged
  PR -- because of `agent-dispatch-service`'s own `[tool.uv.sources]`
  pointer. `tools/materialize_main.py`'s uv-editable materializer only ever
  vendors an escaping reference that resolves to EXACTLY
  `canonical_root/libs/<lib>` (a deliberate containment check -- see its
  own module docstring); `agent-dispatch-service -> ../plugins/agent-dispatch`
  pointed at a PLUGIN, not a lib, which that materializer was never
  designed to handle, so it correctly `SKIP`-refused and
  `promote_release.py` treats any `SKIP` as a hard promotion-blocking error.
- Root-caused via the actual failing job log
  (`agent-dispatch references ../plugins/agent-dispatch ... which is not
  canonical_root/libs/agent-dispatch -- refusing`) rather than guessing.
- **Chose not to touch `tools/materialize_main.py`'s containment logic at
  all** -- it is a security-critical, heavily-reviewed shared promotion
  gate used by every plugin and standalone package, and "retaining strict
  path containment" was explicitly requested. Instead found that
  `agent-index-service` already has the IDENTICAL situation (its own
  dependency on the `agent-index` plugin, not a lib) and solves it a
  different way: its `pyproject.toml` carries a plain versioned dependency
  with **no** `[tool.uv.sources]` escape at all, and
  `tools/run-standalone-tests.py`'s `prepare()` supplies the local plugin
  path (plus its own transitive `libs/*`) as extra flat install sources,
  with a `--override` forcing resolution from the exact local checkout
  (working around `dev`'s frozen source-version floor).
- Mirrored that exact precedent for `agent-dispatch-service`: removed its
  `[tool.uv.sources]` entry, changed its dependency to a plain
  `agent-dispatch>=0.1.2.dev205`, added an `elif component ==
  "agent-dispatch-service"` branch to `run-standalone-tests.py`'s
  `prepare()` (sources list + override), switched its CI job from the
  simpler `uv run --extra dev pytest` shape (only valid for a package whose
  dependencies are all genuinely vendorable libs, like `worktree-manager`)
  to the same `run-standalone-tests.py --prepare` invocation
  `agent-index-service`'s own job uses, and updated the Dockerfile's
  install step to install `plugins/agent-dispatch` alongside
  `agent-dispatch-service` in one flat `uv pip install` call instead of
  relying on the now-removed `[tool.uv.sources]` pointer.
- Validated for real before landing, specifically because normal PR CI
  never ran `materialize_main.py` in the first place (that's exactly how
  this slipped through both #6245 and #6259 clean) -- this time ran the
  **actual promotion-time materializer** locally against a scratch
  destination (`python tools/materialize_main.py --dest <scratch>`):
  exit 0, 125 artifacts materialized, `agent-dispatch-service`'s own
  materialized `pyproject.toml` confirmed carrying the plain versioned
  dependency with no escaping pointer and no `SKIP` emitted for it. Also
  reran the local test harness (`run-standalone-tests.py --prepare`, 11/11
  pass), the existing `tools/test_run_standalone_tests.py` suite (28
  passed, 1 unrelated skip), and a full Docker rebuild + smoke run with the
  new flat-install Dockerfile step.
- #6277 (the fix above) merged into `dev`, reconciled with a separate,
  already-merged #6262 (a narrower materializer allowlist exception for the
  same symptom, landed first); confirmed no conflict and that both sets of
  tests (#6262's 82 new tests plus this change) pass together. Replied to
  the reporting session via agent-bridge confirming resolution.

### 2026-10-12 -- Phase 3: plugin-delivered/standalone interoperability proven
- Proved the "a coordinator installed one way is indistinguishable from one
  installed the other way" claim directly, with a real cross-version wire
  round-trip rather than by inspection alone: `bootstrap.ps1` already hands
  off to the identical, unmodified `plugins/agent-dispatch/scripts/install.ps1`
  every plugin-delivered host uses (confirmed by reading it), so the only
  thing that can differ between a "standalone" and a "plugin-delivered"
  agent-dispatch is which version of the same `agent_dispatch` package got
  installed -- proving interop across two *different* versions is a strictly
  stronger proof than proving it for one shared version.
- Built an isolated scratch venv (`uv venv` + `uv pip install -e
  plugins/agent-dispatch`) pinned at `agent-dispatch==0.1.2.dev205` (the
  floor `agent-dispatch-service`'s `pyproject.toml` depends on) alongside
  this machine's already-installed plugin-delivered CLI at `0.16.7-dev1`.
  Started a fully isolated coordinator from the scratch venv bound to
  `127.0.0.1:48765` with its own scratch DB file, `AGENT_DISPATCH_RUN_DIR`,
  and `AGENT_DISPATCH_ROUTING_DIR` -- this isolation is required: the CLI's
  `client_url()` resolution prefers the host-wide zdd routing table over any
  `AGENT_DISPATCH_DB`/`--url` the caller might expect to matter, so the
  **first** attempt (without an explicit `AGENT_DISPATCH_URL` override on
  the CLI calls) silently talked to this machine's real production
  coordinator instead of the scratch one -- caught immediately via the
  returned task's `version` field, and the accidentally-created production
  task was abandoned right away (`agent-dispatch abandon ... --permit`).
  Learning worth preserving: always pin `AGENT_DISPATCH_URL` explicitly
  (never rely on `AGENT_DISPATCH_DB`/host/port alone) when standing up a
  throwaway/test coordinator on a host that already runs a real one.
- With isolation corrected (explicit `AGENT_DISPATCH_URL` on every CLI
  call, `AGENT_DISPATCH_RUN_DIR`/`AGENT_DISPATCH_ROUTING_DIR` on the scratch
  coordinator only): created a task with the scratch venv's CLI
  (`0.1.2.dev205`); read it back byte-for-byte identical via the
  plugin-delivered CLI (`0.16.7-dev1`), including via `list --repo`; then
  read it back again with the scratch venv's CLI. Same DB row, same JSON
  shape, same status-transition error semantics (a `claim` without a valid
  worktree context and a premature `complete` both produced the identical,
  expected errors from either CLI). This demonstrates DB schema, wire
  protocol, and CLI behavior are install-path-independent by construction
  (both paths install the literal same package), not merely asserted.
  Scratch coordinator, venv, and DB removed after validation; verified via
  `netstat` that no listener remained on the scratch port.
- Checked off the corresponding Plan and Validation Plan items below. The
  remaining two Validation Plan items (standalone bootstrap fresh-machine
  install; existing plugin-delivered flows unaffected) are pre-existing,
  lower-priority gaps already called out in Phase 1/2 -- not newly
  discovered by this slice -- and are left open for a future pass.

### 2026-10-12 -- Phase 4: plugin consumes the upstream installer on demand
- Operator expectation (confirmed via `ask_user` before touching this
  heavily-engineered, race-condition-sensitive file): the plugin's
  deferred-setup (`stamp` -> first-use `provision`) flow should actually
  trigger the upstream installer -- downloading and running it on demand --
  instead of relying on the installer already bundled in the plugin's own
  marketplace payload. Confirmed specifics: keep `install.ps1`/`install.sh`
  in the payload only as a transition fallback (remove once validated);
  the network-at-first-use tradeoff is accepted; pin the fetch to `main`
  (matching `bootstrap.{sh,ps1}`'s own existing default); scope to
  agent-dispatch only, with an explicit expectation this generalizes to
  other plugins later.
- Implemented in both `Deploy-SelfProvisioningBinstub` (install.ps1) and
  `deploy_binstub` (install.sh): the generated self-provisioning binstub's
  first-use path now fetches `agent-dispatch-service/bootstrap.{ps1,sh}`
  from `raw.githubusercontent.com/.../main` into a temp file and runs it
  with `provision` (the bootstrap script already forwards args straight
  into `plugins/agent-dispatch/scripts/install.{ps1,sh}`, so this is
  byte-for-byte the same install path the standalone package's own
  bootstrap uses -- no new installer logic, just a different source for an
  existing one). Only on fetch/run failure (or when
  `AGENT_DISPATCH_PROVISION_OFFLINE=1` is set) does it fall back to the
  locally stamped snapshot's installer, the prior sole behavior.
  `AGENT_DISPATCH_NO_SELFPROVISION=1` still opts out of provisioning
  entirely, unchanged.
- Validated the generated binstub content directly rather than only the
  wrapping function: extracted the literal heredoc/here-doc payload from
  both `install.ps1` (`$ps1Content`) and `install.sh` (the `STUBEOF`
  block) and syntax-checked each in isolation --
  `[System.Management.Automation.Language.Parser]::ParseFile` for the
  PowerShell binstub (clean) and `bash -n` (via WSL, since this machine's
  `bash.exe` resolves to WSL rather than Git Bash -- `/mnt/c/...` paths
  needed) for the POSIX one (clean). Also parsed the full `install.ps1`
  file with the same PowerShell parser API to confirm no syntax damage to
  the surrounding 4000+-line file.
- Ran the plugin's installer-focused test files directly (the ones that
  could plausibly exercise this code path):
  `test_install_ps1_stamp_guard.py`, `test_install_sh_activation_guard.py`,
  `test_installer_engine_adoption.py`, `test_payload_invocation.py`,
  `test_install_ps1_activation_guard.py`, `test_install_ps1_build_snapshot.py`
  -- 86 passed, 13 skipped, 0 failed. (The stamp-guard suite stubs out
  `Deploy-SelfProvisioningBinstub` entirely for its own guard-ordering
  tests, so it exercises the surrounding lock/version logic, not this
  function's new body -- the parser-level check above is this change's
  direct verification.)
- Also ran the full plugin suite (`pytest -q`, no selector) to check for
  any broader regression: it surfaced pre-existing failures confined to
  `tests/test_coordinator.py`, a pure-Python coordinator test file with
  zero overlap with this diff (`git status --short` at the time showed
  only `README.md`, `scripts/install.ps1`, `scripts/install.sh` modified --
  no Python source touched), so they predate and are unrelated to this
  change. Did not chase them further; CI's own full-suite run is the
  authoritative check, same as every prior phase in this effort.
- Updated `plugins/agent-dispatch/README.md`'s installer-lifecycle
  paragraph and both functions' doc comments to describe the new primary/
  fallback order and the new `AGENT_DISPATCH_PROVISION_OFFLINE` escape
  hatch.
- Left open for a follow-up pass: a real (non-mocked) first-use run that
  actually exercises the live `raw.githubusercontent.com` fetch end-to-end
  (this validation stopped at syntax/static-content checks plus the
  existing automated suites, not a live network provisioning run), and the
  actual removal of the fallback installer copy from the plugin payload
  once that's done.

### 2026-10-12 -- services/<topic> migration: agent-dispatch-service -> services/dispatch
- Operator instruction, following directly from the `standalone-service-layout`
  pattern doc published earlier the same day: drive the actual rename now,
  "while nothing uses the updated locations" -- i.e. before any more
  references accumulate against the stuttering `agent-*-service` shape.
  Confirmed scope at pattern-doc time: `services/<topic>` (bare topic, no
  `agent-` prefix, no `-service` suffix); only the two `agent-*-service`
  packages move (`worktree-manager`/`fleet-manager` are a distinct,
  unscoped case, kept as top-level systems).
- Moved `agent-dispatch-service/` -> `services/dispatch/` (and, same pass,
  `agent-index-service/` -> `services/index/`, the owning effort being
  `agent-index-standalone-service`). Distribution names
  (`agent-dispatch-service`, `agent-index-service`), console-script entry
  points, and Python package/module names (`agent_dispatch_service`,
  `agent_index_service`) are all **unchanged** -- only the top-level
  directory moved. This was a deliberate scope limit: renaming the
  distribution/import names too would have multiplied the blast radius
  (uv lock entries, wheel filenames, pip install commands) for no benefit
  the directory move itself doesn't already deliver.
- **Depth-sensitivity was the dominant risk, not the moves themselves.**
  `agent-dispatch-service/` sat 1 path segment below repo root;
  `services/dispatch/` sits 2. Every hardcoded `Path(__file__).resolve()
  .parents[N]` computing a REPO-root reference (not a package-root
  reference -- those stayed correct, since `tests/` is still exactly 1
  level below the package root regardless of overall depth) needed `N+1`:
  found and fixed in `test_launch_scripts_tracked_executable.py` (agent-
  dispatch-service) and `test_release_distribution.py` (agent-index-
  service). The same class of bug hit a relative TOML path: the
  dormant `[tool.uv.sources]` allowlist exception in
  `tools/materialize_main.py` and its by-hand-synced twin in
  `worktree-manager/src/worktree_manager/_trusted_pointer_materializer.py`
  hardcoded `raw_path == "../plugins/agent-dispatch"` (correct from the
  OLD 1-level depth); fixed to `"../../plugins/agent-dispatch"` for the
  new depth, plus the parametrized test fixtures
  (`test_trusted_materializer_parity.py`) that assert against it.
- **The registry key doubling as both a path AND an implicit Python-module
  name was the second real risk.** `tools/check-version-consistency.py`'s
  `_standalone_version_violations` derived the expected `src/<module>/
  __init__.py` path via `component.replace("-", "_")` -- true by
  coincidence for `agent-index-service` (module `agent_index_service`) but
  nonsensical for a registry key containing a literal `/`
  (`services/index` -> `services/index` unchanged, not a valid module
  path). First fix attempt (prefer the package's own `pyproject.toml`
  `[project].name` field, hyphen-mapped) introduced a REAL regression for
  `worktree-manager`: its declared dist name is
  `copilot-extensions-worktree-manager`, but its actual package is
  `src/worktree_manager/` -- a genuine PyPI-name/import-name mismatch
  neither derivation alone handles. Caught by actually running
  `check-version-consistency.py` after the fix (not just the unit tests),
  which immediately surfaced a real violation it shouldn't have. Final fix
  tries every candidate (component-derived, then dist-name-derived) and
  picks whichever one's `src/<candidate>/__init__.py` actually exists on
  disk, falling back to the component-derived guess only when neither
  resolves -- robust to both naming shapes, not just the one this move
  exercised.
- **A third, independently-discovered depth bug: `services/index`'s own
  `setup.py` build hook.** `_build_runtime_resource.py` computed its
  canonical `libs/versioned-runtime/versioned_runtime.py` source via
  `project.parent / "libs" / ...` (correct at the old 1-level depth, wrong
  at the new 2-level one) -- not caught by the earlier `parents[N]`/
  `.parent` sweep because it used a single `.parent`, not `.parent.parent`
  or `parents[N]`, and only surfaced when the REAL
  `run-standalone-tests.py services/index --prepare --smoke` end-to-end
  build actually ran (a real `uv pip install` triggering the real
  setuptools build backend) -- unit tests alone, even the installer-
  focused ones, never exercise a real wheel build. Same class of bug in
  two of that package's OWN test fixtures
  (`test_distribution.py`, `test_runtime_resource_distribution.py`),
  caught by the same end-to-end run plus a repo-wide re-grep for every
  `.parent`/`.parent.parent` chain (not just `parents[N]`) across both
  moved trees. **Lesson generalized for future similar moves:** a
  `parents[N]` sweep alone is insufficient; also grep for `.parent`
  chains, and always run the REAL registered build/test entry point
  (`run-standalone-tests.py <component> --prepare`), not only unit tests,
  since only a real build exercises a package's own setup.py/build hooks.
- Updated, in one pass: `tools/standalone_consumers.py` (the registry
  itself), `tools/run-standalone-tests.py` (component-keyed source lists,
  the `--smoke` guard, and a Windows-specific bug this move's own registry
  value exposed: `tempfile.TemporaryDirectory(prefix=f"{component}-tests-")`
  misreads an embedded `/` as a path separator on Windows -- `WinError 3,
  cannot find the path specified` -- fixed by sanitizing the prefix
  separately from the real `REPO / component` path use, which needs the
  slash), `.github/workflows/ci.yml` and
  `validate-and-promote.yml` (the `run-standalone-tests.py` invocations and
  the `discover` job's path-filter list -- **deliberately left the CI job
  IDs themselves unchanged**, e.g. `agent-index-service-linux:`, since
  those are internal workflow identifiers describing the distribution,
  not a path, and renaming them would only cascade into every `needs:`
  reference for no behavioral benefit), `.dockerignore`, both Dockerfiles'
  self-referencing `COPY`/build-context paths, both packages' own
  `README.md`/`bootstrap.{sh,ps1}` (one-liner URLs, Docker build commands,
  relative doc links -- which also needed a depth fix), the
  `agent-dispatch` plugin's installer scripts and README (the Phase-4
  bootstrap URL), `docs/reference-index.md`, `docs/capabilities.md`, and
  `docs/patterns/standalone-service-layout.md`'s own Status section (now
  "migrated" instead of "tracked separately").
- Explicitly left unchanged (confirmed safe via the same component-by-
  component audit): distribution-name prose and the console-script name in
  both packages' own source (`__main__.py`, `composition.py`, `release.py`,
  `staging.py`, their tests); the OS-level launcher binstub name
  `agent-index-service(.ps1)` in `plugins/agent-index/scripts/cell-runtime.py`
  and the clean-room scenario (the actual installed command users type,
  unrelated to repo layout); historical prose in past Journal entries,
  `.changefiles/`, and unrelated efforts' own historical mentions (not
  rewriting history, consistent with not rewriting git log).
- Validated: full-repo grep swept clean of remaining `agent-dispatch-
  service/`/`agent-index-service/` (trailing-slash, i.e. directory-shaped)
  references outside historical/`.changefiles` content.
  `python tools/check-version-consistency.py`,
  `check-changefile-presence.py`, and `check-docs-consistency.py` all pass.
  Real, non-mocked end-to-end runs (not just unit tests) for both moved
  packages: `python tools/run-standalone-tests.py services/index --prepare
  --smoke` -- 371 passed, 2 skipped (a genuine `uv pip install` + real
  setuptools wheel build, the path that caught the `_build_runtime_
  resource.py` bug above); `services/dispatch`'s own 11 tests passed after
  preparing its environment the same way (worked around this machine's
  unrelated, pre-existing `fastapi>=0.143.0`/`<=0.142.2`-availability
  environment conflict with a local `--override`, confirmed unrelated to
  this change since it also blocks `plugins/agent-dispatch`'s own tests
  identically). A real `docker build -f services/dispatch/Dockerfile`
  reached its network-dependent `pip install uv` step (blocked by this
  managed machine's known CFS egress policy, the same pre-existing,
  already-documented Phase 2 limitation) -- confirmed the `COPY` path
  resolution specifically (the part this move could have broken) with a
  minimal smoke-test Dockerfile reusing the same `COPY` lines without the
  network-dependent step: all paths resolved. Re-ran the installer-focused
  test selection from the prior Journal entry after the move (still 86
  passed, 13 skipped, 0 failed, via the same `fastapi` override
  workaround) to confirm the depth-sensitivity fixes hold, plus
  `worktree-manager`'s `test_trusted_materializer_parity.py` (18/18) and
  the full `tools/` suite touched by this change (210 passed, 1 skipped,
  5 deselected -- the known pre-existing, unrelated
  `test_real_uv_rebuild_preserves_leased_directory_and_exclusion`
  symlink-rmtree flake, confirmed unrelated: it exercises `run-plugin-
  tests.py` with a synthetic `"alpha"` plugin name, nothing this change
  touched).

### 2026-10-13 -- Public-install-surface indirection (closes #6419)
- Operator asked to pick up the one deliberately-deferred item from the
  prior move: the public, durable install URL still pointed directly at
  `services/dispatch/bootstrap.{ps1,sh}`, so a future reorg would again
  force a hardcoded-URL update. `docs/patterns/standalone-service-layout.md`
  named two options without picking one; picked the dedicated top-level
  `install/` proxy directory over a unified `copilot-extensions install
  <name>` dispatcher, since it needs no other app installed first.
- Added `install/dispatch/bootstrap.{ps1,sh}`: a thin, stable entry point
  that resolves and execs the CURRENT real `services/dispatch/bootstrap.
  {ps1,sh}` -- a local sibling copy when run from a cloned checkout (tests,
  dev), else a live network fetch. Only this one proxy file's hardcoded
  real-path needs updating on a future `services/dispatch` move; the
  published URL (`.../main/install/dispatch/bootstrap.{ps1,sh}`) never
  changes again.
- Repointed `plugins/agent-dispatch/scripts/install.{ps1,sh}`'s self-
  provisioning binstub fetch at the new proxy URL. Added
  `services/dispatch/tests/test_install_proxy.py` (static shape + git-
  tracked-executable coverage, mirroring the existing `test_bootstrap_
  shape.py` philosophy -- no network/git-clone in the unit test itself).
- Landed as PR #6559 (merged same day); issue #6419 closed.
- **Same-day clean-room validation of the whole on-demand fetch chain**
  (closing out this effort's own open Phase-4/Validation-Plan item):
  real, non-mocked network fetches in disposable environments with zero
  local state, for BOTH the direct upstream-installer path and the new
  proxy indirection on top of it:
  - Bash: a fresh `ubuntu:24.04` Docker container (no local `~/.agent-
    dispatch*`, no prior clone) fetched `install/dispatch/bootstrap.sh`
    over HTTPS from `dev`, correctly detected no local sibling and fell
    through to a live `raw.githubusercontent.com` fetch of the real
    `services/dispatch/bootstrap.sh`, which cloned the full repo payload
    and delegated to the existing, already-mature
    `plugins/agent-dispatch/scripts/install.sh status` -- clean `[SKIP]`
    output, exit 0, the `status` action forwarded correctly end-to-end.
  - PowerShell: same chain, forced into the network-fetch branch (proxy
    copied to a sibling-free temp dir), `-Action status` forwarded
    correctly through the full clone + delegate chain, exit 0.
  - **Important finding, not a defect in this change:** `origin/main` has
    not yet been promoted past the PRE-migration layout -- it still has
    `agent-dispatch-service/`/`agent-index-service/`, not even
    `services/dispatch/` from the 2026-10-12 move, let alone today's
    `install/dispatch/` proxy. The binstub's hardcoded public URL always
    targets `main` by design (the stable release branch), so the live
    install flow will not reach today's code until the next `dev -> main`
    promotion runs (this repo's normal `workflow_dispatch`-gated release
    cadence, `tools/promote_release.py` -- not something to trigger
    unilaterally; operator confirmed "promotion should happen on its own,
    unless it's blocked"). Validated against `dev` explicitly
    (`AGENT_DISPATCH_SERVICE_REF=dev`) to prove correctness ahead of that
    promotion.
  - This satisfies the Validation Plan's "real (non-mocked) end-to-end
    exercise of the network fetch path" item. Dropping the plugin's own
    offline-fallback installer copy (the last open Phase-4 item) remains
    open -- that needs its own confirmation that offline/network-
    restricted hosts are rare enough to accept the tradeoff, not just proof
    the primary path works.

