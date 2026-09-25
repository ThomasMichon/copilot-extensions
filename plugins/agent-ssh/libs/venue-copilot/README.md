# venue-copilot

Shared, provider-agnostic reserve/connect/release orchestration for a venue's
CLI-mode `copilot` launch verb (`agent-codespaces copilot <name>` /
`agent-containers copilot <name>` -- `agent-bridge-cli-mode-sessions` Phase 4).

## Why a shared lib (not inside either provider plugin)

Both providers run the *same* three steps around a completely different
transport (OpenSSH via `ssh-manager` + a durable Connection Owner tenant hold
for CodeSpaces; OpenSSH into a trusted container or a restricted-fleet
`docker exec` for containers):

1. Reserve the worktree's next CLI-mode Session Host slot on the host
   `agent-bridge` daemon (`agent-bridge --json live-sessions cli-mode
   reserve`).
2. Build the exact remote command that runs `agent-worktrees copilot
   --worktree-id <id> [--driver] [--seed] [--ensure-mux]` *inside* the venue
   -- the same local `copilot` verb (PR #3126), just dispatched over an
   interactive channel rather than reimplementing attach logic a third time.
3. Release the reservation once the interactive session ends, whatever the
   outcome -- a `finally`, not a happy-path-only cleanup.

Only step 2's *transport* (how the remote command is actually run: which SSH
argv, which reverse forwards, whether a tenancy hold needs periodic
heartbeating while attached) differs per provider. Vendoring this shared core
(the same way as `ssh-manager` / `credential-relay`) keeps that difference
the only thing each provider's own `copilot` command has to own.

## Contents

- `venue_copilot.reserve_cli_mode` / `release_cli_mode` -- thin JSON-parsing
  wrappers around the `agent-bridge` CLI binstub (shelled out to, not
  imported -- a provider plugin's venv does not contain `agent_bridge`; see
  `credential-relay`'s own README for the same constraint).
- `venue_copilot.build_copilot_remote_command` -- the exact remote command
  string.
- `venue_copilot.run_venue_copilot` -- the reserve -> `connect` -> release
  orchestration. `connect` is the one provider-supplied callback: given the
  remote command string, it opens the actual interactive channel and returns
  its exit code.
