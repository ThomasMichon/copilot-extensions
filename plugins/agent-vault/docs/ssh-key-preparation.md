# Vault-owned SSH key preparation

`agent-vault ssh-key prepare` validates and reuses one Ed25519 key pair in the
selected existing KeePass database. It returns **only public metadata**:

```json
{"version":1,"status":"reused","public_key":"ssh-ed25519 <base64>","fingerprint":"SHA256:<digest>"}
```

Use the owning payload's session-catalog command. The optional `ssh` extra
(`agent-vault[ssh]`) supplies cryptographic generation and pair validation;
missing support produces `crypto_unavailable`, not a replacement key or
fallback implementation. Other Vault commands do not require the extra.

```text
<agent-vault catalog argv[0]> ssh-key prepare --entry SSH/node-a --json
```

Ordinary preparation **never initializes a missing entry**. A missing entry
means synchronize the existing authoritative database or repair its selection.
It does not mean this machine should mint another identity. Database selection,
named vault/group resolution, unlock providers and installation-local service
discovery remain owned by agent-vault. An existing valid pinned context remains
authoritative. No command discovers, creates or relocates a database.

For a genuinely new identity, separately authorize initialization on the
designated authoritative database, then use both flags:

```text
<agent-vault catalog argv[0]> ssh-key prepare --entry SSH/node-a --initialize --confirm-initialization --json
```

The first successful creation returns `status:"created"`. Repeating it, even
with both flags, reuses a valid existing pair. A malformed entry, ambiguous
listing, read failure or unconfirmed write fails explicitly without deleting,
editing or rotating that entry. Never initialize independently on another
machine because a synchronized database has not arrived yet. The existing
database creation lock serializes processes using the **same local database**;
it is not a distributed lock across separate synced copies.

The whole pair is a canonical base64-encoded schema-v1
`agent-vault.ssh-key` JSON record in one protected Password field. The private
OpenSSH key and derived canonical public key are validated together. Creation
uses the existing authoritative recursive listing and create-only KeePass
backend operation, committing the entire pair in one database add rather than
separate attachment writes. An uncertain add is followed by an authoritative
read, never blindly repeated. Private material stays in process memory and the
encrypted database; preparation writes no plaintext key files or protected
local cache and does not return private material over IPC or on stdout.

This record is deliberately distinct from legacy `import-key` / `export-key`
attachments. Those commands retain their existing behavior; preparation does
not adopt or overwrite an attachment entry. Nor does it convert an existing
machine key, authorize a key, configure SSH, install a server or enroll a
machine. Public metadata is not an admission or readiness receipt. Actual
key materialization, bidirectional onboarding and admitted-key distribution
remain separate consumer operations.

By default the CLI may start its selected existing service, but it never prompts
unless `--prompt` is supplied. `--require-running` checks that service and
refuses activation; use it from a contained setup adapter after independently
provisioning and starting the owning Vault. It does not permit a payload shim
to provision an absent installation. Requests are bounded at 90 seconds, or
120 seconds with explicit prompting. Errors are secret-free JSON with `version`,
`error` and an actionable `message`; exit status is 1 rather than success-shaped
missing metadata.

## Qualification

`tests/test_ssh_key.py` covers validation, explicit initialization, ordinary
reuse, failures, uncertain completion, same-service concurrency and the actual
CLI dispatch/output contract. The declared `dev` extra includes its
cryptographic test prerequisite, so a freshly prepared development environment
does not silently skip these contracts. The opt-in
`tests/test_ssh_key_keepass_live.py` uses a disposable database and isolated
service; select it with `AGENT_VAULT_SSH_REAL_KEEPASS=1` through the repository's
contained plugin test runner. It does not access an installed daemon, an
operator database, a remote machine or a production synchronization service.
