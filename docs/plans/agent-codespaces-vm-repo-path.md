# agent-codespaces: land SSH sessions in the repo checkout via `$VM_REPO_PATH`

**Status:** open · **Area:** agent-codespaces (workspace cd / SSH session landing)

When an agent SSHes into an odsp-web CodeSpace via `agent-codespaces ssh`, the
session should reliably land in the **repo checkout**. Today the workspace-cd
resolution (`config._WORKSPACE_CD`, and the default cd for `--remote-cmd`) leans
on environment variables that are unreliable over an SSH shell.

## The problem

| Var | Reality over SSH |
|-----|------------------|
| `$CODESPACE_VSCODE_FOLDER` | **Empty** — it's a VS Code *client-side* var, not exported to SSH shells. |
| `$GITHUB_REPOSITORY` | The **source** repo (`odsp-microsoft/odsp-web-codespaces`), **not** the checkout. |
| `$VM_REPO_PATH` | **Reliable** checkout path (e.g. `/workspaces/odsp-web`) on odsp-web-codespaces devcontainers. |

Relying on `CODESPACE_VSCODE_FOLDER` (empty) or deriving the path from
`GITHUB_REPOSITORY` (wrong repo) can drop a session into the wrong directory or
`$HOME` instead of the checkout.

## The fix

Make `_WORKSPACE_CD` (and the default cd applied to `ssh` / `--remote-cmd`)
**prefer `$VM_REPO_PATH`** when it is present and non-empty, falling back to the
current heuristics only when it is not. Keep the fallback graceful for
devcontainers that don't set `VM_REPO_PATH`.

## Evidence

Verified live on an odsp-microsoft/odsp-web-codespaces CodeSpace:
`CODESPACE_VSCODE_FOLDER` empty, `VM_REPO_PATH=/workspaces/odsp-web`,
`GITHUB_REPOSITORY=odsp-microsoft/odsp-web-codespaces`,
`RepositoryName=odsp-web-codespaces`.

> Tracked here rather than as a GitHub issue: issue creation on this repo is
> blocked for the EMU account. Migrated from a Copilot memory during the
> dotfiles memory-system triage.
