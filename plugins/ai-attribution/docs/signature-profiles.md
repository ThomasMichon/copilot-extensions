# Signature profiles

The optional JSON profile configures **presentation**, not an exemption from
disclosure, sanitization, truthful review claims or sending approval. The existing
`key=value` policy configuration and unconfigured hook output are unchanged.
No automatic sending/publication integration is installed.

The exact-session writer and optional pure helper share
`scripts/attribution_profile.py` (Python 3.9+ standard library). The native policy
emitters remain dependency-free. The hook already uses Python on a cold path;
profile-enabled cold/changed launches enter that writer. Native warm shortcuts
reuse rendered profile guidance only after checking the discovery environment
and bounded present/missing-file probes produced by the Python owner. Native
wrappers do not repeat its directory normalization or containment decisions.
The Bash reader extracts all probe fields in one NUL-delimited JSON pass and
walks lexical ancestors with shell builtins, not a child process per ancestor.
Changed, added, removed, reordered or unsafe inputs fall back to the shared
writer. A base-only cache is not eligible for native reuse: profile discovery
must have completed, including an explicit snapshot for an unconfigured launch.
Discovery errors are reevaluated without restoring baseline exemptions.
The cache format is migrated automatically. The legacy policy kernel stays
separate, so removing the last profile cannot replay its old presentation.

## Files and authority

Profiles are discovered in this order; later layers override equally specific
slots:

1. `<target-repo>/.github/ai-attribution.json`: repository presentation defaults.
2. `ai-attribution.json` in each `COPILOT_CUSTOM_INSTRUCTIONS_DIRS` directory,
   in environment order: shared harness defaults followed by private overrides.
   The platform path separator and comma are accepted; target-contained
   instruction directories are skipped, as in legacy policy discovery.
3. `$HOME/.copilot/ai-attribution.json`: personal override.
4. `$XDG_CONFIG_HOME/ai-attribution/config.json`, or
   `%APPDATA%\ai-attribution\config.json` on Windows, otherwise
   `$HOME/.config/ai-attribution/config.json`: platform personal override.

Duplicate paths retain their last occurrence so an earlier instruction-directory
entry cannot demote a later personal override.

Paths are resolved without evaluating shell expressions. Symlink/reparse paths
are rejected, including ancestors of missing files during native cache reuse.
Containment exclusions retain lexical safety probes too; a directory cannot
change into or out of a symlink without invalidating its snapshot.
Only regular files are opened; FIFOs/devices are rejected without waiting.
Instruction-directory discovery is bounded to 128 entries and
65,536 characters before probing; overflow or unresolved user directories
produce a profile diagnostic, not a silently truncated configuration.
Each file is at most 64 KiB with 32 rules. Invalid layers produce a
bounded diagnostic without echoing their paths or data. Session guidance retains
the safety kernel and requires disclosure for all contributions until the profile
is resolved; the programmatic CLI fails explicitly
instead of returning a seemingly successful decorated draft. Missing files are
normal. Duplicate JSON keys, unknown fields and malformed selectors are errors.

Repository defaults can choose presentation and tighten disclosure, never waive
operator/plugin policy. Put personal names only in private configuration.

## Sparse schema

```json
{
  "version": 1,
  "variables": {"operator": "Example", "assistant": "Copilot"},
  "rules": [
    {"header": "[{assistant} for {operator}] ", "footer": ""},
    {
      "when": {"domain": "forge", "visibility": "public", "activity": "review"},
      "header": "",
      "footer": "\n\n*AI-assisted on behalf of {operator}; {review_status}.*"
    },
    {
      "when": {"domain": "chat", "activity": "message"},
      "header": "**[AI-assisted for {operator}]** ",
      "required": true
    }
  ]
}
```

`domain` means service/surface family, not a DNS host. Axis values are lowercase
tokens of up to 48 characters: letters, digits and hyphens, beginning with a
letter. Use consistent consumer vocabulary, for example `forge`, `mail`, `chat`;
`public`, `external`, `internal`, `private`; `authored`, `comment`, `reply`,
`review`, `message`. An omitted axis or `*` matches any value. A concrete
programmatic context supplies all three axes. Unknown facts are not a basis for
inferring a policy exemption.

Selection is **per slot**: most concrete selector axes wins, then later layer.
Equally specific, same-layer matching rules with different values are an error,
not an ordering trick. A duplicate selector within one layer is rejected. Missing
slots inherit; explicit `""` suppresses that slot. Defaults are header
`[AI-assisted] ` and empty footer. If both selected slots are blank and disclosure
is required, the default header is restored. `required: true` tightens the
policy; no false/disable setting exists. Configured slots replace the legacy
top/italic presentation, not the underlying disclosure requirement.

Templates are literal text, at most 512 UTF-8 bytes, with only `{operator}`,
`{assistant}` and `{review_status}` interpolation (escaped braces use `{{` and
`}}`). Expressions, field traversal, format specifiers, conversions and control
characters other than newlines are rejected. Variables reject Unicode control
and formatting characters and are bounded literal text,
escaped for Markdown and HTML; templates choose their own Markdown formatting.
No HTML conversion is performed. Templates support visible text, emphasis,
inline code and blockquotes, not raw HTML, character references, links/images,
reference definitions, code fences or invisible formatting controls. These
checks apply after interpolation too, including historical variants, without
treating escaped literal identities as markup. Presentation containing only
punctuation cannot suppress the visible default. Literal review claims, including
claims assembled across template/variable boundaries or historical variants, are rejected:
use `{review_status}`, which resolves to `not human-reviewed` unless the caller
supplies the actual boolean `human_reviewed: true` for this exact draft.
Configuration never proves review.
Unescaped bracket fragments must be balanced within each slot; they cannot
form a link around the draft or hide attribution in a destination across slots.

`header_variants` and `footer_variants` optionally name up to four previous
**owned** marker templates per slot. They use the same interpolation. The
decorator recognizes exact markers only at their intended outer boundaries.
An already-current boundary is preserved before considering historical variants,
so overlapping old markers cannot consume draft content on repeated decoration.
Otherwise at most one historical marker is replaced per slot. It does not fuzzy-match AI mentions,
search quoted body content, or remove another operator's acknowledgement.
Both boundaries are identified in the original draft before any insertion.
Overlapping removals or removals crossing an existing current marker are
preserved rather than consuming draft content or the opposite slot.
Never configure another author's text as an owned variant.
Variants are applied only for a nonempty current slot; a suppressed slot does
not destructively remove old text without a replacement boundary.

## Guidance and pure helper

Session guidance renders the same effective layered data as lightly formatted
Markdown, leaving the agent responsible for the actual circumstance. Large
profiles never displace the policy kernel: a compact disclose-all header preserves
tightening even when no diagnostic notice fits. This conservative mode also
survives native warm-cache reuse. Resolve the profile through the helper before
approval rather than relying on baseline exemptions.
Configuration changes, including adding or removing a profile, take effect on
the next launch without deleting a policy cache.

The optional helper reads a JSON request on stdin and returns JSON on stdout:

```text
python <plugin>/scripts/attribution_profile.py resolve --repo <target-root>
python <plugin>/scripts/attribution_profile.py decorate --repo <target-root>
python <plugin>/scripts/attribution_profile.py guidance --repo <target-root>
```

`guidance` needs no request. `resolve` and `decorate` accept:

```json
{
  "context": {
    "domain": "forge",
    "visibility": "public",
    "activity": "review",
    "required": true,
    "human_reviewed": false
  },
  "draft": "The contribution body."
}
```

The caller derives `required` from the canonical policy and actual audience;
the helper deliberately does not maintain a second audience-policy engine.
Missing `required` defaults to true. A matching tightening rule can turn false
into true, never the reverse. `resolve` returns `signature`;
`decorate` additionally returns `draft`. If disclosure is not required,
decoration leaves the supplied draft byte-for-byte unchanged. Repeating
decoration with the same profile/context is idempotent.

The helper does no authentication, network access, approval or publication.
Use it **before final preview/approval**. Never decorate an already-approved or
published item, and never retry a send merely to repair attribution. Consumers
must stop on nonzero exit, retain independent send authority and verify the live
published surface through their own normal workflow.

Library consumers may call `load_profile`, `resolve`, `decorate` and `guidance`.
Check `load_profile`'s diagnostics before resolving; `validate` admits in-memory
single-layer data. Both return data without publication effects.
