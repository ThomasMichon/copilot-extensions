---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","bodySha256":"b0458cf83dc10c9796a41432eb0d5acee1716334d0f09953afdb213a097085fe","customizationKind":"instructions","deliveryKind":"body","destination":".github/instructions/customizing-copilot/local-cache-catchall.instructions.md","plugin":"customizing-copilot@copilot-extensions","pluginVersion":"0.2.13-dev1","renderedBytes":3936,"schema":"copilot-extensions.instruction-projection","sourceId":"local-cache-catchall","template":"instructions/local-cache-catchall.instructions.md","templateBytes":2910,"templateSha256":"9fc412126ccfcb63aac8f90ce6d69871eccff49c7193c41c6ba3ee0bb35bb4e7","version":1} -->


> If `local-cache-catchall.local.instructions.md` exists here, compare
> `pluginVersion` and prefer whichever is newer. On a tie,
> compare `templateSha256`: matching means prefer local;
> differing means prefer this checked-in file.

Before dependent/consequential action, acquire authoritative applicable
guidance. Resolution failure is a visible blocker, never authorization.
Inventory `.github/instructions/**/*.local.instructions.md` after startup:
a hook may have written bodies after automatic instruction discovery.
Missing local caches are normal; use each selector's reviewed fallback.

Invoke `reviewing-customizations` and use its returned skill base, not an
installed-path guess or a bare `resolve-source` command. Resolve an absolute
Python interpreter and execute this argv (substitute the three placeholders
and the source's exact declared destination):
`["<python>","<skill-base>/scripts/manage-instruction-projections.py","resolve-source","<repository>","<destination>","--from-settings","--json"]`.
Use the attributable agent-worktrees catalog path with `--agent-worktrees-path`
when the repository's marketplace resolver requires it. This validates reviewed
lock/fallback ownership and authenticates paired or unpaired local bytes
against the current enabled payload's canonical render. Self-supplied hashes
and receipts prove consistency, not provenance. Unverifiable locals fall back
to valid reviewed content; invalid reviewed content blocks. Without canonical
payload proof, use the owned reviewed fallback. For authenticated candidates,
compare identity/scope/version/hash exactly: newer version wins; equal version/hash favors local;
equal version/different hash favors reviewed content. Do not compare hashes
from memory. Full fallbacks use literal paths outside automatic instruction
discovery; read only the selected body.

For an unpaired local source, verify its enabled declaration and canonical
template, marketplace/source identity and applicability before reading. Never
trust an arbitrary local file or a global marker as proof of ownership or
delivery. Absence of a checked-in source must not silently omit safe,
verifiably owned new guidance.

Skip a read only when the ENTIRE selected body is directly visible in the
current applicable instruction/tool context, with full opening provenance and
its matching closing body receipt (marketplace/plugin, source ID, version,
template hash and scope). File existence, an opening marker alone, a closing
receipt alone, quoted examples, omitted middle text, truncated output, compacted
summaries and earlier-context claims do not establish coverage. Legacy bodies
without receipts remain readable but cannot license skipping. On uncertainty
read the full selected body, including any omitted ranges.

Coverage is per source, not global: partial admission of one body says nothing
about another. Recompute authority and coverage after resume, compaction or
context reconstruction, and after source changes. Persist no loaded-state
claims. These instructions, selectors and inventories do not attest that any
other body has reached the model.

<!-- copilot-guidance-body-end:v1 {"bindingSha256":"1703d2287461b0aa43c6fb7f38ecf86fa10e8ff5d4a17d587339e0d8e6cdc637"} -->
