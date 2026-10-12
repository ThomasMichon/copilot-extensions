# `.gitignore` convention template (auto-discovered local instructions)

Every repo that adopts this skill's scaffolding also needs the
`.github/instructions/**/*.local.instructions.md` ignore rule this worktree-scoped
dynamic-guidance mechanism depends on (see
`docs/patterns/worktree-scoped-dynamic-guidance.md` §1) -- without it, a
`render_local_cache()` body is automatically discoverable but must remain
untracked, one accidental `git add -A` away from being
committed by mistake.

Add these repository-root-relative rules to the adopting repo's `.gitignore`.
Retain the compatibility rule during migration. This is an ordinary repo mutation: propose
it through that repo's normal PR/contribution flow, same as any other
change to a repo you don't unilaterally own (see the setup skill's own
`.gitignore` section for the full repo-write-authority rationale):

```gitignore
.github/copilot/context-locals/**/*.local.md
.github/instructions/**/*.local.instructions.md
```

## Why this is a scaffolding step, not an assumption

This rule does not make the checked-in orphan scan
(`instruction_projections._iter_projection_files`) recognize a freshly
rendered local-cache file as excludable -- `git ls-files` already omits
every untracked file regardless of ignore matching, so an unignored,
untracked `*.local.instructions.md` file is still excluded correctly
without this rule in place. What the rule actually prevents is a later
`git add -A` (or similar) turning that file into a *tracked* one by
accident; only then does the scan stop excluding it, scanning it like any
other projection file and reporting it as `projection-orphan-file` if
unrecognized, rather than silently hiding it. Adding this rule as part of
scaffolding closes off that accidental-staging path from the start, instead
of leaving each adopting repo to discover the gap only after a stray commit.

## Validation

- `git check-ignore -v .github/instructions/<plugin>/<sourceId>.local.instructions.md`
  exits `0` and names this rule, once added.
- A fresh `render_local_cache()` call against the adopting repo produces no
  `projection-local-cache-tracked` finding for any sibling it writes.
