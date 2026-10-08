# Phase 3 sub-plan — Registrar `extends:` unification

Linked from the main [`README.md`](README.md) Phase 3 checklist. Read this
before starting any Phase 3 work; the main README stays a map.

## Where `extends:` plugs in

`read_declaration_file_set` in `registrar_discovery.py` is the **one
chokepoint** every declaration file passes through: it decodes the raw
mapping, then dispatches purely on `data.get("kind")`:

```python
data = dict(_decode(text, p.suffix, where=str(p)))
if data.get("kind") == "reviewer-loop":
    ...
elif data.get("kind") == "repository-issue-loop":
    ...
else:
    declarations = (load_declaration(data, ...),)
```

`extends:` is a **pre-expansion step inserted before this dispatch**, not a
new kind branch: if `data` carries `extends:`, resolve it to a fully-shaped
`kind: ...` dict (recipe template deep-merged with the declaration's own
override keys), replace `data` with the resolved dict, and fall through to
the *exact same* existing dispatch unchanged. This is what makes "no
behavior change to the existing direct-declaration path" (Phase 3's own
bullet 3) free: a resolved `extends:` declaration is byte-for-byte the same
shape a hand-written one already is, so every existing expand/validate
function needs zero changes.

## Recipe reference syntax

```yaml
extends: "global:repository-issue-loop"   # plugin-shipped, built into agent-dispatch
extends: "./recipes/my-loop.yaml"         # repo-local, relative to the repo root
extends: "../other-repo/.agent-dispatch/recipes/foo.yaml"  # cross-repo, explicit path
```

- **global** — a `global:<name>` ref resolves against an in-code registry
  (`registrar_recipes.py`'s `GLOBAL_RECIPES`), one entry per archetype. A
  global recipe is a dict template whose string fields use `{placeholder}`
  tokens, filled by a plain, never-raising regex substitution
  (`substitute_placeholders`/`_PLACEHOLDER_RE`) that matches only a bare
  `{identifier}` token and leaves every other brace content (JSON-like
  prose, format specs, mismatched braces) completely untouched — this is
  deliberate: a template's prose may legitimately contain literal braces
  (e.g. a worker-guidance string documenting an expected JSON shape), and a
  full format-mini-language parser (`str.Formatter`) raises on exactly that
  content.
- **repo-local** / **cross-repo** — both are plain file paths (relative to
  the repo root or elsewhere), read and decoded the same way
  `read_declaration_file_set` already reads any declaration file (same
  `.yaml`/`.yml`/`.json` suffix contract). No new I/O primitive needed.
- **Placeholder sources and precedence** — a recipe template's placeholders
  are filled from two sources. Every ordinary top-level override key
  (`name`, `repo`, `owner`, ... — minus the reserved `extends`/`params`
  keys) is used for substitution **and** deep-merged into the resolved
  output, since it's a legitimate declaration field in its own right. A
  reserved `params:` block supplies **substitution-only** values — needed
  when a template placeholder isn't itself a valid top-level declaration
  field (e.g. a provider login interpolated into a nested `spec.command`
  string) — and is **never** merged into the resolved output. When the
  same key appears in both, the `params:` value wins (substitution-only
  intentionally takes precedence, since it exists specifically to let an
  author adjust a substituted value without also changing what the
  resolved declaration contains). Only scalar (`str`/`int`/`float`/`bool`)
  values from either source are usable as substitutions.

## Merge semantics

Deep-merge: the declaration's own keys win over the template's on conflict,
recursively for nested mappings (`forge:`, `pool:`, `reservation:`, ...),
with lists/scalars replaced wholesale (never concatenated — a declaration
that sets `include_labels:` means exactly that list, not the template's list
plus its own).

## Why this does **not** require the "single emitter primitive" unification first

Phase 3's first bullet (`emitter` as the one producer primitive, with
schedule/webhook/websocket as *triggers* rather than separate `kind`s) is an
**internal taxonomy refactor** — valuable for its own sake (today's
`kind: reviewer-loop` vs `kind: repository-issue-loop` conflate trigger
mechanism with template completeness, per #4691's own framing), but
`extends:` does not need it to deliver value: a global recipe can resolve to
today's `kind: repository-issue-loop` dict exactly as well as it could
resolve to a future unified `kind: emitter` + `trigger: {...}` dict. Treat
them as **independent, separately-landable slices**, not a hard dependency
chain — this sub-plan sequences `extends:` first since it is smaller, lower
risk, and already delivers the effort's motivating operational finding
(private hand-written declarations migrating to a thin `extends:` form).

## Sub-PRs (each independently reviewable/mergeable)

1. **`extends:` resolution mechanism** — `registrar_recipes.py`
   (resolve-ref + deep-merge + the pre-expansion hook in
   `read_declaration_file_set`), repo-local and cross-repo path refs only
   (no global recipes shipped yet — test with a synthetic template
   fixture). This is the foundational, highest-value, lowest-risk slice.
2. **Ship the four global recipes** (`global:reviewer`,
   `global:conflict-resolution`, `global:goal-driven`,
   `global:repository-issue-loop`) as concrete templates in
   `registrar_recipes.py`'s built-in registry, each covering the fields a
   real declaration commonly varies (target repo, forge producer login,
   worker identity/guidance, pool target) and templating the rest from the
   archetype's sensible default.
3. **Migration note + doc** (`plugins/agent-dispatch/README.md`): document
   `extends:`, the three ref kinds, and a worked migration example (a
   hand-written `repository-issue-loop` declaration → its `extends:`
   equivalent) — this closes Phase 9's migration-note item early for this
   piece.
4. **(Separate, not blocking)** the single-`emitter`-primitive taxonomy
   refactor (Phase 3 bullet 1) and cross-repo ref hardening (symlink/path
   traversal safety for a ref outside the repo root) — track as their own
   follow-on slices once 1-3 land and the extends: shape is proven against
   a real migrated declaration.

## Validation

- Unit: a repo-local `extends:` ref + override resolves to the identical
  `ProfileDeclaration` tuple a hand-written equivalent direct declaration
  produces (byte-for-byte dict equality before `load_declaration`/
  `expand_*` runs — proves zero behavior change).
- Unit: deep-merge semantics (nested dict merge, list/scalar replace,
  declaration wins on conflict).
- Unit: an unresolvable ref (missing global name, missing file, directory
  traversal outside permitted roots) fails loud with a clear `RegistrarError`
  — never silently falls back to an unresolved/partial declaration.
- Integration: the full `agent-dispatch` suite stays green; existing direct
  `kind:` declarations (no `extends:`) are provably unaffected (same test
  fixtures, no new failures).
