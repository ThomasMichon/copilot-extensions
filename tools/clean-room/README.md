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

## What it checks

| Phase | Question it answers |
|-------|---------------------|
| 0 | Is the box actually clean (no pre-existing runtime/binstubs)? |
| 1 | Does registering the marketplace + `plugin install <one plugin>` land the payload? |
| 2 | **Dependency chain:** does installing `agent-codespaces` alone pull `agent-bridge` + `agent-worktrees`? |
| 3 | **Bootstrap crux:** does the *first session* deploy the runtime venv + binstub, or does `bootstrap-check` no-op on a machine with no deploy-manifest yet? |
| 4 | Is `~/.local/bin` on a **stock login-shell PATH** (are the binstubs callable)? Do cross-plugin shell-outs (`agent-worktrees …`) resolve? |
| 5 | **Headless plugin loading:** does `copilot -p` honor `enabledPlugins`, or does it need explicit `--plugin-dir` per plugin (the agent-bridge dispatch mechanism)? |
| 6 | Does `agent-worktrees register` wire the repo as a harness project (`projects.yaml`)? |

Each phase asserts on **filesystem outcomes**, not exact CLI syntax, so it stays
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
./run.ps1 -Mode all                      # build -> device-code login (once) -> validate
./run.ps1                                # validate against the cached authed image (base)
./run.ps1 -Image pristine -Mode shell    # drop into a pristine fresh box (headed copilot)
./run.ps1 -Until 1 -Then shell           # prepare up to phase 1, then hand off to a shell
./run.ps1 -Mode bridge-register          # expose the box as an agent-bridge agent
./run.ps1 -Image pristine -Mode down     # remove the container
```

```bash
# Linux / WSL / macOS host
./run.sh all
./run.sh --image pristine shell
./run.sh --until 1 --then shell run
./run.sh bridge-register
./run.sh --image pristine down
```

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
named container (`cr-<image>`), so you can run the automated `validate.sh` (all
phases, or `-Until <n>` to stop early) and then `-Mode shell` / `-Then shell`
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

Override via `run.ps1` params or `CR_*` env (see `validate.sh` header):
`CR_MARKETPLACE_REPO`, `CR_MARKETPLACE_NAME`, `CR_PRIMARY_PLUGIN`,
`CR_EXPECT_DEPS`, `CR_UNTIL` (stop after phase N).

## Files

| File | Role |
|------|------|
| `Dockerfile` | Credential-free `base` "fresh machine": git, python, node, uv, Copilot CLI — nothing from copilot-extensions. |
| `Dockerfile.pristine` | The `pristine` variant: Copilot + git only (no venv/pip/uv/feed-governance) — forces the harness to self-provision. |
| `validate.sh` | In-container driver + assertions (bind-mounted at run, so edits need no rebuild). Honors `CR_UNTIL` to stop after a phase. |
| `run.ps1` / `run.sh` | Host wrappers: build · one-time auth+commit · run · **shell** (interactive handoff) · **bridge-register/unregister** (drive over agent-bridge) · down; `-Image base\|pristine`. |
| `bridge_register.py` | Stdlib-only helper: register/unregister the container as an agent-bridge `command` agent via the provider API (no copilot-extensions imports). |

## Scope / non-goals

- **Layer 0 only.** True pristine-OS coverage (system python/uv/git assumptions,
  profile-PATH timing) is a follow-up **Layer 1** (fresh WSL distro import).
- Does **not** validate auth itself — it reuses your login by design.
- Read-only w.r.t. the host: everything happens inside the disposable container.
