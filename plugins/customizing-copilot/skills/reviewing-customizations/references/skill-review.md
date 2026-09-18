# Reviewing skills

This rubric belongs to [reviewing-customizations](../SKILL.md). Use it for a
new skill, a changed description or procedure, or a read-only collection audit.
`authoring-skills` remains the format and packaging guide, not a second reviewer.

## Establish scope and optional harness guidance

Read the task and existing skill before suggesting changes. Identify its
intended inputs, output, execution venue, and neighboring skill owners.
For a description-only edit, preserve the procedure unless the new description
would misrepresent it.

Look for an explicit **skill-review rulebook** reference in the target
repository's applicable instructions, or in the instructions of the assigned
harness when trusted session context identifies one. A rulebook is an ordinary
Markdown reference, not another skill and not executable configuration.

- Resolve a relative reference against the repository that declares it. Read
  the declared file; do not scan unrelated repositories or guess an assigned
  harness from its name.
- With no reference, use this generic rubric. No harness registration, registry,
  sibling plugin, or rulebook file is required for standalone use.
- If a declared reference is missing, unreadable, or ambiguous, name the problem
  and mark that policy review incomplete. Do not silently replace it.
- Identify which findings come from the rulebook. It supplements editorial
  guidance, not the runtime's acceptance rules or higher-priority safety policy.
  Surface contradictions instead of blending incompatible requirements.

For example, repository instructions may say:
`Skill-review rulebook: docs/skill-review-rules.md`. This is a reference for the
reviewing agent, not a new CLI configuration key or automatic script loader.

## Description as a routing contract

The description should let the model select a skill without first reading its
body. Prefer concise third-person action language that supplies:

| Signal | Question |
|--------|----------|
| Capability and result | What concrete task does this perform or produce? |
| Positive triggers | What natural requests or inputs should select it? |
| Boundaries | Which system, format, venue, or prerequisite limits its scope? |
| Negative scope where useful | What nearby task belongs to another skill? |

An explicit, narrow trigger is better than "always use this for anything about
development." Be assertive within the skill's owned task; do not compensate for
under-triggering by capturing adjacent workflows. Put selection-critical facts
in the description, not only in the body. Avoid synonym lists and introductions
that merely announce that this is a skill.

Compare the parsed description with the intended text. For example,
`description: Examines release # failures` loses its suffix to a YAML comment,
while quoting the value preserves it. Unquoted `: ` can cause a parse error.
Use normal YAML quoting or a folded scalar and the runtime expectation check,
not a blacklist that pretends to parse YAML.

## Procedure and behavior

| Check | Look for |
|-------|----------|
| Explicit inputs and outcome | A clear starting point and an observable completion condition |
| Useful precision | Literal commands for fragile steps, judgment for genuinely flexible choices |
| One recommended path | Alternatives branch on differing situations, not interchangeable preferences |
| Failure handling | Unavailable data is not an empty result or a successful operation |
| Preserved boundaries | Permissions, confirmations, data handling, venue routing, and cleanup remain explicit |
| Progressive disclosure | A lean body with directly linked detail, rather than copied reference manuals |
| Portability | Resolved paths/tool roots and qualified cross-plugin or service-tool names |
| Helper quality | Deterministic repetition justifies a helper; interfaces explain errors and dependencies |

Check that the steps deliver every promised outcome. Explain unusual rules
briefly, but do not weaken real safety constraints to make prose shorter.
Require explicitly authored name and description even if the host can infer
them. Accepted unknown metadata is not proof the runtime acts on that field.

## Read-only impact preview

When asked what applying the review would do to an existing collection:

1. Enumerate the requested scope before reviewing. Distinguish all repository-
   owned skills (including disabled/venue-scoped ones) from the currently loaded
   set and from external plugin copies.
2. Record one disposition for each skill: unchanged, wording-only suggestion,
   runtime defect, behavior-sensitive recommendation, or unable to assess.
3. Give cited reasons and representative proposed before/after wording for
   material recommendations. Explain what behavior or safety boundary must be
   preserved. Do not automatically apply changes.
4. Separate actual loader evidence from static inference and editorial policy.
   List missing evidence; an incomplete audit is not a clean collection.

This is an impact preview, not a mass rewrite or proof every operational workflow
works end-to-end. A plausible positive and nearby negative request can be used
as reasoning aids without executing either workflow.

## Completion

Apply the existing customization review at the scale of the edit, then run the
[runtime conformance check](skill-runtime-conformance.md) on final bytes.
Formal evals, benchmarks, A/B trials, and user-interview loops are optional unless
the task or its owning workflow requires them. Helper code still needs tests.
Report runtime rejection, editorial findings, and optional improvements
separately; do not repeatedly review unchanged bytes or spawn a new reviewer for
every punctuation edit.
