# agent-remote-login-shell

Shared primitive for wrapping a remote SSH command in a POSIX **login shell**
(``<shell> -lc <command>``), vendored (shared, not duplicated) across every
Copilot CLI plugin that execs a command over SSH.

## Why this exists

A bare ``ssh host "command"`` is both non-interactive AND non-login for the
remote shell. On a POSIX target, neither ``~/.profile`` (login-shell-only) nor
a ``~/.bashrc``/``~/.zshrc`` entry placed after the interactive-shell guard
ever runs for it -- so anything relying on a PATH addition made there (``uv``,
``copilot``, ``gh``, or any other tool installed to ``~/.local/bin``) is
unreachable, even though it works fine from an actual interactive/login
session.

Forcing a login shell (``<shell> -lc``) makes the POSIX target source its own
**login** startup files (``~/.profile``, or a login-only ``.bash_profile``/
``.bash_login``) before running the command -- not the interactive-only
portion of ``~/.bashrc``, since ``-lc`` starts a non-interactive login shell,
never an interactive one. A PATH addition must live in one of those
login-loaded files (or in ``~/.bashrc`` content placed *before* its own
interactive-shell guard, as in the Borealis fix this library's docstring
cites) to be reachable this way.

Before this library existed, at least four plugins (``agent-ssh``,
``agent-codespaces``, ``agent-bridge``, ``agent-worktrees``) each
independently reimplemented ``f"bash -lc {shlex.quote(command)}"`` (or a
richer, shell-aware variant), with the hardcoded-``bash`` copies silently
missing ``sh``/``zsh`` targets that the richer one already handled. See
[copilot-extensions#5207](https://github.com/ThomasMichon/copilot-extensions/issues/5207).

## API

```python
from remote_login_shell import POSIX_LOGIN_SHELLS, is_posix_login_shell, wrap_login_shell

wrap_login_shell("agent-worktrees --version")
# -> "bash -lc 'agent-worktrees --version'"

wrap_login_shell("agent-worktrees --version", shell="zsh")
# -> "zsh -lc 'agent-worktrees --version'"

is_posix_login_shell("pwsh")  # -> False -- never wrap a non-POSIX target
```

- :func:`wrap_login_shell` -- wrap ``command`` for the named POSIX login
  shell (default ``"bash"``), safely quoting it with ``shlex.quote``.
- :data:`POSIX_LOGIN_SHELLS` -- the documented, supported POSIX shell
  vocabulary (``bash``, ``sh``, ``zsh``) that accepts ``-lc``.
- :func:`is_posix_login_shell` -- whether a given shell name is in
  :data:`POSIX_LOGIN_SHELLS`; callers use this to decide whether to wrap at
  all before calling :func:`wrap_login_shell` -- never guess for an
  unrecognized/empty shell value (e.g. ``"pwsh"``), since wrapping a
  non-POSIX target in a POSIX login shell would break it outright.

## Vendoring

**In dev**, consumers' `pyproject.toml` reference this library through a
`uv`-editable canonical pointer --
`agent-remote-login-shell = { path = "../../libs/remote-login-shell", editable = true }`
-- so those consumers resolve to this one source tree with nothing to keep in
sync.

**At release**, `tools/materialize_main.py` rewrites every remaining
`uv`-editable pointer into a real, promoted copy at
`plugins/<plugin>/libs/remote-login-shell/` for that consumer, non-editable,
so a published consumer installs a self-contained source tree with no
cross-plugin `path` reference. `tools/sync-vendored-libs.py --check` verifies
every materialized copy's `src/` tree and version stay byte-identical to this
canonical one and to each other.
