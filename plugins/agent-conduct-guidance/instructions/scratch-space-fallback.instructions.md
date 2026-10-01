---
applyTo: "**"
---

# Scratch-space hygiene fallback

**Fallback policy `[owner: agent-conduct-guidance@0.1.0-dev1]`:** Any ad hoc
working file an agent creates outside a repository checkout -- a downloaded
artifact, an intermediate log, a draft PR/issue body, a one-off JSON/text
dump, a captured command's output -- must never be written as a loose file
directly at a filesystem/drive root or any other shared, un-timestamped
location. A bare root with no per-task structure gives nobody (human or a
later agent) any signal for whether a given file is still in use or long
since expired, and it accumulates indefinitely. This does not apply to an
artifact a session-management framework already owns and places itself
(e.g. a context-handoff brief kept in its own framework-managed location) --
leave that where its owning framework puts it rather than redirecting it
here. For everything else, resolve a scratch root (see the paired skill
for the exact, portable resolution order -- never hardcode one) and create
one freshly-timestamped subfolder per task there before writing anything;
never add loose files beside siblings from unrelated tasks. Invoke the
`using-scratch-space` skill before creating such a file outside a
repository.
