# Attribution guidance inventory

Back to the [canonical effort](README.md).

## Scope and method

The read-only sweep covered source-owned Markdown, Python, shell, PowerShell,
JSON/YAML and JavaScript/TypeScript under `plugins/`, `tools/`, `.github/`,
`docs/` and root policy files. The primary `plugins/ai-attribution` provider was
traced separately. Historical efforts, vendor/generated caches and tests that
merely assert outputs were not treated as active policy owners.

An initial broad search returned 6,828 noisy matches, largely provenance and
fixtures. A narrower exact-phrase/rule review checked assisted-by wording,
AI-generated acknowledgements, disclosure requirements/exemptions and
Copilot/on-behalf wording. Outside the primary provider, one eligible consumer
reference remained; no independent disclosure matrix or fixed signature
template was found in that bounded source inventory. This is a scope-backed
finding, not a claim that lexical absence proves universal absence.

## Findings

| Surface | Evidence | Classification and disposition |
|---------|----------|--------------------------------|
| `plugins/ai-attribution/` | Policy emitters, on-demand workflow, configuration and exact-session writer | Canonical owner; extend here rather than introducing another provider. Native shell/kernel copies remain parity-tested delivery of this owner. |
| `plugins/copilot-extensions-harness/agents/copilot-extensions-reviewer.agent.md:40` | "Include the repository-required AI acknowledgement in every posted review." | Consumer/reference, not its own audience matrix or wording. Preserve target-repository requirements; any wording clarification should defer to the effective canonical policy. |
| `AGENTS.md:301-317`, `.github/copilot-instructions.md:85-93` | Public identifier-neutrality and codename source attribution | Separate publication/provenance requirements, not human-facing disclosure templates. Retain. |
| Contribution skill sanitization guidance | Generic public artifacts and the allowed commit-provenance exception | Separate safety/provenance authority. Retain. |
| Declared instruction projections and offline fallbacks | Generated policy delivery | Intentional delivery copies, not competing ownership. Preserve ownership/locks and the independent #9627 migration. |

## Selected-adopter reconciliation

A separately scoped adopter audit found an independently selected local policy
provider and a fixed unattended-publisher acknowledgement. The adopter driver
will reconcile those through its own contribution flow:

- retire the competing decision provider in favor of upstream configuration;
- keep default signature data and private overrides separate from policy code;
- classify a deterministic unattended fallback separately from instructions
  for agent-authored prose;
- preserve send approval, source labels and commit/session provenance.

No adopter identity, implementation path or private evidence is required for
the upstream contract or its synthetic tests.
