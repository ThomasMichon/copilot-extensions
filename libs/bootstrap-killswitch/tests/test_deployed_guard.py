"""Exercise the actual native copy loops, not just payload/archive inventories."""

from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
ASSETS = (
    "bootstrap-killswitch-json-query.awk", "bootstrap-killswitch-uv.sh",
    "bootstrap-killswitch-python-version.txt", "killswitch_mutation.py",
    "bootstrap-killswitch-guard.sh", "bootstrap-killswitch-guard.ps1",
)
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(os.name == "nt" or BASH is None, reason="native Bash deployment")


@pytest.mark.parametrize(("plugin", "installer"), [
    ("agent-worktrees", "install"), ("budget-guidance", "install"),
    ("agent-ssh", "install"), ("agent-pull-requests", "install"),
    ("agent-machines", "init"),
])
def test_native_deployed_controller_keeps_its_complete_closure(
    tmp_path: Path, plugin: str, installer: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert BASH is not None
    scripts = ROOT / "plugins" / plugin / "scripts"
    text = (scripts / f"{installer}.sh").read_text(encoding="utf-8")
    loop = re.search(
        r"(?ms)^([ \t]*)for (?:h|script) in [^\n]*bootstrap-killswitch-guard\.sh[^\n]*; do\n.*?^\1done\b",
        text,
    )
    assert loop is not None
    runtime = tmp_path / "deployed runtime"
    target = runtime / "bin"
    target.mkdir(parents=True)
    result = subprocess.run(
        [BASH, "-c",
         'set -eu\nSCRIPT_DIR="$1"; PLUGIN_DIR="$1/.."\n'
         'BIN_DIR="$2"; BIN_HOOK_DIR="$2"; bin_hook_dir="$2"\n'
         'ok() { :; }\n_publish_runtime_file() { cp -- "$1" "$2"; }\n'
         'deploy_fixture() {\n' + loop.group() + '\n:\n}\ndeploy_fixture\n',
         "fixture", str(scripts), str(target)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ASSETS:
        assert (target / name).read_bytes() == (scripts / name).read_bytes()
    powershell = (scripts / f"{installer}.ps1").read_text(encoding="utf-8")
    inventory = next(line for line in powershell.splitlines()
                     if "foreach (" in line and "bootstrap-killswitch-guard.sh" in line)
    assert all(f"'{name}'" in inventory for name in ASSETS)
    state = tmp_path / "hold.json"
    environment = {
        **os.environ, "HOME": str(tmp_path),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(state),
        "BOOTSTRAP_KILLSWITCH_RECONCILE_SCAN_ROOT": str(tmp_path / "absent"),
    }
    guard = target / "bootstrap-killswitch-guard.sh"
    for arguments in (["on", "deployed controller"], ["check"], ["off"]):
        executed = subprocess.run(
            [BASH, str(guard), *arguments], env=environment,
            capture_output=True, text=True, timeout=5,
        )
        assert executed.returncode == 0, executed.stdout + executed.stderr
        if arguments[0] == "on":
            assert json.loads(state.read_bytes())["reason"] == "deployed controller"
    assert not state.exists()
    foreign = target / "unrelated.txt"
    foreign.write_text("retained", encoding="utf-8")
    source = ROOT / "libs/installer-engine/installer_engine_uninstall.py"
    monkeypatch.syspath_prepend(str(source.parent))
    spec = importlib.util.spec_from_file_location("deployed_controller_removal", source)
    assert spec is not None and spec.loader is not None
    remover = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, remover)
    spec.loader.exec_module(remover)
    payload = scripts.parent
    profile = remover.profile_for(payload, plugin, scripts / "uninstall-profile.json")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    plan = remover._plan(payload, runtime, profile, (remover.Target(runtime, (), ()),))
    selected = {directory / name for directory, names in plan for name in names}
    owned = {target / name for name in ASSETS}
    assert owned <= selected and foreign not in selected
    for directory, names in plan:
        removal = tuple(name for name in names if directory / name in owned)
        if removal:
            assert directory == target
            remover._native_remove(payload, BASH, directory, removal)
    assert not any(path.exists() for path in owned)
    assert foreign.read_text(encoding="utf-8") == "retained"
