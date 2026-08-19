# agent-bridge: in-repo named-agent config — Proposal

**Status:** proposal. Captures the design and a phased plan; no implementation is
proposed for immediate execution here. Companion to the derived-roster direction
(agent-ssh vision *Mesh introspection & derived roster*) and the "retire
`acp-agents.json` references" cleanup.

## The problem

agent-bridge already derives its agent roster from committed topology
(`machines.yaml` control-plane project + `repos.yaml` `agent: true` checkouts,
machines × repos × environments) and honors an in-repo
`.agent-bridge/config.yaml` (`RepoBridgeConfig`) that travels with the code. The
hand-authored `acp-agents.json` is deprecated — loaded only for explicit
back-compat (`agents_config`), and explicit entries win over derived ones.

But the in-repo `.agent-bridge/config.yaml` today carries **only spawn defaults**
(`default_copilot_args`, `default_env`). It cannot declare a **named agent**. So
any agent that is *not* a plain topology derivation — one with a bespoke charter:
a specific remote `host`/pool, custom `copilot_args`/`env`, worktree settings
(`supports_worktrees`, `worktree_root`, `worktree_discovery`), or a display
name/icon — still has to live in the deprecated hand-authored `acp-agents.json`.

That side-file is a recurring **drift surface**:

- It is maintained separately from where agents are otherwise declared, so it
  falls out of sync with reality.
- The running daemon loads the registry **once at startup** and does not reload
  it when the file changes. An agent added by a merge is therefore *unknown to
  the running daemon* until a manual restart — every spawn of it fails the
  registry lookup (`'<name>' is not a known agent name`) and its dispatch lane
  dead-letters until someone restarts the bridge.

## Proposal

Let the durable, in-repo `.agent-bridge/config.yaml` be the home for **named
agent definitions**, not just spawn defaults — so specialized agents become
reviewed, version-controlled config that travels with the repo to every machine
that syncs it, exactly like the derived roster and the spawn defaults already do.
This retires `acp-agents.json` as the *primary* agent source (back-compat load
stays, deprecated).

Two complementary directions, in preference order:

1. **Named agents in the in-repo config.** Extend `RepoBridgeConfig` with an
   `agents:` map carrying the full `AgentConfig` surface. Precedence mirrors the
   existing model: an explicitly-declared in-repo agent wins over a derived one
   (same `setdefault` semantics as an `agents_config` entry today), so the
   derived roster keeps filling the machine lanes while the in-repo config
   supplies the bespoke bodies.
2. **Auto-discover sub-agents (longer-term).** Where an agent is already declared
   as a repo sub-agent (`.github/agents/*.agent.md`) or contributed by an enabled
   plugin, derive it rather than re-declaring it — so the registry is *derived*
   from what the repo/plugins already state. Keep a bespoke in-repo declaration
   only for the surplus a sub-agent file can't express (remote host/pool, spawn
   args, worktree wiring).

Independently of source, close the **freshness gap**: a newly-declared agent
should become resolvable without a manual daemon restart — the daemon reloads its
registry when the in-repo config / sub-agent set changes, or the reload is part
of the normal deploy/update step.

## Phased plan (design only)

- [ ] **P1 — Schema + reconcile.** Add an `agents:` map to `RepoBridgeConfig`
      (typed to the `AgentConfig` surface; `extra: ignore` keeps older daemons
      forward-compatible). Reconcile the intent to an agent-bridge **vision**
      (agent-bridge has no vision folder yet — this is vision-extending: add the
      "in-repo named-agent config" concept) and check it against
      `docs/patterns/` (config/discovery invariants).
- [ ] **P2 — Resolver wiring.** In `build_resolver`, merge in-repo named agents
      into the roster with the established precedence (explicit in-repo wins over
      derived; `agents_config` remains a deprecated equal-precedence source).
- [ ] **P3 — Registry freshness.** Make a newly-declared agent resolvable without
      a manual restart: reload on change (watch the in-repo config) and/or reload
      as part of the deploy/update step. This is the gap that causes dead-letter
      loops when a new agent lands by merge.
- [ ] **P4 — Sub-agent discovery (optional/longer-term).** Derive agents from
      `.github/agents/*.agent.md` + enabled-plugin sub-agents; keep bespoke
      in-repo declarations only for beyond-discovery customization.
- [ ] **P5 — Retire `acp-agents.json` as primary.** Fold the docs/code sweep
      (the existing "retire `acp-agents.json` references" cleanup) in behind the
      in-repo path: present the in-repo config + derived roster as the source of
      truth, keep `agents_config` as deprecated back-compat only.

## Validation plan

- Unit: a `RepoBridgeConfig` with an `agents:` map parses; unknown/extra keys are
  ignored (forward-compat).
- Unit: `build_resolver` merges in-repo named agents; an explicit in-repo agent
  wins over a derived same-name agent; an `agents_config` entry still loads.
- Integration: a repo that declares a named agent **only** in
  `.agent-bridge/config.yaml` (no `acp-agents.json`) resolves and spawns it.
- Freshness: adding an agent to the in-repo config makes it resolvable without a
  full daemon restart (per the P3 mechanism chosen).

## Reconcile

- **Vision:** vision-extending for agent-bridge's configuration model — the
  durable, in-repo, reviewed config becomes the home for named agents, not a
  hand-authored side-file. (agent-bridge lacks a vision folder today; P1 adds the
  concept.)
- **Patterns:** touches the config/endpoint-discovery conventions in
  `docs/patterns/`; the in-repo config already established the "settings travel
  with the repo" pattern (spawn defaults) — this extends it to named agents.

## Related

- Derived-roster direction — agent-ssh vision *Mesh introspection & derived
  roster* / the `derived-agent-roster` feature (the machines × repos facet;
  this proposal is the adjacent *named specialized bodies* facet).
- The "retire `acp-agents.json` references now the roster is topology-derived"
  cleanup (folded in as P5).
