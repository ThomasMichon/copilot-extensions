"""Standalone lifecycle identity across payload and deployed-hook layouts."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs
from agent_worktrees import locks

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
NATIVE = r"""
import importlib.util, json, pathlib, sys, time
from types import SimpleNamespace
scripts, home, pid, birth, case = sys.argv[1:]
scripts, home, pid = pathlib.Path(scripts), pathlib.Path(home), int(pid)
spec = importlib.util.spec_from_file_location('fixture_hook', scripts / 'hook_client.py')
client = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = client
spec.loader.exec_module(client)
python = home / 'versions' / '0.0.1' / 'Scripts' / 'python.exe'
loader = client._load_sibling
helper = SimpleNamespace(
    _pid_alive=lambda candidate: candidate == pid,
    _pid_image_path=lambda candidate: str(python) if case != 'wrong-image' else str(home / 'other-python'),
)
def sibling(name):
    if name == 'versioned_runtime.py':
        return None if case == 'missing-versioned-helper' else helper
    if name == 'hook_process_identity.py' and case == 'unknown-birth':
        return SimpleNamespace(process_start_time=lambda candidate: None)
    return loader(name)
client._load_sibling = sibling
client._runtime_python = lambda root: python
client._fallback_legacy_session_start = lambda payload, skip_reconcile=False: {'skip': skip_reconcile}
runtime = home / '.agent-worktrees'
runtime.mkdir(parents=True)
record = {'pid': pid, 'started': time.time()}
if case != 'legacy-no-birth':
    record['start_time'] = 'stale-birth' if case == 'stale-birth' else birth
(runtime / 'updater.lock').write_text(json.dumps(record), encoding='utf-8')
owner = client._external_update_driver_active(home, python)
result = client._fallback_session_start({'_agentWorktrees': {'pluginVersion': '999.0.0'}}, home)
expected = case in ('live', 'legacy-no-birth')
assert owner is expected, (case, owner, expected)
assert result == {'skip': expected}, (case, result)
assert 'single_instance_lease' not in sys.modules
print(json.dumps({'case': case, 'owner': owner, 'skip': result['skip'], 'python': list(sys.version_info[:2])}))
"""


@pytest.mark.parametrize("case", [
    "live", "legacy-no-birth", "stale-birth", "wrong-image", "missing-birth-helper",
    "unknown-birth", "missing-versioned-helper",
])
@pytest.mark.parametrize("native", [False, True])
def test_isolated_payload_identity(tmp_path, monkeypatch, case, native):
    if native and not os.environ.get("COPILOT_TEST_HOOK_IDENTITY_PYTHON"):
        pytest.skip("explicit native interpreter fixture")
    interpreter = os.environ["COPILOT_TEST_HOOK_IDENTITY_PYTHON"] if native else sys.executable
    for name in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT"):
        monkeypatch.delenv(name, raising=False)
    scripts = tmp_path / "payload" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("hook_client.py", "hook_runtime.py", "hook_process_identity.py"):
        source = SCRIPTS / name
        if source.is_file() and not (case == "missing-birth-helper" and name == "hook_process_identity.py"):
            shutil.copyfile(source, scripts / name)
    old_identity = scripts.parent / "src" / "agent_worktrees" / "locks.py"
    old_identity.parent.mkdir(parents=True)
    old_payload = os.environ.get("COPILOT_TEST_HOOK_OLD_PAYLOAD") if native else None
    if old_payload:
        shutil.copyfile(Path(old_payload) / "src" / "agent_worktrees" / "locks.py", old_identity)
    else:
        old_identity.write_text("import single_instance_lease\n", encoding="utf-8")
    token = locks.process_start_time(os.getpid())
    assert token is not None
    result = subprocess.run(
        [interpreter, "-I", "-S", "-B", "-c", NATIVE, str(scripts), str(tmp_path / "home"),
         str(os.getpid()), token, case],
        capture_output=True, text=True, timeout=15, check=False, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    assert observed["case"] == case
    if native:
        assert observed["python"] == [3, 9]


def test_standalone_identity_matches_runtime_token():
    spec = importlib.util.spec_from_file_location("hook_identity", SCRIPTS / "hook_process_identity.py")
    identity = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(identity)
    assert identity.process_start_time(os.getpid()) == locks.process_start_time(os.getpid())
    assert identity.process_start_time(0) is None


def test_hook_identity_os_implementations_stay_in_sync():
    names = {"process_start_time", "_start_time_posix", "_start_time_windows", "_start_time_darwin"}

    def implementations(path):
        functions = {}
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.FunctionDef) and node.name in names:
                if ast.get_docstring(node) is not None:
                    node.body = node.body[1:]
                functions[node.name] = ast.dump(node, include_attributes=False)
        assert functions.keys() == names
        return functions

    assert implementations(SCRIPTS / "hook_process_identity.py") == implementations(Path(locks.__file__))
