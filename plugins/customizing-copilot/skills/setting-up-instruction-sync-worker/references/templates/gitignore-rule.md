# `.gitignore` convention template (local-cache siblings)

Every repo that adopts this skill's scaffolding also needs the
`*.local.instructions.md` ignore rule this whole worktree-scoped
dynamic-guidance mechanism depends on (see
`docs/patterns/worktree-scoped-dynamic-guidance.md` §1) -- without it, a
`render_local_cache()` sibling is just an ordinary untracked file sitting
next to a checked-in one, one accidental `git add -A` away from being
committed by mistake.

Add this rule to the adopting repo's own `.gitignore` (a dedicated
`.github/instructions/.gitignore`, or an equivalent recursive rule in the
repo-root `.gitignore` -- either works, so long as it covers the whole
`.github/instructions/` tree). This is an ordinary repo mutation: propose
it through that repo's normal PR/contribution flow, same as any other
change to a repo you don't unilaterally own (see the setup skill's own
`.gitignore` section for the full repo-write-authority rationale):

```gitignore
**/*.local.instructions.md
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
