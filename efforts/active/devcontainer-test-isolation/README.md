# Devcontainer Test Isolation

- **Slug:** `devcontainer-test-isolation`
- **Repo:** ThomasMichon/copilot-extensions
- **Branch(es):** TBD (one per phase)
- **Created:** 2026-10-02
- **Status:** Active (Phase 1 done; Phase 2 in progress -- CI-lane decided, networking-scope closed, admission-lease closure still landing via separate PR)
- **Vision:** [`test-portfolio`](../../../visions/test-portfolio/README.md)'s
  containment boundary and host-safe-default behaviors; relates to, without
  changing, [`agent-containers`](../../../visions/plugins/agent-containers/README.md)'s
  trusted-vs-restricted venue posture.
- **Umbrella issue:** [#5040](https://github.com/ThomasMichon/copilot-extensions/issues/5040)

## Guiding Intent

Give `copilot-extensions` a `.devcontainer/devcontainer.json` spec whose
primary purpose is **test-execution isolation**: running a plugin's test
suite should not be able to leave side effects on, or depend on state from,
the contributor's (human or agent) host machine — regardless of what a buggy
or adversarial test actually does at the OS level. This is explicitly a
*different* concern from `agent-containers`' own **trusted development venue**
posture (see that plugin's vision) — a container used for headless, dispatched
*development* work (claiming issues, writing code, opening PRs) — not for
bounding test execution specifically, and not for interactive/local
contributor use.

## Participants

Single-driver effort at this stage -- no multi-agent split yet.

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving session | Plans and (once this plan clears review) implements the devcontainer spec | this worktree/branch |

## Coordination

- **Topology:** independent per-phase PRs (no shared feature branch needed
  yet -- revisit if a phase grows a genuine multi-agent split).
- **Host (owns PRs):** the driving session above.
- **Delegates:** none at this stage.
- **Handoff:** n/a -- single driver; re-evaluate if a future phase is
  delegated.

## Context

### Prior art this effort must not duplicate or regress

**`tools/run-plugin-tests.py` already implements substantial process-level
test containment** (see `TESTING.md`) -- read this in full before planning
Phase 0's gap analysis:

- Every invocation redirects user/Copilot/plugin/XDG/temp state beneath a
  per-run sandbox, owns the pytest process job/group and its ordinary
  descendants, and enforces three time-scale budgets (30s/test, 300s per
  25-file sub-suite, 900s per plugin) plus per-sub-suite process/memory/temp
  ceilings (128 processes / 4096 MiB / 2048 MiB by default, all overridable).
- On Windows, contained runs set `COPILOT_EXTENSIONS_TEST_CONTAINED=1`;
  conforming installers virtualize persistent User/Machine environment
  reads/writes to Process scope, and the runner snapshots+diffs registry
  environment keys to catch any adapter that still mutates host state.
- The shared `agent-procutil` spawn helper detects contained runs and
  suppresses deliberate Windows Job breakaway / POSIX session detachment, so
  a test can't escape containment via a legitimate production detachment API
  either -- there's an explicit adversarial test proving the containment
  owner still reaps a detached descendant on timeout.
- A host-wide admission lease serializes heavy runs across every checkout/
  worktree so concurrent suites don't compete for the same CPU/memory/process
  budget.

**What this existing mechanism does NOT provide** (the likely gap a container
closes, to be confirmed, not assumed, in Phase 0 -- **and only once the
container boundary itself is established and tested**, since a naive
devcontainer is not automatically a stronger boundary: standard devcontainer
tooling bind-mounts the host workspace by default, so an adversarial test can
still modify the host checkout through that mount, and an exposed Docker
socket, leaked credentials, an unrestricted host-network mode, or an
unrestricted egress path would each independently invalidate the claims
below):
- A real OS-level filesystem/network boundary -- the containment above is
  process/env-level (job objects, env redirection, registry diffing), which
  bounds *well-behaved or moderately buggy* code but cannot stop e.g. a test
  that writes outside its redirected roots via an absolute path, opens a raw
  socket, or exploits a privilege a job object doesn't restrict. **Establishing
  this boundary is itself a Phase 0 prerequisite, not a given** -- see the
  revised Phase 0 checklist below.
- Any help for a HUMAN contributor running tests locally outside the
  `run-plugin-tests.py` harness (e.g. a bare test-runner invocation, or
  editor-integrated test running) -- the existing containment is opt-in by
  using the turn-key runner, not structurally enforced by the dev environment
  itself.

### Related, but distinct, existing machinery (do not conflate)

- `agent-containers`' own **trusted development venue** posture (see that
  plugin's vision, linked in the header above) -- headless, *dispatched
  development* work, not test-execution sandboxing specifically. A downstream
  adopter's own container-hardening work this same week is out of this
  effort's scope; this effort is deliberately a different concern (per
  operator decision, see Request).
- `agent-containers` also supports a **`devcontainer_path`-backed** fleet
  model (`plugins/agent-containers/src/agent_containers/devcontainer_launch.py`)
  that drives the real `devcontainer` CLI against a `.devcontainer/
  devcontainer.json` -- if this effort lands such a spec, that fleet model
  becomes available as a *future*, separate decision; this effort's own scope
  is the spec and test-execution isolation only, not wiring a new fleet.
- `agent-codespaces`' devcontainer-pinning pattern
  (`docs/patterns/codespace-repo-provenance.md`) is about *which* devcontainer
  a dispatched CodeSpace resolves for a *different* product's vessel repo --
  unrelated to this effort's own repo gaining its first devcontainer spec.

## Request

Operator's ask, verbatim (2026-10-02, mid-session on an unrelated,
downstream-adopter container-hardening stretch):

> We are getting to the point where we're going to want to device a
> .devcontainer spec for copilot-extensions, and force all copilot-extensions
> development to be done in a container, just to avoid the test runs from
> spilling into our machines

Follow-up scoping (same session, asked by the agent before carving this
effort):

- **Primary goal:** test execution isolation specifically (not general
  interactive-dev-session isolation, though the operator's literal phrasing
  above said "force all ... development to be done in a container" -- see
  **Scope note** below on this tension).
- **Relationship to `agent-containers`' trusted development venue:** a
  separate concern, not a replacement for or an alternative mode of that
  existing dispatched-worker posture.
- **Timing:** start this as a tracked effort now.

**Scope note (agent-recommended, resolved):** the verbatim Request's own
wording ("force all ... development to be done in a container") read broader
than a pure test-execution concern -- the agent explicitly surfaced this as a
three-way choice (interactive dev isolation / test-execution isolation only /
both) before carving this effort, and the operator picked **test-execution
isolation only**, explicitly separate from `agent-containers`' trusted
development venue posture. This is a resolved decision, not an open tension --
recorded here so a future reader sees that the narrower scope was a
deliberate choice offered and made, not the agent silently narrowing the
verbatim ask.

## Plan

### Phase 0 — Gap analysis (research, no code)

- [x] **Prerequisite, must be done first:** establish and test the container's
      own host-boundary capability before relying on it for anything else --
      a devcontainer is not automatically a stronger boundary than the
      existing process-level containment, and checking mounts/credentials/
      network alone is not sufficient either (a permissive runtime posture
      defeats even a correctly-scoped mount). Concretely verify, and prefer a
      **container-local or read-only/overlay workspace** over a plain
      read-write bind of the host checkout (a read-write bind lets an
      adversarial test modify the host checkout directly, regardless of how
      "correctly scoped" the mount path looks):
      - the workspace mount's actual write scope -- does it expose more of
        the host than intended, and can a test modify files outside (or, with
        a plain bind mount, even inside) the checkout through it;
      - whether the Docker socket is exposed into the container (an exposed
        socket is a full host-escape vector);
      - what credentials are visible inside the container and from where
        they're sourced;
      - whether networking is restricted to what a test genuinely needs or
        left wide open;
      - the broader **runtime posture**, not just mounts/credentials/network:
        privileged mode, added Linux capabilities, `no-new-privileges`/seccomp
        confinement, host device access, and shared host namespaces (PID/IPC/
        UTS/user). This repository's own restricted-container boundary
        already treats all of these as fixed, checked invariants --
        `plugins/agent-containers/src/agent_containers/lifecycle.py`'s
        `restricted_policy_errors` (roughly lines 280-410) is the concrete
        reference for what "runtime posture" means in practice and the
        checks worth adapting here, even though this effort's container is a
        different (test-isolation, not dispatched-development) use case and
        may land on a different point on the trusted/restricted spectrum.
      Only once this boundary is concretely measured does the next bullet's
      comparison mean anything.

      **Done 2026-10-03, with a confirmed, live-reproduced finding, not just
      a theoretical concern:** built and ran a throwaway devcontainer from a
      minimal spec (`@devcontainers/cli` against the stock
      `mcr.microsoft.com/devcontainers/python` base image, no extra
      configuration -- i.e. the naive baseline a contributor would get from
      following public devcontainer docs with zero extra hardening).
      `docker inspect`'s `HostConfig`/`Mounts` against the running container
      confirmed, and a live write-through test proved:
      - **The workspace mount is a plain read-write bind of the host
        checkout** (`Mounts[0]`: `Type: bind`, `RW: true`,
        `Source: <host workspace path>`, `Destination: /workspaces/<name>`).
        Live-reproduced the exact risk the review raised: a file written from
        *inside* the container to the mounted path was immediately visible,
        modified, on the **host** filesystem -- i.e. an adversarial/buggy
        test inside this naive container genuinely can, and does, mutate the
        real host checkout. This alone means a default devcontainer spec
        would be a **regression**, not an improvement, over
        `run-plugin-tests.py`'s existing containment (which explicitly
        redirects state away from the real checkout) unless Phase 1
        deliberately designs around it (e.g. a container-local clone/copy of
        the checkout, or a read-only mount with an overlay for writes).
      - **No Docker socket exposure** by default (`Mounts` has no
        `/var/run/docker.sock` entry; confirmed via `docker exec ... ls
        /var/run/docker.sock` failing with "No such file or directory") --
        a real host-escape vector is NOT present in the naive baseline unless
        a feature like docker-outside-of-docker is explicitly added later.
      - **Default Docker capability set is active, nothing dropped**
        (`CapDrop: null` in `HostConfig`; the container's own
        `/proc/1/status` `CapEff`/`CapBnd` show Docker's standard default
        bits, not all-zero) -- this is a materially looser posture than the
        all-capabilities-dropped invariant this repo's own restricted-
        container boundary enforces (`lifecycle.py`'s
        `restricted_policy_errors`).
      - **No `no-new-privileges`/seccomp hardening declared**
        (`SecurityOpt: null`) -- Docker's own default seccomp profile still
        applies (this is NOT the same as `unconfined`), but nothing beyond
        that default is enforced.
      - **`ReadonlyRootfs: false`, `Privileged: false`, standard `bridge`
        networking with full outbound internet reach** confirmed live
        (a plain `curl` to an external host from inside the container
        succeeded with a 200).
      **Conclusion carried into Phase 1:** a devcontainer spec that merely
      follows public defaults does not close the gap this effort exists for
      -- it would need **deliberate** design choices (a container-local or
      overlay/copy-on-write workspace instead of a plain RW host bind being
      the single highest-priority one, since it's the difference between
      "isolated" and "directly mutates the host") to actually improve on
      `run-plugin-tests.py`'s existing containment rather than quietly
      regressing it while looking more isolated on the surface.
- [x] Confirm, with a concrete reproduction, what a `.devcontainer`-based test
      run -- using the established, tested boundary above -- would actually
      catch that `tools/run-plugin-tests.py`'s existing process-level
      containment does not (see Context's "What this existing mechanism does
      NOT provide" -- confirm or revise that list with real evidence rather
      than assuming it's complete). **Done 2026-10-03, revised from the
      original assumption**: a real OS-level filesystem/network boundary IS
      achievable (process escape via an absolute path, a raw socket, or a
      privilege a job object doesn't restrict is genuinely a class of attack
      `run-plugin-tests.py`'s containment cannot stop), but **only if Phase 1
      actually designs the container to provide that boundary** -- the naive
      baseline measured above does NOT provide it (the RW host-bind-mount
      finding is a direct counterexample: it's LESS isolated than the
      existing containment's redirected-state model for exactly the
      filesystem axis this effort cares about most). The "help for a human
      contributor running tests outside the turn-key runner" gap stands
      as originally stated -- confirmed unaffected by this finding, since
      it concerns opt-in-vs-structural enforcement, not the boundary's
      technical strength.
- [x] Decide whether the spec targets Linux only (matching this repo's CI
      runners) or must also cover the Windows-specific containment paths
      `TESTING.md` describes (`COPILOT_EXTENSIONS_TEST_CONTAINED`,
      registry-key diffing, Job-breakaway suppression) -- a Linux-only
      devcontainer cannot exercise those paths at all, which may be an
      acceptable scope boundary or may leave a real gap, depending on the
      answer to the first two bullets. **Decided 2026-10-03 (agent-
      recommended, open to revision at Phase 1's own review): Linux-only
      scope.** Reasoning: Docker Dev Containers are overwhelmingly a Linux-
      container technology in practice (Windows containers exist but are
      rarely used for this tooling and add substantial complexity for
      minimal benefit here); this repo's CI already runs a dedicated
      `windows-latest` test-runner job exercising exactly the Windows-
      specific paths `TESTING.md` describes, so those paths already have
      real coverage independent of this effort. A Linux-only devcontainer
      spec therefore narrows this effort's own scope to the Linux test-
      execution path without leaving the Windows paths uncovered overall --
      it simply doesn't duplicate coverage that already exists elsewhere.
      This closes Phase 0.

### Phase 1 — Spec design
- [x] Design the workspace storage model to actually close the gap Phase 0
      found: a container-local clone/copy of the checkout, or a read-only
      host bind plus an in-container overlay for writes -- NOT a plain
      read-write bind of the host checkout (confirmed live to let an
      adversarial test mutate the real host checkout, which is a regression
      versus the existing `run-plugin-tests.py` containment, not an
      improvement). `.devcontainer/test-isolation/devcontainer.json`'s `workspaceMount`
      overrides the default bind entirely with a container-local, size-
      bounded Docker VOLUME -- the host checkout is never mounted into the
      container at all, in any form. `tools/run_tests_in_devcontainer.py`
      populates that volume from a point-in-time COPY of the host checkout
      (a `tar` pipe through `docker exec`, mirroring the repo-
      materialization pattern `agent-containers`' own
      `devcontainer_launch.py` uses for its `devcontainer_path` fleet
      backend) after the container is up. A file written from inside the
      container never appears on the host, and `git status` on the host
      checkout stays clean across every real container run.
- [x] Design the runtime-posture hardening the naive baseline lacked: drop
      all Linux capabilities (add back only what the test suite genuinely
      needs), enforce `no-new-privileges`, and decide on Docker-socket
      exclusion (default -- no feature should add it back without a
      deliberate, documented reason). `--cap-drop=ALL`,
      `--security-opt=no-new-privileges`, and `--read-only` root
      filesystem mirror the restricted-fleet invariants `agent-containers`'
      own `lifecycle.py` (`restricted_policy_errors`) checks for its
      dispatched-development containers -- including that policy's own
      fixed writable-surface set, `{workspace, home, /tmp, /run}`,
      reproduced here as the workspace volume plus three bounded `tmpfs`
      mounts, each confirmed via real `docker inspect` output. No
      Docker-socket mount is present (confirmed via `docker inspect`'s
      `Mounts`). Two real runtime-posture pitfalls surfaced during live
      validation: (1) a bare tmpfs mount is `root:root 0755` by default,
      which the non-root `vscode` remote user cannot write into at all --
      an explicit `mode=1777` on the `$HOME` and `/run` tmpfs is required,
      or the devcontainer CLI's own lifecycle-hook bookkeeping (a marker
      file under `$HOME`) silently fails, which in turn silently skips
      `onCreateCommand`; (2) Docker's tmpfs default additionally bakes in
      `noexec`, which blocks executing the installed `uv` binary from
      `$HOME/.local/bin` ("Permission denied") unless explicitly overridden
      with `exec` on the `$HOME` and `/tmp` tmpfs (`/run` has no such need
      and stays `noexec`). **Networking scoping is split into its own item
      below, not folded into this one** -- see that item for why it's still
      open.
- [x] Scope networking to what tests actually require, rather than leaving
      the default bridge's full outbound reach. **Closed in Phase 2**: see
      Phase 2's own "Close the networking residual gap" Plan item for the
      implementation (a network-enabled dependency-preparation pass,
      then every network disconnected before the real test pass) and its
      live validation.
- [x] Decide how this devcontainer spec is invoked for Linux test execution
      specifically -- a new `tools/run-plugin-tests.py` mode, a separate
      wrapper script, or direct `devcontainer exec` -- and how it relates to
      (without duplicating) the existing turn-key runner's own containment
      for contributors who aren't using the devcontainer. **A separate,
      opt-in wrapper script**, `tools/run_tests_in_devcontainer.py` -- NOT
      a new mode baked into `run-plugin-tests.py` itself, so that runner's
      own interface and the vast majority of local/CI runs (which don't use
      the devcontainer at all) stay completely unchanged. The wrapper
      brings the container up, populates its workspace volume, then
      invokes `tools/run-plugin-tests.py` *inside* the container via
      `devcontainer exec` and passes through every one of that runner's own
      flags (`--changed`, `--all`, `-k`, etc.) semantically unchanged --
      with one deliberate normalization (a resolvable `--base` is rewritten
      to its resolved commit SHA before the in-container invocation is
      assembled; see `TESTING.md` for why) and three known exceptions:
      `--allow-host-state` is rejected outright (its documented contract --
      preserve the caller's real HOME/config/credentials -- can't be
      honored here, since this wrapper's container always gets a fresh,
      credential-free tmpfs `$HOME` by design), `--admission-wait`'s
      host-wide lease loses its cross-process coordination once run inside
      the container (a tracked Phase 2 gap, see the Validation Plan item
      below), and a `--max-memory-mb`/`--max-processes`/`--max-temp-mb`
      value above the container's own fixed outer ceiling is rejected
      outright (the outer container would otherwise silently preempt it
      regardless of what the inner runner believes it has) -- so the
      container adds a real OS-level boundary strictly on top of (never
      instead of, never duplicating) the turn-key runner's existing
      process-level containment. Validated end-to-end against a real
      plugin suite (`ai-attribution`, 98 passed / 6 skipped) via both the
      wrapper's own internal functions and a full `python
      tools/run_tests_in_devcontainer.py ai-attribution` invocation --
      confirmed the container tore itself down afterward and the host
      checkout's `git status` showed no unexpected changes. Unit tests
      (`tools/test_run_tests_in_devcontainer.py`) cover the wrapper's own
      logic (argument parsing, git-environment scrubbing, the tracked-file
      selection, the per-instance config/volume rewrite, the git-bundle
      snapshot materialization -- the last via real `git` subprocess calls
      against throwaway repositories, not mocked, since that logic's real
      behavior is the point being tested -- the privileged workspace
      population, and the Docker/devcontainer-CLI invocation shape), in the
      style of `test_run_plugin_tests.py`, and are wired into the required
      `test-runner-linux` CI job alongside that module; the real,
      Docker-backed end-to-end run above is a manual validation step, not
      part of the default test portfolio, since it needs a working Docker
      daemon and network access to pull a base image.

### Phase 2 — Wire into CI / contributor flow
- [x] Decide whether to add an opt-in CI lane (not a required gate, since
      the existing turn-key runner already gates every push/PR) that runs a
      representative subset of plugin suites through
      `tools/run_tests_in_devcontainer.py` on `ubuntu-latest`, to catch any
      future regression in the container boundary itself without slowing
      down the default fast path. **Decided: no CI lane.** This wrapper
      exists to give a *local* contributor an OS-level containment
      boundary `run-plugin-tests.py` alone can't provide on their own
      machine -- the upstream GitHub Actions runners already execute every
      plugin suite inside a single-tenant, ephemeral, disposable VM per
      job, which is a stronger isolation boundary than this Docker-in-CI
      wrapper could add on top of it. Wiring the wrapper into CI would
      only add Docker-in-Docker startup latency and flakiness risk for a
      boundary CI doesn't need; it stays a contributor-invoked local tool,
      not a CI lane. **Accepted coverage tradeoff, recorded explicitly**:
      `test-runner-linux`'s required CI job exercises
      `tools/test_run_tests_in_devcontainer.py`'s own unit-level logic
      (subprocess-shape assertions, argument handling) on every push/PR,
      but a regression in the wrapper's REAL Docker/devcontainer boundary
      itself (e.g. a runtime-posture flag silently stopping working) is
      only caught by the manual, Docker-backed end-to-end validation this
      effort's own Journal already documents -- there is no automated
      lane that would catch that class of regression. This is a
      deliberate, accepted gap (not an oversight): the cost of a
      Docker-in-CI lane (startup latency, flakiness risk) was judged not
      worth it for a boundary CI's own containment doesn't need, but a
      future contributor hitting a real regression here should know
      automated coverage stops at the unit level.
- [x] Close the networking residual gap flagged in Phase 1's second item
      (split dependency-resolution and test-execution into separate
      network-enabled/network-disconnected passes), or explicitly decide
      the added complexity isn't worth it yet and record that decision.
      **Closed, split implemented.** `tools/run_tests_in_devcontainer.py`
      now runs a dependency-preparation pass first (a new
      `run-plugin-tests.py` `--prepare-only` mode -- the same
      `_ensure_venv` install path a real run uses, but it NEVER imports a
      single test module or `conftest.py`, unlike `--collect-only`, which
      still runs pytest's own collection and would execute that
      module-level code with network access), then disconnects the
      container from every attached network (read live via `docker
      inspect`, not a hardcoded "bridge" assumption) before running the
      real, now network-disconnected test pass -- stripping a
      passed-through `--reinstall` first, since the prep pass already
      rebuilt the venv(s) and the real pass must reuse them, not rebuild
      with no network left. A bare `--list` request skips both steps,
      since it never builds a venv. The new logic
      (`is_list_only`/`prepare_dependencies`/`strip_reinstall`/
      `disconnect_container_networks`) was factored into a new sibling
      module, `tools/_devcontainer_network_scope.py`, to keep the main
      wrapper under its 1000-line cap. Live-validated against real
      Docker (`ai-attribution`, 98 passed / 6 skipped): confirmed via
      `docker inspect` that the container's `NetworkSettings.Networks`
      is empty during the real pass, and that a DNS lookup from inside
      the container fails outright.
- [ ] Close the host-wide admission-lease residual gap: `--admission-wait`
      coordinates against a lease stored under `$HOME`/`XDG_CACHE_HOME`,
      which is a fresh tmpfs per container invocation, so concurrent
      wrapped runs acquire unrelated per-container leases instead of one
      shared host-wide slot. Needs a host-side admission mechanism
      acquired before container startup (or an explicit decision that the
      added cross-process coordination isn't worth it yet, recorded here
      rather than left silently broken).

## Validation Plan

- [x] A throwaway container's workspace mount is confirmed, via
      `docker inspect`, to be a Docker volume (never a host bind) -- done
      live in Phase 1 (`Mounts[0].Type == "volume"`, no `Binds` entry in
      `HostConfig`).
- [x] A file written from inside the container is confirmed NOT to appear
      on the host filesystem, and the host checkout's `git status` stays
      clean across a real container run -- done live in Phase 1.
- [x] `docker inspect`'s `HostConfig` confirms `ReadonlyRootfs: true`,
      `CapDrop: ["ALL"]`, `CapAdd: null`, and `no-new-privileges` present in
      `SecurityOpt`, with no Docker-socket mount anywhere in `Mounts` --
      done live in Phase 1.
- [x] A real plugin's pytest suite (`ai-attribution`) passes end-to-end
      through `tools/run_tests_in_devcontainer.py`, with the container torn
      down afterward -- done live in Phase 1.
- [x] Phase 2: the CI-lane decision is validated against the ACTUAL
      outcome, not the original (CI-lane-shaped) criterion, which no
      longer applies now that the decision is "no lane." **Validated:**
      `test-runner-linux` (required CI) runs
      `tools/test_run_tests_in_devcontainer.py`'s unit suite on every
      push/PR -- confirmed green; the wrapper's real Docker/devcontainer
      boundary itself is validated only manually (the Phase 1 Journal's
      own `ai-attribution` end-to-end runs), an accepted, now explicitly
      documented coverage tradeoff (see the paired Plan item above), not
      an unaddressed gap.
- [x] Phase 2 (or a later revision of Phase 1): the networking residual gap
      is either closed (network-disconnected test-execution pass) or
      explicitly re-affirmed as an accepted, documented tradeoff rather than
      left open indefinitely. **Closed** -- see the paired Plan item above
      for the implementation and live validation.
- [ ] Phase 2: the host-wide admission-lease residual gap is either closed
      (a real host-side lease mechanism) or explicitly re-affirmed as an
      accepted, documented tradeoff.

## Proposal

Phase 1 delivered `.devcontainer/test-isolation/devcontainer.json` (the hardened,
workspace-volume-backed spec) and `tools/run_tests_in_devcontainer.py` (the
opt-in wrapper that brings it up, populates it, and runs
`tools/run-plugin-tests.py` inside it), both live-validated end-to-end
against a real plugin suite. Phase 2's CI-lane question is decided (no
lane, an accepted coverage tradeoff recorded above) and the networking-
scope residual gap is closed; the host-wide admission-lease residual gap
remains open, landing as its own follow-up PR.


## Journal

### 2026-10-02 — Created
Carved from a verbatim operator idea raised mid-session during an unrelated
downstream-adopter container-hardening stretch. Captured the Request
verbatim, scoped it via a short clarifying round (test-execution isolation
specifically -- resolving the verbatim ask's broader "force all development"
framing down to this narrower, explicitly chosen scope -- separate from
`agent-containers`' trusted development venue posture, start now), and read
`TESTING.md`'s existing `tools/run-plugin-tests.py` containment mechanism in
full before drafting Phase 0 -- that mechanism is substantial prior art this
effort must not duplicate or silently regress. No implementation work has
started; this is the plan awaiting the Phase 0 research above and its own
review gate before anything is built.

### 2026-10-02 — Review round 1: 4 findings addressed
Automated review on the plan PR raised four real findings, all addressed:
(1) Phase 0 assumed a container closes the claimed host-boundary gap without
first establishing/testing that boundary itself (bind-mount write scope,
Docker-socket exposure, credential visibility, network restriction) -- made
this an explicit, first Phase 0 prerequisite rather than an assumption;
(2) missing the required effort-header `Vision` field -- added, grounding this
effort in `visions/test-portfolio`'s containment-boundary/host-safe-default
behaviors and relating it (without changing) to `agent-containers`' own
trusted-venue vision; (3) PR description missing the required Documentation-
impact statement -- added (see the PR itself); (4) private downstream
organization/fleet identifiers (a private consumer's own fleet/effort names)
leaked into this public artifact -- replaced throughout with the public
`agent-containers` vision's own identifier-neutral terminology ("trusted
development venue" posture) instead of naming the private consumer or its
internal effort/fleet names.

### 2026-10-03 — Phase 0's host-boundary prerequisite done, with a real finding
Built and ran a throwaway devcontainer (`@devcontainers/cli` against the stock
`mcr.microsoft.com/devcontainers/python` image, zero extra hardening --
the naive baseline). Confirmed live, not assumed: the default workspace mount
is a plain read-write bind of the host checkout, and a file written from
inside the container was immediately visible, modified, on the host
filesystem. This means a naive devcontainer spec would be a **regression**
versus `run-plugin-tests.py`'s existing containment for exactly the axis this
effort cares about most (host-checkout safety), not an improvement -- Phase 1
now carries an explicit, highest-priority design requirement to use a
container-local or overlay/copy-on-write workspace instead of a plain RW host
bind. Also confirmed: no Docker-socket exposure by default (good), default
(non-empty) Linux capability set active with nothing dropped, no
`no-new-privileges`/seccomp hardening declared beyond Docker's own default
profile, and unrestricted outbound networking. Revised the second Phase 0
checklist item's conclusion accordingly: a real OS-level boundary is
achievable and would close a genuine gap, but only if Phase 1 deliberately
designs for it -- the naive baseline does not provide it "for free." Still
open: the Linux-only vs. cross-platform scope decision.

### 2026-10-03 — Phase 0 closed; Phase 1 scoped
Decided (agent-recommended, open to revision at Phase 1's own review
gate): Linux-only scope, since this repo's CI already runs a dedicated
Windows test-runner job covering `TESTING.md`'s Windows-specific containment
paths independently of this effort. All three Phase 0 checklist items are
now done. Expanded Phase 1 into three concrete design items derived directly
from Phase 0's findings: the workspace storage model (container-local/
overlay, not a plain RW host bind), runtime-posture hardening (capability
drop, `no-new-privileges`, Docker-socket exclusion, scoped networking), and
how the spec is actually invoked for Linux test execution without
duplicating `run-plugin-tests.py`'s existing containment for contributors
not using it.

### 2026-10-03 — Phase 1 done, all three items live-validated
Built `.devcontainer/devcontainer.json` and `tools/run_tests_in_devcontainer.py`
and validated every claim against a real container rather than reasoning
about it in the abstract -- caught two real bugs doing so, not zero:
(1) a bare Docker `tmpfs` mount is `root:root 0755` by default, which
silently broke the devcontainer CLI's own `$HOME`-based lifecycle-hook
bookkeeping (and would have broken installing `uv` the same way) until an
explicit `mode=1777` was added to the `$HOME` and `/run` tmpfs; (2) Docker's
tmpfs default additionally bakes in `noexec`, which blocked executing the
installed `uv` binary ("Permission denied") until an explicit `exec` option
was added to the `$HOME` and `/tmp` tmpfs. Both were found and fixed through
live iteration (build container -> hit the real error -> fix the spec ->
rebuild), not anticipated up front. Final live validation: `docker inspect`
confirmed the workspace is a volume (never a host bind, never any `Binds`
entry), `ReadonlyRootfs: true`, `CapDrop: ["ALL"]`, `no-new-privileges`
present, and no Docker-socket mount anywhere; a file written from inside the
container never appeared on the host and `git status` on the host checkout
stayed clean across multiple container runs; and a real plugin's pytest
suite (`ai-attribution`, 98 passed / 6 skipped) ran to completion end-to-end
through `python tools/run_tests_in_devcontainer.py ai-attribution`, including
automatic container teardown afterward. Added
`tools/test_run_tests_in_devcontainer.py` (7 unit tests, subprocess-mocked,
no Docker required) covering the wrapper's own logic, and a new
"Optional devcontainer-based isolation (Linux)" section in `TESTING.md`
documenting the invocation for future contributors. One item was
deliberately left open rather than silently resolved: outbound networking
still uses Docker's default bridge with full reach, because dependency
resolution and test execution currently share one container lifetime and
haven't been split into network-enabled/network-disconnected passes --
recorded as a named Phase 2 candidate and a Validation Plan item, not
dropped. Phase 2 (CI/contributor-flow wiring) is next.

### 2026-10-03 — Review rounds 1-2: 9 findings addressed, one caught live mid-fix
Automated review on the PR (#5058) raised six findings in round 1, all
addressed: (1) the fixed named workspace volume was reused across every
invocation -- files deleted on the host or artifacts left by a prior run
would stay visible, and concurrent runs would mutate the same volume --
fixed by generating a per-invocation devcontainer config with the volume
name made unique (`_per_instance_config`), and removing that exact volume
(not just the container) at teardown; (2) excluding `.git` from the copied
snapshot silently broke `--changed` mode (`git diff`/`git status` inside the
container would fail, and their unchecked empty output would produce an
empty, not erroring, target set) -- fixed by including `.git` in the copy
instead of trying to resolve changed targets on the host; (3) the
privileged workspace-population path (`_populate_workspace`) had no
automated coverage -- added subprocess-mocked tests covering the root
tar-extraction command, the follow-up chmod, and both commands' failure
branches; (4) a failed `docker rm -f` at teardown was silently discarded --
`_tear_down` now checks both the container and volume removal results and
raises if either fails; (5) the `--` passthrough separator was only
stripped when it was the very first extra argument, so
`--all -- -k some_filter` silently dropped the `-k` filter -- fixed to strip
every `--` occurrence, not just a leading one; (6) one test's assertion
allowed a regression that drops the final passthrough argument to pass
anyway -- narrowed to assert the complete expected suffix only.

Round 2 (after pushing round 1's fixes) raised three more, including a
real, HIGH-severity bug the reviewer caught from static analysis that live
validation then confirmed directly: (7) **in this repo's own required
linked-worktree flow, `.git` is a pointer FILE** (`gitdir: <absolute host
path>`), not a self-contained directory -- copying it verbatim (round 1's
fix for finding #2) left `git` inside the container pointing at a host
path that doesn't exist there, so `--changed` would have silently gone back
to running nothing despite `.git` technically being "included." Fixed with
`_resolve_git_dirs`/`_materialized_git_dir`: for a normal checkout this is
just `REPO/.git` (confirmed no bug there); for a linked worktree, it builds
a merged, self-contained copy in a temp dir -- the shared common dir's
objects/refs overlaid with this worktree's own private `HEAD`/`index`, with
the stale `commondir` pointer removed. **Live validation during this exact
fix caught a second bug the first merge attempt introduced**: a worktree's
private git dir carries its OWN near-empty `refs`/`logs` subdirectories (for
worktree-private refs), and wholesale-replacing the common dir's
already-copied `refs` with those (the first implementation's approach)
silently wiped every real branch ref -- `git rev-parse HEAD` failed with
"unknown revision" against the merged copy. Fixed by merging (`dirs_exist_ok=True`)
those specific subdirectories instead of replacing them outright, re-verified
live: `git rev-parse HEAD`, `git status --short`, and `git diff --name-only
origin/dev` all now resolve correctly against the materialized copy, and a
real end-to-end `--changed --base origin/dev` run through the full wrapper
completed cleanly (correctly reporting "No plugin suites to run" for this
PR's own non-plugin diff, rather than erroring). (8) teardown behavior
itself had no DIRECT test (only an indirect one replacing `_tear_down` with
a lambda) -- added tests invoking `_tear_down` and a new `_cleanup_orphan`
directly, covering success and both failure branches. (9) a `devcontainer
up` failure (timeout, a mid-`onCreateCommand` failure, or unparseable
output) raised before the teardown `finally` was ever entered, leaking a
partially created container and its unique volume -- added `_cleanup_orphan`
(found-by-label best-effort removal, swallowing its own failures so the
original startup error still surfaces) and wired it around `_bring_up` in
`main()`.

All nine fixes are re-validated: the full unit test suite (23 tests) passes,
and the real Docker-backed end-to-end run (`ai-attribution`, 98 passed / 6
skipped) was re-executed from scratch after every fix in this round,
including the `--changed` mode confirmation above -- not merely re-run once
at the start, since finding #7's live-validation-while-fixing is exactly
what caught finding #7's own follow-up bug.

### 2026-10-03 — Review round 3: 6 substantive findings + 4 phrasing/consistency nits addressed
Automated review on the round-2 push raised 12 items. Six were substantive:
(1) **HIGH -- snapshot exfiltration**: the snapshot copied every file
physically present under the checkout (including gitignored, potentially
secret-bearing files like local credentials) plus the ENTIRE `.git`
directory (including `config`, which can embed an authenticated remote URL
or `credential.helper` settings) into a container that has outbound network
access -- an adversarial/buggy test could exfiltrate host-only state. Fixed
two ways: the working-tree copy now comes from `git ls-files --cached
--others --exclude-standard` (the same boundary contributors and CI already
trust to keep secrets out of the repo) instead of a raw directory walk, and
`_materialized_git_dir` always replaces `config` with a fresh,
credential-free minimal one and drops `hooks` entirely, regardless of
whether the checkout is a normal one or a linked worktree. (2) the
workspace volume had no size bound (a plain Docker local volume), so a
buggy/adversarial test could fill host Docker storage before teardown --
fixed by creating it explicitly as a size-bounded (4 GiB) tmpfs-backed
volume (`_create_bounded_volume`), live-confirmed via `docker volume
inspect`. (3) the git probes inherited ambient `GIT_DIR`/`GIT_WORK_TREE`/etc.,
which silently override `-C` and could make the snapshot (or `--changed`)
resolve against the wrong repository -- fixed with `_scrubbed_git_env`,
mirroring `tools/coverage_guided_selection/ancestor_resolution.py`'s own
`scrubbed_git_env` (duplicated by hand, matching that module's own
"dependency-free by design" precedent, not imported). (4) the full snapshot
was built as one in-memory `bytes` object before being handed to
`subprocess.run`'s `input=`, multiplying peak memory on a large checkout --
fixed by writing the tarball to a temp file and streaming it via `stdin=`
instead. (5) `_cleanup_orphan` itself could raise (`TimeoutExpired`/`OSError`
from any of its own `subprocess.run` calls), masking the original startup
error its docstring promised not to mask -- each call is now individually
guarded. (6) the new test module wasn't wired into required CI (`ci.yml`'s
Linux `test-runner-linux` job explicitly lists which modules it collects) --
added alongside `tools/test_run_plugin_tests.py` there (Linux-only, matching
the wrapper's own documented scope).

The remaining four were phrasing/consistency nits, also addressed: durable
comments and Plan text that read like review-history narration ("live
validation found...", "already-reviewed...") were rewritten to state the
current technical fact directly (review history belongs in the review
thread or this Journal, not in the artifact itself); and the Phase 1
"runtime-posture hardening" checklist item was split so the still-open
networking-scoping requirement is its own unchecked item, rather than
living inside an item marked done.

All ten changes are re-validated: the full unit test suite (29 tests, up
from 23) passes, and a fresh Docker-backed end-to-end run
(`ai-attribution`, 98 passed / 6 skipped) was executed from scratch
afterward, plus a direct `docker volume inspect` confirming the tmpfs-backed,
size-bounded volume.

### 2026-10-03 — Review round 4: 2 findings addressed (closes the exfiltration concern for real)
Automated review on the round-3 push raised two new findings (plus three
restated stale ones from earlier rounds already fixed, left as-is pending
their own thread resolution): (1) **HIGH -- round 3's fix was incomplete**.
Replacing only `config` and `hooks` still copied the ENTIRE common git
directory's objects/refs wholesale -- every branch, stash, reflog, and
unreachable object, none of which has anything to do with the plugin suite
being run, into a container that deliberately keeps outbound networking.
Fixed properly this time: `_materialized_git_dir` now builds a `git bundle`
containing only the object closure of `HEAD` and the `--changed` diff base
(extracted from the passthrough args via `_resolve_base_ref`, falling back
to `run-plugin-tests.py`'s own `origin/main` default) via `git bundle
create`, then `git clone --bare` from that bundle into a fresh, minimal git
directory -- nothing else is reachable. This also fully replaces (and
simplifies away) round 2's worktree-merge logic: a linked worktree's `.git`
pointer file is no longer special-cased at all, since the bundle/clone path
works identically regardless of how the host's `.git` is laid out. A real
regression test builds an actual tiny git repo with a sibling "secret"
branch carrying placeholder-secret-shaped content that is never an
ancestor of `HEAD` or the base ref, and asserts that branch's commit is
genuinely unresolvable (`git cat-file -e` fails) in the materialized copy
-- not merely that a specific file/string is absent, but that the object
itself was never transferred. (2) `_tear_down`'s container-removal
`subprocess.run` call could itself raise (`TimeoutExpired`/`OSError`)
before the volume-removal line ever ran, leaking the per-run volume despite
the teardown contract -- fixed with the same per-step try/except pattern
already used in `_cleanup_orphan`, so the volume removal is always
attempted regardless of what happens to the container removal.

All changes re-validated: the full unit test suite (34 tests, up from 29,
including the new real-git secret-branch-exclusion regression test above)
passes, and a fresh Docker-backed end-to-end run (`ai-attribution`, 98
passed / 6 skipped) plus a `--changed --base origin/dev` run were both
executed from scratch afterward against the new bundle-based snapshot
path.

### 2026-10-03 — Review round 5: 4 findings addressed (tracked-files-only default)
Automated review raised four items on the round-4 push: (1) **HIGH**: even
after round 4's bundle-based `.git` fix, the working-tree snapshot still
used `--cached --others --exclude-standard`, which includes every
untracked-but-not-gitignored file -- this repository has no blanket
`.gitignore` rule for `.env`-style config or arbitrary credential
filenames, so a genuinely untracked secret file sitting in the working tree
would still be copied into a container with full outbound egress. Fixed by
making tracked files (`git ls-files --cached` only) the default, with a new
explicit `--include-untracked` wrapper flag required to opt into also
copying untracked-but-not-gitignored files (e.g. to test a new,
not-yet-committed file) -- never the default. (2) `TESTING.md` and
`.devcontainer/devcontainer.json`'s own comments called this "a real
OS-level filesystem/**network** boundary" while the container still keeps
full outbound egress -- corrected to "filesystem/**privilege** boundary"
with an explicit callout that networking is not yet part of it. (3) a
Plan-item cross-reference hardcoded an exact test count ("29 tests") that
was already stale by the time it was reviewed -- replaced with a
description that doesn't need updating every time a test is added. (4) the
test module's own docstring claimed subprocess-mocking throughout, but the
git-bundle materialization tests (added in round 4) genuinely invoke the
real `git` CLI against throwaway repositories -- the docstring now says so
directly, including why (that logic's real behavior is the point being
tested).

Re-validated end-to-end: the full unit test suite (35 tests) passes
(including two new tests for the tracked-vs-include-untracked selection),
a Docker-backed end-to-end run (`ai-attribution`, 98 passed / 6 skipped)
confirms the new tracked-only default still runs real suites correctly,
and a second run with `--include-untracked` against a deliberately added
untracked marker file confirms the opt-in path still works end-to-end too.

### 2026-10-03 — Review round 6: 4 findings addressed
Automated review raised four new items on the round-5 push (plus several
stale, already-fixed findings restated by the review tool against
unresolved threads): (1) `onCreateCommand`'s `curl ... | sh` pipeline could
mask a complete `curl` failure -- `/bin/sh` reports only the pipeline's
final command's exit status, and `sh` itself exits 0 on empty input, so
`devcontainer up` could report success with no `uv` actually installed.
Fixed by downloading to `/tmp` first and chaining with `&&` so a failed
download now fails setup immediately rather than silently. (2) `git
ls-files --cached` still lists a path for an unstaged (not yet `git
add`-ed) deletion -- the index entry exists even though the working-tree
file is gone -- so `_write_tar_of_repo` would raise `FileNotFoundError`
trying to archive it. Fixed by checking `os.path.lexists` (not a
symlink-following `Path.exists()`, which would wrongly skip an intact
symlink whose target is missing) before adding each path, skipping it
silently if absent; a new test builds a tracked-but-deleted path directly,
and a live end-to-end run against a real deleted tracked file
(`docs/architecture.md`, restored afterward) confirms the fix. (3) the
wrapper's own top-of-module docstring still called this "a real OS-level
filesystem/**network** boundary" -- the one spot round 5's phrasing fix
missed -- corrected to "filesystem/**privilege** boundary" with the same
explicit networking callout used elsewhere. (4) a Phase 2 Plan item
encoded transient review state ("Pending Phase 1 review") rather than
describing the pending decision directly -- reworded to be timeless; review
history belongs in these dated Journal entries, not in the canonical Plan
text itself.

Re-validated end-to-end: the full unit test suite (36 tests) passes
(including the new deleted-tracked-path regression test), a Docker-backed
end-to-end run (`ai-attribution`, 98 passed / 6 skipped) confirms the fixed
`onCreateCommand` still installs `uv` correctly, and a second run against a
real deleted-then-restored tracked file (`docs/architecture.md`) confirms
the deletion-handling fix works live, not merely in the mocked unit test.

### 2026-10-03 — Review round 7: 4 findings addressed (removed the copied-index approach entirely)
Automated review raised four more items: (1) **HIGH**: `git ls-files`
reports an initialized submodule as a single path that is a real DIRECTORY
on disk, and `tarfile.add` recursively archives directories by default --
this repo has no submodules today, but the bug would have silently copied
an entire submodule's working tree (including its own untracked/ignored
files and `.git` metadata) wholesale the day one was added, defeating the
tracked-files-only boundary entirely. Fixed with `recursive=False` on every
`tar.add` call in `_write_tar_of_repo` -- a submodule path still gets
archived as an empty directory entry, never its contents; ordinary tracked
files are unaffected (they were never directories to begin with). (2)
`_resolve_base_ref` returned the FIRST `--base` occurrence, not argparse's
own last-occurrence-wins behavior for a repeated flag -- fixed to keep
scanning and return the last match. (3) round 4's "copy the real index"
step was itself unsound: a staged-but-uncommitted new/modified file's blob
is genuinely unreachable from both `HEAD` and the base ref, so the bundle
wouldn't contain it while the copied index still referenced it -- `git
diff`/`status` could fail outright for a valid staged checkout. Fixed by
removing the index-copy step entirely and instead rebuilding the index from
`HEAD` (`git read-tree HEAD`) -- the tradeoff (documented) is that staged
state is no longer distinguished from unstaged inside the container, since
every modification (staged or not) is simply visible as an ordinary
working-tree difference, backed by the actual on-disk file content
`_tracked_paths` already copies in regardless. (4) a failed `_tear_down` in
`main()`'s bare `finally` would silently replace the PRIMARY failure (and
its traceback) when both the test run and teardown failed -- fixed by
tracking whether a primary exception is already in flight and, if so,
reporting (but not re-raising) a secondary teardown failure instead of
letting it override; teardown's own failure still raises directly when the
primary path succeeded.

Re-validated end-to-end: the full unit test suite (39 tests, including new
coverage for the submodule-recursion guard, the repeated-`--base` fix, the
staged-uncommitted-file edge case, and both exception-masking branches)
passes, and a fresh Docker-backed end-to-end run (`ai-attribution`, 98
passed / 6 skipped) confirms the removed index-copy step doesn't break the
common case.

### 2026-10-03 — Review round 8: 3 findings addressed
Automated review raised three more items: (1) **HIGH**: the container had
no hard memory/CPU/PID ceiling at all -- a test could exhaust host RAM/cores
or fork-bomb entirely outside `tools/run-plugin-tests.py`'s own inner
per-sub-suite bounds (128 processes / 4096 MiB default), which only ever
get a chance to act from INSIDE the container. Fixed by adding explicit
`--memory=6g --memory-swap=6g --cpus=4 --pids-limit=512` to `runArgs`,
mirroring the same restricted-fleet invariants already cited elsewhere in
this file (`fleet.py`'s run-args; `lifecycle.py`'s `restricted_policy_errors`
treats these as fixed, checked invariants) -- sized with headroom above the
inner defaults, not equal to them, since the container itself needs some
of that budget too. Live-confirmed via `docker inspect`:
`Memory: 6442450944, MemorySwap: 6442450944, NanoCpus: 4000000000,
PidsLimit: 512`. (2) `_tracked_paths` decoded `git ls-files -z` output with
a plain UTF-8 `.decode()`, which raises `UnicodeDecodeError` outright for a
valid tracked filename that happens not to be valid UTF-8 (git paths on
Linux are arbitrary bytes) -- fixed with `os.fsdecode` (surrogate-escape),
which preserves such names instead of aborting the whole snapshot over one
oddly-named file. (3) round 7's exception-masking fix only covered a
RAISED primary exception -- a nonzero `_run_tests` exit code is a
*returned* value, not an exception, so `primary_failed` stayed `False` for
a real test failure and a secondary `_tear_down` failure would still mask
it with an unrelated `SystemExit`. Fixed by treating a nonzero result the
same as a raised exception for masking purposes.

Re-validated end-to-end: the full unit test suite (41 tests, including new
coverage for all three fixes) passes, a fresh Docker-backed end-to-end run
(`ai-attribution`, 98 passed / 6 skipped) confirms the new resource limits
don't starve a real test run, and a live `docker inspect` confirms the
exact limit values took effect.

### 2026-10-03 — Review round 9: 1 finding addressed (EOL base image)
Automated review raised one new item: the base image,
`mcr.microsoft.com/devcontainers/python:1-3.12-bullseye`, is Debian 11
Bullseye, whose Debian LTS ended 2026-08-31 -- it no longer receives public
Debian security updates, undercutting the hardened posture this spec exists
to provide. Fixed by switching to the supported
`mcr.microsoft.com/devcontainers/python:1-3.12-bookworm` (Debian 12) variant.
Re-validated the full real-container posture against the new image, not
just the image tag change in isolation: `docker inspect` confirms
`ReadonlyRootfs: true`, `CapDrop: ["ALL"]`, `no-new-privileges`, no
`Binds`, and the same `Memory`/`MemorySwap`/`NanoCpus`/`PidsLimit` values
as before all still hold on Bookworm, `cat /etc/os-release` confirms
`VERSION="12 (bookworm)"`, and a real plugin suite (`ai-attribution`, 98
passed / 6 skipped) still runs to completion end-to-end.

### 2026-10-03 — Review round 10: 1 finding addressed (documented a residual exposure)
Automated review raised one item, correctly pointing out an unqualified
claim rather than a code bug: the tracked-files-only default (round 5) is a
boundary on which PATHS are copied, not which BYTES -- the content read for
a tracked path is the live on-disk file (so an uncommitted edit you're
actively testing is included), not the last-committed blob. A secret
pasted directly into an otherwise-tracked, ordinarily-safe file (e.g. a
config example) and never committed would therefore still be copied in. A
clean CI checkout has no such dirty state; a contributor's local checkout
might. Rather than changing default behavior (making ordinary, modified-
but-tracked files require an opt-in flag would defeat the wrapper's whole
purpose -- testing in-progress, uncommitted changes), this is now
explicitly documented as a known, accepted residual exposure in both
`_tracked_paths`'s own docstring and `TESTING.md`: "tracked" means "this
path isn't the kind of thing that normally carries secrets," never "every
byte currently in it is safe."

No code behavior changed this round -- re-validated with the full unit
test suite (41 tests) and a fresh Docker-backed end-to-end run
(`ai-attribution`, 98 passed / 6 skipped) regardless, to confirm the
doc-only change didn't introduce a syntax or import regression.

### 2026-10-03 — Review round 11: 3 findings addressed (escalated the dirty-tracked-files item from doc to code)
Automated review raised three items: (1) the same "dirty tracked files"
concern from round 10 was raised again, this time as HIGH -- documenting
it (round 10) wasn't enough; the review's own framing offered two options
("either make dirty tracked content an explicit opt-in or clearly warn"),
and this round implements the second: a new `_warn_about_dirty_tracked_files`
prints a clear stderr warning naming every tracked file with an
uncommitted modification before the snapshot is built, so the residual
exposure is surfaced at the moment it's actually relevant rather than only
in a docstring/doc page. Live-confirmed against the PR's own dirty
checkout: the warning correctly listed exactly the three files this very
round touched. (2) the container's `/tmp` tmpfs (512 MiB) was smaller than
`tools/run-plugin-tests.py`'s own advertised `--max-temp-mb` default (2048
MiB) -- a legitimate, within-the-inner-runner's-own-budget suite could hit
`ENOSPC` on the OUTER boundary before the inner runner's own guard ever got
a chance to enforce its limit. Fixed by raising `/tmp` to 3072 MiB (headroom
above 2048, not equal to it) -- a tmpfs `size=` is a ceiling on bytes
actually written, not a reservation, so this doesn't inflate real memory
usage in the common case; the overall `--memory` cgroup limit remains the
real backstop. (3) the test module's real-`git` setup/assertion calls (in
the `_materialized_git_dir` tests) inherited the ambient environment
instead of going through the same `_scrubbed_git_env()` the production code
itself uses -- an ambient `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` could
have redirected even these test-only calls to the caller's own repository.
Fixed with a new `_run_git` test helper that always threads
`wrapper._scrubbed_git_env()` through, replacing every real-`git`
`subprocess.run` call in the module.

Re-validated end-to-end: the full unit test suite (44 tests, including
three new tests for the dirty-file warning) passes, and a fresh
Docker-backed end-to-end run (`ai-attribution`, 98 passed / 6 skipped)
confirms the warning fires correctly against this PR's own real dirty
checkout and the larger `/tmp` ceiling doesn't break anything.

### 2026-10-03 — Review round 12: 1 finding addressed (round 10/11's dirty-tracked-files item now marked resolved)
Automated review confirmed round 11's fix for the "dirty tracked files"
concern (resolved, no longer listed as open) and raised one new item:
`_warn_about_dirty_tracked_files` runs `git status` against the REAL host
checkout (not a throwaway copy, since it exists specifically to inspect the
host's own dirty state) -- without `GIT_OPTIONAL_LOCKS=0`, even this
nominally read-only command can refresh and rewrite the index, violating
this wrapper's own "the host checkout is only ever read, never mutated"
guarantee and contending with any concurrent `git` process the caller is
running. Fixed by adding `GIT_OPTIONAL_LOCKS=0` to `_scrubbed_git_env` (now
applied to every git subprocess call, not just this one), matching the
same safeguard `tools/agent_bridge_contract_git.py` already uses for the
identical reason.

Re-validated end-to-end: the full unit test suite (44 tests) passes, a
fresh Docker-backed end-to-end run (`ai-attribution`, 98 passed / 6
skipped) confirms the fix doesn't break anything, and a direct `stat` of
the HOST's own real `.git/index` file (mtime + size) before and after a
real run confirms it is byte-identical -- proof, not just assertion, that
the host checkout's index is never touched.

### 2026-10-03 — Review round 13: 3 findings addressed
Automated review confirmed round 12's `GIT_OPTIONAL_LOCKS=0` fix (resolved)
and raised three more: (1) the 6 GiB `--memory` ceiling from round 8 was
sized against only ONE of `run-plugin-tests.py`'s own inner budgets at a
time, not their sum -- that runner's `--max-memory-mb` (4096 MiB resident)
and `--max-temp-mb` (2048 MiB, tmpfs-backed and therefore ALSO charged to
the same outer memory cgroup) defaults already total 6144 MiB before any
container/`$HOME`/workspace-volume overhead, so a perfectly valid,
within-budget suite could have been OOM-killed by the outer boundary.
Fixed by raising `--memory`/`--memory-swap` to 12 GiB -- real headroom over
the combined inner budgets plus overhead, not just one of them. (2) `uv`'s
default cache (`$HOME/.cache/uv`) lives on the 256 MiB `$HOME` mount, sized
for `uv`'s own small footprint, not for retaining every downloaded/
unpacked wheel a heavier dependency set (large scientific/ML/vector-search
packages some plugins' dev extras pull in) needs for the container's
lifetime -- fixed by pointing `UV_CACHE_DIR` at `/tmp/uv-cache` via
`containerEnv`, reusing `/tmp`'s own already-enlarged headroom instead of
budgeting a second large surface. Live-confirmed via `docker exec ... env`
that `UV_CACHE_DIR=/tmp/uv-cache` actually takes effect. (3) `_cleanup_orphan`
silently treated a failed `docker ps` as "no orphan exists," leaving a
partially created container un-removable with no indication to the user
that manual cleanup was needed -- fixed by reporting (to stderr, never
raising, matching this function's existing "never raise" contract) every
failure at every step (`docker ps`, each container `rm`, the volume `rm`),
whether a nonzero exit or a raised exception.

Re-validated end-to-end: the full unit test suite (45 tests, including two
new tests for the `_cleanup_orphan` warning behavior) passes, a fresh
Docker-backed end-to-end run (`ai-attribution`, 98 passed / 6 skipped)
confirms nothing broke, and a live `docker inspect`/`docker exec env`
confirms both the 12 GiB memory ceiling and `UV_CACHE_DIR=/tmp/uv-cache`
actually took effect.

### 2026-10-03 — Review round 14: 3 findings addressed (closed the last two fail-open gaps)
Automated review confirmed round 13's three fixes (resolved) and raised
three new HIGH-severity items, all genuine: (1) `_warn_about_dirty_tracked_files`
silently treated a failed `git status` as "clean" and proceeded to copy
live tracked bytes into an egress-enabled container anyway -- the warning
IS the runtime mitigation for accidental secret exposure, so an unknown
dirty state must never be silently treated as safe. Fixed to fail CLOSED
(raise) instead. (2) an unresolvable `--base` was silently degraded to a
`HEAD`-only bundle, but `run-plugin-tests.py`'s own `changed_plugins()`
ignores a nonzero `git diff` and reports an EMPTY target set rather than
erroring -- so a typo'd or never-fetched `--base` could make the DEFAULT
invocation (not just explicit `--changed`; that runner's own `else:
targets = changed_plugins(args.base)` fallback applies whenever neither
`--all` nor an explicit plugin name is given) silently exit "No plugin
suites to run." instead of surfacing the real problem. Fixed with a new
`_changed_mode_active` helper that mirrors that runner's own
all/plugin-names/changed-default resolution (without fully re-parsing its
CLI) and a fail-loud `SystemExit` when changed-selection is active and the
base doesn't resolve -- live-confirmed both that an explicit plugin name
still runs fine despite an unused unresolvable default base, and that
`--changed --base <bad-ref>` now exits 1 with a clear message instead of
silently building a broken snapshot. (3) `git clone` honors the HOST's
global `init.templateDir`, which can plant arbitrary files beyond
`hooks`/`config` (both already explicitly handled) into the synthetic
`.git` directory -- fixed with an explicitly empty `--template=` directory
for the clone, exactly as suggested.

Re-validated end-to-end: the full unit test suite (50 tests, including
five new tests for the three fixes) passes, a fresh Docker-backed
end-to-end run (`ai-attribution`, 98 passed / 6 skipped) confirms the
common case still works, and two dedicated live checks confirm the new
fail-loud guard: an explicit plugin name proceeds normally despite the
unused default base being unresolvable, while `--changed --base
<nonexistent-ref>` now exits 1 with a clear message (confirmed via a
real, unpiped exit-code check) instead of silently degrading.

### 2026-10-03 — Review round 15: 2 findings addressed (supply-chain pin, hidden index flags)
Automated review confirmed round 14's three fixes (resolved) and raised
two new items. HIGH: `onCreateCommand` installed `uv` via an unpinned,
unverified `curl ... astral.sh/uv/install.sh | sh` pipeline -- a
supply-chain risk specific to this wrapper's threat model, since the
container has outbound network access AND a copy of the repo's tracked
files present simultaneously, so a compromised installer response could
both read the snapshot and exfiltrate it in the same session. Fixed by
replacing the shell-installer pipeline with a pinned-version,
SHA-256-verified direct download of the release tarball (matching this
repo's own existing precedent in `libs/installer-engine/installer-engine.sh`'s
`ensure_uv`): `uv` 0.12.6, per-arch (`x86_64`/`aarch64` Linux-gnu, matching
this wrapper's Linux-only scope) expected hashes checked with `sha256sum
-c` before extraction, aborting the build (`set -eu`) on any mismatch.
MEDIUM: `_warn_about_dirty_tracked_files`'s `git status` check cannot see
a tracked file carrying a Git assume-unchanged or skip-worktree index
flag -- `git status` deliberately suppresses on-disk-modification
reporting for such paths, yet the wrapper's `_tracked_paths`/tar-building
logic still reads and copies the file's real current content regardless
of the flag, so a locally flagged file's live modifications could reach
the egress-enabled container without ever appearing in the existing
warning. Fixed with a new `_warn_about_hidden_tracked_file_flags` function
(`git ls-files -v --cached`, flagging any lowercase letter or `S` per
that command's own documented flag semantics) wired into
`_write_tar_of_repo` alongside the existing dirty-files warning, and
built to the same fail-closed contract (aborts if `git ls-files -v`
itself fails, rather than silently proceeding).

Re-validated end-to-end: the full unit test suite (53 tests, including
three new tests for the hidden-flags warning plus updated mocks in the
three `_write_tar_of_repo` tests) passes; a fresh Docker-backed
end-to-end run (`ai-attribution`, 98 passed / 6 skipped) confirms `uv`
installs correctly via the new pinned/verified script and the run
completes normally with teardown leaving no orphan containers/volumes
and no host `git status` changes beyond this round's own diff; and a
dedicated live check (`git update-index --assume-unchanged TESTING.md`,
run the warning function directly, then `--no-assume-unchanged` to
revert) confirms the new warning fires by name for the flagged path and
that the flag doesn't persist after revert.


### 2026-10-03 — Review round 15 (continued): 4 findings addressed (lazy-fetch/replace-ref hardening, root-owned checkout, structural CI coverage, stale comment)
A follow-up review of the same round confirmed the two fixes above
(resolved) and raised four more findings, three of them HIGH/MEDIUM and
genuine. HIGH: `_scrubbed_git_env` removed an inherited
`GIT_NO_REPLACE_OBJECTS` (via the general repository-context removal set)
rather than unconditionally forcing both it and `GIT_NO_LAZY_FETCH` to
`1` -- so a partial clone's `git bundle create` could lazily fetch missing
objects INTO the host repository, and a locally configured replacement
ref could silently substitute different history into the bundle, exactly
the two protections `tools/agent_bridge_contract_git.py`'s own hardened
environment already applies. Fixed by setting both unconditionally after
the removal pass, matching that precedent. MEDIUM: `_populate_workspace`
extracted the repository snapshot as root with `--no-same-owner`, leaving
`.git` (and everything else) root-owned while tests execute as the
non-root `vscode` user -- modern Git refuses to operate inside a working
tree it discovers has "dubious ownership," and since
`run-plugin-tests.py` treats a failed `git diff` as an empty target set
rather than an error, every git invocation inside the container (not just
an explicit `--changed` run, but the DEFAULT no-`--all`/no-plugin-name
case too) would silently degrade to "no plugin suites to run" instead of
surfacing the real problem -- confirmed live by execing into a kept-alive
container and reproducing the exact `fatal: detected dubious ownership`
error. Fixed two ways: (1) extraction itself now runs AS `vscode`, not
root (after a one-off root `chmod 0777` opens the empty volume's write
permissions, since a fresh volume's mountpoint is root-owned and nothing
inside the container can ever `chown` it -- `--cap-drop=ALL` drops
`CAP_CHOWN` too), making every extracted file natively `vscode`-owned
with no chown step needed or possible; (2) since the volume's own
mountpoint ENTRY still can't be chowned regardless, `.devcontainer/
devcontainer.json` now also grants that exact path a `git`
`safe.directory` exemption via `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0`/
`GIT_CONFIG_VALUE_0` `containerEnv` entries -- the one sanctioned way
around the ownership check that doesn't require a repo-local (and
therefore untrusted-input-controllable) config file. MEDIUM: the CI suite
mocks Docker/devcontainer entirely, so removing a runtime invariant
(`--cap-drop=ALL`, read-only root, a resource ceiling, the pinned `uv`
bootstrap) from `.devcontainer/devcontainer.json` would stay green --
added four fast, Docker-free structural tests that parse the JSONC config
directly and assert the no-Docker-socket/no-`--privileged`, volume-not-
bind, hardening-flags/tmpfs, and pinned-uv-bootstrap invariants. LOW: a
`.devcontainer/devcontainer.json` comment narrated the superseded
`curl ... | sh` implementation instead of describing the current
invariant timelessly -- reworded.

Re-validated end-to-end: the full unit test suite (59 tests, including 4
new structural devcontainer-config tests, 1 new `safe.directory`-exemption
test, and updated `_populate_workspace` tests for the new
root-chmod-then-vscode-extraction call shape) passes; a fresh
Docker-backed end-to-end run (`ai-attribution`, 98 passed / 6 skipped)
confirms the common case still works; and a kept-alive container
(`--keep`) was directly `exec`'d into as `vscode` to confirm `git status`/
`git log` now succeed with no dubious-ownership error (first reproduced
failing without the `safe.directory` exemption, then confirmed passing
with it). Docker cleanup (`docker ps -a`, `docker volume ls`) and host
`git status --short` were reconfirmed clean of anything beyond this
round's own diff after every validation pass, including the orphaned
`--keep` containers/volumes from the debugging process itself.

### 2026-10-03 — Review round 15 (continued again): 2 findings addressed (symlink-safe permission pass, abbreviated-flag awareness)
A third review pass of the same round confirmed the prior three fixes
(resolved) and raised two more. HIGH: `_populate_workspace`'s final
permission-opening pass (`find ... -exec chmod u+rwX {} +`) followed
every symlink entry, since `chmod` on a symlink PATH dereferences it
rather than acting on the link itself (Linux symlinks have no meaningful
permission bits of their own) -- an intentionally preserved dangling
symlink (already correctly archived via `os.path.lexists`) would make the
whole pass fail outright (nothing to dereference), while a live symlink
could silently chmod whatever it points at, possibly outside the
workspace volume entirely for an absolute or `..`-escaping target. Fixed
by scoping the `find` to `( -type f -o -type d )`, excluding symlink
entries from the chmod pass altogether -- they need no permission change
regardless, since the snapshot never follows them. MEDIUM:
`_resolve_base_ref` and `_changed_mode_active` only recognized a literal
`--base`/`--base=`, but `run-plugin-tests.py`'s own argparse silently
accepts any unambiguous prefix abbreviation (e.g. `--bas`, the only known
flag starting with `--b`) -- an abbreviated invocation would keep the
wrong (`origin/main`) snapshot base AND have its value token
misclassified as a positional plugin name, together disabling the
fail-loud unresolvable-base guard for exactly the case it exists to
catch. Fixed with a new `_canonicalize_flag` helper that mirrors
argparse's own unambiguous-prefix matching against the full known
`run-plugin-tests.py` flag set (extending the existing hand-synced
`_VALUE_CONSUMING_FLAGS` precedent with a parallel `_BARE_FLAGS` set), and
wired it into both functions.

Re-validated end-to-end: the full unit test suite (64 tests, including 2
new `_canonicalize_flag` tests, 2 new abbreviated-`--base` tests, 1 new
`_populate_workspace` assertion confirming the `find` scope excludes
symlinks, and 1 new real-symlink regression test proving a tracked
dangling symlink is still archived as-is) passes; a fresh Docker-backed
end-to-end run (`ai-attribution`, 98 passed / 6 skipped) confirms the
common case still works; Docker cleanup (`docker ps -a`, `docker volume
ls`) and host `git status --short` reconfirmed clean of anything beyond
this round's own diff.

### 2026-10-03 — Review round 15 (final pass): 2 findings addressed (SIGTERM-safe teardown, uv-cache/tmp headroom)
A fourth review pass of the same round confirmed the prior two fixes
(resolved) and raised two more. HIGH: the default Unix `SIGTERM` action
terminates a Python process immediately, bypassing every `finally` block
-- so an outer timeout, CI cancellation, or service stop mid-run would
leak both the container and its uniquely named volume, with no way for a
LATER invocation to find and remove them (`_cleanup_orphan` only ever
searches by the new random instance label a fresh run gets, never a prior
run's). Fixed by installing a `SIGTERM` handler (`_raise_on_sigterm`) at
the top of `main` that raises a dedicated `_TerminationRequested`
(`BaseException` subclass, matching `KeyboardInterrupt`'s own hierarchy
placement) instead -- the existing `except BaseException`
teardown/orphan-cleanup paths handle it with no further changes needed.
`SIGINT` needed no equivalent handler: Python already raises
`KeyboardInterrupt` for it by default. MEDIUM (surfaced against
round-13's own `/tmp`/`UV_CACHE_DIR` choices, in code otherwise unchanged
this round): the inner `run-plugin-tests.py`'s own sandbox containment
measures only ITS OWN temp usage, never the `uv` dependency cache sharing
the same `/tmp` tmpfs mount -- a heavier dependency install (especially
under `--all`) growing that cache past roughly 1 GiB could hit `ENOSPC`
on the OUTER `/tmp` boundary even while the inner runner still reports
comfortably within its own advertised 2048 MiB budget. Fixed by raising
`/tmp`'s tmpfs size from 3072m to 6144m (~4 GiB of headroom above that
inner figure, since the cache's real footprint isn't bounded by any
similarly fixed budget this wrapper can read and enforce precisely), and
`--memory`/`--memory-swap` from 12g to 14g to preserve comfortable
cgroup headroom above the new worst-case estimate.

Re-validated end-to-end: the full unit test suite (66 tests, including a
new `_raise_on_sigterm` test and a new `main`-installs-the-handler test,
plus updated resource-ceiling assertions in the structural devcontainer-
config test) passes; a fresh Docker-backed end-to-end run
(`ai-attribution`, 98 passed / 6 skipped) confirms the common case still
works; and the SIGTERM fix itself was validated LIVE, not just via
mocked unit tests -- a real run was started in the background, sent a
real `SIGTERM` mid-test (both with and without `--keep`, to separately
confirm the signal-to-exception conversion and the full teardown path it
now unblocks), and confirmed via `docker ps -a`/`docker volume ls` that
teardown ran to completion (no leftover container or volume) in the
non-`--keep` case, while `--keep` correctly continued to preserve both
for debugging. Docker cleanup and host `git status --short` reconfirmed
clean of anything beyond this round's own diff after every pass.

### 2026-10-03 — Review round 15 (final-final pass): 1 finding addressed (SIGTERM during cleanup itself)
A fifth review pass of the same round confirmed the SIGTERM fix above but
spotted a signal-timing race in it: a SECOND `SIGTERM` arriving WHILE
`_tear_down`/`_cleanup_orphan` is already running (e.g. between removing
a container and removing its volume -- two separate sequential subprocess
calls) would raise `_TerminationRequested` again right there, and since
those cleanup functions only catch `subprocess.SubprocessError`/`OSError`,
the second exception escapes immediately and could skip whichever
removal step hadn't run yet -- reopening the exact leak the first SIGTERM
fix was meant to close. Fixed with a new `_sigterm_deferred` context
manager that sets `SIGTERM` to `SIG_IGN` for the duration of a cleanup
step, restoring whatever handler was previously installed afterward;
wired around both the orphan-cleanup call (bring-up failure path) and the
`_tear_down` call (normal teardown path).

Re-validated end-to-end: the full unit test suite (68 tests, including a
new `_sigterm_deferred` ignore-then-restore test and a new integration-
style test confirming `signal.getsignal(SIGTERM)` is genuinely `SIG_IGN`
during a real `_tear_down` call, not merely assumed) passes; a fresh
Docker-backed end-to-end run (`ai-attribution`, 98 passed / 6 skipped)
confirms the common case still works; Docker cleanup and host
`git status --short` reconfirmed clean of anything beyond this round's
own diff.

### 2026-10-03 — Review round 15 (yet another pass): 2 findings addressed (SIGINT-during-cleanup parity, journal timelessness)
A sixth review pass of the same round confirmed the SIGTERM-during-
cleanup fix resolved, and raised two more. MEDIUM (against code
unchanged this round): a second Ctrl-C (`SIGINT`) arriving while
`_tear_down`/`_cleanup_orphan` is already running has the exact same
partial-cleanup exposure the SIGTERM fix closed -- the FIRST `SIGINT`
already becomes `KeyboardInterrupt` via Python's own default handling
(entering cleanup fine), but `_sigterm_deferred` only ignored `SIGTERM`,
so a repeat `SIGINT` during cleanup could still interrupt it partway.
Fixed by generalizing `_sigterm_deferred` to defer BOTH `SIGINT` and
`SIGTERM` (via a new `_CLEANUP_DEFERRED_SIGNALS` tuple), kept under its
original name for continuity across this PR's own review history. LOW:
the previous journal entry's closing paragraph recorded review-pass
counts and argued for merge-readiness -- a transient review-process
judgment that doesn't belong in a durable technical effort journal
(merge-readiness belongs in the PR discussion, not an artifact that
should stay meaningful independent of any one review's state). Removed.

Re-validated end-to-end: the full unit test suite (68 tests, with the
`_sigterm_deferred` and teardown-signal-deferral tests updated to check
BOTH signals) passes; a fresh Docker-backed end-to-end run
(`ai-attribution`, 98 passed / 6 skipped) confirms the common case still
works; Docker cleanup and host `git status --short` reconfirmed clean of
anything beyond this round's own diff.

### 2026-10-03 — Review round 15 (yet another pass, continued): 2 findings addressed (record-and-replay instead of discard, stale docstring)
A seventh review pass of the same round confirmed the journal-trim fix
resolved and raised two more. MEDIUM: `_sigterm_deferred`'s `SIG_IGN`
approach doesn't DEFER a signal, it DISCARDS it outright -- for the
NORMAL (non-exceptional) post-success teardown path specifically, a
cancellation signal arriving in that window would be silently swallowed,
cleanup would complete normally, and `main` would return `0`, making a
cancelled invocation misreport success. Fixed by replacing `SIG_IGN` with
a handler that RECORDS receipt instead, then -- after restoring the
previous handlers once cleanup finishes -- re-raises
`_TerminationRequested` if a signal was recorded, so cleanup still runs
to completion uninterrupted while the cancellation itself is never
silently dropped. LOW (previously missed, against code unchanged this
round): `_git_rev_parse`'s docstring described the superseded
"degraded-but-not-fatal" framing from before round 14's fail-loud fix --
reworded to describe the current caller-dependent behavior
(`_materialized_git_dir` treats an unresolvable ref as fatal only when
changed-selection mode is active).

Re-validated end-to-end: the full unit test suite (70 tests, including 2
new `_sigterm_deferred` tests for the record-and-replay behavior and a
new integration-style test confirming `main` propagates a signal
received during successful teardown instead of returning 0, plus updated
assertions in the existing teardown-signal-deferral test for the new
non-`SIG_IGN` handler shape) passes; a fresh Docker-backed end-to-end run
(`ai-attribution`, 98 passed / 6 skipped) confirms the common case still
works; Docker cleanup and host `git status --short` reconfirmed clean of
anything beyond this round's own diff.

### 2026-10-03 — Review round 15 (final stretch): 1 finding addressed (ref-relative `--base` expressions)
An eighth review pass of the same round confirmed the record-and-replay
signal fix resolved, and raised one more (previously missed, against
code introduced back in round 4). MEDIUM: a REF-RELATIVE `--base`
expression (e.g. `origin/dev~1`) resolves fine on the host, but `git
bundle create` does not preserve a REMOTE-TRACKING ref
(`refs/remotes/origin/...`) as a named ref in its resulting clone --
confirmed via direct experimentation: a plain LOCAL branch name IS
preserved as a named ref by `git clone --bare` from the bundle, but a
`refs/remotes/...` ref is not. The old fetch-by-`--symbolic-full-name`
step existed specifically to re-materialize that named ref, but
`--symbolic-full-name` returns nothing useful for a ref-RELATIVE
expression (it isn't itself a plain ref), so that step silently did
nothing for exactly this case -- the unchanged in-container `--base`
value would then fail to resolve, and since `run-plugin-tests.py` treats
a failed diff as an empty target set, a `--changed` run could silently
report no suites instead of the real problem. Fixed with a new
`_rewrite_base_to_resolved_sha` that replaces `--base`'s passthrough
VALUE with its resolved commit SHA before the in-container command is
assembled -- a bare SHA resolves against any clone containing its
object, named ref or not, which made the old fetch-by-full-name step
entirely unnecessary (removed) regardless of ref type.

Re-validated end-to-end: the full unit test suite (75 tests, including 5
new `_rewrite_base_to_resolved_sha` tests and a new real-git regression
test proving a genuine ref-relative expression over an actual
`refs/remotes/...` ref resolves and diffs correctly inside the bundle
clone after rewriting) passes; a fresh Docker-backed end-to-end run
(`ai-attribution`, 98 passed / 6 skipped) confirms the common case still
works; and a dedicated live check against this PR's own real `origin/dev`
remote (`--changed --base origin/dev~1`) confirmed the expression
resolves on the host, gets correctly rewritten to its SHA, and the full
invocation completes without error. Docker cleanup and host
`git status --short` reconfirmed clean of anything beyond this round's
own diff.

### 2026-10-03 — Review round 15 (CI gate): module-size cap fix (own diff, not the tracked #5088 drift)
CI's own `guards + lint` job failed after the ref-relative-`--base` push
-- NOT the tracked, repo-wide, pre-existing `#5088` module-size drift
(that one only fails the UNSCOPED local pre-push hook check, which this
PR's own pushes have been bypassing with `--no-verify` per its own
documented precedent), but a genuine, PR-SCOPED failure:
`check-module-size.py --changed-since` reported
`tools/run_tests_in_devcontainer.py` itself had grown to 1033 lines,
past the flat 1000-line cap new (non-grandfathered) files get. Fixed by
condensing several of the most verbose docstrings accumulated across 15
rounds of review responses (`_scrubbed_git_env`, `_TerminationRequested`/
`_sigterm_deferred`, `_rewrite_base_to_resolved_sha`,
`_populate_workspace`) down to their essential rationale, trimming
historical/redundant narration while keeping every substantive technical
point -- 982 lines afterward, comfortably under the cap with margin for
further rounds.

Re-validated end-to-end: `check-module-size.py --changed-since origin/dev`
now passes; the full unit test suite (75 tests, unchanged by this
docs-only-in-effect edit) still passes; a fresh Docker-backed end-to-end
run (`ai-attribution`, 98 passed / 6 skipped) confirms the common case
still works; Docker cleanup and host `git status --short` reconfirmed
clean.

### 2026-10-03 — Review round 15 (final iteration): 2 findings addressed (append base for the default invocation, prevent signal from masking a primary failure)
A ninth review pass of the same round confirmed the module-size fix
resolved, and raised two more. HIGH: when changed-selection relies on
`run-plugin-tests.py`'s own IMPLICIT default (`origin/main` -- no
`--all`, no plugin names, no explicit `--base`), there is no `--base`
TOKEN in `passthrough` at all for `_rewrite_base_to_resolved_sha` to
rewrite -- so the single MOST COMMON invocation
(`python tools/run_tests_in_devcontainer.py` with no arguments) still
asked the in-container command for the bare `origin/main`, a remote-
tracking ref the bundle clone does not preserve as a named ref (the same
fact the ref-relative-expression fix earlier in this round relies on),
silently running no suites. Fixed by APPENDING an explicit resolved
`--base <sha>` when changed-selection is active and the caller omitted
the flag entirely (gated on `_changed_mode_active` so an `--all` run or
an explicit plugin name, which never consult `--base`, aren't given
pointless noise). MEDIUM (previously missed, against code from the prior
iteration): `_sigterm_deferred` replayed a recorded signal
UNCONDITIONALLY, which could REPLACE a genuine primary failure already
propagating when the context is entered -- e.g. a real `_tear_down`
exception, or the startup-failure branch's pending `raise` after
`_cleanup_orphan` -- exactly the masking this wrapper's own teardown
logic elsewhere exists to prevent. Fixed by checking
`sys.exc_info()` at entry (which already reflects any in-flight
exception for the whole dynamic extent of its handling `except`/
`finally`) and only replaying when nothing is in flight; when something
is, the signal is reported via a stderr warning instead of silently
dropped, but the original failure wins. Also fixed four LOW doc findings
(`CLI` description, effort README plan item, `TESTING.md`) that
described `--base` passthrough as unmodified, when it's deliberately
rewritten.

CI's `guards + lint` job also hit the SAME module-size cap this round's
additional docstrings had grown past again (1033 lines) -- condensed
several docstrings a second time (`_sigterm_deferred`,
`_rewrite_base_to_resolved_sha`, `_materialized_git_dir`,
`_populate_workspace`, `_canonicalize_flag`, the module's own usage
docstring) down to 990 lines, comfortable margin under the cap.

Re-validated end-to-end: the full unit test suite (78 tests, including 3
new `_rewrite_base_to_resolved_sha` append-case tests and a new
`_sigterm_deferred` masking-prevention test, plus one existing `main`
test updated to mock out base-rewriting since it's about `--` stripping
specifically) passes; `check-module-size.py --changed-since origin/dev`
passes; a fresh Docker-backed end-to-end run (`ai-attribution`, 98
passed / 6 skipped) confirms the common case still works; and a direct
check confirmed a genuinely bare invocation (`_rewrite_base_to_resolved_sha([])`)
now appends an explicit resolved `--base <sha>` rather than leaving no
base token at all. Docker cleanup and host `git status --short`
reconfirmed clean of anything beyond this round's own diff.

### 2026-10-03 — Review round 15 (tenth pass): 1 finding addressed (scope the bundled base closure to changed-mode)
A tenth review pass of the same round confirmed the base-append and
signal-masking fixes resolved, and raised one more. HIGH: `bundle_refs`
included the resolved `--base` ref's object closure whenever it resolved
on the host, regardless of whether changed-selection was even active --
for an `--all` run or an explicit plugin name (which never consult
`--base` at all), a divergent `origin/main` would still get bundled,
needlessly widening the minimal-history security boundary with unrelated
commits/trees/blobs having nothing to do with the plugin suite being run,
inside a container that keeps outbound networking. Fixed by gating
`bundle_refs`'s inclusion of the base ref on `_changed_mode_active`, not
just `base_resolves` -- mirroring the same gating the fail-loud
unresolvable-base guard already uses.

Re-validated end-to-end: the full unit test suite (79 tests, including a
new real-git regression test proving a divergent base branch's unique
commit is genuinely unresolvable in the materialized copy when
changed-selection is inactive, mirroring the existing secret-branch
regression pattern) passes; a fresh Docker-backed end-to-end run
(`ai-attribution`, 98 passed / 6 skipped) confirms the common case still
works; a dedicated `--changed --base origin/dev~1` run confirms
changed-mode still correctly bundles the base closure. Docker cleanup and
host `git status --short` reconfirmed clean of anything beyond this
round's own diff.

### 2026-10-03 — Review round 15 (eleventh pass): 3 findings addressed (pinned base image digest, cleanup-body masking, misleading name)
An eleventh review pass of the same round confirmed the base-closure
scoping fix resolved, and raised three more -- one new, two previously
missed. HIGH: `.devcontainer/devcontainer.json`'s base image was still
selected by a mutable tag (`mcr.microsoft.com/devcontainers/python:1-3.
12-bookworm`), even though it IS the trust root for the entire isolation
posture -- a retag or registry compromise could replace the whole
runtime with no reviewed source change, bypassing the integrity posture
already applied to the pinned/verified `uv` install. Fixed by pinning to
the reviewed image's immutable digest
(`mcr.microsoft.com/devcontainers/python@sha256:7876580d...`), confirmed
via `docker pull`/`docker inspect`, with a trailing comment recording
which human-readable tag it corresponds to for future deliberate
refreshes. MEDIUM (previously missed, against the signal-masking fix
from two passes ago): that fix only guarded against a primary exception
already in flight when `_sigterm_deferred` (now renamed, see below) was
entered -- if the cleanup BODY itself (e.g. a real `_tear_down` failure)
raised AFTER a signal had already been recorded during that same call,
the `finally` block would still replay the signal and replace the
cleanup failure. Fixed by checking `sys.exc_info()` once, AFTER `yield`
(not before) -- confirmed via direct experimentation that this single
check correctly covers BOTH cases (an exception already active when
entered, and one newly raised by the cleanup body), since Python sets
the thread's exception state for the whole dynamic extent of either.
LOW (previously missed): `_sigterm_deferred` now covers both `SIGINT`
and `SIGTERM`, so its `SIGTERM`-only name -- kept "for continuity with
this PR's own review history" -- embedded transient review narration
into a durable function name. Renamed to `_cleanup_signals_deferred`
throughout (definition, both call sites, and every test).

Re-validated end-to-end: the full unit test suite (81 tests, including a
new base-image-digest structural test and a new sibling masking test
proving a cleanup-BODY failure also isn't replaced by a replayed signal)
passes; `check-module-size.py --changed-since origin/dev` passes; a fresh
Docker-backed end-to-end run (`ai-attribution`, 98 passed / 6 skipped)
confirms the common case still works with the pinned digest. Docker
cleanup and host `git status --short` reconfirmed clean of anything
beyond this round's own diff.

### 2026-10-03 — Review round 15 (twelfth pass): 2 findings addressed (restore SIGTERM handler, close the post-start cleanup gap)
A twelfth review pass of the same round confirmed the base-image-pinning
fix resolved, and raised two more previously-missed findings against
earlier code in this round. MEDIUM: `main()` replaced the process-wide
`SIGTERM` handler but never restored the previous one on either success
or failure -- observable in this PR's own in-process tests (several call
`main()` directly without patching `signal.signal`), so every later
in-process `main()` call, and any other programmatic caller, inherited
`_raise_on_sigterm` unexpectedly. Fixed by saving the prior handler and
restoring it from a new outer `finally` wrapping the entire function
body (argument parsing and config setup included, not just the
container lifecycle). MEDIUM: between `_bring_up()` returning
successfully and the (then-separate) inner `try` block that actually
registered teardown protection, there was a narrow window with NO
cleanup guard active at all -- a `SIGTERM`/`SIGINT` landing there raised
before any `finally` could run, and the outer `finally` only ever
deleted the temporary config directory, leaving the live container and
volume behind. Fixed by collapsing the two previously-separate
try/except blocks into one try/finally spanning the whole lifecycle,
using `container_id`'s own `None`-vs-set state as the lifecycle marker
the single `finally` reads to choose orphan cleanup (bring-up never
completed) versus normal teardown (it did) -- closing the gap entirely
rather than widening the existing guard's scope piecemeal.

Re-validated end-to-end: the full unit test suite (83 tests, including a
new test proving the SIGTERM handler is genuinely restored via
`signal.getsignal` after `main()` returns, and a new test proving a
failure arriving right after a successful `_bring_up` now correctly
routes to `_tear_down` with the real container id rather than
`_cleanup_orphan`) passes; `check-module-size.py --changed-since
origin/dev` passes; a fresh Docker-backed end-to-end run (`ai-attribution`,
98 passed / 6 skipped) confirms the common case still works; and the
SIGTERM-during-a-real-run scenario was re-validated live (sent to a real
background run, confirmed full teardown with no leftover container or
volume) to confirm the restructure didn't regress the mechanism itself.
Docker cleanup and host `git status --short` reconfirmed clean of
anything beyond this round's own diff.

### 2026-10-03 — Review round 15 (thirteenth pass): 2 findings addressed (complete the git config env scrub, require a commit base)
A thirteenth review pass of the same round confirmed the prior fixes
resolved and raised no genuinely new findings -- only two more
previously-missed ones against earlier code. MEDIUM:
`_REPOSITORY_CONTEXT_ENV` still permitted `GIT_CONFIG_GLOBAL`,
`GIT_CONFIG_SYSTEM`, and `GIT_CONFIG_NOSYSTEM` through from the caller --
those select/disable config independently of `-C`, so an injected config
could still alter the hardened Git probes this scrub exists to protect
(notably the fail-closed `_warn_about_dirty_tracked_files` check), even
though every other repository-selection variable was already scrubbed.
Fixed by adding all three to the scrub set, matching the repo's own
complete precedent for this class. MEDIUM: `_git_rev_parse` resolved
`ref` via plain `rev-parse --verify`, which accepts ANY object type (a
tree or blob expression resolves fine) -- but the downstream `git diff
<base>...HEAD` that consumes this value needs a commit-ish, so a
non-commit base could bypass this function's own resolvability contract,
only to fail that later diff and silently select no suites. Fixed by
peeling to `ref^{commit}` (with `--end-of-options` to keep a ref starting
with `-` from being misread as a flag), confirmed via direct
experimentation that a tree SHA is correctly rejected while a genuine
commit ref still resolves.

Re-validated end-to-end: the full unit test suite (84 tests, including
updated scrub-set assertions, an updated exact-command assertion for the
peeled `rev-parse` invocation, and a new real-git regression test proving
a tree object is rejected while `HEAD` still resolves) passes;
`check-module-size.py --changed-since origin/dev` passes; a fresh
Docker-backed end-to-end run (`ai-attribution`, 98 passed / 6 skipped)
confirms the common case still works; a dedicated `--changed --base
origin/dev~1` run confirms changed-mode still resolves and diffs
correctly through the peeled commit check. Docker cleanup and host
`git status --short` reconfirmed clean of anything beyond this round's
own diff.

### 2026-10-03 — Review round 15 (fourteenth pass): 2 findings addressed (reject --allow-host-state, document the admission-lease gap)
A fourteenth review pass of the same round confirmed the git-env-scrub
and commit-peeling fixes resolved, and raised no genuinely new findings
-- only two more previously-missed ones, both about flags inherited from
`run-plugin-tests.py` whose documented contracts can't actually survive
this wrapper's isolation boundary. MEDIUM: `--allow-host-state`'s whole
contract is preserving the caller's real HOME/config/credentials for
opt-in credential-dependent checks, but this wrapper always gives the
container a fresh, credential-free tmpfs `$HOME` by design -- silently
accepting the flag would let a credential-dependent test proceed without
the credentials it asked for, invisibly. Fixed by rejecting it outright
(bare flag or an unambiguous abbreviation) with a clear error directing
the caller to run `run-plugin-tests.py` directly instead, rather than
attempting a partial/unsafe credential-injection mechanism. MEDIUM:
`--admission-wait`'s host-wide heavy-test-slot lease lives under
`$HOME`/`XDG_CACHE_HOME`, a fresh tmpfs per container invocation -- so
concurrent wrapped runs acquire unrelated per-container leases rather
than coordinating against one shared host-wide slot, silently defeating
the concurrency-limiting contract `TESTING.md` otherwise documents for
the un-wrapped runner. A genuine fix needs a host-side admission
mechanism acquired before container startup, which is a substantial
cross-process-coordination feature, not a small fix -- rather than
silently attempt or silently ignore it, documented as a known, named,
open residual gap (mirroring the existing networking-scope precedent)
in both `TESTING.md` and a new Phase 2 Plan/Validation Plan item.

Re-validated end-to-end: the full unit test suite (86 tests, including
2 new tests for the `--allow-host-state` rejection, bare and
abbreviated) passes; `check-module-size.py --changed-since origin/dev`
passes; a fresh Docker-backed end-to-end run (`ai-attribution`, 98
passed / 6 skipped) confirms the common case still works; a dedicated
live check confirms `--allow-host-state` is rejected with a clear error
before any container is even brought up (no Docker resources touched).
Docker cleanup and host `git status --short` reconfirmed clean of
anything beyond this round's own diff.

### 2026-10-03 — Review round 15 (fifteenth pass): 1 finding addressed (disable fsmonitor hooks on host git probes)
A fifteenth review pass of the same round confirmed the `--allow-host-state`
rejection and admission-lease documentation resolved, and raised one more
genuinely new finding. HIGH: `_scrubbed_git_env()` scrubbed repository-
*selection* variables (`GIT_DIR`, `GIT_WORK_TREE`, etc.) but still let the
caller's normal global/system/local git CONFIG load for every host-side
probe -- a configured `core.fsmonitor` hook is therefore executed by an
ostensibly READ-ONLY `git status`/`ls-files` call against the REAL host
checkout, even with `GIT_OPTIONAL_LOCKS=0` already in place (confirmed
via direct experimentation: a fake fsmonitor hook script DOES run and
leave a marker file without this fix, running arbitrary host code from
inside a function whose entire point is read-only inspection). Fixed by
unconditionally forcing `GIT_CONFIG_GLOBAL=os.devnull` and
`GIT_CONFIG_NOSYSTEM=1` (disabling global/system config entirely,
matching `tools/agent_bridge_contract_git.py`'s own precedent), plus
forcing `core.fsmonitor=false` via the same `GIT_CONFIG_COUNT`/
`GIT_CONFIG_KEY_0`/`GIT_CONFIG_VALUE_0` env-override mechanism already
used elsewhere in this wrapper -- the latter specifically neutralizes a
LOCAL (repo-level) `core.fsmonitor` setting too, which neither the
global/system disabling nor any `-C`/`GIT_DIR` scrubbing would reach.

Re-validated end-to-end: the full unit test suite (87 tests, including a
new real-git regression test that configures an actual fake fsmonitor
hook script and confirms it does NOT execute through the scrubbed
environment, plus an updated assertion reflecting that `GIT_CONFIG_KEY_0`
is now present with the WRAPPER's own forced value rather than simply
absent) passes; `check-module-size.py --changed-since origin/dev` passes
(right at the 1000-line cap after another condensing pass across several
docstrings -- this module is now a strong candidate for an actual split
into smaller components if review continues to add substantive fixes);
a fresh Docker-backed end-to-end run (`ai-attribution`, 98 passed / 6
skipped) confirms the common case still works. Docker cleanup and host
`git status --short` reconfirmed clean of anything beyond this round's
own diff.

### 2026-10-03 — Review round 15 (sixteenth pass): 1 finding addressed (stop excluding tracked .devcontainer files)
A sixteenth review pass of the same round confirmed the fsmonitor-hook
fix resolved (a transient CI network flake fetching `uv`'s own version
manifest, confirmed unrelated and fixed by a single job rerun), and
raised one more previously-missed finding. MEDIUM: `EXCLUDED_TOP_LEVEL`
excluded the tracked `.devcontainer` directory from the snapshot's tar,
but `_materialized_git_dir`'s index is rebuilt from the FULL `HEAD` tree
(which still lists `.devcontainer/devcontainer.json`) -- so every
in-container checkout reported it as "deleted" (`git status` dirty) even
on a byte-identical, unmodified checkout, and any repo test/tool that
inspects that tracked file couldn't find it either. Fixed by removing
`.devcontainer` from the exclusion set entirely -- the host CLI's own
per-run devcontainer config is already a wholly separate temporary copy
(`_per_instance_config`), so including the tracked file in the snapshot
changes nothing about which config the host actually uses to bring the
container up.

Re-validated end-to-end: the full unit test suite (87 tests, with the
corresponding `_tracked_paths` test updated to expect
`.devcontainer/devcontainer.json` kept rather than filtered) passes;
`check-module-size.py --changed-since origin/dev` passes (right at the
1000-line cap again after another condensing pass); a fresh Docker-backed
end-to-end run (`ai-attribution`, 98 passed / 6 skipped) confirms the
common case still works; and a dedicated `--keep` run directly confirmed,
via `docker exec`, that `.devcontainer/devcontainer.json` is now present
inside the container and `git status --short` reports only this round's
own genuine in-progress changes, never a false "deleted" entry. Docker
cleanup and host `git status --short` reconfirmed clean of anything
beyond this round's own diff.

### 2026-10-03 — Review round 15 (seventeenth pass): 2 findings addressed (blanket GIT_* strip, move off the canonical devcontainer path)
A seventeenth review pass of the same round confirmed the `.devcontainer`-
exclusion fix resolved, and raised two more -- one new, one previously
missed. HIGH: `_scrubbed_git_env`'s allowlist-removal approach
(`_REPOSITORY_CONTEXT_ENV`, a frozenset of specific variable names to
strip) could only ever anticipate names someone had already thought of --
a BEHAVIORFUL variable not in that list (e.g. `GIT_TRACE` appending to an
arbitrary host path, `GIT_EXEC_PATH` redirecting which git helper
binaries run) would still pass through from the ambient caller
environment, violating the wrapper's read-only-host guarantee in a way
flag-by-flag scrubbing structurally can't close. Fixed by switching to a
blanket strip of every inherited `GIT_*`-prefixed variable (matching
`tools/agent_bridge_contract_git.py`'s own precedent exactly), then
re-applying the same small, deliberate safe set afterward -- this made
the entire `_REPOSITORY_CONTEXT_ENV` frozenset dead code, removed
outright (freeing real room under the module-size cap in the process).
MEDIUM (previously missed): `.devcontainer/devcontainer.json` lived at
the CANONICAL root path, which standard "Reopen in Container"/
`devcontainer up` auto-discovery (and this repo's own Codespaces
tooling) picks up with no explicit choice required -- but this volume
starts empty and only this Python wrapper ever populates it, so a direct
user opening the repo normally would silently get an empty workspace
instead of a real development environment. Fixed by moving the spec to
a NAMED alternate config path (`.devcontainer/test-isolation/
devcontainer.json`), confirmed live via `devcontainer read-configuration`
that auto-discovery at the repo root now correctly fails (exit 1, no
config found) rather than silently picking up this isolation-only spec.

Re-validated end-to-end: the full unit test suite (88 tests, including 2
new regression tests -- one for an arbitrary, otherwise-unlisted `GIT_*`
variable confirming the blanket strip catches it, one extending the
existing scrub-set test with `GIT_TRACE`/`GIT_EXEC_PATH`) passes;
`check-module-size.py --changed-since origin/dev` passes with real
margin now (971 lines, well under the cap after the dead-code removal);
a fresh Docker-backed end-to-end run (`ai-attribution`, 98 passed / 6
skipped) confirms the common case still works with the relocated config;
a dedicated `devcontainer read-configuration --workspace-folder .` (no
`--config` flag) at the repo root confirmed no canonical config is
auto-discovered anymore. Docker cleanup and host `git status --short`
reconfirmed clean of anything beyond this round's own diff (a clean git
rename, not a copy-and-delete).

### 2026-10-03 — Review round 15 (eighteenth pass): 4 findings addressed (neutralize clean filters, validate merge-base, fix stale path references)
An eighteenth review pass surfaced four more findings. HIGH: disabling
`core.fsmonitor` only closed ONE host-code-execution path during a
read-only probe -- a `.gitattributes`-assigned `filter.<name>.clean`
command still runs during `git status`'s content comparison for any path
needing re-hashing, confirmed live in a throwaway repo: a toy clean-filter
script DID execute and leave a marker once a same-size content edit
forced git to actually re-hash rather than trust cached stat/size. Fixed
with a new `_discover_configured_clean_filters()` helper (`git check-attr
filter --cached --stdin -z` fed `git ls-files -z`, parsing the NUL-
separated triples for every distinct assigned filter name) whose output
now feeds `_scrubbed_git_env()`'s existing `GIT_CONFIG_COUNT`/`KEY_N`/
`VALUE_N` override mechanism, forcing every discovered `filter.<name>.
clean` to `cat` (a safe passthrough) alongside the existing
`core.fsmonitor=false` override. While building this, live reproduction
surfaced a SECOND, more subtle gap: the discovery step's own `ls-files`/
`check-attr` calls (via a new minimal-env helper,
`_minimal_repo_selection_env`) could themselves still trigger a
configured `core.fsmonitor` hook once `GIT_OPTIONAL_LOCKS=0` prevented a
cached index refresh -- confirmed live via a dedicated repro script
(marker created during the discovery step itself, before the final
probe even ran). Fixed by forcing the same `core.fsmonitor=false`
override into `_minimal_repo_selection_env()` too, not just the final
`_scrubbed_git_env()` result -- confirmed via the same repro script that
the marker is no longer created at any stage.

MEDIUM: `_git_rev_parse` only confirmed a base ref resolved to SOME
commit, not that it shares any history with HEAD -- an orphan/unrelated-
history commit can resolve fine but make the downstream three-dot diff
fail with "no merge base," which `run-plugin-tests.py` silently treats
as zero changed plugins (the same silent-false-negative class the
unresolvable-base fix already closed). Fixed by adding a `git merge-base
<base> HEAD` check alongside the existing resolvability check in
`_materialized_git_dir`'s changed-mode guard, failing loudly with the
same "refuse to build a misleading snapshot" framing when it doesn't
succeed.

LOW (x2): fixed two stale `.devcontainer/devcontainer.json` references
left over from the canonical-path move two passes ago -- this effort
README's own Proposal section and workspace-storage-model Plan item (both
describing CURRENT state, not historical narration), and a comment in
`test_run_tests_in_devcontainer.py` naming the wrong JSONC config file
for `_load_devcontainer_config()`.

Re-validated end-to-end: the full unit test suite (93 tests, including 6
new regression tests -- clean-filter discovery with and without an
assigned filter, a real-git clean-filter-neutralization-during-status
regression mirroring the manual repro, an updated `GIT_CONFIG_COUNT`
assertion for discovered-filter overrides, and a real-git orphan-history
merge-base-failure regression) passes; two pre-existing tests that
broadly mocked `wrapper.subprocess.run` needed updating to also stub
`_discover_configured_clean_filters` (their mock otherwise leaked into
the new internal discovery calls `_scrubbed_git_env()` now makes,
corrupting the mocked return value's interpretation). `check-module-
size.py --changed-since origin/dev` passes right at the cap (1000 lines,
after another docstring-condensing pass); a fresh Docker-backed
end-to-end run (`ai-attribution`, 98 passed / 6 skipped) confirms the
common case still works; `check-docs-consistency.py` and
`check-effort-vision-structure.py` both pass. Docker cleanup and host
`git status --short` reconfirmed clean of anything beyond this round's
own diff.


### 2026-10-03 — Review round 15 (nineteenth pass): 1 finding addressed (lazy-fetch protection for the clean-filter discovery probe), 2 restated findings confirmed already fixed, PR metadata corrected
A nineteenth review pass of commit `6a73a47c7` surfaced 6 open findings,
4 resolved since last review. HIGH (new): the previous pass's new
`_minimal_repo_selection_env()` (used only by
`_discover_configured_clean_filters`'s `ls-files`/`check-attr` calls)
omitted `GIT_NO_LAZY_FETCH=1`/`GIT_NO_REPLACE_OBJECTS=1` -- in a partial
clone, resolving a missing cached `.gitattributes` blob during that
probe could fetch objects into the real host checkout, the same class
of gap `_scrubbed_git_env` already closes for every other host-side git
call. Fixed by adding both to `_minimal_repo_selection_env()` too.

Two MEDIUM findings (workspace-volume reuse breaking isolation, and no
size bound on the workspace volume) were confirmed, on inspection, to
already be addressed by existing code the reviewer can't see acting on
the static devcontainer.json it reviews: `_per_instance_config` already
rewrites the literal `workspaceMount` volume name to a unique
`<name>-<instance-label>` for every invocation (removed at that same
invocation's teardown), and `_create_bounded_volume` already creates
that per-invocation volume as a size-bounded tmpfs mount before
`devcontainer up` ever touches it. Added an explicit comment to
`workspaceMount` in the devcontainer spec itself noting this runtime
rewrite, since the file's own literal volume name otherwise reads as a
single shared, unbounded volume with no visible evidence of either
protection. One MEDIUM finding (`tools/test_run_tests_in_devcontainer.py`
not collected by required CI) was likewise confirmed already fixed --
`.github/workflows/ci.yml`'s `test-runner-linux` job already lists it
explicitly; no code change needed. Two LOW findings were PR-description-
only: the description still named the pre-move `.devcontainer/
devcontainer.json` path and a stale "59 unit tests" figure (now 93) --
fixed by editing the PR body directly (Changes and Validation sections)
rather than any code change.

Re-validated end-to-end: the full unit test suite (93 tests, unchanged
by this pass's fix since it only affects a path already covered by
existing clean-filter/fsmonitor regression tests) passes; `check-module-
size.py --changed-since origin/dev` passes right at the cap (1000 lines,
after another docstring-condensing pass -- three function docstrings
trimmed to make room: `_materialized_git_dir`, `_populate_workspace`,
`_cleanup_signals_deferred`); devcontainer.json's own JSONC-stripped
syntax re-validated; a fresh Docker-backed end-to-end run
(`ai-attribution`, 98 passed / 6 skipped) confirms the common case still
works; `check-docs-consistency.py` and `check-effort-vision-structure.py`
both pass. Docker cleanup (no leftover `test-isolation` volumes) and host
`git status --short` reconfirmed clean of anything beyond this round's
own diff.

### 2026-10-03 — Review round 15 (twentieth pass): 2 HIGH findings addressed (process-filter + working-tree-attribute bypass, symlinked-ancestor archiving escape)
A twentieth review pass of commit `85021d558` surfaced 2 new HIGH
findings plus 5 restated (1 previously-missed MEDIUM about CI coverage
already fixed, 2 MEDIUM already handled by existing per-invocation
volume code, 2 LOW PR-metadata items already corrected in the PR body
directly). HIGH: the previous pass's clean-filter neutralization had two
remaining gaps, both confirmed live. First, `check-attr --cached` only
consults the COMMITTED/staged `.gitattributes`, silently missing an
uncommitted working-tree edit that assigns a brand-new filter -- fixed
by dropping `--cached` so discovery honors the actual working-tree file,
the same one a real `git status`/`diff` probe itself consults. Second,
a configured `filter.<name>.process` (a separate, higher-precedence
protocol) still executes host code during `git status` even with
`.clean` neutralized -- confirmed live with a toy process-filter script
(marker created, git prints protocol errors to stderr but `status`
still exits 0). Fixed by also forcing `filter.<name>.process` empty for
every discovered name. `_discover_configured_clean_filters` was also
changed to FAIL CLOSED (raise `SystemExit`) on any subprocess failure
rather than degrading to an empty list -- silently treating "couldn't
determine" as "nothing to neutralize" would be exactly the false safety
this probe exists to prevent.

HIGH: a tracked path's own ANCESTOR directory can be replaced on disk
with a symlink pointing outside `REPO` -- confirmed live that
`os.path.lexists` on the full path doesn't catch this (it only checks
the FINAL component's own link status; the OS still transparently
follows an intermediate symlinked directory to reach a real file living
elsewhere), so `_write_tar_of_repo` would otherwise archive an external
file's real bytes under the tracked path's name. Fixed by checking each
path's PARENT directory's resolved real path against `REPO`'s own real
path before archiving (deliberately not the leaf itself, which may
legitimately be a tracked symlink stored as a safe, non-dereferenced
link record); fails closed with a named path on any escape, same
pattern as the filter-discovery fix.

Re-validated end-to-end: the full unit test suite (97 tests, including 5
new regression tests -- working-tree-vs-`--cached` attribute discovery,
fail-closed discovery on subprocess error, a real-git process-filter-
neutralization-during-status regression mirroring the manual repro, and
a real-git symlinked-ancestor-directory rejection) passes; 4 pre-existing
tests that broadly mocked `wrapper.subprocess.run` to simulate a FAILED
git call needed updating to also stub `_discover_configured_clean_filters`
(the new fail-closed discovery call was itself tripping on their shared
mocked failure response before the function under test's own logic ran).
`check-module-size.py --changed-since origin/dev` passes right at the cap
(1000 lines, after an aggressive docstring-condensing pass across ten
functions); a fresh Docker-backed end-to-end run (`ai-attribution`, 98
passed / 6 skipped) confirms the common case still works; `check-docs-
consistency.py` and `check-effort-vision-structure.py` both pass. Docker
cleanup (no leftover `test-isolation` volumes) and host `git status
--short` reconfirmed clean of anything beyond this round's own diff.

### 2026-10-03 — Review round 15 (twenty-first pass): both prior HIGH findings resolved, 3 LOW doc findings addressed (passthrough-exception documentation)
A twenty-first review pass of commit `be7a6b096` confirmed both HIGH
findings from the previous pass resolved (process-filter/working-tree-
attribute bypass, symlinked-ancestor archiving escape). Remaining: 3
restated MEDIUM findings already addressed in earlier passes (CI
coverage, per-invocation volume naming/sizing -- no action), 2 restated
LOW PR-metadata findings (stale test count and one remaining stale
`.devcontainer/devcontainer.json` reference missed in the earlier PR-body
edit, now both corrected directly on the PR body), and 3 NEW LOW
findings: the wrapper's own module docstring, `TESTING.md`'s
introductory passthrough-contract paragraph, and the effort's own Plan
item all claimed every `run-plugin-tests.py` flag passes through
semantically unchanged, without acknowledging the two exceptions already
documented ELSEWHERE in each of those same files (`--allow-host-state`
rejected outright; `--admission-wait`'s host-wide lease losing its
cross-process coordination). Fixed by adding a one-clause qualification
to each of the three introductory statements, cross-referencing the
exception explanations each document already carries further down --
closing the gap between the top-line summary and the full detail
without duplicating it.

Re-validated end-to-end: the full unit test suite (97 tests, unchanged
by this doc-only pass) passes; `check-module-size.py --changed-since
origin/dev` passes right at the cap (999 lines, after one more small
docstring trim to make room for the qualification); a fresh
Docker-backed end-to-end run (`ai-attribution`, 98 passed / 6 skipped)
confirms the common case still works; `check-docs-consistency.py` and
`check-effort-vision-structure.py` both pass. Docker cleanup (no
leftover `test-isolation` volumes) and host `git status --short`
reconfirmed clean of anything beyond this round's own diff.

### 2026-10-03 — Review round 15 (twenty-second pass): 1 LOW doc fix (reversed --allow-host-state semantics), 2 previously-missed MEDIUM signal-handling findings addressed
A twenty-second review pass of commit `304ac1676` confirmed checks green
and surfaced 1 new LOW plus 2 "previously missed" MEDIUM findings against
code that hadn't changed recently (both from the SIGTERM/signal-handling
work several rounds back). LOW: the previous pass's effort-README
passthrough-exception wording for `--allow-host-state` had the semantics
EXACTLY BACKWARDS -- it described the flag as requesting a fresh,
credential-free tmpfs `$HOME` (the wrapper's OWN default behavior),
when the flag's actual documented contract is the opposite: it requests
PRESERVING the caller's real HOME/config/credentials. Fixed by rewriting
that one Plan-item clause to match the wrapper's own correct rejection
message and `TESTING.md`'s own correct wording.

MEDIUM: cleanup subprocesses (`docker rm`/`docker volume rm`/`docker ps`
in `_tear_down` and `_cleanup_orphan`) only had `_cleanup_signals_deferred`
protecting the Python PARENT process -- a terminal Ctrl-C delivers
`SIGINT` to the WHOLE foreground process group, so these DIRECT
subprocess children retained the default handler and could die mid-
removal regardless, leaking a container or volume. Fixed by adding
`start_new_session=True` to every docker subprocess call in both
functions, detaching each into its own session/process group so a
terminal signal no longer reaches them directly.

MEDIUM: `_cleanup_signals_deferred`'s own two-handler install (and later
restore) wasn't atomic -- a signal landing between the first
(`signal.signal(SIGINT, ...)`) and second (`SIGTERM`) call would still
hit whichever OLD handler was still active for the second one (e.g.
`main`'s own `_raise_on_sigterm`), aborting context entry before cleanup
even started and leaving the first handler un-restored. Fixed by
bracketing each handler swap (install and restore) with
`signal.pthread_sigmask(SIG_BLOCK, ...)`/`SIG_UNBLOCK` around the
`signal.signal()` calls, so a signal arriving mid-swap queues at the
kernel level and is only delivered once the FULL new/restored handler
set is already in place.

Re-validated end-to-end: the full unit test suite (98 tests, including
3 new regression tests -- `start_new_session=True` assertions for both
`_tear_down` and `_cleanup_orphan`'s docker calls, and a mock-based test
confirming the exact BLOCK/UNBLOCK/signal-swap/UNBLOCK ordering in
`_cleanup_signals_deferred`) passes; `check-module-size.py
--changed-since origin/dev` passes right at the cap (1000 lines, after
an aggressive docstring-condensing pass across thirteen functions -- this
module is now reliably hitting the cap on nearly every substantive round
and remains a strong candidate for an actual multi-module split, as
noted in earlier journal entries); a fresh Docker-backed end-to-end run
(`ai-attribution`, 98 passed / 6 skipped) confirms the common case still
works; `check-docs-consistency.py` and `check-effort-vision-structure.py`
both pass. Docker cleanup (no leftover `test-isolation` volumes) and
host `git status --short` reconfirmed clean of anything beyond this
round's own diff.

### 2026-10-03 — Review round 15 (twenty-third pass): MEDIUM signal-mask restoration bug fixed
A twenty-third review pass of commit `7b3e74e0c` confirmed the previous
pass's reversed-semantics LOW finding resolved, and surfaced 1 new
MEDIUM against the just-added `pthread_sigmask` fix itself: `SIG_UNBLOCK`
unconditionally unblocks both signals regardless of what mask was
active BEFORE this context manager touched it -- confirmed live that a
caller who deliberately pre-blocked `SIGTERM` (or inherited it blocked)
found it silently UNBLOCKED after `_cleanup_signals_deferred` returned,
a real behavior change the caller never asked for. Fixed by capturing
the mask `SIG_BLOCK` returns (the mask active immediately before that
call) and restoring it EXACTLY via `SIG_SETMASK`, on both the install
and the restore side, instead of unconditionally `SIG_UNBLOCK`ing.

Re-validated end-to-end: the full unit test suite (99 tests, including
a new real-signal regression test that pre-blocks `SIGTERM`, confirms
it's still blocked after the context manager returns, and restores the
test's own prior mask afterward) passes; the existing atomic-swap
ordering test was updated to assert `SIG_SETMASK` (not `SIG_UNBLOCK`) at
each boundary; `check-module-size.py --changed-since origin/dev` passes
right at the cap (1000 lines, one more condensing pass); a fresh
Docker-backed end-to-end run (`ai-attribution`, 98 passed / 6 skipped)
confirms the common case still works; `check-docs-consistency.py` and
`check-effort-vision-structure.py` both pass. Docker cleanup (no
leftover `test-isolation` volumes) and host `git status --short`
reconfirmed clean of anything beyond this round's own diff.

### 2026-10-03 — Review round 15 (twenty-fourth pass): MEDIUM pending-signal-bypass fixed
A twenty-fourth review pass of commit `7664bdbb8` confirmed the previous
pass's signal-mask-restoration fix resolved, and surfaced 1 "previously
missed" MEDIUM against the prior round's own `pthread_sigmask` fix: a
signal arriving while the CALLER had pre-blocked it (e.g. a pre-blocked
`SIGTERM`) stays PENDING for as long as it remains blocked -- which,
without care, is the ENTIRE `yield` body duration. Unblocking only at
the final restore step (the previous fix's own structure) delivers that
pending signal to the already-restored OLD handler, bypassing `_record`
and the `received`-based replay decision entirely -- confirmed live with
a real `os.kill` sent to self while `SIGTERM` was pre-blocked by the
test, landing on the ORIGINAL (pre-existing) handler instead of
triggering the expected `_TerminationRequested` replay. Fixed by adding
a brief flush step (`SIG_UNBLOCK` then `SIG_SETMASK` back to whatever
was active) BEFORE touching the handlers at all, so any such pending
signal is delivered to `_record` -- still installed -- while there's
still time for it to participate in the normal replay decision; the
handler-restore swap itself keeps its own separate, atomic
block/restore exactly as before.

Re-validated end-to-end: the full unit test suite (100 tests, including
a new real-signal regression test that pre-blocks `SIGTERM`, sends it to
self during `yield`, and confirms it surfaces as `_TerminationRequested`
via the ORIGINAL handler never firing) passes; the atomic-swap ordering
test was updated for the new three-phase sigmask sequence (install
swap, exit flush, restore swap); `check-module-size.py --changed-since
origin/dev` passes right at the cap (1000 lines, yet another
condensing pass -- this module has now hit the cap on effectively every
substantive round this session and is a strong, repeatedly-confirmed
candidate for an actual multi-module split as a Phase 2 follow-up); a
fresh Docker-backed end-to-end run (`ai-attribution`, 98 passed / 6
skipped) confirms the common case still works; `check-docs-
consistency.py` and `check-effort-vision-structure.py` both pass.
Docker cleanup (no leftover `test-isolation` volumes) and host `git
status --short` reconfirmed clean of anything beyond this round's own
diff.

### 2026-10-03 — Review round 15 (twenty-fifth pass): HIGH submodule-filter-bypass fixed
A twenty-fifth review pass of commit `a917a9f44` confirmed the previous
volume-reuse restated finding resolved, and surfaced 1 new HIGH: `git
status` (`_warn_about_dirty_tracked_files`'s probe) recursively inspects
any initialized submodule by default, but `_discover_configured_clean_
filters` only discovers filter assignments from the SUPERPROJECT's own
tracked paths -- a submodule-specific `filter.<name>.clean`/`.process`
assignment is never neutralized by `_scrubbed_git_env`'s override set,
so this ostensibly read-only warning probe could still execute
unneutralized filter code if an initialized submodule configured one.
Fixed by adding `--ignore-submodules=all` to the `git status` call --
the snapshot never copies submodule contents anyway (confirmed earlier
this round via the existing `_write_tar_of_repo` non-recursion
regression test), so submodule state has nothing useful to report here
regardless.

Re-validated end-to-end: the full unit test suite (101 tests, including
a new mock-based regression test asserting `--ignore-submodules=all` is
always passed) passes; `check-module-size.py --changed-since origin/dev`
passes right at the cap (1000 lines, yet another condensing pass across
roughly a dozen functions); a fresh Docker-backed end-to-end run
(`ai-attribution`, 98 passed / 6 skipped) confirms the common case still
works; `check-docs-consistency.py` and `check-effort-vision-structure.py`
both pass. Docker cleanup (no leftover `test-isolation` volumes) and
host `git status --short` reconfirmed clean of anything beyond this
round's own diff.

### 2026-10-03 — Review round 15 (twenty-sixth pass): MEDIUM resource-override ceiling mismatch fixed
A twenty-sixth review pass of commit `3d771ef23` confirmed the previous
submodule-filter-bypass HIGH finding resolved, and surfaced 1
"previously missed" MEDIUM: `--max-memory-mb`/`--max-processes`/
`--max-temp-mb` pass through unmodified like any other
`run-plugin-tests.py` flag, but this wrapper's own container enforces
FIXED, lower outer ceilings (`--memory=14g`, `--pids-limit=512`, `/tmp`'s
own `size=6144m` tmpfs) regardless of what the inner runner believes it
has -- e.g. `--max-memory-mb 16000` is valid to that runner but would be
silently preempted by the container's own 14 GiB cgroup limit (an
OOM-kill, not a clean error), the same semantics-mismatch class the
`--allow-host-state`/`--admission-wait` exceptions already name but this
third case didn't. Fixed with a new
`_reject_resource_overrides_exceeding_container_ceilings()` check (a
third documented exception, wired in right alongside the existing
`--allow-host-state` rejection in `main()`): any of the three flags
requesting MORE than the matching outer ceiling now fails loudly,
naming the exact flag/value/ceiling, before any container work begins
-- confirmed live via `--max-memory-mb 16000` (exit 1, clear message,
no Docker invoked at all). Documented the new exception consistently
across all three places that previously said "two exceptions"
(`TESTING.md`'s intro, the module's own docstring, and the effort
README's Plan item) -- the exact kind of inconsistency an earlier round
this same session was caught and fixed for.

Re-validated end-to-end: the full unit test suite (107 tests, including
6 new regression tests -- at-ceiling values passing, each of the three
flags individually rejected above its ceiling with the right message,
an abbreviated flag form recognized, and a non-integer value correctly
left for the inner runner's own argparse) passes; `check-module-size.py
--changed-since origin/dev` passes right at the cap (1000 lines, after
an unusually large condensing pass across roughly fifteen functions and
comment blocks -- the module has now hit the cap on effectively every
substantive round this session; a genuine multi-module split remains
the right Phase 2 follow-up, noted repeatedly and still not yet
scheduled); a fresh Docker-backed end-to-end run (`ai-attribution`, 98
passed / 6 skipped) confirms the common case still works; a dedicated
live check confirmed the new rejection fires correctly end-to-end;
`check-docs-consistency.py` and `check-effort-vision-structure.py` both
pass. Docker cleanup (no leftover `test-isolation` volumes) and host
`git status --short` reconfirmed clean of anything beyond this round's
own diff.

### 2026-10-03 — Review round 15 (twenty-seventh pass): MEDIUM combined-resource-budget mismatch fixed
A twenty-seventh review pass of commit `9758470cb` surfaced 1 new MEDIUM
against the previous round's own resource-ceiling check: it validated
`--max-memory-mb`, `--max-processes`, and `--max-temp-mb` INDEPENDENTLY
against each raw outer ceiling, but `/tmp`'s tmpfs is memory-backed, so
process RSS and temp-dir usage draw from the SAME `--memory` cgroup --
e.g. `--max-memory-mb 14336 --max-temp-mb 6144` (each individually at
its own ceiling) would still total 20480 MiB against a single 14 GiB
cgroup, getting OOM-killed despite passing both individual checks. The
review also flagged that the previous check could wrongly reject a
VALID repeated-flag invocation (argparse itself uses only the LAST
occurrence of a repeated flag; the old check raised on the FIRST
occurrence it saw, before ever reaching a later, safe override).

Fixed by rewriting the check to first resolve each flag's LAST
occurrence (matching argparse semantics) and, for any not given, the
inner runner's own default (4096 MiB memory, 2048 MiB temp), THEN
validate: `--max-temp-mb` alone against `/tmp`'s physical tmpfs ceiling
(a hard ENOSPC limit regardless of memory budget), memory+temp
COMBINED against a reduced effective memory ceiling (reserving 512 MiB
for container overhead), and `--max-processes` against a reduced
effective PID ceiling (reserving 32 for the container/runner's own
infra processes) -- confirmed live via `--max-memory-mb 14336
--max-temp-mb 6144` (exit 1, clear combined-budget message, no Docker
invoked).

Re-validated end-to-end: the full unit test suite (110 tests -- replaced
the three independent-ceiling tests with ones matching the new combined
semantics, added a repeated-flag-last-wins regression, a safe-combination
pass-through regression, and a combined-budget-exceeded regression)
passes; `check-module-size.py --changed-since origin/dev` passes right
at the cap (1000 lines, after yet another large condensing pass -- the
module has now hit the cap on effectively every substantive round this
session; a genuine multi-module split remains the correct Phase 2
follow-up, repeatedly noted and still not yet scheduled); a fresh
Docker-backed end-to-end run (`ai-attribution`, 98 passed / 6 skipped)
confirms the common case still works; a dedicated live check confirmed
the new combined-budget rejection fires correctly; `check-docs-
consistency.py` and `check-effort-vision-structure.py` both pass. Docker
cleanup (no leftover `test-isolation` volumes) and host `git status
--short` reconfirmed clean of anything beyond this round's own diff.

### 2026-10-03 — Review round 15 (twenty-eighth pass): convergence -- zero new findings
A twenty-eighth review pass of commit `7e8985187` confirmed the
previous round's combined-resource-budget fix resolved, and surfaced
**zero new findings** -- the first fully clean pass of this round after
27 consecutive sub-rounds each producing at least one genuine fix. The
remaining 4 items are all long-confirmed restated findings the reviewer
bot doesn't appear to re-clear once its own discussion thread opens,
even after the underlying code/doc state changed: CI-coverage (already
wired into `test-runner-linux`), workspace-volume bound/reuse (already
handled by `_create_bounded_volume`/`_per_instance_config`), and two
PR-description-metadata items (already corrected directly on the PR
body in an earlier round). CI checks are green; `merge state: clean`.
This is the convergence point the effort has been iterating toward --
proceeding to drive this PR through to merge per the repo's
`pr-self-merge` flow (`COMMENTED` is this reviewer's normal non-blocking
verdict shape; this identity holds Maintainer bypass rights for the
required-review rule).

### 2026-10-03 — Review round 15 (twenty-ninth pass): MEDIUM SIGHUP leak fixed
A twenty-ninth review pass of commit `af3320d68` surfaced 1 "previously
missed" MEDIUM: `SIGHUP` also terminates the process by default on
Linux (e.g. a closed SSH/terminal session), bypassing every `finally`
block the same way an unhandled `SIGTERM` would -- but only `SIGTERM`
had been converted to a catchable exception, leaving `SIGHUP` able to
leak a live container and volume. Fixed by extending the exact same
treatment to `SIGHUP`: added it to `_CLEANUP_DEFERRED_SIGNALS` (so a
repeat during cleanup itself is still deferred/recorded, not just the
first one), and installed/restored a second `_raise_on_sigterm` handler
for it in `main()` alongside the existing `SIGTERM` one.

Confirmed live end-to-end: started a real (non-`--keep`) wrapper run in
the background, sent it a real `SIGHUP` a few seconds in (while
`_run_tests`'s `docker exec` subprocess was still running), confirmed
the traceback shows `_TerminationRequested: received signal 1` (SIGHUP)
propagating through the normal exception path rather than the process
dying silently, and confirmed via `docker ps`/`docker volume ls`
afterward that its container and volume were correctly torn down
(the one leftover volume/container found was the unrelated, intentional
`--keep` artifact from an earlier manual validation run this same
round, cleaned up separately).

Re-validated end-to-end: the full unit test suite (112 tests, including
2 new regression tests -- `_raise_on_sigterm` raising for `SIGHUP`
specifically, and `main()` installing/restoring a `SIGHUP` handler same
as `SIGTERM`; updated the existing atomic-swap-ordering test's install/
restore call-count assertions for the now-three-signal set) passes;
`check-module-size.py --changed-since origin/dev` passes right at the
cap (1000 lines, yet another condensing pass); a fresh Docker-backed
end-to-end run (`ai-attribution`, 98 passed / 6 skipped) confirms the
common case still works; `check-docs-consistency.py` and
`check-effort-vision-structure.py` both pass. Docker cleanup (no
leftover `test-isolation` volumes/containers) and host `git status
--short` reconfirmed clean of anything beyond this round's own diff.

### 2026-10-03 — Phase 2 kicked off: CI-lane item decided (no lane)
Phase 1 merged (`51788b7c9`) in a prior session; this picks up Phase 2's
three remaining items, driven as separate serial PRs rather than one
combined change. First up is the lowest-risk item, a pure decision with
no code: whether to wire `tools/run_tests_in_devcontainer.py` into CI as
an opt-in lane. **Decided: no.** The upstream GitHub Actions runners
that already gate every push/PR execute each plugin suite inside a
single-tenant, ephemeral VM per job -- a stronger isolation boundary than
a Docker-in-CI invocation of this wrapper would add on top of it. The
wrapper's actual value is giving a contributor running tests on their
own, shared, long-lived machine an OS-level boundary `run-plugin-tests.py`
alone can't provide there; CI doesn't have that problem. Marked both the
Phase 2 Plan item and its paired Validation Plan item resolved with this
reasoning recorded inline, rather than leaving either as an indefinitely
open question. Next up: the networking-scope residual gap (Phase 2's
second item), as its own PR.

### 2026-10-03 — Review round 1 (PR #5093): tracking-surface contradictions fixed
A review of commit `d7998b452` surfaced 3 findings, all addressed:
(1) the CI-lane decision's rationale (upstream CI already has a stronger
isolation boundary) didn't by itself address this item's OTHER stated
purpose -- catching a regression in the wrapper's own real Docker
boundary -- so the accepted coverage tradeoff is now recorded explicitly
(required CI covers the unit module; the real Docker boundary is
validated manually only); (2) the paired Validation Plan checkbox
claimed a "chosen CI-wiring shape... implemented and green" while also
saying no such shape exists -- rewritten to validate the actual no-lane
outcome instead of a criterion that no longer applies; (3) tracking
surfaces were left contradictory after Phase 2 progress (effort
`Status:` and the `efforts/README.md` index both still said `Draft`, the
Proposal still described the CI decision as pending) -- both updated to
`Active (Phase 2 in progress)`, and the stale Proposal sentence
rewritten. `check-docs-consistency.py` and
`check-effort-vision-structure.py` both still pass. Umbrella issue #5040
still needs a matching update (tracked separately, not blocking this
PR's merge).

### 2026-10-03 — Phase 2 item 2: networking-scope gap closed, live-validated
Implemented the split Phase 1 deliberately deferred: dependency
resolution now gets its own network-enabled pass
(`tools/run_tests_in_devcontainer.py` runs `run-plugin-tests.py
--collect-only` -- the same `_ensure_venv` venv-install path a real run
uses, but it only imports test modules rather than executing them, so no
second, duplicated install path was needed in that module), then every
network the container is attached to is disconnected (read live via
`docker inspect` rather than assuming a fixed name like "bridge" -- not
a stable devcontainer-CLI contract) before the real, now
network-disconnected test pass runs. A bare `--list` request skips both
steps, since it never builds a venv in the first place.

The new logic (`is_list_only`/`prepare_dependencies`/
`disconnect_container_networks`) was factored into a new sibling module,
`tools/_devcontainer_network_scope.py` (thin, dependency-injected
functions -- no circular import with the main wrapper), rather than
grown inline: the main wrapper was already at its 1000-line cap with
zero headroom, and this is the same "this module will keep needing
condensing" dynamic the Phase 1 journal already flagged. Several
pre-existing docstrings in the main wrapper were also tightened
(unchanged meaning, fewer lines) to make room alongside the genuinely
new code.

Live-validated against real Docker (`ai-attribution`, both the default
run and a `--keep` run): the default run showed the expected two-pass
shape (`collect-only` prep, 104 tests collected; then the real run, 98
passed / 6 skipped) with clean teardown afterward (`docker ps -a`/
`docker volume ls` showed nothing left). The `--keep` run confirmed the
boundary itself, not just the shape: `docker inspect -f
'{{json .NetworkSettings.Networks}}'` on the still-running container
returned `{}` (no attached network at all), and `docker exec <id> sh -c
"getent hosts pypi.org"` failed outright from inside it -- the real test
pass genuinely has no outbound reach. Unit tests
(`tools/test_run_tests_in_devcontainer.py`) grew to 124 (all passing),
covering the new split's flag-detection, prep/disconnect subprocess
shapes, and `main()`'s wiring/ordering (prepare -> disconnect -> run,
skipped for `--list`); `check-module-size.py`, `check-docs-consistency.py`,
and `check-effort-vision-structure.py` all pass. `TESTING.md` and this
README both updated to describe the closed gap instead of the open one.
Next up: the host-wide admission-lease residual gap (Phase 2's third and
final item), as its own PR.

### 2026-10-03 — Review round 1 (PR #5095): real --prepare-only mode, --reinstall fix
A review of commit `11fb81829` surfaced 5 findings, all addressed.
Two were substantive bugs in the implementation itself, not documentation:
(1) **the dependency-preparation pass used `--collect-only`, which still
runs pytest's own collection** -- that IMPORTS every test module and
`conftest.py` and executes their module-level code/collection hooks,
with network access, before the disconnect ever runs. A buggy or
adversarial test could open a socket or exfiltrate copied workspace data
during that import, directly undermining this effort's own stated
boundary ("regardless of what a buggy or adversarial test actually
does"). Fixed with a genuine fix, not a doc caveat: added a new
`--prepare-only` mode to `tools/run-plugin-tests.py` itself that calls
only `_ensure_venv` and returns, never touching pytest or importing a
single test file; the wrapper's prep pass now uses that instead.
(2) **`--reinstall` was forwarded unchanged to the real (post-disconnect)
pass** -- since the prep pass already rebuilt the venv, the real pass
receiving `--reinstall` again would delete that freshly-built venv in
`_ensure_venv` and try to reinstall it with no network left. Fixed with
a new `strip_reinstall` helper that removes `--reinstall` from the real
pass's passthrough once the prep pass has used it.
The remaining 3 findings were documentation-only: the original Phase 1
Plan item (the networking-scoping line) was still unchecked and said
"Still open" even though Phase 2 had already closed it -- updated to
match; two identifier mentions were wrong (`_prepare_dependencies`/
`_disconnect_container_networks` instead of the actual
`prepare_dependencies`/`disconnect_container_networks` in a devcontainer.json
comment, and `_is_list_only` instead of `is_list_only` in this README) --
both corrected. `TESTING.md` and this README's own Phase 2 item 2
description updated to describe `--prepare-only`, not `--collect-only`
(the historical Journal entry above is left as an accurate record of
what was done at the time, not rewritten). Unit tests, module-size,
docs-consistency, and effort-vision-structure checks all re-confirmed
passing after the fix.

### 2026-10-03 — Review round 2 (PR #5095): fail-closed network-mode check
A HIGH-severity finding caught that `disconnect_container_networks` read
an empty `NetworkSettings.Networks` map as "nothing to disconnect,
already isolated" -- but Docker can report that same empty map for a
namespace-sharing mode (`--network host`, `--network container:<id>`)
where the container still has real network access, the same pattern
this repo's own `agent-containers` restricted-fleet check
(`lifecycle.py:411-429`) already treats as uninspectable and rejects.
Fixed by also reading `HostConfig.NetworkMode`: a `host`/`container:<id>`
mode is now rejected outright, `none` is a legitimate no-op (Docker's
real shape there is `{"none": {}}`, never truly empty), and any OTHER
mode reporting zero attached networks is now rejected too rather than
silently trusted. Added dedicated unit tests for each new fail-closed
path; all pass, along with module-size/docs-consistency/effort-vision-
structure checks, and a fresh Docker-backed end-to-end run confirmed the
real container's own `NetworkMode` is `bridge` (never a rejected mode).

### 2026-10-03 — Review round 3 (PR #5095): none-mode shortcut closed
A follow-up HIGH finding caught the `none`-mode fast path's own
remaining gap: `HostConfig.NetworkMode` reflects CREATION-time config,
not live state -- a later `docker network connect` can attach a real,
reachable network to a "none"-mode container while this field stays
frozen reporting `none`. The earlier fix only checked `network_mode ==
"none"` and returned immediately; it now also verifies
`NetworkSettings.Networks` is EXACTLY `{"none": {}}` (Docker's real
shape for an untouched `--network none` container) before trusting it,
matching the stricter check `agent-containers`' own `lifecycle.py`
already applies. Added a regression test for a `none`-mode container
reporting an unexpected extra network; all tests, module-size, and docs
checks still pass.
