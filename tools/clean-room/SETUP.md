# Setting up the clean room on a machine

Step-by-step to stand up the clean-room Docker rig on a **new machine** — install
Docker, verify it can do what the rig needs, build the image, and prove it works
end-to-end. Once this is done, use the runners as documented in
[`README.md`](README.md) (§Usage).

The clean room is a disposable **Docker "fresh machine"** (an `ubuntu:24.04`
container). Everything the rig validates runs *inside* the container; your host
only needs Docker, a Copilot-entitled login, and the two runner scripts
(`run.ps1` on Windows, `run.sh` on Linux/WSL/macOS).

---

## 0. What you need

- **Docker** capable of running **Linux containers** (the images are
  `ubuntu:24.04`). Docker Desktop (Windows/macOS) or Docker Engine (Linux).
- **A Copilot-entitled GitHub account** reachable via `gh` (or a PAT). Auth is
  injected from the host — there is no in-container login.
- **Outbound network at *build* time** to: Docker Hub (`ubuntu:24.04`),
  `deb.nodesource.com` (Node), `astral.sh` (uv), and an npm registry
  (`registry.npmjs.org`, or an internal feed on a governed box — see §3).
- **~2–3 GB disk** for the image; the first build takes a few minutes.
- **For Tier-E agent-driven evals only:** `agent-bridge` on the host PATH (plain
  Tier-P scenarios do not need it — see §6).

Pick your runner by host OS: **`run.ps1`** (Windows PowerShell) or **`run.sh`**
(Linux / WSL / macOS). They are feature-equivalent; every example below shows both.

---

## 1. Install Docker

### Windows (dev box)

1. Install **Docker Desktop** with the **WSL2 backend** (the default).
2. Launch Docker Desktop and wait until it reports **Running**.
3. Make sure it is in **Linux containers** mode (Docker Desktop only runs Linux
   containers by default now; if you ever switched to Windows containers, switch
   back — tray menu → *Switch to Linux containers…*).

`run.ps1` calls the `docker` CLI directly, so Docker Desktop must be **running**
whenever you use the rig.

### Linux

1. Install **Docker Engine** (your distro's packages, or the convenience script
   at <https://get.docker.com>).
2. Add yourself to the `docker` group so the CLI works without `sudo`:
   ```bash
   sudo usermod -aG docker "$USER"    # then log out and back in
   ```
3. Ensure the daemon is running: `sudo systemctl enable --now docker`.

### macOS

1. Install **Docker Desktop** (or an equivalent that provides a Linux VM, e.g.
   Colima/Rancher Desktop).
2. Launch it and wait until it reports **Running**.

### WSL2 (Ubuntu inside Windows) as the host

Two options — either works:
- **Docker Desktop + WSL integration:** in Docker Desktop → *Settings →
  Resources → WSL Integration*, enable your distro. Then `docker` works inside
  WSL and you use **`run.sh`**.
- **Docker Engine inside the distro:** install Engine directly in the WSL distro
  (as in the Linux steps above).

---

## 2. Verify Docker can do what the rig needs

Run these three checks (PowerShell or bash — the commands are identical):

```bash
docker version               # MUST show a "Server:" section, not just "Client:"
docker run --rm hello-world  # can pull + run a Linux container
docker pull ubuntu:24.04     # can reach Docker Hub (the rig's base image)
```

If any fail, fix Docker before continuing (see [Troubleshooting](#troubleshooting)).
A missing **Server** section almost always means the daemon / Docker Desktop is
not running.

---

## 3. Build the clean-room image

From `tools/clean-room` in your `copilot-extensions` checkout:

```powershell
# Windows
cd tools/clean-room
./run.ps1 -Mode build                     # builds copilot-cleanroom:base
./run.ps1 -Image pristine -Mode build     # (optional) the harsher pristine variant
```

```bash
# Linux / WSL / macOS
cd tools/clean-room
./run.sh build
./run.sh --image pristine build           # (optional)
```

This builds a **local** image tagged `copilot-cleanroom:base` (or
`copilot-cleanroom:pristine`) from the in-repo `Dockerfile`. Nothing from
copilot-extensions is baked in — the base box is just git / python3 / node / uv /
the Copilot CLI. (`run`, `eval`, and `shell` auto-build the image if it is
missing, so this explicit step is optional — but doing it first makes a clean
"did the build work?" checkpoint.)

> **Governed / corp box (internal npm feed).** If the public
> `registry.npmjs.org` is TLS-blocked, the `npm install -g @github/copilot` build
> step fails. Pass an internal feed **at build time**:
>
> ```powershell
> ./run.ps1 -Mode build -NpmRegistry https://<internal-npm-feed>/
> ```
> ```bash
> ./run.sh --npm-registry https://<internal-npm-feed>/ build
> ```
>
> The feed is a build-time convenience *only* — it installs the given Copilot
> prereq, is deleted after install, and is never inherited into the container's
> runtime (so it does not bias the fresh-machine experiment). A blocked **uv**
> index is a separate, *run-time* concern handled per scenario with
> `-UvIndex` / `--uv-index` (see [`README.md`](README.md)).

---

## 4. Auth (usually automatic)

By default the runner grabs a Copilot token from your host `gh` and injects it
into the container as `COPILOT_GITHUB_TOKEN` — **no interactive device-code
step**. Just make sure a Copilot-entitled account is logged in:

```bash
gh auth status        # confirm the intended, Copilot-entitled account is active
```

- Choose a specific account: `-TokenAccount <user>` / `--token-account <user>`.
- Or set `$env:COPILOT_GITHUB_TOKEN` yourself (e.g. a fine-grained PAT with the
  **Copilot Requests** permission).
- Or, if you cannot use a token at all, do the one-time device-code login and
  cache an `:authed` image: `-Mode auth` / `auth` (see [`README.md`](README.md)),
  then add `-NoToken` / `--no-token` to later commands.

---

## 5. Smoke-test the clean room

Prove the whole path (Docker + image + auth) works with the reference scenario:

```powershell
./run.ps1                 # runs generic-single-plugin against the base image
```
```bash
./run.sh                  # same
```

Expected: the runner starts `cr-base`, runs the scenario's stages, prints a
`== report ==` block, and writes `cr-report.json` (with `passed` / `failed`
counts + an `env{}` snapshot) plus per-phase logs to a machine-local results dir
it prints (`%LOCALAPPDATA%\copilot-cleanroom\runs\<ts>\` on Windows,
`${XDG_STATE_HOME:-~/.local/state}/copilot-cleanroom/runs/<ts>/` elsewhere).

**What "working" looks like — read this carefully:** the signal that your *clean
room* is set up correctly is that the scenario **ran to completion and produced a
`cr-report.json`** with a captured `env{}` (copilot ran in the container, the
stages executed). It is **not** `failed: 0`. The `generic-single-plugin`
reference scenario deliberately asserts hard PASS/FAIL lines about the
**install/bootstrap flow under test**, and on the current product it is *expected*
to surface some `FAIL`s (e.g. installing one plugin not pulling its siblings, or
the first-session bootstrap not deploying a runtime venv on a truly fresh box).
Those are **findings about the product**, not problems with your Docker setup —
the rig doing its job. A setup problem looks different: the run doesn't start
(Docker errors), or every phase-0 line is missing (auth/image failure). If phase 0
captured the environment and the stages ran, your clean room is good.

> **Governed / corp box (internal uv index).** Just as the *build* needs an
> internal npm feed (§3), the reference scenario's provision stage installs a
> runtime venv with **uv** — on a box where the public PyPI is TLS-blocked, add
> the internal uv index so that stage doesn't jam:
> ```powershell
> ./run.ps1 -UvIndex https://<internal-pypi-index>/ -TokenAccount <copilot-acct>
> ```
> ```bash
> ./run.sh --uv-index https://<internal-pypi-index>/ --token-account <copilot-acct> run
> ```
> (`-UvIndex`/`--uv-index` is the run-time analog of `-NpmRegistry`; see
> [`README.md`](README.md) for the full feed-governance notes.)

Tear the container down when done:

```powershell
./run.ps1 -Mode down
```
```bash
./run.sh down
```

---

## 6. (Optional) verify the agent-driven eval path

Tier-E "agent-driven" evals additionally require **`agent-bridge` on the host
PATH** — the runner registers the container as a bridge agent and drives the
in-container Copilot over it. Quick check + a first eval:

```powershell
agent-bridge --version
./run.ps1 -Scenario agent-vault-eval -Mode eval
```
```bash
agent-bridge --version
./run.sh --scenario agent-vault-eval eval
```

See [`TIER-E-EXECUTION.md`](TIER-E-EXECUTION.md) for the eval model and the judge
handoff. Plain Tier-P scenarios (§5) do **not** need `agent-bridge`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `docker: command not found`, or `docker version` shows only a **Client** section | Docker isn't installed, or the daemon / Docker Desktop isn't running. Install Docker (§1) and start it. |
| **Windows:** `error during connect … dockerDesktopLinuxEngine … pipe … not found` | Docker Desktop isn't running, or WSL integration is off. Launch Desktop; enable *Settings → Resources → WSL Integration* for your distro. |
| `docker` not found **inside WSL** | Enable WSL integration in Docker Desktop for the distro, or install Docker Engine inside the distro (§1). |
| Build fails at `npm install -g @github/copilot` with a **TLS / handshake / could-not-connect** error | Governed box: public npm is blocked. Pass `-NpmRegistry` / `--npm-registry <internal-feed>` at build time (§3). |
| Build fails **pulling `ubuntu:24.04`**, or fetching `deb.nodesource.com` / `astral.sh` | No outbound network to those hosts. Build on a box with access, or point Docker/apt/npm at internal mirrors. |
| A scenario phase **jams on a `uv` / `pip` install** (TLS / handshake / could-not-resolve) at the provision stage | Governed box: public PyPI is blocked. Add the internal uv index at run time: `-UvIndex` / `--uv-index <internal-pypi-index>` (§5). |
| The reference scenario reports some `FAIL`s (e.g. dependency-not-pulled, bootstrap venv not deployed) | Usually **not** a setup problem — those are findings about the install flow under test (§5). Your clean room is fine if the run completed and wrote a `cr-report.json` with an `env{}` snapshot. |
| **Linux:** `permission denied` on `/var/run/docker.sock` | Add your user to the `docker` group and re-login (`sudo usermod -aG docker $USER`), or prefix commands with `sudo`. |
| The runner errors on `docker rm` / `docker exec` while Desktop is up | Ensure Docker is in **Linux containers** mode (Windows Docker Desktop can be switched to Windows containers — switch back). |
| A run hangs at the first turn or the plugin install for a long time | First-ever image build + first Copilot session are slow (image pull + venv provision). Give it a few minutes; check the printed results dir's `cr-logs/` for progress. |
| `eval` mode: `agent-bridge: command not found` | Install/enable the agent-bridge plugin on the **host** — Tier-E evals need it (Tier-P scenarios do not). |
| `no host token and no …:authed image` | You passed `-NoToken` without a cached auth image, or `gh` has no Copilot-entitled account. Log in with `gh` (§4), or run `-Mode auth` once. |

---

## Recap (the whole thing on a fresh box)

```bash
# 1. install Docker for your OS and start it (§1)
docker version && docker run --rm hello-world     # 2. verify (§2)
cd tools/clean-room
./run.sh build            # 3. build the image        (Windows: ./run.ps1 -Mode build)
gh auth status            # 4. Copilot-entitled login  (auth is auto-injected)
./run.sh                  # 5. smoke-test              (Windows: ./run.ps1)
./run.sh down             # tear down
```

> On a **governed box**, steps 3 and 5 take the internal feeds:
> `./run.sh --npm-registry https://<internal-npm-feed>/ build` and
> `./run.sh --uv-index https://<internal-pypi-index>/ --token-account <acct> run`.

That's a working clean room. From here, [`README.md`](README.md) covers the full
scenario catalog, the `pristine` image, driving the box over agent-bridge, and
the Tier-E eval flow.
