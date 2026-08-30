# Efforts — copilot-extensions

This tree holds active implementation campaigns for copilot-extensions. An
effort connects standing vision intent and GitHub issues to a phased plan,
participants, validation, and an append-only journal. The canonical lifecycle
and schema come from the `efforts:planning-efforts` skill; this page only binds
that pattern to this repository.

## Active effort index

| Effort | Status | Coordination |
|--------|--------|--------------|
| [agent-bridge AHP Convergence](active/agent-bridge-ahp-convergence/README.md) | Draft | #1266 |
| [Migration Intake](active/migration-intake/README.md) | Draft | See effort |
| [Account-Aware Operations](active/account-aware-operations/README.md) | Draft | See effort |
| [Agent Machines Declarative Control Plane](active/agent-machines-declarative-control-plane/README.md) | Draft | See effort |
| [Review Automation Reliability](active/review-automation-reliability/README.md) | Draft | See effort |
| [Worktree Finality and Obligations](active/worktree-finality-and-obligations/README.md) | Draft | #1312 |
| [Marketplace-Scoped Installations](active/marketplace-scoped-installations/README.md) | Active | #1096 |
| [Native-Construct Convergence](active/native-construct-convergence/README.md) | Active | #985 |
| [Plugin Process Hygiene](active/plugin-process-hygiene/README.md) | Active | #736 |
| [Restricted Venue Targets](active/restricted-venue-targets/README.md) | Draft | #1188 |
| [Session Context Aggregation](active/session-context-aggregation/README.md) | Draft | #1325 |
| [Test Portfolio Rationalization](active/test-portfolio-rationalization/README.md) | Draft | #1303 |
| [Turn-key Reviewer Loops](active/turnkey-reviewer-loops/README.md) | Draft | #1403 |
| [Venue Parity](active/venue-parity/README.md) | Active | #954 |
| [Windows Launch Hardening](active/windows-launch-hardening/README.md) | Active | #786 |
| [Worktree Manager Control Plane](active/worktree-manager-control-plane/README.md) | Active | #352 |
| [agent-index Engine Daemon](active/agent-index-engine-daemon/README.md) | Done; pending archive | See effort |
| [Uniform Runtime Resolution](active/uniform-runtime-resolution/README.md) | Done; pending archive | #765 |

## Local conventions

### Grouping and archive layout

- Active efforts use the flat layout `efforts/active/<slug>/`.
- Completed efforts archive to `efforts/<YYYY>/MM/DD <slug>/`.
- `efforts/TEMPLATE.md` is the local starting template.

### Participants and coordination

The `Participants` seam may name operating-system agents, worktrees, branches,
or other execution venues. Record only generic, public-safe identities and how
the participant is reached. Multi-agent efforts must name the branch topology,
the PR owner, slice ownership, and handoff rule in `## Coordination`.

### Issues and sources

- GitHub issues in `ThomasMichon/copilot-extensions` are the discrete tracking
  and public coordination tokens. Claim the relevant issue before beginning a
  stretch.
- Efforts are carved from vision-to-reality deltas, existing GitHub issues, and
  resumable plans under `docs/plans/`.
- Same-repository issue references may use `#NNN`; references to other
  repositories must be fully qualified.

### Cross-repo placement and sequencing

This repository hosts efforts for subjects implemented primarily in
copilot-extensions. A downstream or private driver may maintain a linked
elaboration, but the public effort remains generic and canonical for upstream
implementation. The equivalent cross-repo sequencing rule in `AGENTS.md`
requires reviewed upstream intent to land before any unreviewed downstream
publication; only completion markers follow implementation.

### Section set

Use the canonical template section set without renaming. Extract substantial
phase designs or inventories into sibling documents and link them from the
effort README rather than allowing the shared coordination document to sprawl.
