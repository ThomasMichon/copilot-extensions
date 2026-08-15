---
name: agent-worktrees-wsl-provision
description: >
  Provision the current repo in WSL -- clone, install agent-worktrees, and
  register the WSL environment so Windows Terminal profiles work. Use this
  after adopting a repo on Windows to enable WSL sessions. Trigger phrases
  include:
  - 'provision in WSL'
  - 'set up WSL'
  - 'WSL provision'
  - 'enable WSL sessions'
  - 'clone to WSL'
  - 'agent-worktrees wsl'
---

# Agent Worktrees WSL Provision

Provision the **current project** in WSL so that Windows Terminal's WSL
profile launches correctly.  This is the full-adoption path -- it clones
the repo into WSL, runs `install.sh`, and updates the Windows-side
projects registry.

**Prerequisite:** WSL must be installed with at least one distribution.
The agent-worktrees runtime must already be installed on Windows (see
the `copilot-extensions-setup` skill).

## When to Use

- After adopting a repo on Windows, the `(WSL)` terminal profile won't
  appear because the repo doesn't exist in WSL yet.
- The Windows installer deploys a **bootstrap stub** that handles
  first-run clone + install interactively.  This skill does the same
  thing but with full agent guidance -- better for complex repos.

## Provision Flow

### 1. Check WSL availability

```powershell
wsl.exe --status
```

If WSL is not installed or no distro exists, stop and inform the user.

### 2. Identify the target distro

```powershell
# List available distros
wsl.exe -l -q
```

If multiple distros exist, ask the user which one to use.  Default to
the first (default) distro.

### 3. Determine clone target

Default: `~/src/{project-name}` inside WSL.

Ask the user to confirm or customize:

```powershell
wsl.exe -d <distro> -- bash -c 'echo $HOME/src'
```

Check if the repo already exists at the target path:

```powershell
wsl.exe -d <distro> -- bash -c 'test -d ~/src/{project}/.git && echo exists'
```

If it exists, skip cloning.

### 4. Validate prerequisites inside WSL

```powershell
wsl.exe -d <distro> -- bash -c 'command -v git && command -v bash'
```

If `git` is missing, instruct the user to install it. `uv` needs no separate
step **as long as a downloader is present**: `install.sh`'s `_ensure_uv`
bootstraps uv from `astral.sh` via `curl` (else `wget`, else `python3`'s
urllib), so at least one of `curl`/`wget`/`python3` must exist in the distro
(most ship `python3`, so this is usually satisfied). If none is present,
pre-install `uv` in the distro before provisioning.

### 5. Clone the repo

Get the remote URL from the Windows-side repo:

```powershell
git remote get-url origin
```

Clone into WSL:

```powershell
wsl.exe -d <distro> -- bash -c 'git clone <remote-url> ~/src/{project}'
```

**Important:** Verify that git auth works inside WSL.  SSH keys may not
be shared between Windows and WSL.  If clone fails, help the user set up
SSH key forwarding or HTTPS credentials.

### 6. Run install.sh

```powershell
wsl.exe -d <distro> -- bash -c 'cd ~/src/{project} && bash plugins/agent-worktrees/scripts/install.sh install --project-name {project}'
```

The installer path assumes the standard `plugins/agent-worktrees/`
layout.  If the repo uses a different layout, adjust accordingly.

### 7. Update Windows-side registry

After successful installation in WSL, update the Windows projects
registry to reflect WSL adoption:

```powershell
{project} register-project-entry {project} `
  --repo-dir <windows-repo-dir> `
  --wsl-state adopted `
  --wsl-distro <distro> `
  --wsl-path "~/src/{project}"
```

Then re-run the Windows-side update/repair path to regenerate terminal profiles.

### 8. Refresh terminal profiles

```powershell
# Re-run the installer's update action to regenerate the fragment
pwsh -NoProfile -File <plugin-dir>/scripts/install.ps1 update
```

This regenerates the Windows Terminal fragment with the WSL profile now
included (since `wsl.state` is `adopted`).

### 9. Verify

```powershell
# Check the WSL binstub works
wsl.exe -d <distro> -- bash -lc '{project} --version'

# Check the WT fragment includes the WSL profile
$frag = Get-Content "$env:LOCALAPPDATA\Microsoft\Windows Terminal\Fragments\AgentWorktrees\agent-worktrees.json" | ConvertFrom-Json
$frag.profiles | Where-Object { $_.name -like '*WSL*' }
```

## Edge Cases

- **Multiple distros:** Ask the user.  Store the chosen distro in the
  registry so profiles target it specifically.
- **Repo already exists in WSL:** Skip clone, run install only.
- **SSH auth failure:** Help set up `~/.ssh/` in WSL or suggest HTTPS.
- **Private repos:** Ensure credentials are available inside WSL.
- **Different remote URLs:** WSL may need a different remote (e.g.,
  SSH vs HTTPS).  Ask if the Windows remote URL works from WSL.

## Registry Format

After provisioning, the project entry in `projects.yaml` will include:

```yaml
projects:
  my-project:
    anchor: "D:\\Src\\my-project"
    # ... other fields ...
    wsl:
      state: "adopted"
      distro: "Ubuntu"
      path: "~/src/my-project"
```

The `state` field controls terminal profile generation:
- `adopted` -- full install exists, profile launches normally
- `bootstrap` -- bootstrap stub deployed, profile triggers first-run flow
- absent -- no WSL profile generated
