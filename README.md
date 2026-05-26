# copilot-extensions

A [Copilot CLI](https://docs.github.com/copilot/how-tos/use-copilot-agents/use-copilot-cli)
plugin marketplace for developer workflow automation.

## Plugins

| Plugin | Description |
|--------|-------------|
| [worktree-manager](plugins/worktree-manager/) | Worktree isolation system for concurrent Copilot CLI sessions |

## Installation

```bash
# Register the marketplace
copilot plugin marketplace add ThomasMichon/copilot-extensions

# Install a plugin
copilot plugin install worktree-manager@copilot-extensions
```

Or install directly without registering the marketplace:

```bash
copilot plugin install ThomasMichon/copilot-extensions:plugins/worktree-manager
```

## What You Get

After installing the `worktree-manager` plugin:

- **Skills loaded automatically** — `worktree` (lifecycle, finalization,
  cleanup) and `service-lifecycle` (deployment patterns) are available in
  all Copilot CLI sessions
- **Bootstrap check** — a lightweight session-start hook checks whether the
  runtime is installed and prints a hint if not
- **Setup skill** — ask Copilot to "set up worktree-manager" to bootstrap
  the Python runtime (venv + binstubs)

### Runtime Bootstrap

The plugin ships the Python source for the worktree-manager CLI. After
plugin installation, bootstrap the runtime:

1. Ask Copilot: *"set up worktree-manager"* (invokes the `worktree-setup`
   skill)
2. Or follow the manual steps in the
   [setup skill](plugins/worktree-manager/skills/worktree-setup/SKILL.md)

### Project Registration

Once the runtime is installed, register a project:

```bash
worktree-manager install --machine <machine-name>
```

This creates per-project config at `~/.{project}/` and a binstub for
launching worktree sessions.

## License

[MIT](LICENSE)
