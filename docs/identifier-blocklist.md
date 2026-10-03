# Identifier-blocklist sweep

A pluggable, cross-repo mechanism for keeping employer-internal (or any
other operator-private) identifiers out of a public repo, without hand-
maintaining a copy of the denylist in every place that needs to enforce it.

## The problem this solves

[`tools/check-no-internal-identifiers.py`](../tools/check-no-internal-identifiers.py)
blocks a push that would leak a forbidden identifier into this public repo.
Its denylist historically had to be supplied by hand -- an env var, a
machine-local file, or (for CI) a repository secret whose content an operator
copy-pasted in from wherever they maintained the authoritative list.

That worked, but it meant:

- The authoritative list lived in exactly one place (often a single
  operator's private knowledge repo), with no discoverable convention for a
  *second* repo (this one, or anyone else's) to contribute its own terms.
- Updating the list meant hand-copying content into a secret, with no way to
  audit "what's actually enforced right now" without re-reading that private
  source.
- Nothing distinguished "this repo is fully private" from "this repo is
  public and needs the guard" -- the decision of *which* terms apply to
  *which* target was implicit in whichever list got pasted where.

## The mechanism

**Vision reconciliation:** this is new capability -- a repo-visibility
classification plus a cross-repo blocklist-aggregation convention -- with no
existing `agent-worktrees` vision item governing it (see
`docs/patterns/README.md` § Design principle 0: cite, extend, or declare
below-altitude). It is below-altitude: it composes with the existing
`repos.yaml` registry and the pre-existing CI-secret identifier-guard
mechanism without altering either's own governing intent, rather than
introducing new architectural intent of its own.

`agent-worktrees` owns the centralized, pluggable half (so any repo it
manages can use it, not just this one):

1. **Every registered repo can declare its own audience-exposure tier** via
   `agent-worktrees repos set-visibility <name> private|internal|public` (or
   `repos add --visibility ...`). This is machine-local, like every other
   fact in `~/.agent-worktrees/repos.yaml` -- an operator classifies each repo
   they've registered once.
2. **Any registered repo may carry a `.identifier-blocklist/` directory**,
   checked into the repo itself (travels with a fork/clone, unlike the
   visibility classification above). Inside it, one YAML file per exposure
   tier it wants to protect against:
   - `block-for-internal.yaml` -- terms that must never appear in a repo
     whose own visibility is `internal` or more exposed (`public` too).
   - `block-for-public.yaml` -- terms that must never appear in a repo whose
     own visibility is `public`.

   There is deliberately no `block-for-private.yaml`: nothing is more exposed
   than `private`, so such a file could never apply to any *other* repo.
3. **`agent-worktrees identifiers sweep [--repo NAME]`** discovers every
   locally registered repo's `.identifier-blocklist/` directory, resolves
   `NAME`'s (or the active project's) own visibility, and aggregates every
   *applicable* tier's entries into one deduplicated list -- in the same
   `token|reason` / `regex:<pattern>|reason` format
   `check-no-internal-identifiers.py`'s CI-secret source has always used.
4. **`check-no-internal-identifiers.py` consumes the sweep automatically**,
   as a fourth, best-effort identifier source: when `agent-worktrees` is
   installed and reachable on `PATH`, the guard shells out to `identifiers
   sweep --format json` (scoped to this repo via cwd auto-resolution) and
   folds the result in alongside whatever `COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI`
   (or the other two sources) already supplied. The JSON format is used
   (rather than the CLI's own default `ci` text format) so a parse failure
   in one peer repo's blocklist still surfaces whatever entries DID parse
   successfully. Absent anywhere
   `agent-worktrees` isn't installed or this repo isn't registered (a fresh
   clone, CI), this source silently contributes nothing -- it's additive,
   never a new requirement. Opt out entirely with
   `COPILOT_EXTENSIONS_DISABLE_LIVE_SWEEP=1`.

Because the pre-push git hook
([`tools/hooks/pre-push`](../tools/hooks/pre-push)) and `agent-worktrees`'
own `create-pr`/`push-changes` flow both ultimately run `git push` (which
fires the same `core.hooksPath` hook), this plays nice with `create-pr`
automatically: no separate wiring needed, no duplicate invocation to keep in
sync.

## Writing a `.identifier-blocklist/block-for-<tier>.yaml` file

```yaml
entries:
  - token: legacy-system
    reason: Internal org/repo name -- use a generic product placeholder

  - token: '\bSPO\b'
    kind: regex
    reason: Standalone internal abbreviation -- use a generic service placeholder

  - token: ABC
    whole_word: true
    case_sensitive: true
    reason: Case-sensitive acronym for a private repo -- refer to it generically
```

Each entry under the top-level `entries:` list is a mapping:

| Field | Required | Meaning |
|---|---|---|
| `token` | yes | The literal substring, or (when `kind: regex`) a Python regular-expression fragment. Must not contain a newline or semicolon -- the CI consumer treats both as entry delimiters. A `kind: regex` token must compile and must not match the empty string (which would flag every file). |
| `kind` | no (default `literal`) | `literal` matches as a plain, case-insensitive substring. `regex` treats `token` as a full Python regex fragment. |
| `whole_word` | no (default `false`) | Wrap the token in `\b...\b` boundaries (auto-escaping a literal token first). Prefer this over hand-writing a regex for "don't flag this as a substring of another word." |
| `case_sensitive` | no (default `false`) | Match this token's exact case only (scoped with an inline `(?-i:...)` group, so the rest of the pattern and every other entry stays case-insensitive). |
| `reason` | no | Explains why the token is forbidden and names the generic replacement to use instead. Surfaced in the non-`--ci` guard output and in `identifiers sweep`'s own output. |

A `kind: regex` entry with `case_sensitive: true` wraps the **whole** pattern
in `(?-i:...)`, matching the convention this guard has always used for a
case-sensitive acronym (e.g. a private repo's capitalized abbreviation).

## Reviewing a new token before adding it

Before committing a new entry, check it against the actual public tree it
will guard, not just reason about it in the abstract -- a token can look safe
and still collide with existing legitimate content (a test fixture, an
already-public dependency name, a generic placeholder):

```powershell
git grep -i -n -- '<candidate-token>'
```

Read the matches in context. A hit inside a file the guard would actually
scan is a real conflict: decide whether to narrow the token (`whole_word`/
`case_sensitive`/a bespoke `regex:` fragment), skip it, or accept it and
track the existing occurrences as a separate cleanup (see
`ThomasMichon/copilot-extensions#4834` for a worked example of exactly this
kind of pre-existing-leak cleanup).

### A token can reveal a pre-existing leak, not just future risk

Adding a new token sometimes shows that the string it targets is *already*
committed in the public tree from before this guard existed. Because the
default scan scope is the **push diff**, this is not an immediate
across-the-board failure -- but the next PR that happens to touch one of
those already-affected files will fail until it's cleaned up. When this
happens:

- **Still add the token.** The intent of a blocklist is to flag and block
  real internal content, not to carve out exceptions for however the leak
  presently manifests.
- **File a tracking issue** naming the exact files and the token(s)
  involved, so the cleanup is visible and actionable without blocking the
  blocklist's own maintenance.

## Resolving a repo's own visibility (and why the default is maximal)

```powershell
agent-worktrees repos set-visibility <name> public
agent-worktrees repos list --json    # inspect what's currently classified
```

An operator who hasn't yet classified a registered repo's visibility gets
**more** enforcement swept in, not less: `identifier_blocklist.py`'s
`resolve_visibility_rank()` treats an unset/unknown visibility as `public`
(the most exposed tier) until it's explicitly classified otherwise. This
fails toward safety -- a repo you forgot to classify still gets every
applicable blocklist tier applied, rather than silently skipping enforcement
because nobody got around to setting `visibility:` yet.

## Inspecting what the sweep would enforce

```powershell
agent-worktrees identifiers sweep --repo copilot-extensions
agent-worktrees identifiers sweep --repo copilot-extensions --json
```

The non-JSON form prints `token|reason` lines (the CI-secret-ready format);
`--json` reports the resolved target visibility, which tiers applied, and
the full entry list with source-repo/source-tier provenance for each one --
useful for auditing which registered repo actually contributed a given term
before trusting it.

## Provisioning the CI secret from a live sweep

CI runners don't have a local `repos.yaml` or any other repo's checkout, so
the live sweep (source 4) never fires there -- `FORBIDDEN_IDS_WORK` /
`FORBIDDEN_IDS_FACILITY` must still be provisioned by hand, but can now be
*generated* from the same live sweep an operator's own pre-push hook already
uses, rather than hand-copied from a separate private file:

```powershell
$aw = "agent-worktrees"   # the exact argv[0] from your session command catalog
$list = & $aw identifiers sweep --repo copilot-extensions
if ($LASTEXITCODE -ne 0) {
  Write-Error "identifiers sweep failed (exit $LASTEXITCODE) -- aborting; fix the reported blocklist/configuration issue before provisioning."
  exit 1
}
($list -join "`n") | & $aw repos gh ThomasMichon/copilot-extensions -- `
  secret set FORBIDDEN_IDS_WORK --repo ThomasMichon/copilot-extensions
```

PowerShell does not stop a script on a native command's nonzero exit by
default, so the explicit `$LASTEXITCODE` check above is required -- without
it, an unresolved project or a malformed peer blocklist could silently
replace a complete `FORBIDDEN_IDS_WORK` with an empty or partial one.

Rotate the secret any time a contributing repo's own
`.identifier-blocklist/` content changes. Never print the swept list to a
console shared with anyone else, and never place it (or a copy of it) in a
public checkout.

## History

This convention supersedes an earlier single-repo scheme (a hand-maintained
`docs/identifier-leak-guard/forbidden-identifiers-work.txt` pipe-delimited
file in one operator's harness repo, copy-pasted into the CI secret by hand).
That file's content is slated to migrate into a `.identifier-blocklist/
block-for-public.yaml` at that repo's own anchor root, in the new YAML
schema above, now that this cross-repo sweep mechanism exists to discover it
generically -- tracked as the next slice of
`efforts/active/ci-identifier-leak-guard`'s Phase 5, not yet complete.
