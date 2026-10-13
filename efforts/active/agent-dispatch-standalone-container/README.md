# agent-dispatch: standalone (non-plugin) package + container image

- **Slug:** `agent-dispatch-standalone-container`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice PRs against `dev`
- **Created:** 2026-10-11
- **Status:** Active; Phase 1 + Phase 2 landed; release-promotion regression
  fixed; Phase 3 interoperability proof done; Phase 4 (plugin consumes
  upstream installer on demand) implemented -- fresh-machine bootstrap
  validation, the plugin-delivered-flows-unaffected check, a live
  on-demand-fetch exercise, and dropping the fallback installer copy all
  remain open
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
- [ ] Validate the on-demand fetch path for real (a live first-use
  provisioning run against the actual `raw.githubusercontent.com` URL, not
  just static/syntax checks) and confirm the offline fallback still
  activates correctly when the fetch is unreachable.
- [ ] Once validated, remove `install.ps1`/`install.sh` from the plugin's
  own payload entirely (thin trigger only) -- the actual "drop the
  fallback" step named above.
- [ ] **Naming/layout note (2026-10-12):** `agent-dispatch-service/` is
  slated to move to `services/dispatch/` under the new
  `docs/patterns/standalone-service-layout.md` convention (`agent-<topic>`
  reserved for plugins; standalone packages live at `services/<topic>`).
  Tracked separately in
  [#6419](https://github.com/ThomasMichon/copilot-extensions/issues/6419),
  which also covers updating the hardcoded upstream-installer URL this
  phase just added. Do not hardcode further references to the current
  `agent-dispatch-service/` path without checking that issue first.

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
  Phase 4 and the Journal). A real (non-mocked) end-to-end exercise of the
  network fetch path remains open -- see Phase 4.
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
