# agent-procutil

Shared **Windows-headless / detached** process-spawn kwargs for Copilot CLI
plugins. One vendored helper so every plugin suppresses console-window flashes
the same way, without each reinventing the creation flags.

```python
import subprocess
import sys
from agent_procutil import (
    detached_kwargs,
    no_window_kwargs,
    windowless_daemon_kwargs,
    windowless_python,
)

# Non-interactive child, output captured, no console window on Windows:
subprocess.run(cmd, capture_output=True, text=True, **no_window_kwargs())

# Fully detached background daemon (no console, survives the parent):
subprocess.Popen(
    [windowless_python(), "-m", "my_daemon"],
    **detached_kwargs(breakaway=True),
)

# Windowless host whose own console-subsystem children must stay invisible:
subprocess.Popen(
    [sys.executable, "-m", "my_host"],
    **windowless_daemon_kwargs(breakaway=True),
)
```

The helpers are no-ops off Windows (`no_window_kwargs()` -> `{}`;
`detached_kwargs()` -> `{"start_new_session": True}`), so call sites stay
platform-agnostic. `windowless_python()` selects the sibling `pythonw.exe` on
Windows because a detached venv `python.exe` launcher can re-exec a base console
interpreter that allocates a new DefTerm console. When
`COPILOT_EXTENSIONS_TEST_CONTAINED=1`, deliberate Windows Job breakaway and
POSIX session detachment are suppressed so the test runner retains descendant
ownership. Runtime code with an additional in-process survival step can use
`contained_test_mode()` to suppress that step under the same policy.
`windowless_daemon_kwargs()` preserves a `CREATE_NO_WINDOW` host on Windows
instead of using `DETACHED_PROCESS`, so console-subsystem grandchildren do not
allocate a visible console.

## Vendoring

This lib is **vendored per plugin** at `plugins/<plugin>/libs/agent-procutil`
(a marketplace-installed plugin can only reference libs inside its own dir via
`[tool.uv.sources] agent-procutil = { path = "libs/agent-procutil" }`). Every
copy's `src/` tree must stay **byte-identical** and declare the **same version**
— enforced by `tools/check-vendored-libs-sync.py`. A source change to one copy
MUST be propagated to all, with a version bump.
