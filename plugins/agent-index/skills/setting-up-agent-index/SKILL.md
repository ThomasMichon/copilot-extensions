---
name: setting-up-agent-index
description: >
  Configure agent-index for a repository and operator: checked-in repo defaults,
  repo-local machine overlay, bound-knowledge-repo corpus overlay, indexer
  designation, and verification/troubleshooting. Use for first-time setup,
  repairing configuration, or explaining the layered config model. Trigger
  phrases include:
  - 'set up agent-index'
  - 'configure agent-index'
  - 'agent-index setup'
  - 'enable agent-index for this repo'
  - 'add corpus sources'
  - 'designate an indexer'
  - 'why is agent-index not enabled'
  - 'configure the knowledge repo overlay'
---

# Setting up agent-index

Use this skill when the task is to **enable or configure** agent-index, not to
search an already-enabled index. For day-to-day querying, use the
`searching-the-harness-index` skill instead.

## Readiness

- Inside an agent session, invoke the exact `argv` from the session command
  catalog. Do not search `PATH` or substitute another `agent-index` binary.
- Session start is non-mutating: it only decides whether the command catalog and
  scope guidance should appear, then runs a cheap `ensure` safety net for an
  already-installed host runtime. It does **not** provision a runtime or
  reindex.
- The runtime can stay inactive even when the plugin is enabled globally. A repo
  must opt in through config.
- Once the runtime is installed on a host, `install` / `update` register the
  default durable autostart tier automatically: HKCU Run on Windows, or
  systemd --user on POSIX when available. `register-tasks` on Windows is now an
  explicit per-machine upgrade, not the default path to persistence.

## The layered config model

Use the same layered naming and precedence documented for the other
`.agent-*` configs in `docs/configuration.md` and
`plugins/agent-worktrees/docs/config-reference.md`:
**machine-local > knowledge overlay > in-repo**.

There are three distinct configuration roles:

1. **In-repo** — `<repo>/.agent-index/config.yaml`
   - Shareable, tracked, safe to commit.
   - Declares default `corpus.sources` for that repository.
   - Should not carry machine identity.
2. **Knowledge overlay** — `<knowledge-repo>/.agent-index/config.yaml`
   - Shareable within the operator's private knowledge repo.
   - Sits above the in-repo base for repositories that require external state.
   - Typically self-declares knowledge-repo corpus sources that should follow
     the operator across stateless-harness repos.
3. **Machine-local** — `<repo>/.copilot-extensions/agent-index/config.yaml`
   - Personal or machine-local; usually gitignored.
   - Adds private `corpus.sources` and/or the local `indexer:` designation.
   - This is the highest-precedence layer.

`corpus.sources` is the one intentional exception to the ordinary merge rule.
The established config model replaces list-valued keys wholesale from the
highest-precedence layer that sets them; agent-index keeps that rule for
`indexer`, `indexers`, and every other key. But `corpus.sources` behaves like a
set of independent declarations, so it unions by `name`, with the
higher-precedence layer winning duplicate names and lower-precedence layers
contributing only missing names.

This separation keeps a public/shareable repo's defaults in tree while letting
an operator privately designate the host/indexer machine and add personal
corpora.

## Author the checked-in repo defaults

In the target repo, add `<repo>/.agent-index/config.yaml` with a `corpus.sources`
list. Example:

```yaml
corpus:
  sources:
    - name: github:gim-home/odsp-web-harness
      trust_domain: odsp-web-harness
    - name: github:ThomasMichon/copilot-extensions
      trust_domain: copilot-extensions
```

Use one source entry per logical corpus. The built-in `github:<owner>/<repo>`
connector already covers that repo's code, commits, issues, and pull requests.

## Designate the indexer machine privately

Keep the host designation in the repo-local overlay, not the checked-in file:

```yaml
indexer:
  machine: <machine-name>
corpus:
  sources:
    - name: git:dotfiles
      repo: dotfiles
      trust_domain: dotfiles
```

You can write this explicitly, or use:

```text
<catalog argv[0]> setup --single --repo <repo>
<catalog argv[0]> setup --indexer <machine> --ssh <alias> --repo <repo>
```

`setup` writes the machine role into `~/.agent-index/config.yaml` and writes the
repo-local designation/overlay into
`<repo>/.copilot-extensions/agent-index/config.yaml`.

## Add a bound knowledge-repo overlay

In the bound knowledge repo, add `<knowledge-repo>/.agent-index/config.yaml`
with any self-declared corpus sources that should follow the operator across
stateless-harness repos. Example:

```yaml
corpus:
  sources:
    - name: git:dotfiles
      repo: dotfiles
      trust_domain: dotfiles
```

This file is the shareable, checked-in declaration for the knowledge repo
itself. It is the **knowledge overlay** tier in the precedence model and is
distinct from the current repo's machine-local overlay.

## Verify the setup

1. Inspect the repo activation:
   - `<catalog argv[0]> capability --json`
   - `<catalog argv[0]> status`
2. Read the session guidance file for this session:
   `instructions/agent-index/session-guidance.instructions.md`
   - It should say agent-index is enabled and list the merged sources.
3. Confirm retrieval sees the expected corpora:
   - `<catalog argv[0]> search "<query>" --json`
4. When validating indexing rather than activation, inspect:
   - `<catalog argv[0]> status`
   - the per-source coverage section in its JSON output

## Troubleshooting

- **Not enabled / repository-config-absent**: the repo lacks both a checked-in
  `.agent-index/config.yaml` and a usable external-only fallback.
- **Repository enabled but missing private corpora**: check the repo-local
  `.copilot-extensions/agent-index/config.yaml` overlay and, for stateless
  harnesses, the bound knowledge repo's `.agent-index/config.yaml`.
- **Source present in config but not indexed**: activation and indexing are
  separate. Confirm the source appears in `status`, then run the operator
  indexing flow if needed.
- **Setup wrote machine-specific state into the wrong file**: machine identity
  belongs in the repo-local overlay or `~/.agent-index/config.yaml`, never the
  checked-in `.agent-index/config.yaml`.
- **Knowledge repo source not advertised in another repo**: verify the current
  repo requires external state, the knowledge repo is bound, and the knowledge
  repo's checked-in `.agent-index/config.yaml` is present and valid.
