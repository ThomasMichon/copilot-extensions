"""agent-cli-lazy-dispatch: regression coverage for the deferred FastAPI/
uvicorn/pydantic import boundary and the lazy ``__version__``/``--version``
resolution.

Review finding (PR #4157): the existing tests only replaced
``__main__.__version__`` references and monkeypatch ``cli.serve`` outright,
so none of them actually proves the two measured startup-cost wins this
change claims:

1. ``import agent_index.__main__`` (and every subcommand that never touches
   the service shell) must never import ``agent_index.server`` -- which is
   what pulls in the entire FastAPI/starlette/pydantic stack.
2. A plain ``import agent_index`` must never resolve ``__version__`` (and so
   never touch ``importlib.metadata``) until something actually reads the
   attribute -- the whole point of the PEP 562 ``__getattr__`` lazy shim.

Both are proven here by running real, fresh subprocesses (so no other test
module's imports in this same process can hide a regression) with a meta
path finder that raises on any attempt to import fastapi/uvicorn/pydantic,
simulating a base-only client install without actually needing one.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]

_BLOCK_HEAVY_IMPORTS = """
import sys


class _Blocker:
    BLOCKED = ("fastapi", "uvicorn", "pydantic")

    def find_spec(self, name, path, target=None):
        if name in self.BLOCKED or name.startswith(tuple(f"{b}." for b in self.BLOCKED)):
            raise ImportError(f"blocked for this test: {name}")
        return None


sys.meta_path.insert(0, _Blocker())
"""


def _run(program: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-X", "utf8", "-c", _BLOCK_HEAVY_IMPORTS + program],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        cwd=PLUGIN,
    )


def test_importing_main_never_loads_server_module_even_with_fastapi_blocked():
    """`import agent_index.__main__` must succeed, and `agent_index.server`
    (and therefore fastapi/uvicorn/pydantic) must stay unloaded, even when
    those three packages are entirely unimportable -- proving the deferred
    boundary protects a genuine base-only client install, not just a lazily
    -timed import in a venv that happens to have them anyway."""
    result = _run(
        "import agent_index.__main__\n"
        "assert 'agent_index.server' not in sys.modules, 'server module loaded eagerly'\n"
        "print('OK')\n"
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_non_service_subcommands_work_with_fastapi_blocked():
    """`--version`, `--help`, and `status` never touch the service shell, so
    all three must succeed even with fastapi/uvicorn/pydantic blocked.
    Each runs in its own fresh subprocess so a regression in one path (e.g.
    ``--help`` accidentally importing ``agent_index.server``) can't hide
    behind another path's success."""
    version_result = _run(
        "from agent_index.__main__ import main\n"
        "try:\n"
        "    main(['--version'])\n"
        "except SystemExit as exc:\n"
        "    assert exc.code in (0, None), f'--version exited {exc.code}'\n"
        "else:\n"
        "    raise AssertionError('expected --version to call parser.exit()')\n"
        "assert 'agent_index.server' not in sys.modules\n"
        "print('OK')\n"
    )
    assert version_result.returncode == 0, version_result.stderr
    assert "OK" in version_result.stdout

    help_result = _run(
        "from agent_index.__main__ import main\n"
        "try:\n"
        "    main(['--help'])\n"
        "except SystemExit as exc:\n"
        "    assert exc.code in (0, None), f'--help exited {exc.code}'\n"
        "else:\n"
        "    raise AssertionError('expected --help to call parser.exit()')\n"
        "assert 'agent_index.server' not in sys.modules\n"
        "print('OK')\n"
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "OK" in help_result.stdout

    status_result = _run(
        "from agent_index.__main__ import main\n"
        "rc = main(['status'])\n"
        "assert rc == 0, f'status failed: {rc}'\n"
        "assert 'agent_index.server' not in sys.modules\n"
        "print('OK')\n"
    )
    assert status_result.returncode == 0, status_result.stderr
    assert "OK" in status_result.stdout


def test_start_command_fails_only_at_actual_invocation_not_at_import():
    """`start` (and `serve`/`__cell-start`) DO need the service shell -- with
    fastapi blocked, invoking one must fail with the blocked-import error at
    call time, never at module-import time, proving the import really is
    deferred to the moment it's needed rather than merely renamed."""
    result = _run(
        "import agent_index.__main__ as m\n"
        "assert 'agent_index.server' not in sys.modules\n"
        "try:\n"
        "    m.main(['start'])\n"
        "except ImportError as exc:\n"
        "    assert 'blocked for this test' in str(exc)\n"
        "    print('OK')\n"
        "else:\n"
        "    raise AssertionError('expected the blocked fastapi import to raise')\n"
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_plain_import_never_resolves_version_or_touches_importlib_metadata():
    """A bare `import agent_index` must not call `importlib.metadata.version`
    -- the PEP 562 `__getattr__` shim's entire point -- until something
    actually reads `agent_index.__version__`."""
    result = _run(
        "import agent_index\n"
        "assert 'importlib.metadata' not in sys.modules, "
        "'plain import already touched importlib.metadata'\n"
        "_ = agent_index.__version__\n"
        "assert 'importlib.metadata' in sys.modules, "
        "'accessing __version__ did not resolve it lazily'\n"
        "print('OK')\n"
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_missing_attribute_still_raises_attribute_error():
    result = _run(
        "import agent_index\n"
        "try:\n"
        "    agent_index.definitely_not_a_real_attribute\n"
        "except AttributeError:\n"
        "    print('OK')\n"
        "else:\n"
        "    raise AssertionError('expected AttributeError for an unknown name')\n"
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
