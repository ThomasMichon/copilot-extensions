---
name: setting-up-pr-identity
description: Sets up and verifies a shared vault-backed PR identity key on a local machine with an already configured agent-vault KeePass database. Use for "set up PR identity", "shared attribution key", "enable encrypted PR attribution", "PR identity cache", or "configure attribution on another machine". Separately confirms first-owner initialization; ordinary additional-machine setup never generates a key.
---

# Set up shared PR identity

Use this flow locally on each machine; no fleet transport, private dotfiles,
bridge, or repository edit is needed. Agent-vault must already have the user's
shared KeePass database configured. If not, use `agent-vault:agent-vault` for
vault configuration first.

1. Read the agent-worktrees and agent-vault command catalogs from this
   session's guidance. Use their exact absolute argv, not PATH guesses.
   If selecting a non-default vault installation, retain its source-qualified
   receipt for `--vault-context`; never pass worktrees' receipt as the vault's.
2. Run worktrees `identity-vault status --json`. For a new source, pass
   `--vault-argv '<JSON array of the vault catalog argv>'`. Status does not
   unlock, start the daemon, populate a cache, or create a key.
3. Run worktrees `identity-vault setup --vault-argv '<same JSON array>' --json`.
   The selected vault service must already run independently. On
   `service_unavailable`, use `agent-vault:agent-vault` to start it through its
   owning lifecycle outside the contained adapter, then retry. Do not weaken
   reader containment or treat a daemon started-and-killed inside a Job as ready.
   The default entry is `Identity/PR-attribution`; specify a complete
   `--entry group/entry` only when the user selected another common entry.
   If unlock is needed, get permission before adding `--prompt`; never ask
   for the master password in chat or a command argument.
4. If the entry is absent, stop normal setup. Ask whether this is a genuinely
   new owner with no existing common key, on the designated authoritative
   database copy. Only after that explicit confirmation, rerun setup with
   **both** `--initialize-owner --confirm-first-owner`. Never do this because
   of an unavailable file, cache miss, locked vault, failed read, or sync lag.
   Existing malformed keys are refused, not replaced.
5. Run worktrees `identity-vault verify --json`. Require `ready: true` and
   `roundtrip: true`; this uses a fresh process and the normal cache-only
   reader. Do not lock a shared daemon or publish a PR to test readiness.

For additional machines, first confirm the shared database has synchronized,
then use **ordinary setup**, reusing the entry. Never copy another machine's
protected cache or generate a per-machine owner key. Report local readiness,
not fleet readiness or assumed sync completion.
On `cache_unavailable`, use the vault's documented cache/runtime setup rather
than installing packages into a completed immutable runtime by hand.

See [the setup reference](../../docs/identity-vault.md) for source precedence,
installation scope, timeout behavior, and failures. Preserve conflicting legacy
overrides and explain them; do not silently delete or change user settings.
