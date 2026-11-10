from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills" / "binding-knowledge" / "scripts" / "binding_yaml.py"
)
spec = importlib.util.spec_from_file_location("transaction_binding_yaml", SCRIPT)
if spec is None or spec.loader is None:
    raise ImportError(f"Cannot load binding transactions from {SCRIPT}")
binding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(binding)

CHILD = """
import importlib.util,json,sys,time
from pathlib import Path
spec=importlib.util.spec_from_file_location('child_binding',sys.argv[1])
binding=importlib.util.module_from_spec(spec)
spec.loader.exec_module(binding)
config=Path(sys.argv[2])
name=sys.argv[3]
original=binding.check_existing_binding
def checked(text,knowledge):
    result=original(text,knowledge)
    time.sleep(0.2)
    return result
binding.check_existing_binding=checked
config.with_name('ready-'+name).write_text('ready')
while not config.with_name('go').exists():time.sleep(0.01)
try:
    binding.write_binding(config,'harness',name,preserve=sys.argv[4]=='preserve')
except RuntimeError as error:
    print(json.dumps({'conflict':str(error)}))
    sys.exit(7)
print(json.dumps({'bound':name}))
"""


def test_configuration_rejects_registration_and_path_overrides_without_effects(tmp_path: Path) -> None:
    command = SCRIPT.with_name("bind_knowledge.py")
    for extra in (["--register"], ["--knowledge-path", str(tmp_path / "wrong-checkout")]):
        result = subprocess.run(
            [sys.executable, str(command), "--configuration", "preview", "--json",
             "--harness", "sample", "--knowledge", "knowledge", *extra],
            cwd=tmp_path, capture_output=True, text=True, timeout=5,
            env={**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
                 "PYTHONSAFEPATH": "1"},
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0,
        )
        assert result.returncode == 1 and "Configuration only supports" in result.stderr
        assert json.loads(result.stdout) == {"status": "refused", "pointer_committed": False}
        assert list(tmp_path.iterdir()) == []


def _start(config: Path, name: str, mode: str = "preserve") -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, str(SCRIPT), str(config), name, mode],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0,
    )


def _wait_ready(config: Path, names: Iterable[str]) -> None:
    deadline = time.monotonic() + 5
    while not all(config.with_name(f"ready-{name}").exists() for name in names):
        assert time.monotonic() < deadline, "binding child did not reach the start barrier"
        time.sleep(0.01)


def _cleanup(children: Iterable[subprocess.Popen[str]]) -> None:
    for child in children:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_competing_first_touch_writers_observe_the_winning_selection(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("repo_name: harness\n", encoding="utf-8")
    children = [_start(config, name) for name in ("first", "second")]
    try:
        _wait_ready(config, ("first", "second"))
        config.with_name("go").write_text("go")
        results = [child.communicate(timeout=5) for child in children]
        assert sorted(child.returncode for child in children) == [0, 7], results
        winner = next(
            json.loads(output)["bound"]
            for child, (output, _error) in zip(children, results) if child.returncode == 0
        )
        assert binding.check_existing_binding(config.read_text(), winner)
    finally:
        _cleanup(children)


def test_explicit_repoint_writer_uses_the_same_os_lock(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("knowledge_repo: original\n", encoding="utf-8")
    children = []
    try:
        with binding.binding_lock(tmp_path):
            child = _start(config, "replacement", "explicit")
            children.append(child)
            _wait_ready(config, ("replacement",))
            config.with_name("go").write_text("go")
            time.sleep(0.2)
            assert child.poll() is None
            assert config.read_text() == "knowledge_repo: original\n"
        output, error = child.communicate(timeout=5)
        assert child.returncode == 0, error
        assert json.loads(output)["bound"] == "replacement"
        assert config.read_text() == "knowledge_repo: replacement\n"
    finally:
        _cleanup(children)


def test_capability_probe_is_available_before_repository_selection_without_effects(tmp_path: Path) -> None:
    command = SCRIPT.with_name("bind_knowledge.py")
    result = subprocess.run(
        [sys.executable, str(command), "--capabilities", "--json"],
        cwd=tmp_path, capture_output=True, text=True, timeout=5,
        env={**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "version": 1, "registration_classes": ["knowledge", "worktree"],
        "preserves_registration_class": True, "control_plane_pair": True,
        "existing_binding_configuration": 2,
        "portable_binding_precondition": 1,
        "binding_precondition_freshness": 1,
    }
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("original", [None, b"repo_name: harness\r\n", b"knowledge_repo: knowledge\n"])
def test_callback_change_preserves_external_config_without_commit_receipt(
    tmp_path: Path, original: bytes | None,
) -> None:
    config = tmp_path / "config.yaml"
    if original is not None:
        config.write_bytes(original)
    changed = (original or b"") + b"headless: true\n"
    receipts = []
    with pytest.raises(RuntimeError, match="configuration changed"):
        binding.write_binding(
            config, "harness", "knowledge", preserve=True,
            precondition=lambda: config.write_bytes(changed), committed=lambda: receipts.append(True),
        )
    assert config.read_bytes() == changed and not receipts
    assert {path.name for path in tmp_path.iterdir()} == {"config.yaml", ".knowledge-binding.lock"}


def test_change_during_render_is_rechecked_before_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "config.yaml"
    config.write_bytes(b"repo_name: harness\n")
    original: Callable[[str, str, str], str] = binding.set_top_yaml_key

    def render(text: str, key: str, value: str) -> str:
        config.unlink()
        return original(text, key, value)

    monkeypatch.setattr(binding, "set_top_yaml_key", render)
    receipts = []
    with pytest.raises(RuntimeError, match="configuration changed"):
        binding.write_binding(
            config, "harness", "knowledge", preserve=True, committed=lambda: receipts.append(True),
        )
    assert not config.exists() and not receipts
    assert {path.name for path in tmp_path.iterdir()} == {".knowledge-binding.lock"}
