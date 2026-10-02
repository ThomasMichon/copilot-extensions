---
applyTo: "**"
---

Check `.github/instructions/**/*.local.instructions.md` for any files
present now and read each one. When a checked-in `.instructions.md` file
exists for the same plugin and source, compare both files' embedded
marker `pluginVersion` fields and prefer whichever is newer; on a tie,
compare `templateSha256` instead of whole-file bytes (which always
differ -- only the checked-in file carries the preamble) -- prefer the
checked-in file only if that hash differs too, otherwise the local file
stays authoritative. With no checked-in file yet for that path, the
local file is authoritative on its own. Their absence is not an error.
