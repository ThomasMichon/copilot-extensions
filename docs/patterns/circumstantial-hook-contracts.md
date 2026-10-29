# Circumstantial hook contracts

This pattern serves the harness-guidance vision's circumstance-based skills and
agent-worktrees' cooperative write-guard responsibility. It complements
[performance measurement](performance-measurement.md) and existing hook-output
composition and existing required-guard contracts.
The current worktree write guards illustrate the role, not proof that every
existing implementation already satisfies this contract.

## Canonical portable authoring contract

The practical contract ships with customizing-copilot:
[circumstantial hook contracts](../../plugins/customizing-copilot/skills/authoring-skills/references/circumstantial-hooks.md).
Both installed authoring and review skills link to that same reference. Keep
mechanics, the five exposure mechanisms and review cases there, rather than
maintaining a second full copy in repository-only documentation.

## Binding owner responsibilities

- Preserve event-time occurrence evaluation and contextual intervention;
  startup context and session-wide acknowledgements cannot replace them.
- Name the fact acquisition, cheap trigger evaluation and intervention stages.
  Reuse canonical input-bound facts, not cached authorization or occurrences.
- Document each hook's behavior/data contract, freshness, completeness,
  acknowledgement invalidation, error effects, safe recovery and retry cost.
- Default to fail open when an uninstalled, missing or broken backing suite
  prevents ordinary evaluation; inspection and recovery must stay reachable.
  Narrow exceptions require a documented protected condition/prerequisite,
  positively applicable trusted inputs independent of the missing helper,
  bounded actionable errors and a reachable recovery path. Root absence or a
  failed validator is not proof of applicability. Preserve confirmed policy
  violations without inventing successful evaluation or lifecycle outcomes.
- Own inner/outer deadline margin, response delivery, cancellation and descendant
  cleanup across the entire composed path. Optional costly work belongs to an
  existing owner outside the hot path, never a new per-event daemon.
- Qualify policy/error/lifecycle/cleanup parity before a transition; preserve
  one active implementation per event and existing owner responsibilities.

Existing reusable checks enforce only what they can establish mechanically.
The customization scanner validates explicit native command timeout values;
omissions remain compatibility warnings and external findings stay advisory.
Semantic conformance remains a review/test obligation, not a word-presence
assertion, a new runtime decision engine or a mandatory declaration rollout.
