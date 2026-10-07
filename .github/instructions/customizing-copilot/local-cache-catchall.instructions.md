---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","customizationKind":"instructions","destination":".github/instructions/customizing-copilot/local-cache-catchall.instructions.md","plugin":"customizing-copilot@copilot-extensions","pluginVersion":"0.2.6-dev1","renderedBytes":1451,"schema":"copilot-extensions.instruction-projection","sourceId":"local-cache-catchall","template":"instructions/local-cache-catchall.instructions.md","templateBytes":654,"templateSha256":"e5b6cc8bdf06330916e726021e615d6f2a533df5f2b89d4ce9103f4ed0c6ca3d","version":1} -->

> If `local-cache-catchall.local.instructions.md` exists here, compare
> `pluginVersion` and prefer whichever is newer. On a tie,
> compare `templateSha256`: matching means prefer local;
> differing means prefer this checked-in file.

Check `.github/instructions/**/*.local.instructions.md` for any files
present now and read each one. When a checked-in `.instructions.md` file
exists for the same plugin and source, compare both files' embedded
marker `pluginVersion` fields and prefer whichever is newer; on a tie,
compare `templateSha256` instead of whole-file bytes (which always
differ -- only the checked-in file carries the preamble) -- prefer the
checked-in file only if that hash differs too, otherwise the local file
stays authoritative. With no checked-in file yet for that path, the
local file is authoritative on its own. Their absence is not an error.
