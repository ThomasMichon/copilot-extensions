"""Tests for tools/check-hook-architecture.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_PATH = Path(__file__).resolve().parent / "check-hook-architecture.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_hook_architecture", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load()


@pytest.mark.parametrize(
    ("text", "powershell"),
    [
        ('python3 "$ROOT/hook.py"', False),
        ("command -v python3 >/dev/null", False),
        ("& $python -I $script", True),
        ("Start-Process -FilePath $py -ArgumentList $a", True),
        ('"$py" -I "$script"', False),
        ("uv run --python 3.11 x.py", False),
        ("exec hook-uv-python run", False),
        ("python3 - <<'PY'\nprint(1)\nPY", False),
    ],
)
def test_reports_python_starts(text: str, powershell: bool) -> None:
    assert guard.python_starts(text, powershell)


@pytest.mark.parametrize(
    ("text", "powershell"),
    [
        ("$inputs = @('hook_client.py', 'policy.py')", True),
        ("if ($name -eq 'python' -and $ok) { }", True),
        ("# python3 hook.py", False),
        ("<#\npython3 hook.py\n#>", True),
        ('cat "$HOME/.agent/published/guidance.md"', False),
        ("& $binstub hook session-start", True),
    ],
)
def test_ignores_native_lines(text: str, powershell: bool) -> None:
    assert guard.python_starts(text, powershell) == []


def _write_plugin(repo: Path, script_body: str) -> None:
    plugin = repo / "plugins" / "demo"
    (plugin / "scripts").mkdir(parents=True)
    (plugin / "scripts" / "start.sh").write_text(script_body, encoding="utf-8")
    hooks = {"hooks": {"sessionStart": [{"bash": "sh scripts/start.sh", "powershell": "Write-Output ok"}]}}
    (plugin / "hooks.json").write_text(json.dumps(hooks), encoding="utf-8")


def _write_allowlist(path: Path, keys: list[str]) -> None:
    entries = {key: {"status": "pending-conversion", "reason": "test"} for key in keys}
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")


def test_new_python_start_fails(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "python3 -I x.py\n")
    allowlist = tmp_path / "allow.json"
    _write_allowlist(allowlist, [])
    problems = guard.find_problems(tmp_path, allowlist)
    assert problems == [
        "plugins/demo/scripts/start.sh starts Python (python command) and is not in allow.json"
    ]


def test_listed_python_start_passes(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "python3 -I x.py\n")
    allowlist = tmp_path / "allow.json"
    _write_allowlist(allowlist, ["plugins/demo/scripts/start.sh"])
    assert guard.find_problems(tmp_path, allowlist) == []


def test_stale_entry_fails(tmp_path: Path) -> None:
    _write_plugin(tmp_path, 'cat "$HOME/.demo/published/guidance.md"\n')
    allowlist = tmp_path / "allow.json"
    _write_allowlist(allowlist, ["plugins/demo/scripts/start.sh"])
    problems = guard.find_problems(tmp_path, allowlist)
    assert problems == [
        "plugins/demo/scripts/start.sh no longer starts Python; remove it from allow.json"
    ]


def test_unknown_status_is_rejected(tmp_path: Path) -> None:
    allowlist = tmp_path / "allow.json"
    allowlist.write_text(json.dumps({"entries": {"x": {"status": "forever", "reason": "r"}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown status"):
        guard.load_allowlist(allowlist)


def test_repository_allowlist_matches_scan() -> None:
    assert guard.find_problems(guard.REPO, guard.ALLOWLIST) == []
