from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills" / "binding-knowledge" / "scripts" / "binding_yaml.py"
)
spec = importlib.util.spec_from_file_location("transaction_binding_yaml", SCRIPT)
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


def _start(config, name, mode="preserve"):
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, str(SCRIPT), str(config), name, mode],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )


def _wait_ready(config, names):
    deadline = time.monotonic() + 5
    while not all(config.with_name(f"ready-{name}").exists() for name in names):
        assert time.monotonic() < deadline, "binding child did not reach the start barrier"
        time.sleep(0.01)


def _cleanup(children):
    for child in children:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_competing_first_touch_writers_observe_the_winning_selection(tmp_path):
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


def test_explicit_repoint_writer_uses_the_same_os_lock(tmp_path):
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


def test_capability_probe_is_available_before_repository_selection_without_effects(tmp_path):
    command = SCRIPT.with_name("bind_knowledge.py")
    result = subprocess.run(
        [sys.executable, str(command), "--capabilities", "--json"],
        cwd=tmp_path, capture_output=True, text=True, timeout=5,
        env={**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "version": 1, "registration_classes": ["knowledge", "worktree"],
        "preserves_registration_class": True, "control_plane_pair": True,
    }
    assert list(tmp_path.iterdir()) == []
