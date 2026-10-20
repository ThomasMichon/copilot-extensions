# Inactive recipe base files

These five JSON fragments expose existing recipe bases as ordinary files:
`backlog-triager`, `issue-reproducer`, `effort-builder`, `effort-driver`, and
`goal-driven`. Each extends its matching `global:` recipe; the Python-owned
recipe remains the sole implementation and prompt authority. Existing direct
`global:` references are unchanged.

This nested directory is **inactive by construction**: registrar discovery
reads immediate declaration files, not nested directories. Merely installing
these files creates no producer, evaluator, pool, task, or agent. Do not point
an active directory pointer at `templates` itself. There is no `enabled: false`
field; adding one to an ordinary declaration is rejected.

## Ownership and required inputs

| Base | Emitter and pool | Evaluator | Task contract / identity |
|---|---|---|---|
| `goal-driven` | Dispatch repository-issue-loop expands to one emitter and one headless pool | Consumer-selected verification, if required | Dispatch built-in goal-driven identity |
| `backlog-triager` | Same repository-issue-loop mechanism | Consumer registers trusted `backlog-triager` evidence policy | Dispatch recipe contract and built-in identity |
| `issue-reproducer` | Same repository-issue-loop mechanism | Consumer registers trusted `issue-reproducer` evidence policy | Dispatch recipe contract and built-in identity |
| `effort-builder` | Same repository-issue-loop mechanism | Consumer registers trusted `effort-builder` evidence policy | Dispatch recipe contract and built-in identity |
| `effort-driver` | Dispatch effort-driver-loop expands to one emitter and one headless pool | Consumer registers trusted `effort-driver` archive evidence policy | Dispatch recipe contract and built-in identity |

An evaluator reference does not register an evaluator. The consumer owns its
repository evidence policy, source selection, execution target, placement,
capacity, credentials, and activation consent. These fragments are incomplete
bases, not ready-to-run registrations.

## Extend without a catalog or a new schema

A consumer-owned file can use the existing plugin-qualified reference:

```json
{
  "extends": "plugin:agent-dispatch@copilot-extensions:.agent-dispatch/registrar/templates/goal-driven.json"
}
```

A further leaf can extend that file using
`repo:<registered-repo>:.agent-dispatch/registrar/templates/<base>.json`.
All five bases need their ordinary name, repo/source/task label, cadence,
`pool.body.agent`, and consumer placement. The four repository-issue-loop
bases also need forge and reservation inputs. **Effort-driver instead needs
its state root and selected effort slugs; it rejects `forge` and
`reservation` as unknown fields.**

Nested mappings deep-merge; lists replace. `params` supplies scalar
substitution-only inputs when an intermediate base uses placeholders. No
commands or scripts need appear in a placement-only leaf.

Use explicit `repo:` resources for execution paths rather than relying on
relative cross-owner paths. Existing origin tracking covers an emitter's
`spec.cwd`, not every reviewer field or consumer-derived worker identity.
Missing, escaping, cyclic, inactive-plugin, and ambiguous references fail
rather than selecting another owner.

This is ordinary registrar file inheritance, **not** provider-catalog
instantiation, a template index, or an activation API. See
[plugin README](../../../README.md) (Registrar `extends:`) for composition and
the plugin README's repository-issue-loop / effort-driver-loop sections for
complete input contracts. Publish or activate a concrete leaf only through
the consumer's normal reviewed flow and explicit operational consent.
