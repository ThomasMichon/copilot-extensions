# Agent Worktrees — Configuration Reference

Every configuration option for a repo adopted by agent-worktrees.

## Config sources (layered)

agent-worktrees merges configuration from the layers below at load time.
**Highest precedence wins**, per key (deep merge):

| Precedence | Source | Path | Scope | Committed? |
|------------|--------|------|-------|-----------|
| **Highest** | **Machine-local** | `~/.{project}/config.yaml` | Per-machine overrides + machine paths (anchor, custom worktree_root). The **adapter** that makes a *foreign* repo compatible. | No |
| *(conditional)* | **Knowledge overlay** | bound knowledge repo's config | For a **stateless harness** bound to a knowledge repo, portable operator-preference keys only (`copilot_profiles`, `profile_assignment`, `headless`, `auto_fast_forward`, `new_picker`). Machine-specifics and the binding never graft. | Yes |
| **Middle** | **In-repo** | `<anchor>/.agent-worktrees/config.yaml` | The repo's **own** committed settings — the base, shared by every machine. | Yes |
| **Lowest** | **Global** | `~/.agent-worktrees/config.yaml` | Machine-wide defaults: `srcroot`, `machine`, `platform`, `copilot_profiles`, `session_backend`. | No |

**A repo designed for this system needs no machine-local file.** Its anchor
resolves from the repos registry (`~/.agent-worktrees/repos.yaml`), its settings
come from the in-repo config, and machine-wide defaults come from the global
config. Machine-local config is only needed to **override** a setting on a
specific machine, or to adopt a *foreign* repo (work product, external GitHub)
that carries no in-repo config.

- **Portable top-level fields** (`srcroot`/`machine`/`platform`/
  `copilot_profiles`/`profile_assignment`/`headless`/`auto_fast_forward`/
  `new_picker`) resolve **machine-local > knowledge overlay (portable prefs
  only) > global > detected/default**.
- **Machine-host fields** (`session_backend`) resolve **machine-local >
  global > default**. They are never read from committed in-repo config or a
  knowledge overlay.
- **Per-repo settings** merge **in-repo flat settings < machine-local
  `repos.<name>` block**. The global tier carries *only* machine-wide top-level
  settings — never per-repo settings.
- No file holds the "full stack": the complete merged config for a target repo
  is **computed on-demand** by the loader from these layers. `agent-worktrees
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
session_backend:                  # optional same-machine hosted sessions
  kind: ahp
  endpoint_url: ws://127.0.0.1:8765
  github_account: octocat

repos:
  my-project:
    anchor: C:\Data\Src\my-project          # machine path (or omit → from repos.yaml)
    worktree_root: C:\Data\Src\.worktrees\my-project   # only if non-default
    # default_branch / remote / pr / ... may live in-repo instead (below)
    copilot_path:
      windows: C:\src\copilot-runtime\dist-bin\win32-arm64\copilot.exe
```

> **Keep the overlay minimal — don't restate registry-owned facts.**
> `load_config` falls back to the registries/global for `anchor` (repos.yaml),
> `default_branch` (repos.yaml), `base_repo` (projects.yaml), and
> `srcroot`/`machine`/`platform` (global config). So an overlay that repeats any
> of these is **redundant** — a second copy that must be kept in sync. Set a key
> here only to **override** the registry value on this machine. `agent-worktrees
> repos doctor` flags redundant/conflicting overlay keys and `--fix` strips the
> redundant ones (comments preserved). A base-repo enlistment overlay typically
> shrinks to just `repo_name` + the `env_script` it alone provides.

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

profile_assignment:               # optional; default-off
  name: balanced-default
  mode: balanced-random
  armed: true
  profiles: [cloud, local]
  assignment_label: cohort-a      # optional opaque label
  eligible_lanes: [new, handoff-cutover]

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
| `new_picker` | bool | `true` | Use the Textual picker. `picker disable` writes `false` to opt the machine out to the legacy picker. |
| `copilot_profiles` | list | `[]` | Selectable Copilot backend profiles (Tab-cycle in the picker). |
| `profile_assignment` | map | absent/off | Optional balanced assignment policy over existing `copilot_profiles`. Only a user-owned global, knowledge-overlay, or machine-local/per-project block can set `armed: true`. |
| `session_backend` | map | `{kind: direct}` | Machine-local interactive session host. `direct` preserves one ordinary Copilot process per launched terminal; `ahp` binds worktrees to a same-machine `copilotd`. Not accepted from in-repo config or a knowledge overlay. |
| `repos` | map | `{}` | Per-repo configuration, keyed by repo name. |

### Same-machine AHP session backend — `session_backend`

This experimental opt-in hosts durable worktree sessions in an externally
managed same-machine `copilotd`. Agent-worktrees still creates, tracks, lands,
and finalizes the exact worktree. AHP owns the Copilot conversation and lets
terminal clients detach and later reattach to the same session.

```yaml
session_backend:
  kind: ahp
  endpoint_url: ws://127.0.0.1:8765
  github_account: octocat
  protocol_versions: ["0.7.0"]
  auth_resource: https://api.github.com
  connect_timeout_seconds: 15
```

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `kind` | string | `direct` | `direct` or `ahp`. The default leaves existing launch behavior unchanged. |
| `endpoint_url` | string | `""` | Required for `ahp`. Must be `ws://` on `localhost`, `127.0.0.1`, or `::1`, with an explicit port and no credentials, query, or fragment. |
| `github_account` | string | `""` | GitHub login used to mint a repository-scoped token. When omitted, the registered repository account must resolve it. Ambient-account fallback is not allowed. |
| `protocol_versions` | list[string] | `["0.7.0"]` | Offered AHP versions. Startup fails closed if the host selects a version outside this list. |
| `auth_resource` | string | `https://api.github.com` | AHP authentication resource paired with the minted GitHub token. |
| `connect_timeout_seconds` | number | `15` | Connection and ordinary request timeout, in `(0, 120]`. Session create/dispose use a 30-second minimum lifecycle budget and retry one host-owner startup timeout with a fresh session id. |

The endpoint is intentionally explicit in the first release: agent-worktrees
does not start, stop, upgrade, or discover `copilotd`. The launcher runs
`session-backend ensure` after worktree preflight, persists the exact hosted
session id, and starts Copilot with
`--experimental --ahp <endpoint> --resume=<session-id>`. Selecting the worktree
again verifies the same id and exact working directory before attach.
The launcher mints one account-scoped token per fresh attach and passes that
same token privately to both the controller and Copilot client. Mux launches
use a one-shot protected handoff consumed by the pane wrapper, so the token is
not inherited by the tmux/psmux server, status updater, or resident monitor. It
is never written to the binding or JSON output.

Exiting the terminal client does **not** dispose the hosted session or trigger
ordinary post-exit finalization. `session-backend status` reads the persisted
binding; `ensure` is the host-backed liveness/path check. Finalization refuses
bindings in `active` or `unknown` state. Run `session-backend dispose` only when
the hosted transcript may be retired; a successful dispose marks the binding
terminal and permits normal finalization. Switching configuration back to
`direct` while a binding is active or unknown fails closed rather than launching
a duplicate local Copilot process. The initial backend is wired only through the
normal Worktree Manager launcher; `embody` and `handoff-cutover` fail closed
instead of starting an unbound direct client.

### Config drop-ins — `~/.{project}/config.d/`

A **service** can register machine-local config without editing the shared
`config.yaml`. Valid entries are sorted by name and deep-merged as a **base
UNDER** the machine-local `config.yaml` — so an explicit `config.yaml` still
wins, and multiple services coexist. The merged result then layers over the
in-repo + global tiers as usual.

Two entry classes are supported:

- `*.yaml` / `*.yml` — direct, permanent **operator-owned** fragments.
- `*.json` — a managed plugin pointer with exactly:

  ```json
  {
    "schema_version": 1,
    "plugin": "example-plugin@example-marketplace",
    "plugin_root": "/current/verified/plugin/root",
    "target": "/current/verified/plugin/root/config/fragment.yaml"
  }
  ```

Managed pointers activate only while the plugin is enabled globally or for this
project, the stored root exactly matches its current identity-verified root, and
the target is a regular YAML file canonically contained by that root. Each file
is parsed and structurally validated independently. Confirmed invalidity or
absence withdraws the fragment; transient registry/entry/target I/O retains only
that entry's last-known contribution. Operational warnings are bounded;
`agent-worktrees doctor [--json]` is exhaustive and report-only.

Use it for service-owned settings that shouldn't live in the committed repo
config. Example — a credential helper registering its askpass path so
`sudo -A` works in the session, without leaking a vault-specific key into the
shared repo config:

```yaml
# ~/.test-chamber/config.d/vault.yaml
repos:
  test-chamber:
    session_env:
      SUDO_ASKPASS: /home/me/.local/bin/vault-askpass
```

This operator fragment deep-merges with the repo's own `session_env` (e.g.
`COPILOT_FEATURE_FLAGS`), so both keys reach the session. (Read by
agent-worktrees ≥ 1.5.3-dev113.)

### Per-repo keys — `repos.<name>`

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `anchor` | string | **required** | The main checkout worktrees branch from. |
| `worktree_root` | string | `<anchor>.worktrees` | Where worktrees are created (a sibling folder by default). |
| `default_branch` | string | `master` | Upstream branch worktrees rebase/merge onto. |
| `remote` | string | `origin` | Git remote name. |
| `profile_assignment` | map | absent/off | Machine-local per-project override for the top-level assignment policy. This user-owned location may arm it; the committed in-repo block with the same key may only template/narrow. |
| `launch` | map(platform→list) | `{}` | Config-driven launch command per platform. Overrides the repo convention and built-in default. |
| `launch_recovery` | map(platform→list) | `{}` | Launch command used in recovery mode (`-Recovery`). |
| `copilot_path` | map(platform→string) | `{}` | Project-scoped Copilot executable. Uses `windows` or `linux` (`wsl` maps to `linux`) and supports `{work_dir}`, `{anchor}`, `{machine}`, `{repo_name}`, and `{home}` placeholders. The normalized launcher uses it for interactive, resume, recovery, and agent-bridge project launches without changing global `PATH`. An explicit `launch`/`launch_recovery` template remains authoritative. |
| `setup_hook` | map(platform→**path**) | `{}` | Repo session setup hook (a script path, relative to `anchor`). Declaring it opts the repo into the **normalized launch**: agent-worktrees' launcher runs the hook (context by argument — `-Machine`/`-Recovery` — not ambient env), then execs Copilot. The hook does repo-specific setup (vault, MCP) and returns; it must NOT launch Copilot. Skipped in recovery. |
| `env_script` | map(platform→**path**) | `{}` | Repo **environment-priming** script (a script path, relative to `anchor`). Unlike `setup_hook` (a child process whose env is discarded), the launcher runs this **in its own shell and captures the resulting environment** so the Copilot exec inherits it (Windows: `call <script>` then snapshot `set`; POSIX: `source` with `set -a`). For **Windows enlistment-style repos** whose build tooling only works inside a dynamically-established env (e.g. an Office/SPO `OpenEnlistment.bat` setting OTOOLS/VC++/SDK vars + PATH): a plain `copilot` there can read code but not build. Also opts the repo into the normalized launch; runs **even in recovery** (the build env is always needed). Ignored when an explicit `launch` template is set. |
| `session_path` | map(platform→list) | `{}` | Directories the normalized launcher prepends to `PATH` before launch (templated: `{work_dir}`, `{anchor}`, `{machine}`, `{repo_name}`) — e.g. `["{work_dir}/tools/bin"]`. The generic mechanism for a repo to expose its tool binstubs without an ambient PATH export. |
| `session_env` | map(str→str) | `{}` | Environment variables the launch plan applies to the Copilot session (e.g. `COPILOT_FEATURE_FLAGS: extensions`, or `SUDO_ASKPASS: "{home}/.local/bin/vault-askpass"`). Values are **templated** (`{work_dir}`, `{anchor}`, `{machine}`, `{repo_name}`, `{home}`) so a per-machine path is portable. Merged below the backend profile. This is how a repo contributes session env **without** an ambient export — and it works with the normalized launcher, where the setup hook runs as a child process and so cannot set env for the Copilot exec. |
| `validate_paths` | list[str] | `[]` | Repo-relative paths the `validate` command checks for. |
| `validate_hook` | map(platform→list) | `{}` | Custom validation command per platform. |
| `service_paths` | list[str] (globs) | `[]` | Globs for service discovery (`services` subcommands). |
| `post_install_hook` | map(platform→list) | `{}` | Command run after install, per platform. |
| `pr` | map | *(disabled)* | PR-workflow policy — see below. **Can also live in-repo.** |
| `base_repo` | bool | `false` | Drive the anchor directly with **no worktrees** (for repos that can't use worktrees, e.g. enlistment-based monorepos). Pair with `env_script` (Windows enlistment env) or a custom `launch`. |

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

The normalized `setup_hook` surface is also the supported cooperative boundary
for write-capable setup. Before the hook runs, agent-worktrees resolves and
validates the per-project machine-local root (`~/.<project>/`) through
`agent-worktrees config-root`, then exports it as
`AGENT_WORKTREES_CONFIG_ROOT`. A hook that writes concrete operator or product
configuration must write beneath that root. An explicit destination can be
preflighted with `agent-worktrees config-root --destination <path>`; a path
inside a stateless checkout fails before the hook executes. Custom `launch`
commands and legacy `tools/setup/setup.*` scripts are outside this cooperative
boundary and must invoke the same resolver themselves before writing.

Select a local Copilot build for one project without replacing the ambient
`copilot` command:

```yaml
repos:
  my-project:
    copilot_path:
      windows: "C:\\src\\copilot-runtime\\dist-bin\\win32-arm64\\copilot.exe"
      linux: "{home}/src/copilot-runtime/dist-bin/linux-arm64/copilot"
```

The selected file must be directly executable. For a source build that is
started through another interpreter, point this setting at a small executable
wrapper or at the build's standalone executable.

`env_script` is likewise a **path** (relative to `anchor` unless absolute). It
suits a Windows base-repo enlistment whose build env is established by a setup
`.bat` — declare it and drop any hand-authored `call …bat && copilot` wrapper:

```yaml
repos:
  SPO.Core:
    base_repo: true
    env_script:
      windows: "otools\\bin\\OpenEnlistment.bat"
```

### Backend profiles — `copilot_profiles[]`

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `name` | string | **required** | Profile id (must be unique; duplicates are dropped). |
| `label` | string | = `name` | Human-readable label shown in the picker. |
| `env` | map(str→str) | `{}` | Environment variables exported for the session. Keys must be valid env-var identifiers. |
| `copilot_args` | list[str] | `[]` | Extra arguments passed to `copilot`. |

### Balanced profile assignment — `profile_assignment`

Profile assignment is an explicit, default-off policy over the existing named
profiles above. It never defines model/backend semantics itself: the selected
`CopilotProfile` contributes its normal `env` and `copilot_args` to the ordinary
launch planner.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `name` | string | **required when configured** | Stable policy identifier. |
| `mode` | string | **required when configured** | Currently `balanced-random`: a deterministic shuffled bag emits each pool member once per generation before reshuffling. |
| `armed` | bool | `false` | Enables assignment. Only user-owned global, knowledge-overlay, or machine-local/per-project config has arming authority. |
| `profiles` | list[str] | `[]` | Non-empty user-owned pool of names already present in `copilot_profiles`. |
| `assignment_label` | string | `""` | Optional opaque label persisted with each assignment; agent-worktrees assigns no analytics meaning to it. |
| `eligible_lanes` | list[str] | `[new, handoff-cutover]` | Eligible new-session lanes. Supported values are `new` and `handoff-cutover`. |

`armed` is strictly boolean. Quoted values such as `armed: "true"` are
configuration errors rather than truthy aliases or silent disarming.

Eligibility is deliberately narrow:

- `new` covers a cold/new generation in a tracked, user-origin, interactive CLI
  worktree. A launch retry reuses the same pending token and bag position.
- `handoff-cutover` treats the successor as a new generation. Retrying the same
  pending cutover reuses its assignment.
- Ordinary resume replays the assignment bound to that Copilot session, even if
  the policy was later disarmed. It never advances the bag. If that persisted
  profile is absent or renamed on the current machine, resume warns and falls
  back to the ordinary default/manual profile rather than failing.
- An explicit `--profile` remains authoritative and stays outside assignment.
- Recovery launches, base-repo launches, ACP/bridge sessions, daemon/system
  worktrees, and agent-delegated worktrees are hard-excluded; configuration
  cannot opt them in. Exclusion does not clear the ordinary concrete profile:
  the picker/launch path retains its selected default or manual profile,
  including that profile's arguments and environment.

Launch-class exclusion happens only after authoritative user configuration is
validated. An invalid armed user-owned policy therefore fails before any
worktree mutation even when the caller supplied `--profile`, requested
recovery, or selected another excluded launch class. A malformed repository-only
default-off template remains non-load-bearing.

The project-local state file stores the installation seed, bag
generation/position, token-keyed pending outcomes, and terminal history.
Compaction never evicts a live pending assignment, so the ledger may
temporarily exceed its history limit until assignments bind or expire. The
pending record is durable before the launch plan is returned. Session
registration binds its token to the actual Copilot session id and retires the
one-shot capability. Public worktree/session records never contain the token.
An unbound token expires to `abandoned` on a launch, session-registration, or
status/list maintenance pass and retains its consumed bag position. Corrupt,
future-schema, unreadable, or contended optional state emits a bounded warning
and the core launch/session lifecycle continues without assignment; invalid
armed user configuration still fails before any worktree mutation.

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
| `source_attribution` | bool | `false` | Embed a hidden marker in the initial PR body and publish later pushed heads as dedicated managed PR comments. Consumers use the newest marker. Opt in only for closed-circuit systems where source identifiers are safe to publish; public repos should leave this disabled. `--no-attribution` can suppress it for one invocation. |
| `required_body_sections` | list[string] | `[]` | Markdown heading names that must exist with non-empty visible content before `create-pr` may publish, for example `[Intent, Changes, Validation]`. Use `--body` or `--body-file`; hidden comments do not satisfy a section. |
| `automerge_label` | string | *(empty)* | **Review-vocabulary binding** for the `pr-*` command family. The label whose presence signals **merge consent** — the author's post-approval "auto-complete this" (applied by `pr-merge`). Empty ⇒ no auto-merge mechanism configured (the family declines rather than guessing a label). Example value: `auto-merge`. |
| `hold_labels` | list | `[]` | **Review-vocabulary binding.** Labels that **block** consent/merge — an explicit hold or a state needing author action (e.g. a rebase). Empty ⇒ nothing is treated as a hold. Example value: `[do-not-merge, needs-rebase, wip]`. |
| `wip_title_prefixes` | list | `[]` | **Review-vocabulary binding.** Case-insensitive PR-title prefixes treated as work-in-progress (never eligible for consent). Empty ⇒ no title is WIP. Example value: `["wip:", "[wip]", "draft:", "[draft]"]`. |
| `approval_required` | bool | `true` | Must a PR be **approved** before `pr-merge` requests auto-complete? `true` preserves the review-gated shape (a `CHANGES_REQUESTED` still always blocks). `false` suits a **self-complete** repo (we own the merge): eligible when simply *not* changes-requested — no approval vote needed. |
| `allow_stale_approval` | bool | `false` | Allow the latest approval to authorize `pr-merge` after the PR head changes only when the tracked current head was already published before that approval was submitted. A post-approval push remains unapproved; missing or mismatched publication evidence fails closed. The `pr-status` / `pr-watch` payloads report `approval_stale` and `approval_stale_authorized`. Enable only when repository policy permits this bounded stale-review race. |
| `squash` | bool | `true` | Auto-complete completion option (Azure DevOps): squash-merge. |
| `delete_source_branch` | bool | `true` | Auto-complete completion option (Azure DevOps): delete the source branch on merge. |
| `bypass_policy` | bool | `false` | Complete the PR **past** branch policies when requesting auto-complete (Azure DevOps). Needed for a default branch whose policy never auto-satisfies for our own PRs (e.g. a central governance **status** policy). Only set true where we are authorized to self-complete. |
| `bypass_reason` | string | *(empty)* | Reason recorded on the policy bypass. |

> **"Request auto-complete" is the first-class concept; the label is an
> implementation detail.** `pr-merge` asks the provider to *auto-complete* the
> PR. On **gitea / github** the provider honors that by applying `automerge_label`
> (the review gate then merges). On **Azure DevOps** there is no label —
> the provider sets **native auto-complete** (`az repos pr update --auto-complete`
> with `squash` / `delete_source_branch` / `bypass_policy`), and a snapshot
> reports the `auto-complete` consent marker in its labels once set. So an ADO
> repo binds `automerge_label: auto-complete` (the abstract consent-marker name)
> and gets the full `pr-agent-merge` flow — no new flow shape.

> **Review-vocabulary binding (the "multi-machine system hook").** `automerge_label` /
> `hold_labels` / `wip_title_prefixes` — alongside the provider fields
> `provider` / `api_base` / `token_command` / `token_env` — are how a repo binds
> the provider-generic `pr-*` command family (`pr-watch`, `pr-merge`,
> `pr-status`) to its review backend. The plugin ships them **empty**: absent a
> binding the family is a no-op, never a crash. Verdict semantics
> (approve / request-changes) are provider-intrinsic, not a binding; a
> `review:*`-style status tag needs no binding — being neither the auto-merge
> nor a hold label, the classifier ignores it. See the `pr-command-family` effort in
> test-chamber.

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
profile_assignment:
  name: balanced-default
  mode: balanced-random
  armed: false          # committed config never arms, even if set true
  profiles: [cloud]     # may only narrow a user-owned pool
  eligible_lanes: [new]
```

- These settings are the **base**; a machine-local `repos.<name>` block
  overrides them per key.
- `profile_assignment` is trust-aware rather than a normal override: committed
  config may publish a named default-off template and intersect an already
  user-armed pool/eligible-lane set. It cannot arm assignment or introduce a
  profile absent from the user-owned pool. A malformed committed template is
  non-load-bearing while no user-owned policy is armed; if a user arms the
  policy, malformed repository defaults or restrictions fail validation before
  launch side effects.
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
  - name: alternate
    label: "Alternate"
    copilot_args: ["--model", "example-model"]

profile_assignment:
  name: balanced-default
  mode: balanced-random
  armed: true
  profiles: [cloud, alternate]
  assignment_label: cohort-a
```

| Key | Type | Meaning |
|-----|------|---------|
| `srcroot` / `machine` / `platform` | string | Machine-wide top-level defaults (overridable per machine-local). |
| `copilot_profiles` | list | Machine-wide backend profiles. |
| `profile_assignment` | map | Optional user-owned balanced assignment policy. |
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
primary: example-web                  # the default/primary related repo
related:
  example-web:
    role: product                  # product|dependency|consumer|tooling|docs|sibling
    summary: "Primary product monorepo we ship changes to."
    doc: related/example-web.md       # narrative, relative to .agent-worktrees/
    locus:
      preferred: codespace         # local | machine:<key> | codespace | container
      machines: [dev6]             # boxes a *local* checkout is available on (optional)
      codespace: { repo: org/example-web-codespaces,
                   workspace_folder: /workspaces/example-web }   # cloud: any machine
      container: { repo: org/example-web-codespaces,
                   workspace_folder: /workspaces/example-web,
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

An active plugin may contribute a lowest-precedence related-repo fragment by
shipping `.agent-worktrees/related.yaml` in its payload. The active corpus is
resolved from the effective user plus adopted-project plugin configuration, so
copied plugins and live directory-marketplace plugins follow the same enabled
and identity-verified rules. Disabled or unresolved plugins contribute nothing;
the project and knowledge layers still override plugin entries wholesale.
