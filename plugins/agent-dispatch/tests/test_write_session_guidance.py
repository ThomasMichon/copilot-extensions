"""Unit and integration tests for the agent-dispatch session guidance writer."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_PLUGIN = Path(__file__).resolve().parents[1]
_SCRIPT = _PLUGIN / "scripts" / "write_session_guidance.py"
_SPEC = importlib.util.spec_from_file_location("write_session_guidance_under_test", _SCRIPT)
assert _SPEC and _SPEC.loader
write_session_guidance_module = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = write_session_guidance_module
_SPEC.loader.exec_module(write_session_guidance_module)

_FOCUS_GUIDANCE_TEST_PATH = Path(__file__).resolve().parent / "test_focus_guidance.py"
_FOCUS_GUIDANCE_SPEC = importlib.util.spec_from_file_location(
    "test_focus_guidance_helpers", _FOCUS_GUIDANCE_TEST_PATH
)
assert _FOCUS_GUIDANCE_SPEC and _FOCUS_GUIDANCE_SPEC.loader
_focus_guidance_helpers = importlib.util.module_from_spec(_FOCUS_GUIDANCE_SPEC)
sys.modules[_FOCUS_GUIDANCE_SPEC.name] = _focus_guidance_helpers
_FOCUS_GUIDANCE_SPEC.loader.exec_module(_focus_guidance_helpers)
_powershell = _focus_guidance_helpers._powershell
_repo = _focus_guidance_helpers._repo
_tool_path = _focus_guidance_helpers._tool_path


def _target(home: Path) -> Path:
    return (
        home
        / ".copilot"
        / "session-state"
        / "session-1"
        / "instructions"
        / "agent-dispatch"
        / "session-guidance.instructions.md"
    )


def test_combines_catalog_and_focus_guidance(monkeypatch, tmp_path):
    monkeypatch.setattr(
        write_session_guidance_module,
        "_run_contributor",
        lambda root, stem, payload, *args: (
            "## agent-dispatch session command catalog\n\ncatalog"
            if stem == "emit-command-catalog"
            else "[owner: agent-dispatch@1.0.0]\nfocus text"
        ),
    )

    assert write_session_guidance_module.write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )
    content = _target(tmp_path).read_text(encoding="utf-8")
    assert content == (
        "# Agent Dispatch session guidance\n\n"
        "## agent-dispatch session command catalog\n\ncatalog\n\n"
        "[owner: agent-dispatch@1.0.0]\nfocus text\n"
    )


def test_no_content_writes_explicit_unavailable_status(monkeypatch, tmp_path):
    monkeypatch.setattr(
        write_session_guidance_module, "_run_contributor", lambda *a, **k: ""
    )

    assert write_session_guidance_module.write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )
    assert "No current agent-dispatch session guidance was available" in (
        _target(tmp_path).read_text(encoding="utf-8")
    )


def test_over_budget_content_writes_explicit_omitted_status(monkeypatch, tmp_path):
    monkeypatch.setattr(
        write_session_guidance_module,
        "_run_contributor",
        lambda root, stem, payload, *args: (
            "x" * 5000 if stem == "emit-command-catalog" else ""
        ),
    )

    assert write_session_guidance_module.write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )
    assert "omitted because" in _target(tmp_path).read_text(encoding="utf-8")


def test_replaces_stale_guidance_on_logical_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        write_session_guidance_module, "_run_contributor", lambda *a, **k: ""
    )
    target = _target(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("stale guidance", encoding="utf-8")

    assert write_session_guidance_module.write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )
    content = target.read_text(encoding="utf-8")
    assert "stale guidance" not in content
    assert "unavailable" in content


@pytest.mark.parametrize(
    "session_id", ("", "../escape", "slash/value", "a" * 129)
)
def test_rejects_unsafe_session_ids(monkeypatch, tmp_path, session_id):
    monkeypatch.setattr(
        write_session_guidance_module,
        "_run_contributor",
        lambda *a, **k: pytest.fail("must not run contributors for a bad id"),
    )
    assert not write_session_guidance_module.write_session_guidance(
        {"sessionId": session_id}, home=tmp_path
    )
    assert not (tmp_path / ".copilot").exists()


@pytest.mark.parametrize("component", (".copilot", "session-state"))
def test_rejects_state_root_escape(monkeypatch, tmp_path, component):
    monkeypatch.setattr(
        write_session_guidance_module,
        "_run_contributor",
        lambda root, stem, payload, *args: "catalog",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    if component == ".copilot":
        link = tmp_path / ".copilot"
    else:
        copilot_root = tmp_path / ".copilot"
        copilot_root.mkdir()
        link = copilot_root / "session-state"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not available")

    assert not write_session_guidance_module.write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )
    assert not (outside / "session-1").exists()


def test_rejects_session_root_escape(monkeypatch, tmp_path):
    monkeypatch.setattr(
        write_session_guidance_module,
        "_run_contributor",
        lambda root, stem, payload, *args: "catalog",
    )
    state_root = tmp_path / ".copilot" / "session-state"
    state_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (state_root / "session-1").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not available")

    assert not write_session_guidance_module.write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )
    assert not (outside / "instructions").exists()


def test_fails_open_on_resolve_runtime_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        write_session_guidance_module,
        "_run_contributor",
        lambda root, stem, payload, *args: "catalog",
    )
    original_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path.name == "session-state":
            raise RuntimeError("symlink loop")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert not write_session_guidance_module.write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )


def test_reparse_detection_does_not_require_creating_a_link():
    path = SimpleNamespace(
        lstat=lambda: SimpleNamespace(
            st_mode=write_session_guidance_module.stat.S_IFDIR,
            st_file_attributes=(
                write_session_guidance_module.stat.FILE_ATTRIBUTE_REPARSE_POINT
            ),
        )
    )
    assert write_session_guidance_module._is_link_or_reparse(path)


def test_main_always_emits_empty_object(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("not json"))
    assert write_session_guidance_module.main() == 0
    assert capsys.readouterr().out == "{}"


@pytest.mark.parametrize("suffix", (".ps1", ".sh"))
def test_wrapper_end_to_end_writes_bounded_session_file(
    tmp_path, suffix
):
    if suffix == ".sh" and os.name == "nt":
        pytest.skip(
            "bash-on-Windows resolves to a non-functional WindowsApps stub; "
            "the repo convention skips native-Windows bash coverage"
        )
    shell = _powershell() if suffix == ".ps1" else shutil.which("bash")
    if not shell:
        pytest.skip("required shell is unavailable")

    repo = _repo(tmp_path / "repo")
    tools = _tool_path(tmp_path / "bin")
    home = tmp_path / "home"
    home.mkdir()
    hook = _PLUGIN / "scripts" / f"write-session-guidance{suffix}"
    command = (
        [shell, "-NoProfile", "-File", str(hook)]
        if suffix == ".ps1"
        else [shell, str(hook)]
    )
    payload = json.dumps(
        {"sessionId": "session-1", "cwd": str(repo), "source": "copilot-cli"}
    )
    env = {
        **os.environ,
        "PATH": os.pathsep.join((str(tools), os.environ.get("PATH", ""))),
        "HOME": str(home),
        "USERPROFILE": str(home),
    }
    for var in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(var, None)
    result = subprocess.run(
        command,
        cwd=repo,
        env=env,
        input=payload.encode(),
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout == b"{}"
    target = (
        home
        / ".copilot"
        / "session-state"
        / "session-1"
        / "instructions"
        / "agent-dispatch"
        / "session-guidance.instructions.md"
    )
    content = target.read_text(encoding="utf-8")
    assert content.startswith("# Agent Dispatch session guidance\n\n")
    assert "## agent-dispatch session command catalog" in content
    assert len(content.encode("utf-8")) <= write_session_guidance_module._GUIDANCE_MAX_BYTES
