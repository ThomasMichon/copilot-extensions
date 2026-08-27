# Agent Plugin Bootstrap and Command-Glossary Audit

[Effort](README.md) ·
[Phase 2 issue #1103](https://github.com/ThomasMichon/copilot-extensions/issues/1103)

## Invariant

Every runtime-bearing marketplace plugin whose name starts `agent-` must:

1. declare every agent-facing command in `payload-invocation.json`;
2. carry generated extensionless POSIX, PowerShell, and CMD payload shims;
3. wire both bootstrap-check implementations from `hooks.json`;
4. wire both session command-catalog emitters from `hooks.json`;
5. resolve hook scripts from `COPILOT_PLUGIN_ROOT`, with the runtime-defined
   payload CWD only as a compatibility fallback; and
6. keep service/provider registration as an explicit management boundary rather
   than smuggling it into an agent-facing command catalog.

Static skill prose may use another plugin's logical command name, but never its
direct path. The command-owning plugin emits the exact attributable `argv`;
absence or ambiguity never falls back to ambient `PATH`.

Initial command glossaries contain only static invocation data. A coordination
plugin may add stable machine/repository breadcrumbs, but never snapshots of
worktrees, sessions, leases, or other rapidly changing resources. Those are
discovered live through the mapped command.

## 2026-08-26 roster audit

| Plugin | Commands | Installer | Bootstrap | Glossary |
|--------|----------|-----------|-----------|----------|
| agent-worktrees | `agent-worktrees` | install | complete | complete |
| agent-bridge | `agent-bridge` | install | complete | complete after final #1103 slice |
| agent-codespaces | `agent-codespaces` | install | complete | complete |
| agent-containers | `agent-containers` | init | complete | complete |
| agent-mcp | `agent-mcp` | init | complete | complete |
| agent-ssh | `agent-ssh` | install | complete | complete |
| agent-logger | `agent-logger`, `collate-session`, `read-session-digest`, `prepare-session-log`, `ramp-up-session`, `session-sync` | install | complete | complete |
| agent-dispatch | `agent-dispatch` | install | complete | complete |
| agent-vault | `agent-vault` | install | complete | complete |
| agent-index | `agent-index` | install | complete | complete |
| agent-machines | `agent-machines` | init | complete | complete |

The audit is enforced by
`libs/payload-invocation/tests/test_agent_plugin_coverage.py` plus the canonical
generated-file check. The roster guard parses actual `sessionStart` commands,
requires attributable bootstrap/catalog wiring for both platforms, and rejects
skills that hardcode another plugin's payload bin path.

```text
python libs/payload-invocation/generate.py --all --check
python -m pytest -q libs/payload-invocation/tests
```

## Deliberate boundaries

- Payload shims still resolve legacy `~/.agent-*` roots until installation-cell
  activation is explicitly enabled.
- Compatibility generic binstubs remain during migration; skills and hooks do
  not depend on them.
- Bridge provider registration, CodeSpace/container connection ownership,
  scheduled work, and daemon supervision remain management surfaces. Catalogs
  expose commands; they do not transfer service ownership.
- The agent-bridge catalog emits its command mapping only. Worktrees and
  sessions are queried on demand; they are never copied into initial context.
