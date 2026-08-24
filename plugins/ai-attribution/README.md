# ai-attribution

> Keep publication, sanitization, and AI-attribution safety active wherever an
> agent works.

`ai-attribution` is a payload-only Copilot CLI plugin. A dependency-free
`sessionStart` hook emits a concise, attributable policy kernel in git
repositories, while an on-demand skill supplies the detailed publication
workflow. There is no runtime, venv, daemon, binstub, installer, network call,
or authentication requirement.

The plugin realizes the
[`harness-guidance`](../../visions/harness-guidance/README.md) vision's
authoritative ownership, concise context kernel, portable operator policy, and
resilient safety boundary through the
[`context-injection`](../../docs/patterns/context-injection.md) pattern.

## What it does (and how to use it)

| Entry point | When it applies | Result |
|-------------|-----------------|--------|
| `sessionStart` hook | Every session whose launch payload names a git repository | Injects the generic publication-safety kernel, operator tightening, host-qualified local remote hint, and additive target-repo contribution-guide paths. |
| `ai-attribution` skill | Preparing or auditing code, issues, pull requests, comments, releases, docs, or other published artifacts | Walks through audience and ownership classification, disclosure placement, public writing, sanitization, and live post-publication verification. |
| `ai-attribution-setup` skill | Adopting or repairing the plugin in a repository | Idempotently reconciles the marked always-on fallback, configures host-qualified operator policy, and validates hook-less launch paths. |

The ambient policy requires agents to:

- determine audience and repository ownership before publishing;
- prominently disclose AI assistance at the top of contributions to another
  party's repository;
- make disclosure optional in operator-owned repositories unless operator
  policy tightens it to always;
- keep public artifacts persona-neutral and scrub private identifiers,
  credentials, paths, hosts, accounts, record IDs, and private rationale;
- write public contributions in first-person singular and follow target-repo
  conventions;
- audit the live published surface after publication.

Local git remotes provide ownership hints only. No remote inference is treated
as proof, and uncertainty uses the third-party policy.

### Typical setup

1. Add this marketplace and enable the plugin at the desired Copilot settings
   scope:

   ```bash
   copilot plugin marketplace add ThomasMichon/copilot-extensions
   copilot plugin install ai-attribution@copilot-extensions
   ```

2. Invoke **`ai-attribution-setup`** in the adopting repository. It installs or
   updates the stable marked fallback in the repository's always-on instructions
   and configures host-qualified operator ownership policy.
3. Optionally add the target repo's additive
   `.github/ai-attribution.conf`, following
   [`examples/repository.ai-attribution.conf`](examples/repository.ai-attribution.conf).
4. Launch Copilot in a git repository. The hook supplies ambient policy
   automatically; invoke **`ai-attribution`** for the detailed workflow before
   publication.

The safe generic policy is emitted when no config exists. See
[docs/configuration.md](docs/configuration.md) for the exact `key=value`
grammar, keys, precedence, authority boundaries, and diagnostics.

## What this plugin provides - and what it doesn't

**Provides**

- A concise, stable-owner-marked ambient policy kernel.
- Safe default behavior with no configuration.
- Portable operator policy from the user config home and
  `COPILOT_CUSTOM_INSTRUCTIONS_DIRS`.
- Narrow validation before repository paths, configured accounts, or parsed
  remote identities can enter ambient context.
- Host-qualified local git remote hints without network access.
- A detailed publication and sanitization skill.
- An explicit setup skill for the stable static fallback and operator policy.
- Equivalent dependency-free Bash and PowerShell hooks.

**Does not provide**

- Proof of repository ownership. Remote parsing is deliberately only a hint.
- Secret or private-identifier storage. Literal private identifier lists do not
  belong in public repo config.
- Automatic publication, hosting-service API access, or live-surface fetching.
  The agent uses the target repository's normal tools and workflow.
- Enforcement by mutating a contribution. The hook supplies policy; the agent
  applies it.
- Mutation without an explicit setup action. The `ai-attribution-setup` skill is
  the idempotent adoption mechanism for the fallback and operator policy.

## Dependencies & assumptions

- Copilot CLI must support command-type `sessionStart` hooks.
- `git` must be available for repository gating and local remote inspection.
- On POSIX, `bash` is required. On Windows, PowerShell 5.1 or `pwsh` is
  required.
- The plugin is independently installable and assumes no sibling plugin,
  Python, jq, YAML module, network access, or authenticated account.
- The hook receives a JSON `sessionStart` payload on stdin and uses its `cwd`;
  process working directory is deliberately not authoritative.

## What's in this plugin

| Path | Purpose |
|------|---------|
| `hooks.json` | Registers the cross-platform `sessionStart` command. |
| `scripts/emit-policy.sh` | Dependency-free Bash config parser, git gate, ownership hint, and JSON emitter. |
| `scripts/emit-policy.ps1` | PowerShell implementation with parity-equivalent semantics and output. |
| `skills/ai-attribution/SKILL.md` | Source of truth for the detailed on-demand publication workflow. |
| `skills/ai-attribution-setup/SKILL.md` | Idempotent fallback adoption and operator-policy setup workflow. |
| `docs/configuration.md` | Config grammar, keys, precedence, authority boundary, ownership inference, and failure behavior. |
| `examples/*.conf` | Generic operator and target-repository config examples. |
| `tests/test_emit_policy.py` | Hook behavior, safety, exact-output, size, and parity tests. |

## Troubleshooting, contributing & issues

- **The hook emits `{}`:** run from inside a git worktree. Outside a repository,
  the no-op is intentional.
- **A setting is ignored:** read stderr. Malformed, unknown, invalid, and
  unauthorized keys are diagnosed and ignored without weakening safe defaults.
- **Ownership is unresolved or unexpectedly third-party:** configure the
  public forge account as `owned_account=<host>/<account>` in operator scope; do
  not place it in target-repo config. Still verify ownership before using the
  disclosure-only own-repo exception.

Run the plugin suite and high-signal lint from the repository root:

```bash
python tools/run-plugin-tests.py ai-attribution
ruff check --select F,E9 plugins/ai-attribution/tests
```

Contributions follow this repository's PR-required flow in
[`CONTRIBUTING.md`](../../CONTRIBUTING.md), including synchronized plugin and
marketplace version changes. File defects and enhancement requests in the
repository's public issue tracker using generic, non-sensitive examples.
