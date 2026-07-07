# Agent Worktrees — Configuration Reference

Every configuration option for a repo adopted by agent-worktrees.

## Three config sources (layered)

agent-worktrees merges configuration from three layers at load time. **Highest
precedence wins**, per key (deep merge):

| Precedence | Source | Path | Scope | Committed? |
|------------|--------|------|-------|-----------|
| **Highest** | **Machine-local** | `~/.{project}/config.yaml` | Per-machine overrides + machine paths (anchor, custom worktree_root). The **adapter** that makes a *foreign* repo compatible. | No |
| **Middle** | **In-repo** | `<anchor>/.agent-worktrees/config.yaml` | The repo's **own** committed settings — the base, shared by every machine. | Yes |
| **Lowest** | **Global** | `~/.agent-worktrees/config.yaml` | Machine-wide defaults: `srcroot`, `machine`, `platform`, `copilot_profiles`. | No |

**A repo designed for this system needs no machine-local file.** Its anchor
resolves from the repos registry (`~/.agent-worktrees/repos.yaml`), its settings
come from the in-repo config, and machine-wide defaults come from the global
config. Machine-local config is only needed to **override** a setting on a
specific machine, or to adopt a *foreign* repo (work product, external GitHub)
that carries no in-repo config.

- **Top-level fields** (`srcroot`/`machine`/`platform`/`copilot_profiles`/
  `headless`/`auto_fast_forward`) resolve **machine-local > global > detected**.
- **Per-repo settings** merge **in-repo flat settings < machine-local
  `repos.<name>` block**. The global tier carries *only* machine-wide top-level
  settings — never per-repo settings.
- No file holds the "full stack": the complete merged config for a target repo
  is **computed on-demand** by the loader from these three sources. `agent-worktrees
  get …` reads through that on-demand merge.
- A missing or malformed file at any tier is skipped safely — config loading
  never breaks the CLI on a bad file.

> **Version note:** the in-repo **directory form**
> (`<anchor>/.agent-worktrees/config.yaml`) and the global tier are read by
> **agent-worktrees ≥ v1.5.3-dev34**. Older plugins read only the machine-local
> file plus a legacy single-file `<anchor>/.agent-worktrees.yaml` (which carried
> just a `pr:` block) — still honored as a back-compat fallback when the
> directory form is absent.

---

## Machine-local config — `~/.{project}/config.yaml`

Optional. Only what is specific to **this machine**, or overrides. The installer
writes a slim version (project marker + anchor); machine-wide fields live in the
global config.

```yaml
repo_name: my-project             # which repos.<name> is the active/default repo
headless: false                   # CLI-only project (bare binstub lists worktrees)
auto_fast_forward: true           # FF a stale, clean worktree on resume (override)

repos:
  my-project:
    anchor: C:\Data\Src\my-project          # machine path (or omit → from repos.yaml)
    worktree_root: C:\Data\Src\.worktrees\my-project   # only if non-default
    # default_branch / remote / pr / ... may live in-repo instead (below)
```

It may *also* carry the machine-wide fields below (they then override the global
config), but the slim form above is preferred:

```yaml
srcroot: C:\Data\Src              # parent of your repos (or ~/src on Linux)
machine: my-machine               # machine key (auto-detected if omitted)
platform: windows                 # windows | wsl | linux (auto-detected)
copilot_profiles:                 # optional: selectable backend profiles
  - name: cloud
    label: "Cloud (GitHub)"
  - name: local
    label: "Local model"
    env:
      COPILOT_PROVIDER_BASE_URL: "http://localhost:8090/v1"
    copilot_args: ["--deny-tool", "shell"]

repos:
  my-project:
    anchor: C:\Data\Src\my-project
    # ... per-repo keys (below)
```

### Top-level keys

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `srcroot` | string | `""` | Source root — parent directory of your repos. |
| `machine` | string | auto-detected | Machine key (matches `machines.yaml`). |
| `platform` | string | auto-detected | `windows` \| `wsl` \| `linux`. Selects which platform-keyed command map applies. |
| `repo_name` | string | `""` | Which `repos.<name>` is the default repo. Optional when exactly one repo is defined. |
| `headless` | bool | `false` | CLI-only project: the bare binstub lists worktrees instead of launching an interactive Copilot session. |
| `auto_fast_forward` | bool | `true` | On resume, fast-forward a clean worktree that is strictly behind upstream. Only ever a FF — never touches dirty / ahead / diverged worktrees. |
| `copilot_profiles` | list | `[]` | Selectable Copilot backend profiles (Tab-cycle in the picker). |
| `repos` | map | `{}` | Per-repo configuration, keyed by repo name. |

### Config drop-ins — `~/.{project}/config.d/*.yaml`

A **service** can register machine-local config without editing the shared
`config.yaml`. Every `*.yaml` in `~/.{project}/config.d/` (sorted by name, later
names win among drop-ins) is deep-merged as a **base UNDER** the machine-local
`config.yaml` — so an explicit `config.yaml` still wins, and multiple services
coexist. The merged result then layers over the in-repo + global tiers as usual.

Use it for service-owned settings that shouldn't live in the committed repo
config. Example — the Aperture Vault registering its askpass path so
`sudo -A` works in the session, without leaking a vault-specific key into the
shared repo config:

```yaml
# ~/.aperture-labs/config.d/vault.yaml   (written by the vault installer)
repos:
  aperture-labs:
    session_env:
      SUDO_ASKPASS: /home/me/.local/bin/vault-askpass
```

This deep-merges with the repo's own `session_env` (e.g.
`COPILOT_FEATURE_FLAGS`), so both keys reach the session. (Read by
agent-worktrees ≥ 1.5.3-dev113.)

### Per-repo keys — `repos.<name>`

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `anchor` | string | **required** | The main checkout worktrees branch from. |
| `worktree_root` | string | `<anchor>.worktrees` | Where worktrees are created (a sibling folder by default). |
| `default_branch` | string | `master` | Upstream branch worktrees rebase/merge onto. |
| `remote` | string | `origin` | Git remote name. |
| `launch` | map(platform→list) | `{}` | Config-driven launch command per platform. Overrides the repo convention and built-in default. |
| `launch_recovery` | map(platform→list) | `{}` | Launch command used in recovery mode (`-Recovery`). |
| `setup_hook` | map(platform→**path**) | `{}` | Repo session setup hook (a script path, relative to `anchor`). Declaring it opts the repo into the **normalized launch**: agent-worktrees' launcher runs the hook (context by argument — `-Machine`/`-Recovery` — not ambient env), then execs Copilot. The hook does repo-specific setup (vault, MCP) and returns; it must NOT launch Copilot. Skipped in recovery. |
| `session_path` | map(platform→list) | `{}` | Directories the normalized launcher prepends to `PATH` before launch (templated: `{work_dir}`, `{anchor}`, `{machine}`, `{repo_name}`) — e.g. `["{work_dir}/tools/bin"]`. The generic mechanism for a repo to expose its tool binstubs without an ambient PATH export. |
| `session_env` | map(str→str) | `{}` | Environment variables the launch plan applies to the Copilot session (e.g. `COPILOT_FEATURE_FLAGS: extensions`, or `SUDO_ASKPASS: "{home}/.local/bin/vault-askpass"`). Values are **templated** (`{work_dir}`, `{anchor}`, `{machine}`, `{repo_name}`, `{home}`) so a per-machine path is portable. Merged below the backend profile. This is how a repo contributes session env **without** an ambient export — and it works with the normalized launcher, where the setup hook runs as a child process and so cannot set env for the Copilot exec. |
| `validate_paths` | list[str] | `[]` | Repo-relative paths the `validate` command checks for. |
| `validate_hook` | map(platform→list) | `{}` | Custom validation command per platform. |
| `service_paths` | list[str] (globs) | `[]` | Globs for service discovery (`services` subcommands). |
| `post_install_hook` | map(platform→list) | `{}` | Command run after install, per platform. |
| `pr` | map | *(disabled)* | PR-workflow policy — see below. **Can also live in-repo.** |
| `base_repo` | bool | `false` | Drive the anchor directly with **no worktrees** (for repos that can't use worktrees, e.g. enlistment-based monorepos; pair with a custom `launch`). |

**Platform-keyed maps** (`launch`, `launch_recovery`, `validate_hook`,
`post_install_hook`) use the keys `windows`, `wsl`, `linux`, each mapping to a
command expressed as a list of arguments:

```yaml
launch:
  windows: ["pwsh.exe", "-NoProfile", "-File", "scripts/setup.ps1"]
  linux:   ["bash", "scripts/setup.sh"]
```

`setup_hook` is a **path** (not a command list); `session_path` is a list of
directories. Both are platform-keyed:

```yaml
setup_hook:
  windows: "tools/setup/session-setup.ps1"   # relative to anchor
  linux:   "tools/setup/session-setup.sh"
session_path:
  windows: ["{work_dir}\\tools\\bin"]
  linux:   ["{work_dir}/tools/bin"]
```

### Backend profiles — `copilot_profiles[]`

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `name` | string | **required** | Profile id (must be unique; duplicates are dropped). |
| `label` | string | = `name` | Human-readable label shown in the picker. |
| `env` | map(str→str) | `{}` | Environment variables exported for the session. Keys must be valid env-var identifiers. |
| `copilot_args` | list[str] | `[]` | Extra arguments passed to `copilot`. |

---

## PR workflow — `repos.<name>.pr` (machine-local **or** in-repo)

Controls whether sign-off goes through a pull request instead of direct-push
finalization. This block can be set in the machine-local config **or** in the
in-repo overlay (below); the in-repo version wins when both are present.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `enabled` | bool | `false` | Turn on PR mode — makes `create-pr` available. With `enabled` alone the PR path is *optional* per worktree: it is taken once a `create-pr` has run; a worktree with no PR record still finalizes direct to the default branch. |
| `required` | bool | `false` | **Enforce** PRs: `push-changes` and the unmerged-work guard in `finalize` refuse the direct-to-default-branch path. The only way to land work is `create-pr` → open PR → merge. **Implies `enabled`.** |
| `provider` | string | `gitea` | `gitea` \| `github` \| `azure-devops`. Selects which sub-agent / CLI opens the PR. |
| `strategy` | string | `detach` | Default disposition after `create-pr`: `keep-alive` (keep the worktree open to iterate on review feedback, pushing updates to the feature branch) or `detach` (finalize the worktree immediately; resume later via a fresh `create`). Does **not** affect squash timing — squashing always happens at `create-pr`. |
| `branch_prefix` | string | `feature` | Prefix for generated feature-branch names (e.g. `feature/<slug>-<suffix>`). Used by the `snapshot` head scheme and as the `{prefix}` token in `head_pattern`. |
| `head_scheme` | string | `refspec` | How `create-pr` publishes the PR head — **naming + push mechanism only**. Both schemes leave the local worktree on `worktree/<id>` at the squashed commit (never reset off it, #1804). `refspec` (default, #1815/#1899): push `worktree/<id>` directly to the PR head ref (`worktree/<id>:refs/heads/pr/<slug>`) — no local feature branch. Requires the repo's pre-push hook to permit the mediated `worktree/<id> → pr/<slug>` push (honor `AGENT_WORKTREES_PR_PUSH=1`). `snapshot` (legacy/compatible): copy the squashed commit onto a separate local `feature/<slug>` branch and push that — no reset, no checkout dance; needs no pre-push-hook cooperation, so it's the safe opt-out for a repo whose hook still blocks the refspec push. A parallel `--new` PR auto-falls-back to a snapshot ref even under `refspec`. A present-but-invalid value falls back to `snapshot` (the compatible scheme), not the refspec default. |
| `head_pattern` | string | *(scheme default)* | Template for the PR head branch name. Tokens: `{prefix}` `{slug}` `{suffix}` `{username}` `{machine}`. Empty ⇒ scheme default: `pr/{slug}-{suffix}` under `refspec` and `{prefix}/{slug}-{suffix}` under `snapshot` (`feature/<slug>`). Repos that want e.g. `user/{username}/{slug}-{suffix}` set it explicitly. `{username}` resolves from the repo's git identity (`user.email` local-part, then `user.name`). |

Query the effective (post-merge) values at runtime:

```bash
agent-worktrees get pr-enabled    # true | false
agent-worktrees get pr-required   # true | false
agent-worktrees get pr-provider   # gitea | github | azure-devops (empty when off)
```

See the `worktree` skill § PR Workflow for the end-to-end flow
(`create-pr` → open PR → review → merge → `finalize`).

---

## In-repo config — `<anchor>/.agent-worktrees/config.yaml`

A committed file carrying the repo's **own repo-level settings** — the base
layer, identical on every machine that checks out the repo. The schema is
**flat repo-settings**: the same per-repo keys as a `repos.<name>` block, but
**without** `anchor` / `worktree_root` (machine paths) and without a `repos:`
map. Any of these may appear:

```yaml
# <repo-root>/.agent-worktrees/config.yaml
default_branch: main
remote: origin
validate_paths: [src, tests]
service_paths: ["services/*"]
launch:
  linux: ["bash", "scripts/setup.sh"]
pr:
  required: true        # implies enabled; blocks direct-to-default-branch
  provider: gitea
  strategy: keep-alive  # default disposition after create-pr
```

- These settings are the **base**; a machine-local `repos.<name>` block
  overrides them per key.
- Omitting `pr:` leaves PR mode **off** (direct-push finalization) — appropriate
  for a repo with no automated reviewer.
- **Location:** the directory form `<anchor>/.agent-worktrees/config.yaml`
  (constant `INREPO_CONFIG_DIRNAME` + `config.yaml`) is canonical. The legacy
  single-file `<anchor>/.agent-worktrees.yaml` (`INREPO_CONFIG_FILENAME`, `pr:`
  only) is still read as a fallback when the directory form is absent; the
  directory form wins when both exist.
- A missing or malformed file safely degrades to "no in-repo settings" — the
  machine-local + global tiers still resolve the repo.

---

## Global config — `~/.agent-worktrees/config.yaml`

The **user-owned base tier**: machine-wide settings shared across **every**
project on the machine. The installer **scaffolds it once when missing**, then
**never overwrites it** — not even with `--force` (which targets installer-owned
artifacts). Only a deliberate schema migration should rewrite it. Profiles are
user-authored.

It holds **only machine-wide top-level settings** — never per-repo settings, and
never a registry of repos or machines. (The full merged config for any repo is
computed on-demand by the loader; nothing materializes it here.)

```yaml
# ~/.agent-worktrees/config.yaml
srcroot: /home/me/src     # parent of your repos
machine: my-machine       # machine key (matches machines.yaml)
platform: wsl             # windows | wsl | linux

copilot_profiles:         # machine-wide backend profiles (Tab-cycle in picker)
  - name: cloud
    label: "Cloud (GitHub)"
```

| Key | Type | Meaning |
|-----|------|---------|
| `srcroot` / `machine` / `platform` | string | Machine-wide top-level defaults (overridable per machine-local). |
| `copilot_profiles` | list | Machine-wide backend profiles. |
| `auto_fast_forward` / `headless` | bool | Machine-wide top-level defaults. |

A convention-adopted repo with its anchor in `~/.agent-worktrees/repos.yaml`,
its settings in the in-repo config, and machine defaults here needs **no**
`~/.{project}/config.yaml` at all.

---

## Related repos -- `<anchor>/.agent-worktrees/related.yaml`

A separate **committed, in-repo** file (a sibling of the in-repo `config.yaml`)
that records, **from this repo's point of view**, the OTHER repos relevant to
it. It is *directional* and *per-project* -- distinct from the global,
machine-wide `repos.yaml` registry. Keys reference **global-registry names**;
the file adds only relationship + locus + delegate, never checkout paths (those
still resolve from `repos.yaml`).

Managed by `agent-worktrees related ...`; see the **`agent-worktrees-related`**
skill (authoring the index) and **`working-cross-repo`** skill (using it).

```yaml
# <anchor>/.agent-worktrees/related.yaml
primary: odsp-web                  # the default/primary related repo
related:
  odsp-web:
    role: product                  # product|dependency|consumer|tooling|docs|sibling
    summary: "Primary product monorepo we ship changes to."
    doc: related/odsp-web.md       # narrative, relative to .agent-worktrees/
    locus:
      preferred: codespace         # local | machine:<key> | codespace | container
      machines: [dev6]             # boxes a *local* checkout is available on (optional)
      codespace: { repo: org/odsp-web-codespaces,
                   workspace_folder: /workspaces/odsp-web }   # cloud: any machine
      container: { repo: org/odsp-web-codespaces,
                   workspace_folder: /workspaces/odsp-web,
                   machines: [dev6] }                         # local fleet: dev6 only
    delegate: { via: agent-codespaces }   # agent-bridge | agent-codespaces | agent-containers | none
```

| Key | Type | Meaning |
|-----|------|---------|
| `primary` | string | The default related repo (`related resolve` with no name uses it). |
| `related.<name>` | map | One related repo, keyed by its **global-registry** name. |
| `related.<name>.role` | string | `product` \| `dependency` \| `consumer` \| `tooling` \| `docs` \| `sibling` (free-form; stored verbatim). |
| `related.<name>.summary` | string | One line: why the repo matters to this one. |
| `related.<name>.doc` | string | Narrative-doc path, relative to `.agent-worktrees/` (default `related/<name>.md`). |
| `related.<name>.locus.preferred` | string | Where work happens: `local` \| `machine:<key>` \| `codespace` \| `container`. |
| `related.<name>.locus.machines` | list | Machine keys a *local* checkout is available on (per-machine availability the per-platform registry can't express). |
| `related.<name>.locus.codespace` | map | GitHub CodeSpace hints: `repo` / `machine` / `location` / `workspace_folder`. Cloud venue -- usable from any machine. |
| `related.<name>.locus.container` | map | Local Docker dev-container fleet: `repo` / `workspace_folder` + a `machines` list scoping it to the fleet hosts. Local venue -- `machines` restricts where it runs. |
| `related.<name>.delegate.via` | string | How to hand off work: `agent-bridge` \| `agent-codespaces` \| `agent-containers` \| `none`. |

Reads degrade safely (a missing/malformed file yields an empty index); a bare
`name:` is a valid minimal link. Writes emit only non-empty fields, keeping the
committed file minimal.

