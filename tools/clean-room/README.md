# Clean-room install-flow validation

A disposable **Docker "fresh machine"** that reproduces what a naive operator
experiences when they stand up a brand-new harness repo and run
`copilot plugin install agent-codespaces@copilot-extensions` on a clean box —
so every "I *believe* it does X / mixed reports" question about the
install + bootstrap flow becomes a hard **PASS/FAIL** line.

This is **Layer 0** of the validation strategy: the cheapest high-fidelity
clean room. It isolates *everything* under test (no `~/.agent-*`, no
`~/.local/bin`, no marketplace, a stock login-shell PATH) while reusing your
Copilot **login** (auth is not what we're validating).

> **First time on this machine? → [`SETUP.md`](SETUP.md)** walks you from "no
> Docker" to a green smoke-test (install Docker per OS → verify → build → auth →
> run). This README is the reference for everything after that.

## Image variants

| Image | Toolchain present | Use |
|-------|-------------------|-----|
| `base` (default) | git, python3, node, uv | Plugin-install checks where a stock dev toolchain is assumed. |
| `pristine` | **Copilot + git only** — a system `python3` exists (as on any real box) but **no venv module, no pip, no uv, no `~/.local/bin`, no feed governance** | The harshest fresh **internal** machine: forces the harness to provision its own toolchain, so uv/venv/pip-feed jams **surface** instead of being hidden. Select with `-Image pristine`. |

Feed governance is injected per-scenario at run time, never baked into an image.
The realistic corp-box case to reproduce is an **asymmetry**: a policy sets
**pip's** internal feed but not **uv**, so `uv`/`uv pip install` still hit the
TLS-blocked public index while pip works.

Both images also carry a distro `rg` in `/opt/copilot-cleanroom/bin`, outside
the stock PATH. Only the driven Tier-E Copilot process tree sees it, with
`USE_BUILTIN_RIPGREP=false`; this keeps Copilot's bundled ARM64 `rg` from
crashing on 16 KiB-page hosts without granting Tier-P scenarios an extra
ambient prerequisite. Agent-invoked subprocesses inherit that Tier-E PATH by
design. Tier-E ACP processes also run with core dumps disabled so a tool crash
cannot mutate the decision fixture.

## What it checks (the `generic-single-plugin` reference scenario)

| Stage | Question it answers |
|-------|---------------------|
| 0 | Is the box actually clean (no pre-existing runtime/binstubs)? |
| 1 | Does registering the marketplace + `plugin install <one plugin>` land the payload? |
| 2 | **Dependency chain:** does installing `agent-codespaces` alone pull `agent-bridge` + `agent-worktrees`? |
| 3 | **Bootstrap crux:** does the *first session* cheaply stamp a callable binstub, and does its first use provision the runtime instead of `bootstrap-check` no-oping on a machine with no deploy manifest? |
| 4 | Is `~/.local/bin` on a **stock login-shell PATH** (are the binstubs callable)? Do cross-plugin shell-outs (`agent-worktrees …`) resolve? |
| 5 | **Headless plugin loading:** does `copilot -p` honor `enabledPlugins`, or does it need explicit `--plugin-dir` per plugin (the agent-bridge dispatch mechanism)? |
| 6 | Does `agent-worktrees register` wire the repo as a harness project (`projects.yaml`)? |

Each stage asserts on **filesystem outcomes**, not exact CLI syntax, so it stays
robust across `copilot` versions and records the CLI surface + full logs it saw.

## Prerequisites

- Docker (Linux containers). `docker --version` should work.
- A `gh` login (or a `COPILOT_GITHUB_TOKEN`/PAT) for a **Copilot-entitled**
  account — injected automatically so no in-container login is needed.

> **Setting this up on a new machine?** Follow **[`SETUP.md`](SETUP.md)** — a
> step-by-step to install Docker (per OS), verify it, build the image, wire auth,
> and smoke-test the rig end-to-end. The notes below are the quick reference;
> `SETUP.md` is the from-zero walkthrough.

> **Governed machines / internal npm feed.** On a corp-governed box the public
> `registry.npmjs.org` is TLS-blocked, so the in-image `npm install -g
> @github/copilot` fails. Pass an internal feed **explicitly** to install the
> Copilot CLI prereq: `-NpmRegistry https://<your-internal-npm-feed>/`
> (`--npm-registry` on `run.sh`, or `$env:CR_NPM_REGISTRY`). The runner does
> **not** auto-forward the host's npm config — silently inheriting the host feed
> makes the container non-fresh and **biases the experiment**; the feed is a
> build-time convenience to install a *given* prereq, not part of what's tested,
> and is not inherited into the operator's runtime environment.

## Usage

```powershell
# Windows host
./run.ps1 -Mode all                      # build -> device-code login (once) -> run
./run.ps1                                # run generic-single-plugin against the base image
./run.ps1 -Scenario generic-single-plugin  # (the default) run a named scenario
./run.ps1 -Image pristine -Mode shell    # drop into a pristine fresh box (headed copilot)
./run.ps1 -Image base -NameSuffix agc    # a SECOND concurrent base clean-room (container cr-base-agc) -- won't clobber another agent's cr-base
./run.ps1 -Until 1 -Then shell           # prepare up to stage 1, then hand off to a shell
./run.ps1 -UvIndex https://…/pypi/simple/  # opt-in uv-index fixture (governed box)
./run.ps1 -Mode bridge-register          # expose the box as an agent-bridge agent
./run.ps1 -Image pristine -Mode down     # remove the container
```

```bash
# Linux / WSL / macOS host
./run.sh all
./run.sh --scenario generic-single-plugin
./run.sh --image pristine shell
./run.sh --image base --name-suffix agc   # a SECOND concurrent base clean-room (container cr-base-agc)
./run.sh --until 1 --then shell run
./run.sh --uv-index https://…/pypi/simple/ run
./run.sh --scenario agent-vault-eval eval        # Tier-E agent-driven eval (mirrors run.ps1 -Mode eval)
./run.sh bridge-register
./run.sh --image pristine down
```

## Scenarios & the scenario contract

The runner is **scenario-driven** (design doc `docs/clean-room-test-rig.md`
Sec.6): `-Scenario <name|dir>` selects a self-describing scenario directory that
the runner mounts (with the shared `lib/`) read-only into the box and runs. This
keeps the public runner **name-free** of any operator's repos.

```
scenarios/<name>/
├── manifest.json   # name; image variant; prereqs; auth; expected artifacts; stages
├── scenario.sh     # sources lib/clean-room-lib.sh; defines its stages via the helper API
└── fixtures/       # optional seed files / opt-in fixtures (e.g. the uv-index unjam)
```

`lib/clean-room-lib.sh` provides the uniform helper API every scenario uses:
`phase <n> <title>` (also the `--until` gate) · `pass`/`fail`/`info` ·
`capture <label> -- <cmd…>` · `envdump` · `jam <category> <evidence> [hint]`
(the Sec.7 diagnostic taxonomy) · `cr_meta <key> <value>` · `cr_finalize`. The
report shape (`cr-report.json`) keeps its historical top-level keys and adds an
`env{}` snapshot and a classified `jams[]` array.

### Windows arm — `run.ps1 -Os windows`

The runner has a **Windows counterpart** that harmonizes the clean-room across
both OSes: `run.ps1 -Os windows` runs a **Windows scenario in a Windows
container** (Hyper-V isolated) on a Windows-container host, alongside the default
Linux arm. It shares the report contract, so Linux and Windows scenarios produce
the same `cr-report.json`.

```
scenarios/<name>/
├── scenario.sh    # the Linux arm (bash) — sources lib/clean-room-lib.sh
└── scenario.ps1   # the Windows arm (PowerShell) — dot-sources lib/clean-room-lib.ps1
```

- **`lib/clean-room-lib.ps1`** — the PowerShell port of the helper API (same
  `phase`/`pass`/`fail`/`info`/`capture`/`envdump`/`jam`/`cr_meta`/`cr_finalize`
  vocabulary, same `cr-report.json` shape). Windows-PowerShell-5.1 compatible, so
  it runs under the Server Core image's built-in `powershell.exe` — no pwsh 7 in
  the image. *(NB: PowerShell is case-insensitive — don't rely on `$X` vs `$x`
  being distinct the way the bash lib does.)*
- **`Dockerfile.windows`** — the Windows "fresh machine": a Server Core-based
  python image (python3 + Windows PowerShell 5.1). `--isolation=hyperv` runs the
  ltsc2022 base on a newer host.
- **Usage** (on a Windows-container host whose non-elevated engine is reached via
  a loopback-TCP broker):

  ```powershell
  ./run.ps1 -Os windows -Scenario partner-harness-setup `
      -PartnerPath C:\path\to\partner-tree -DockerEndpoint tcp://127.0.0.1:2375
  ./run.ps1 -Os windows -Scenario installation-mode-governance `
      -PartnerPath C:\path\to\copilot-extensions -DockerEndpoint tcp://127.0.0.1:2375
  # or -PartnerRepo <url> to clone the partner tree on the host first
  ```

  It builds `copilot-cleanroom:windows` (once), mounts the scenario + lib + the
  partner tree + a results dir, and drives `scenario.ps1`. The
  **remote-to-containers** driver (transfer a drop from another box to the
  Windows host) lives in the consuming harness; the rig runs locally on the host.

For **Tier-E live** scenarios (that actually create/connect a real CodeSpace) the
lib also carries the generic **auth shim** — the rig injects only
`COPILOT_GITHUB_TOKEN` and runs no inner login flow, so a live scenario calls:
`cr_ensure_gh` (install gh if the base image lacks it) · `cr_ensure_ssh_client`
(install an ssh client — connect needs one) · `cr_seed_gh_codespace_auth` (seed
the gh **keyring** from the token **and** keep `GH_TOKEN` exported — the first is
what agent-codespaces' create scope-check reads, the second is what the
agent-bridge daemon propagates to the in-CodeSpace copilot at ACP launch; both
are needed or `create`/`dispatch` fail with confusing auth errors). These stay
**product-agnostic**; a tenant-specific inner-loop credential (e.g. an ADO/`az`
bearer) is relayed **on top** by the consuming harness via `--pass-env` (below),
not baked into this substrate.

**`generic-single-plugin`** is the reference scenario — today's Layer-0 install
check — proving the substrate generalises without an internal dependency.

### Available scenarios (F1 — the public suite)

| Scenario | Tier | What it validates |
|----------|------|-------------------|
| [`generic-single-plugin`](scenarios/generic-single-plugin/) | P | The reference: register the marketplace + install ONE (configurable) plugin on a fresh box; assert the filesystem outcomes of install → bootstrap → binstub → plugin-load → register. |
| [`agent-worktrees-solo`](scenarios/agent-worktrees-solo/) | P | The **worktree base** stands up solo: provisions, binstub reports a real version, read verbs enumerate, and a worktree **round-trips** (register → create → finalize). |
| [`worktree-manager-bootstrap`](scenarios/worktree-manager-bootstrap/) | P | The **out-of-plugin bootstrap one-liner** on a **pristine** box (no uv): it **self-provisions** uv (user-local), fetches the payload, and publishes the versioned slot + `current-version` marker + the `~/.local/bin/worktree-manager` binstub — then the binstub runs on a stock login-shell PATH. Falsifies "the bootstrap dead-ends without a pre-installed toolchain." |
| [`agent-bridge-solo`](scenarios/agent-bridge-solo/) | P | **agent-bridge without an agent-worktrees base** — the degrade-safe contract: it provisions, exposes a versioned binstub, and its read verbs (agents/machines/sessions) answer rather than crash on a missing base. |
| [`agent-codespaces-solo`](scenarios/agent-codespaces-solo/) | P | **agent-codespaces without an agent-worktrees base** — its AW shell-out touch points (account map / leases / state-root) fall open to ambient behavior; read verbs (list/status/leases/validate) answer rather than hard-fail. |
| [`agent-containers-solo`](scenarios/agent-containers-solo/) | P | **agent-containers without an agent-worktrees base** — the knowledge-overlay `state-root` shell-out falls open when the base is absent; read verbs (leases / relay-profile / namespace-target-repo) answer rather than crash. |
| [`agent-ssh-solo`](scenarios/agent-ssh-solo/) | P | **agent-ssh standalone** (a profile emitter/verifier that owns the transport-provider contract): installs with no sibling, provisions, versioned binstub, and read verbs (emit-profile / mesh-status / verify --help / explore --help) answer. |
| [`agent-machines-solo`](scenarios/agent-machines-solo/) | P | **agent-machines standalone** reconciler: provisions, versioned binstub, read verbs (discover / plan / validate --json) answer, and `restore` (default dry-run) **refuses cleanly** on validator errors instead of crashing on absent config. |
| [`agent-machines-installation-cells`](scenarios/agent-machines-installation-cells/) | P | **Agent Machines dual-cell lifecycle:** two independent sources carrying the same core identity install and run in separate cells; one updates and rolls back without changing its peer; requested-only/invalid/foreign/maintenance/orphaned evidence fails closed; unrelated and payload-only identities are rejected. Linux and Windows arms keep all state disposable. |
| [`agent-logger-solo`](scenarios/agent-logger-solo/) | P | **agent-logger standalone**: provisions, versioned binstub, read verbs (config / organization / chronicle status / session-sync status) answer, and `session-sync run --dry-run` reports would-push only (no push/prune). |
| [`agent-mcp-solo`](scenarios/agent-mcp-solo/) | P | **agent-mcp — the standalone MCP-wrapper exemplar** (no agent-bridge import, no resolver): provisions, versioned binstub, `validate` schema-checks a local stdio bridge and returns a clean error for a missing bridge. *(Green in a Docker smoke run.)* |
| [`agent-dispatch-solo`](scenarios/agent-dispatch-solo/) | P | **agent-dispatch standalone** task-queue/coordinator: provisions, binstub `--version` matches package + manifest (not the `0.0.0` fallback), and read verbs (health / inbox / installer status) answer on an empty queue rather than crash. |
| [`agent-index-solo`](scenarios/agent-index-solo/) | P | **agent-index standalone** retrieval service: provisions the **service** runtime **without** requiring the heavy embedding engine; read verbs (status / engine status / role / --version) answer and the read-only agent-mcp bridge + direct MCP tools register. |
| [`agent-vault-solo`](scenarios/agent-vault-solo/) | P | **agent-vault standalone** secret store: provisions, versioned binstub + the `vault-askpass` SUDO_ASKPASS helper, and read verbs (vault list / which / cache-status / ping) report cleanly with **no `.kdbx` configured** (no crash, no hard KeePassXC dependency). |
| [`agent-bridge-cutover`](scenarios/agent-bridge-cutover/) | P | **Graceful daemon cutover — agent-bridge (the reference adopter):** a version cutover never kills in-flight work — routing-flip-retire (stand a new daemon up beside the old, flip `active.json`, retire the old), drain-gate (turn boundary), breadcrumb-recover (heal an aborted cutover). Stdlib-only probe, verifiable off-Docker. |
| [`agent-bridge-concurrent-relay`](scenarios/agent-bridge-concurrent-relay/) | P | **Credential-relay bind resilience — agent-bridge:** under port contention (the classic racing-daemon-from-a-concurrent-reinstall case), the relay still comes up and publishes the port it bound — dynamic-default-bind (port 0 → OS-assigned ephemeral) and live-occupant-ephemeral-fallback (a live occupant holds a pinned port → the relay falls back to an ephemeral port, never left silently unbound). Stdlib-only probe driving the real `credential_relay` server, verifiable off-Docker. |
| [`agent-bridge-concurrent-daemon`](scenarios/agent-bridge-concurrent-daemon/) | P | **Single-instance daemon guard — agent-bridge:** a second daemon racing the first (the concurrent-reinstall case) is REFUSED before it can bind a colliding port — duplicate-refused (a second acquire raises `AlreadyRunningError` naming the holder pid), dead-holder-reclaimed (a killed holder's lock is freed by the OS, no stale wedge), passive-coexist-by-port (active/passive coexist on one config dir by port). The prevention half of the relay scenario's recovery. Stdlib-only probe driving the real `agent_bridge.singleton` guard with cross-process holders, verifiable off-Docker. |
| [`agent-bridge-concurrent-flip`](scenarios/agent-bridge-concurrent-flip/) | P | **Version-slot flip coherence — agent-bridge:** while installers race to flip the active runtime version (several sessions firing the sessionStart reinstall at once), a reader always resolves a valid existing slot — flip-storm-coherent-resolution (thousands of reads under two racing flippers, never None/never a missing path, both versions seen) and marker-never-torn (`current-version` never half-written; `os.replace` atomicity). Third leg of the concurrent-update triad (relay recovers · duplicate refused · flip stays coherent). Stdlib-only probe driving the real `versioned_runtime` manager with cross-process flippers, verifiable off-Docker. |
| [`agent-dispatch-cutover`](scenarios/agent-dispatch-cutover/) | P | **Graceful daemon cutover — agent-dispatch** (correct-install-flows, dotfiles#1393): a queued task survives; a claimed+started **held task** survives and the worker re-adopts via the durable queue DB; an **aborted cutover** is healed (undrain); a **wedged daemon** is stood-up-beside and retired. Stdlib-only probe. |
| [`agent-index-cutover`](scenarios/agent-index-cutover/) | P | **Graceful daemon cutover — agent-index** (service + durable engine): the swappable versioned service cuts over without disturbing the durable, warm embedding engine/model on its own lifecycle. Stdlib-only probe. |
| [`agent-vault-cutover`](scenarios/agent-vault-cutover/) | P | **Cutover witness — agent-vault** (#609): proves the client-side rendezvous **cutover fallback ladder** (override → live file → legacy) deterministically, and reports the **daemon-side** active/passive zdd cutover as a forward-looking gap (INFO — vault has not yet vendored `zdd`). Stays green today; its phase-3 battery lights up once vault adopts the connection-owner contract. |
| [`context-injection-eval`](scenarios/context-injection-eval/) | P + E | **Session-context completeness witness:** a supported local marketplace installs the unpublished authority, two synthetic canary producers, and a restart-safe idempotent side-effect-only hook. Tier P proves authority-first, producer-first, concurrent, session, and CWD permutations; Tier E uses fresh agent-bridge sessions to prove the model received every canary without reading fixtures. Run variant A with two sessions and variant B with one; evidence is counts-only. |
| [`installation-mode-governance`](scenarios/installation-mode-governance/) | P | **Windows installation-governance proof:** runs the real PowerShell 5.1 resolver in a disposable Windows container and verifies policy precedence, activation-required legacy pinning, migration-required behavior, sticky active namespaced roots, orphaned-transfer refusal, maintenance blocking, and read-only evaluation. |
| [`partner-harness-setup`](scenarios/partner-harness-setup/) | P | **Downstream partner-harness setup gate:** given a partner harness tree (mounted `CR_PARTNER_PATH` or cloned `CR_PARTNER_REPO`), assert the vendored plugin **drop is structurally coherent** (plugins parse + are marketplace-listed + ship installers; setup entrypoint + golden-path doc present), the partner's **read-only `setup check` runs without crashing**, and the partner's **OWN setup/update test suite passes**. Name-free via `CR_PARTNER_*`. A consuming gate is a downstream vendored-plugin sync — *never publish a drop that breaks the partner's setup flow.* |

Downstream/internal scenarios (naming a specific harness's repos — e.g.
`harness-health`, the citadel north-star) live with the **consuming harness** and
are mounted verbatim via `-Scenario <dir>`, per the ownership split in
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## Machine verdict for a consuming gate (`verdict.sh`)

A scenario emits a rich `cr-report.json` (phases + jams + env). A **consuming
gate** — a downstream sync/publish step, a mirror, a CI job — usually just needs a
single, uniform PASS/FAIL. **`verdict.sh`** is that thin adapter:

```bash
# after a scenario run wrote $CR_REPORT:
tools/clean-room/verdict.sh --report <cr-report.json> [--pretty]
# stdout: {"ok":bool,"scenario":str,"passed":int,"failed":int,"degraded":bool,"jams":[…]}
# exit:   0 iff ok (failed==0); 1 on validation failure; 2 on usage/parse error
```

`degraded` is true when the only jams are **environment** gaps (`validator-env`)
rather than a real product failure — a caller running fail-closed treats
`degraded` as a hold too. This is the consistent verdict contract every partner
gate shares.

### The partner-harness setup gate (deterministic, Tier P)

`partner-harness-setup` + `verdict.sh` is the shared, **fail-closed** gate a
downstream **vendored-plugin sync** runs before publishing a re-blit, so it never
ships a drop that breaks the partner's setup flow. Point it at the
to-be-published tree (mount it as `CR_PARTNER_PATH`) and read the verdict:

```bash
docker run --rm \
  -v <clean-room-dir>:/cr:ro -v <partner-tree>:/partner \
  -e CR_LIB=/cr/lib/clean-room-lib.sh -e CR_PARTNER_PATH=/partner \
  -e CR_REPORT=/out/cr-report.json -e CR_LOGDIR=/out/cr-logs \
  copilot-cleanroom:base \
  'bash /cr/scenarios/partner-harness-setup/scenario.sh; bash /cr/verdict.sh --report "$CR_REPORT"'
```

**Platform split.** This Linux box runs the partner's **unix-native** flow
(`setup.sh` + `*_sh` tests). The **Windows-native** flow (`setup.ps1` + `*_ps1`
tests) runs on a **Windows container host** — a partner is validated on the
platform whose native suite is faithful. A consuming gate points its validator
seam at this scenario where a container host is available; it may also ship a
lightweight host-side fill that runs the same checks directly on the sync box.

## Driving the box over agent-bridge

Beyond the interactive shell, the runner can register the container as an
**agent-bridge agent** so you (or an orchestrator) can drive the in-container
Copilot programmatically:

```powershell
./run.ps1 -Image base -Mode bridge-register     # agent name: cleanroom-base
agent-bridge send cleanroom-base "install agent-codespaces and report PASS/FAIL"
./run.ps1 -Image base -Mode bridge-unregister
```

The agent is a `command`-type provider agent whose transport is
`docker exec -i cr-<image> bash -lc "copilot --acp --stdio --allow-all-tools"`.
The in-container Copilot authenticates via the injected `COPILOT_GITHUB_TOKEN`,
so no token is embedded in the spawn command. Registration is TTL-scoped (1h)
against the live daemon's provider API.

> **agent-bridge ergonomics (discovery).** There is **no** `agent-bridge
> register` CLI, and `~/.agent-bridge/config.yaml` has **no inline agents list** —
> the roster is *derived* from topology (`machines.yaml` + `related.yaml`). A
> per-topology `agents_config:` pointer to a hand-authored **`acp-agents.json`**
> is still honored (deprecated, explicit-wins) and is the accepted way to declare
> a *couple of manual agents* — **but** its parser only supports `host`/`ssh` and
> `copilot_path` agents; it does **not** read a raw `spawn_command`. A container's
> `docker exec …` transport therefore can't be a static-file agent — it must come
> through the **runtime provider API** (what `bridge-register` uses), the same
> path `agent-codespaces`/`agent-containers` take. So: static file for
> host/ssh/local agents; provider API for arbitrary command transports.



**Interactive shell / headed smoke tests.** The runner drives a **persistent**
named container (`cr-<image>`), so you can run the automated scenario (all
stages, or `-Until <n>` to stop early) and then `-Mode shell` / `-Then shell`
into the *same* box to run the real interactive `copilot` — Copilot CLI does not
fully enable every feature in `-p`/ACP, so the rig automates what it can and
hands off for the rest. The container stays up until `-Mode down`.

**Auth is automatic.** By default the runner grabs a Copilot token from your host
`gh` and injects it into the container as `COPILOT_GITHUB_TOKEN`, so there is
**no interactive device-code step** and no need to pre-build an `:authed` image —
`run`/`shell` work against the plain image directly. The selected account must
have Copilot entitlement. Options:
- `-TokenAccount <user>` (`--token-account` on `run.sh`) picks which `gh` account
  (default: the active one).
- Set `$env:COPILOT_GITHUB_TOKEN` yourself (e.g. a fine-grained PAT with the
  **Copilot Requests** permission) and it is used as-is.
- `-NoToken` (`--no-token`) falls back to the one-time device-code login
  committed to a cached `:authed` image (`-Mode auth`); re-run `auth` when it
  expires.

**Relaying an extra host credential into the box (`-PassEnv` / `--pass-env`).**
`COPILOT_GITHUB_TOKEN` is what the rig itself needs. A **Tier-E live** scenario
may need one more host-provided credential for its inner loop — e.g. a
host-minted ADO/`az` bearer so the credential relay can proxy it into the
CodeSpace (closing the ADO manual gate). Rather than bake any tenant specifics
into this public runner, `-PassEnv <NAME>` (repeatable; `--pass-env NAME` on
`run.sh`) forwards a **named host env var** into the container by name (value from
the runner's env, never on the docker CLI args). A downstream harness mints the
credential on the host and passes it through:

```powershell
$env:ADO_BEARER_TOKEN = (az account get-access-token --scope 499b84ac-…/.default --query accessToken -o tsv)
./run.ps1 -Scenario <downstream-live-scenario> -PassEnv ADO_BEARER_TOKEN …
```

A `-PassEnv` name that is not set on the host is skipped with a warning (so a
downstream scenario fails loud rather than silently unauthenticated). The
tenant-specific wiring (which scope to mint, how the in-container relay consumes
it) lives with the **consuming harness**, which can hold the fuller credential
stack — not in this substrate.

> **Credential hygiene:** the token is passed via the runner's environment (not
> on the docker CLI args) and lives only in the disposable container. The
> base/pristine images stay credential-free; never push an `:authed` image.

Results land in a **machine-local dir outside the repo** — by default
`%LOCALAPPDATA%\copilot-cleanroom\runs\<timestamp>\` (Windows) or
`${XDG_STATE_HOME:-~/.local/state}/copilot-cleanroom/runs/<timestamp>/`
(Linux/WSL/macOS). Each run prints its exact path. Override with
`-ResultsDir` / `$env:CR_RESULTS_DIR`. Contents: `cr-report.json` (structured
PASS/FAIL) plus per-phase command logs under `cr-logs/`.

> **Never write run artifacts into the repo tree.** This harness may run from an
> anchor checkout; per-run state in a repo (especially an anchor) is a hazard, so
> the default results dir is deliberately machine-local and out-of-tree.

## Configuration

Override via `run.ps1` params or `CR_*` env (see the `scenarios/generic-single-plugin/scenario.sh`
header): `CR_MARKETPLACE_REPO` (a GitHub `owner/repo`, or a container-local
marketplace directory mounted with `-HarnessMount` for uncommitted-worktree
validation), `CR_MARKETPLACE_NAME`, `CR_PRIMARY_PLUGIN`,
`CR_EXPECT_DEPS`, `CR_UV_INDEX` (opt-in uv-index fixture), `CR_UNTIL` (stop after
stage N). The scenario name + stage list live in `manifest.json`.

## Files

| File | Role |
|------|------|
| [`SETUP.md`](SETUP.md) | From-zero machine setup: install Docker (per OS), verify it, build the image, wire auth, smoke-test, and troubleshoot. |
| `Dockerfile` | Credential-free `base` "fresh machine": git, python, node, uv, Copilot CLI — nothing from copilot-extensions; a hidden distro `rg` is reserved for Tier-E Copilot compatibility. |
| `Dockerfile.pristine` | The `pristine` variant: Copilot + git only (no venv/pip/uv/feed-governance) — forces the harness to self-provision; the same hidden Tier-E `rg` is outside the stock PATH. |
| `lib/clean-room-lib.sh` | Shared scenario helper API (`phase`/`pass`/`fail`/`info`/`capture`/`envdump`/`jam`/`cr_meta`/`cr_finalize`) + uniform `cr-report.json` writer. Bind-mounted read-only at `/home/operator/lib`; scenarios source it via `$CR_LIB`. |
| `<suite>/_lib/` (downstream) | Optional per-suite shared phase helpers beside a harness's scenario dirs. When the selected scenario has a sibling `_lib/`, the runner mounts it read-only at `/home/operator/scenario-lib` and exposes it as `$CR_SCENARIO_LIB` (opt-in; absent → unchanged). |
| `scenarios/<name>/manifest.json` | Scenario descriptor: image variant, prereqs, auth, expected artifacts, ordered stages. |
| `scenarios/<name>/scenario.sh` | In-container driver + assertions for one scenario (bind-mounted at run, so edits need no rebuild). Sources the lib; honors `CR_UNTIL`. |
| `scenarios/generic-single-plugin/` | The reference scenario (today's Layer-0 install check). |
| `run.ps1` / `run.sh` | Host wrappers: build · one-time auth+commit · run (`-Scenario`) · **eval** (Tier-E agent-driven; `-Mode eval` / `eval`) · **shell** (interactive handoff) · **bridge-register/unregister** (drive over agent-bridge) · down; `-Image base\|pristine`, `-UvIndex`. |
| `bridge_register.py` | Stdlib-only helper: register/unregister the container as an agent-bridge `command` agent via the provider API (no copilot-extensions imports). |

## Scope / non-goals

- **Layer 0 only.** True pristine-OS coverage (system python/uv/git assumptions,
  profile-PATH timing) is a follow-up **Layer 1** (fresh WSL distro import).
- Does **not** validate auth itself — it reuses your login by design.
- Read-only w.r.t. the host: everything happens inside the disposable container.
