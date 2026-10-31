---
applyTo: "**"
---

# Windows elevation fallback

**Fallback policy `[owner: agent-conduct-guidance@0.1.0-dev1]`:** Before
requesting Windows administrator rights or a UAC prompt, invoke
`requesting-windows-elevation`. Bind approval to one named operation, reach the
operator's interactive desktop through a temporary limited task when needed,
capture output to a specified private log, and wait for its matching terminal
marker and actual exit result. A highest-privilege task is not a consent
prompt; do not create persistent privileged tasks, store credentials, weaken
UAC, retry canceled consent silently or infer success from task creation.
Reboots and unrelated administrative changes require separate authorization.
