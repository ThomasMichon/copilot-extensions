# agent-procutil

Shared **Windows-headless / detached** process-spawn kwargs for Copilot CLI
plugins. One vendored helper so every plugin suppresses console-window flashes
the same way, without each reinventing the creation flags.

```python
import subprocess
from agent_procutil import no_window_kwargs, detached_kwargs

# Non-interactive child, output captured, no console window on Windows:
subprocess.run(cmd, capture_output=True, text=True, **no_window_kwargs())

# Fully detached background daemon (no console, survives the parent):
subprocess.Popen(cmd, **detached_kwargs(breakaway=True))
```

Both helpers are no-ops off Windows (`no_window_kwargs()` -> `{}`;
`detached_kwargs()` -> `{"start_new_session": True}`), so call sites stay
platform-agnostic.

## Vendoring

This lib is **vendored per plugin** at `plugins/<plugin>/libs/agent-procutil`
(a marketplace-installed plugin can only reference libs inside its own dir via
`[tool.uv.sources] agent-procutil = { path = "libs/agent-procutil" }`). Every
copy's `src/` tree must stay **byte-identical** and declare the **same version**
— enforced by `tools/check-vendored-libs-sync.py`. A source change to one copy
MUST be propagated to all, with a version bump.
