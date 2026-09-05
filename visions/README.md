# Visions — copilot-extensions

The **standing north star** for this repo: what its plugins, services, and
shared systems are *ultimately meant to be*. A vision is pure **should-be**,
revised **in place** (Git is the history), and **intent-level** — it states
*what* should be true and leaves *how* to the work that realizes it. It is not a
spec and not a status tracker.

The canonical vision pattern is governed by the **`envisioning`** skill (from
the `visions` plugin this repo ships); this page adds only the repo's local
bindings. Copy [`TEMPLATE.md`](TEMPLATE.md) to `visions/<path>/README.md` to
author a new one.

## How visions relate to the other constructs

| Construct | Question | Tense | Home |
|-----------|----------|-------|------|
| **Vision** | "What should this *ultimately* be?" | should-be (standing) | `visions/` (revised in place) |
| **Effort** | "What are we doing now?" | should-be (a campaign) | a driver/control repo, or this repo's adopted `efforts/` |
| **Doc** | "How does it *actually* work?" | is (truth) | `docs/`, per-plugin `docs/` |
| **Issue** | "What discrete thing?" | to-do | GitHub issues on this repo |

**Efforts are carved from a vision's delta vs. reality** — diff the vision's
should-be against the reality docs (and code), file the misalignments as issues
that *cite the vision item*, and group them into an effort. The vision itself is
never edited to record that cycle; it changes only when the **intent** changes.

## Vision index

| Vision | Scope | Subject |
|--------|-------|---------|
| [harness-guidance](harness-guidance/README.md) | leaf | **Harness guidance ownership and delivery** — repositories own local identity and invariants, plugins own concise generic ambient policy, skills own task-time procedures, operator policy remains portable, and context use is attributable and budgeted. |
| [plugins/efforts](plugins/efforts/README.md) | leaf | The **durable planning and continuity layer** — repositories explicitly adopt effort-backed work, reviewed plans execute in waves, worktrees remain responsible until the effort completion gate, handoffs stay compact, and cross-repository ownership follows validated target capability. |
| [plugin-services](plugin-services/README.md) | branch | The plugin **service model** — how installer-deployed plugin runtimes expose, coordinate, and are reached as local services, à la carte and without shared infrastructure. |
| [plugin-services/installation-cells](plugin-services/installation-cells/README.md) | leaf | **Marketplace installation cells** — independently installed marketplaces can ship same-named plugin ecosystems to one host without sharing runtime, state, lifecycle, adoption, discovery, or invocation ownership. |
| [installer](installer/README.md) | leaf | The **Installer & Configurator** — the standalone, out-of-plugin, self-updating app that bootstraps a bare machine into a working harness (one-line bootstrap → prereqs → core install → first harness repo), remains the non-agentic surface for doctoring, config, plugin-prerequisite validation, **plugin updating + cross-plugin alignment**, repo discovery, and Git-referenced presets, and serves as the **optional worktree/agent control-plane** (the Worktree Picker, session management, terminal muxing, launch) — never a dependency of the self-sufficient plugins. |
| [agent-fabric](agent-fabric/README.md) | branch | The layered **agent coordination fabric** — how many Copilot agents across worktrees, machines, CodeSpaces, and containers are spun up, discovered, delegated to, communicated with, and recovered as one legible whole. |
| [session-hosting](session-hosting/README.md) | leaf | **Provider-neutral Copilot session hosting** — execution rigs own launch, interaction, observation, reconnection, cutover, and retirement while durable agency/worktree state remains independent of TMux, PSMux, CLI, ACP, SDK, App, or third-party hosts. |
| [native-convergence](native-convergence/README.md) | branch | **Native-construct convergence** — how the harness converges onto Copilot CLI's *own* native constructs (worktrees, workspaces, session boundary, catalogued projects, source/worktree roots, cloud steering over the agent-host protocol): delegate the primitive, align vocabulary + layout, ride native identity/steering, keep the durable value the CLI lacks — never regressing a capability, never hard-depending on an unreleased construct. |
| [plugins/agent-worktrees](plugins/agent-worktrees/README.md) | leaf | The durable **worktree-lifetime agency authority** — repository/worktree identity, relationships, head and succession, claims, obligations, asserted disposition, and source-control completion, independent of whichever provider hosts the interactive Copilot process (child of agent-fabric). |
| [picker](picker/README.md) | leaf | The **Worktree Picker** — the interactive terminal *front door* for viewing, joining, and creating a project's worktree-backed agents, and the fabric's unified presentation surface; **delivered by the optional Installer & Configurator control-plane** (child of agent-fabric). |
| [plugins/agent-bridge](plugins/agent-bridge/README.md) | leaf | The **coordination layer** — hosts, addresses, observes, messages, resumes, and hands off live Copilot sessions across projects, worktrees, machines, and venue providers, while evolving those contracts safely across supported version skew (child of agent-fabric). |
| [plugins/agent-dispatch](plugins/agent-dispatch/README.md) | branch | The **delegation layer** — durable tasks, atomic claims, registered supervision, recipe-driven loops, worktree identity, recorded outcomes, and visible recovery (child of agent-fabric). |
| [plugins/agent-dispatch/reviewer](plugins/agent-dispatch/reviewer/README.md) | leaf | The **cooperative reviewer loop** — one task/worktree/session lineage per target review, declarative PR modules, revision-driven resume, required verdicts, and bounded verdict latency/retries (child of agent-dispatch). |
| [plugins/agent-codespaces](plugins/agent-codespaces/README.md) | leaf | The **CodeSpace venue provider** — provisions GitHub Codespaces for a repo, sets them up to run agents headlessly, reaches them over one secured transport, lends the host's identity via the credential relay (borrow-not-bottle), presents them to the fabric under the one coordination contract, and stewards them as a **finite, budget-bounded, shared pool** — leased, reused, state-tracked (in-use/idle/clean/stale), stale-cycled, and telemetry-captured (child of agent-fabric). |
| [plugins/agent-containers](plugins/agent-containers/README.md) | leaf | The **local-container venue provider** — provisions repo-shaped container agents under one fabric contract while separating trusted-development fleets from restricted low-trust sandboxes whose credentials, filesystem, network, tools, and resources are bounded by construction (child of agent-fabric). |
| [venue-parity](venue-parity/README.md) | leaf | **Venue parity** — one dispatch core in agent-bridge with **thin, symmetric venue transports** (`agent-codespaces`/`agent-containers`) that differ only in lifecycle, GitHub-token bootstrap, and cold-boot/idle; same model/skills/cwd/auth/monitoring in every venue, one SSH transport, one auth-relay back-channel; local containers as the first-class repro/hardening harness (child of agent-fabric). |
| [agent-index](plugins/agent-index/README.md) | leaf | The portable **indexing & semantic-search engine** — meaning-based retrieval over a harness repo's own code, docs, issues, PRs, and commits, ingested as a *good citizen* of managed upstreams; the reusable core a richer search product builds on. |
| [clean-room-validation](clean-room-validation/README.md) | leaf | **Clean-room validation** — turning every install/bootstrap/provision/behave claim into a hard PASS/FAIL on a disposable fresh machine: two tiers (programmatic/CI-able + agent-eval under literal mode), the solo self-sufficiency contract, a scenario matrix (solo → combinations → assembled, ×with/without the worktree base) the suite is measured against as it grows, and a turn-key assembly acceptance gate. |
| [test-portfolio](test-portfolio/README.md) | leaf | **Test portfolio effectiveness** — a contract-mapped, evidence-bearing, tiered, host-safe, and budgeted set of tests that maximizes assurance density rather than raw test count. |
| [plugins/agent-logger](plugins/agent-logger/README.md) | leaf | The **chronicler** — a scheduled, fleet-wide background daemon that turns the synced session corpus into an objective, retrievable daily chronicle, filed into each session's origin repo. A **consumer** of agent-dispatch's scheduled-production + claimable mesh (not a fabric layer). |

<!-- Add rows as visions are authored. A per-plugin vision lives at
     visions/plugins/<name>/; a cross-cutting capability vision at
     visions/<capability>/. -->

## Local conventions

This is the repo's **addendum** to the canonical pattern. It specializes only
the bindings below; it does not restate the core (see the `envisioning` skill).

### Organization

Two placement lanes, depth = specificity:

- **Cross-cutting capability visions** live at the top level:
  `visions/<capability>/` (e.g. [`visions/plugin-services/`](plugin-services/README.md)).
  Use these for intent that spans plugins — the service model, the install
  contract, the credential-relay trust model.
- **Per-plugin visions** mirror the code layout: `visions/plugins/<name>/`
  (e.g. [`visions/plugins/agent-bridge/`](plugins/agent-bridge/README.md)). Use these when a vision maps
  1:1 to a single plugin, so the vision↔`plugins/<name>/docs/architecture.md`
  diff is straightforward.

A **branch** README (a folder with children) is a lean map that links its
children; a **leaf** README is concrete. Decompose liberally — push a component
that is its own subject down into a child vision rather than inlining it.

### Schema

Use the core section set (Purpose & Intent · Concepts & Components · Features ·
Behaviors · Non-Goals / Boundaries · See Also, plus the optional non-authoritative
Provenance). No repo-specific renames or additions.

### Issue & effort linkage

- **Tracker:** GitHub issues on `ThomasMichon/copilot-extensions`. Per the
  repo's contribution rules, **claim a stretch with an issue first** (search
  open issues, then take or comment on one). Cite the vision item precisely,
  e.g. *"advances Vision plugin-services §Behaviors/collision-free-endpoints"*.
- **Public-artifact rule:** issues and commits are world-readable — keep them
  generic (no downstream-private names or context), per `AGENTS.md`.
- **Efforts:** this repo has adopted an in-repo `efforts/active/<slug>/` tree
  (see [`efforts/active/`](../efforts/active/)); the vision→reality delta is
  carved into **GitHub issues** here and grouped into an in-repo **effort** that
  cites the vision item it closes. Where a private driver additionally runs the
  work, that driver's plan may **link back** to the public issue/effort.
- **Reality docs:** a vision's *See Also* points at the architecture/README that
  records what *is* (chiefly [`docs/architecture.md`](../docs/architecture.md),
  [`docs/install-contract.md`](../docs/install-contract.md), and per-plugin
  `docs/`). Keep those links live when docs move.

### Cross-repo sequencing

This repo is **PR-required** with a `pr-self-merge` profile. A vision revision
that also drives implementation elsewhere lands through its PR before any
unreviewed direct change in a related repository. Completion-only markers may
follow implementation.

This sequencing policy is intentionally retained in the stable
`visions:cross-repo-sequencing` owner region in `AGENTS.md`. The marker makes
the always-on compatibility/fallback explicit and idempotently reconcilable;
future plugin injection may shrink it through that same marker, but must not
silently remove the ordering invariant.
