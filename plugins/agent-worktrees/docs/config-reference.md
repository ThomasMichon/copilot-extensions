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
- **Per-repo settings** merge **global `repo_defaults` < in-repo flat settings <
  machine-local `repos.<name>` block**.
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

### Per-repo keys — `repos.<name>`

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `anchor` | string | **required** | The main checkout worktrees branch from. |
| `worktree_root` | string | `<anchor>.worktrees` | Where worktrees are created (a sibling folder by default). |
| `default_branch` | string | `master` | Upstream branch worktrees rebase/merge onto. |
| `remote` | string | `origin` | Git remote name. |
| `launch` | map(platform→list) | `{}` | Config-driven launch command per platform. Overrides the repo convention and built-in default. |
| `launch_recovery` | map(platform→list) | `{}` | Launch command used in recovery mode (`-Recovery`). |
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
| `branch_prefix` | string | `feature` | Prefix for generated feature-branch names (e.g. `feature/<slug>-<suffix>`). |

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
  overrides them per key, and a global `repo_defaults` block underlies them.
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

Machine-wide defaults shared across **every** project on the machine — the
lowest tier. Generated by the installer (`install` / `register`); profiles are
user-authored.

```yaml
# ~/.agent-worktrees/config.yaml
srcroot: /home/me/src     # parent of your repos
machine: my-machine       # machine key (matches machines.yaml)
platform: wsl             # windows | wsl | linux

copilot_profiles:         # machine-wide backend profiles (Tab-cycle in picker)
  - name: cloud
    label: "Cloud (GitHub)"

repo_defaults:            # optional: per-repo defaults underlying every repo
  remote: origin          # (lowest precedence; in-repo + machine-local override)
```

| Key | Type | Meaning |
|-----|------|---------|
| `srcroot` / `machine` / `platform` | string | Machine-wide top-level defaults (overridable per machine-local). |
| `copilot_profiles` | list | Machine-wide backend profiles. |
| `repo_defaults` | map | Flat repo-settings applied as the **lowest** layer to every repo. |

A convention-adopted repo with its anchor in `~/.agent-worktrees/repos.yaml`,
its settings in the in-repo config, and machine defaults here needs **no**
`~/.{project}/config.yaml` at all.
