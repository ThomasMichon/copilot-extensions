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

## Image variants

| Image | Toolchain present | Use |
|-------|-------------------|-----|
| `base` (default) | git, python3, node, uv | Plugin-install checks where a stock dev toolchain is assumed. |
| `pristine` | **Copilot + git only** — a system `python3` exists (as on any real box) but **no venv module, no pip, no uv, no `~/.local/bin`, no feed governance** | The harshest fresh **internal** machine: forces the harness to provision its own toolchain, so uv/venv/pip-feed jams **surface** instead of being hidden. Select with `-Image pristine`. |

Feed governance is injected per-scenario at run time, never baked into an image.
The realistic corp-box case to reproduce is an **asymmetry**: a policy sets
**pip's** internal feed but not **uv**, so `uv`/`uv pip install` still hit the
TLS-blocked public index while pip works.

## What it checks (the `generic-single-plugin` reference scenario)

| Stage | Question it answers |
|-------|---------------------|
| 0 | Is the box actually clean (no pre-existing runtime/binstubs)? |
| 1 | Does registering the marketplace + `plugin install <one plugin>` land the payload? |
| 2 | **Dependency chain:** does installing `agent-codespaces` alone pull `agent-bridge` + `agent-worktrees`? |
| 3 | **Bootstrap crux:** does the *first session* deploy the runtime venv + binstub, or does `bootstrap-check` no-op on a machine with no deploy-manifest yet? |
| 4 | Is `~/.local/bin` on a **stock login-shell PATH** (are the binstubs callable)? Do cross-plugin shell-outs (`agent-worktrees …`) resolve? |
| 5 | **Headless plugin loading:** does `copilot -p` honor `enabledPlugins`, or does it need explicit `--plugin-dir` per plugin (the agent-bridge dispatch mechanism)? |
| 6 | Does `agent-worktrees register` wire the repo as a harness project (`projects.yaml`)? |

Each stage asserts on **filesystem outcomes**, not exact CLI syntax, so it stays
robust across `copilot` versions and records the CLI surface + full logs it saw.

## Prerequisites

- Docker (Linux containers). `docker --version` should work.
- A `gh` login (or a `COPILOT_GITHUB_TOKEN`/PAT) for a **Copilot-entitled**
  account — injected automatically so no in-container login is needed.

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
./run.sh --until 1 --then shell run
./run.sh --uv-index https://…/pypi/simple/ run
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

**`generic-single-plugin`** is the reference scenario — today's Layer-0 install
check — proving the substrate generalises without an internal dependency.

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
header): `CR_MARKETPLACE_REPO`, `CR_MARKETPLACE_NAME`, `CR_PRIMARY_PLUGIN`,
`CR_EXPECT_DEPS`, `CR_UV_INDEX` (opt-in uv-index fixture), `CR_UNTIL` (stop after
stage N). The scenario name + stage list live in `manifest.json`.

## Files

| File | Role |
|------|------|
| `Dockerfile` | Credential-free `base` "fresh machine": git, python, node, uv, Copilot CLI — nothing from copilot-extensions. |
| `Dockerfile.pristine` | The `pristine` variant: Copilot + git only (no venv/pip/uv/feed-governance) — forces the harness to self-provision. |
| `lib/clean-room-lib.sh` | Shared scenario helper API (`phase`/`pass`/`fail`/`info`/`capture`/`envdump`/`jam`/`cr_meta`/`cr_finalize`) + uniform `cr-report.json` writer. Mounted read-only. |
| `scenarios/<name>/manifest.json` | Scenario descriptor: image variant, prereqs, auth, expected artifacts, ordered stages. |
| `scenarios/<name>/scenario.sh` | In-container driver + assertions for one scenario (bind-mounted at run, so edits need no rebuild). Sources the lib; honors `CR_UNTIL`. |
| `scenarios/generic-single-plugin/` | The reference scenario (today's Layer-0 install check). |
| `run.ps1` / `run.sh` | Host wrappers: build · one-time auth+commit · run (`-Scenario`) · **shell** (interactive handoff) · **bridge-register/unregister** (drive over agent-bridge) · down; `-Image base\|pristine`, `-UvIndex`. |
| `bridge_register.py` | Stdlib-only helper: register/unregister the container as an agent-bridge `command` agent via the provider API (no copilot-extensions imports). |

## Scope / non-goals

- **Layer 0 only.** True pristine-OS coverage (system python/uv/git assumptions,
  profile-PATH timing) is a follow-up **Layer 1** (fresh WSL distro import).
- Does **not** validate auth itself — it reuses your login by design.
- Read-only w.r.t. the host: everything happens inside the disposable container.
