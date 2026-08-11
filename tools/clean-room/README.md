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
- A GitHub account entitled to Copilot (for the one-time device-code login).

> **Governed machines / internal npm feed.** On a corp-governed box the public
> `registry.npmjs.org` is blocked at the TLS layer, so the in-image
> `npm install -g @github/copilot` would fail with an SSL handshake error. The
> wrappers auto-detect the host's configured npm registry (`npm config get
> registry`) and forward it as the `NPM_REGISTRY` Docker build-arg, so the
> container installs the Copilot CLI through the same governed feed the host
> uses. Override explicitly with `-BuildArg`/`CR_NPM_REGISTRY` if needed.

## Usage

```powershell
# Windows host
./run.ps1 -Mode all        # build image -> device-code login (once) -> validate
./run.ps1 -Mode run        # re-run validation against the cached authed image
```

```bash
# Linux / WSL / macOS host
./run.sh all
./run.sh run
```

**Auth is a one-time step.** `-Mode auth` (or the auto-prompt on first `run`)
opens an interactive Copilot session in the container; run `/login` if you're
not prompted, authorize the device code in your browser, then `/exit`. The
wrapper `docker commit`s the result to `copilot-cleanroom:authed` so subsequent
runs reuse the login. Re-run `auth` when the token expires.

> **Credential hygiene:** the *base* image (`Dockerfile`) is credential-free and
> safe to rebuild/share. Only the local `:authed` image holds your session —
> never push it to a registry.

Results land in `./results/` — `cr-report.json` (structured PASS/FAIL) plus
per-phase command logs under `results/cr-logs/`.

## Configuration

Override via `run.ps1` params or `CR_*` env (see `validate.sh` header):
`CR_MARKETPLACE_REPO`, `CR_MARKETPLACE_NAME`, `CR_PRIMARY_PLUGIN`,
`CR_EXPECT_DEPS`.

## Files

| File | Role |
|------|------|
| `Dockerfile` | Credential-free "fresh machine": git, python, node, uv, Copilot CLI — nothing from copilot-extensions. |
| `validate.sh` | In-container driver + assertions (bind-mounted at run, so edits need no rebuild). |
| `run.ps1` / `run.sh` | Host wrappers: build · one-time auth+commit · run. |

## Scope / non-goals

- **Layer 0 only.** True pristine-OS coverage (system python/uv/git assumptions,
  profile-PATH timing) is a follow-up **Layer 1** (fresh WSL distro import).
- Does **not** validate auth itself — it reuses your login by design.
- Read-only w.r.t. the host: everything happens inside the disposable container.
