"""A standalone Budget payload loads its own SDK without a repository library tree."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "budget_materialize_subject", REPO / "tools" / "materialize_installer_engine.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(REPO / "tools"))
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("host", ("powershell.exe", "pwsh", "bash"))
def test_materialized_budget_status_uses_only_packaged_engine(tmp_path: Path, host: str) -> None:
    if host == "bash" and os.name == "nt":
        pytest.skip("Native POSIX packaged startup requires POSIX")
    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    candidate = tmp_path / "release"
    payload = candidate / "plugins" / "budget-guidance"
    shutil.copytree(
        REPO / "plugins" / "budget-guidance", payload,
        ignore=shutil.ignore_patterns("__pycache__", ".venv", "tests"),
    )
    MODULE.materialize_refs(candidate, canonical_root=REPO)
    assert not (candidate / "libs").exists()
    home = tmp_path / "home"
    home.mkdir()
    root = home / ".budget-guidance"
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "BUDGET_GUIDANCE_", "COPILOT_"))
    }
    environment.update(HOME=str(home), USERPROFILE=str(home),
                       COPILOT_EXTENSIONS_TEST_CONTAINED="1")
    if host == "bash":
        command = [executable, str(payload / "scripts" / "install.sh"),
                   "status", "--install-dir", str(root)]
    else:
        command = [executable, "-NoProfile", "-NonInteractive", "-File",
                   str(payload / "scripts" / "install.ps1"), "-Action", "status",
                   "-InstallDir", str(root)]
    result = subprocess.run(
        command, cwd=home, env=environment, capture_output=True,
        text=True, timeout=60,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "budget-guidance status" in result.stdout
    assert not root.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows wheel installer")
def test_native_budget_wheel_update_reuses_and_preserves_commands(tmp_path: Path) -> None:
    from packaging.markers import default_environment
    from packaging.version import Version

    import materialize_main
    from runtime_profile import SCHEMA, validate_profile
    from wheel_input_fixture import wheel_bytes

    host, uv = shutil.which("powershell.exe"), shutil.which("uv")
    if host is None or uv is None:
        pytest.skip("native PowerShell and UV are required")
    candidate = tmp_path / "release"
    payload = candidate / "plugins" / "budget-guidance"
    shutil.copytree(
        REPO / "plugins" / "budget-guidance", payload,
        ignore=shutil.ignore_patterns("__pycache__", ".venv", "tests"),
    )
    materialize_main.materialize_uv_editable_ref_into(
        source_consumer_dir=REPO / "plugins" / "budget-guidance",
        dest_consumer_dir=payload, canonical_root=REPO,
    )
    MODULE.materialize_refs(candidate, canonical_root=REPO)
    policy = payload / "scripts" / "installer_dependency_policy.py"
    policy.write_text(
        policy.read_text(encoding="utf-8").replace(
            'if __name__ == "__main__":',
            'if __name__ == "__main__":\n'
            '    raise RuntimeError("native wheels reached the source/registry CLI")',
        ),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    root = home / ".budget-guidance"
    root.mkdir()
    configuration = root / "config.yaml"
    configuration.write_text("unconfigured: true\n", encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "BUDGET_GUIDANCE_", "COPILOT_"))
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home),
        HOMEDRIVE=home.drive, HOMEPATH=str(home)[len(home.drive):],
        COPILOT_EXTENSIONS_TEST_CONTAINED="1", UV_OFFLINE="1",
        UV_PYTHON_DOWNLOADS="never", PYTHONDONTWRITEBYTECODE="1",
    )
    packages = [payload, payload / "libs" / "agent-procutil"]
    wheel_directory = tmp_path / "wheels"
    wheel_directory.mkdir()
    rows = []
    for package in packages:
        project = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        name = project["name"]
        version = str(Version(project["version"]))
        normalized = name.replace("-", "_")
        metadata = (
            f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n"
            f"Requires-Python: {project['requires-python']}\n"
            + "".join(f"Requires-Dist: {item}\n" for item in project.get("dependencies", []))
            + "\n"
        )
        archive = wheel_directory / f"{normalized}-{version}-py3-none-any.whl"
        archive.write_bytes(wheel_bytes(
            name=normalized, prefix=f"{normalized}-{version}.dist-info/",
            metadata=metadata.encode("utf-8"),
            extra={
                path.relative_to(package / "src").as_posix(): path.read_bytes()
                for path in (package / "src").rglob("*")
                if path.is_file() and "__pycache__" not in path.parts
            },
        ))
        rows.append({
            "name": name, "version": version, "filename": archive.name,
            "sha256": "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
            "requires_python": project["requires-python"],
            "requires_dist": project.get("dependencies", []),
            "provides_extras": [], "selected_extras": [],
        })
    profile = validate_profile({
        "schema": SCHEMA, "version": 1,
        "release": {"identity": "fixture-release", "sha256": "sha256:" + "2" * 64},
        "mode": "source", "artifact": None, "component": "budget-guidance", "backend": "cpu",
        "approval_source": "fixture-authority", "acquisition_policy": "fixture-policy",
        "target": {"device": "cpu", "environment": default_environment(), "tags": ["py3-none-any"]},
        "roots": ["budget-guidance==" + rows[0]["version"]], "wheels": rows,
    }).record()
    profile_path = tmp_path / "profile.json"

    def install(record):
        profile_path.write_text(json.dumps(record), encoding="utf-8")
        result = subprocess.run(
            [host, "-NoProfile", "-NonInteractive", "-File",
             str(payload / "scripts" / "install.ps1"), "-Action", "update",
             "-InstallDir", str(root), "-WheelProfile", str(profile_path),
             "-WheelDirectory", str(wheel_directory), "-WheelPython", sys.executable,
             "-WheelUv", uv],
            cwd=home, env=environment, capture_output=True, text=True,
            timeout=120, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return (root / "current-version").read_text(encoding="utf-8-sig").strip()

    first = install(profile)
    slot = root / "versions" / first
    receipt = json.loads((slot / ".runtime-input-origins.json").read_text(encoding="utf-8"))
    assert receipt["wheel_inputs"]["binding"] == profile
    assert receipt["wheel_inputs"]["distributions"]["budget-guidance"]["version"] == rows[0]["version"]
    before = {path.relative_to(slot): path.read_bytes() for path in slot.rglob("*") if path.is_file()}
    assert install(profile) == first
    assert {path.relative_to(slot): path.read_bytes() for path in slot.rglob("*") if path.is_file()} == before
    changed = validate_profile({
        **profile, "release": {"identity": "second-fixture-release", "sha256": "sha256:" + "3" * 64},
    }).record()
    second = install(changed)
    assert second != first, "the profile must participate in native generation selection"
    assert slot.is_dir(), "update must preserve the previous usable generation"
    assert configuration.read_text(encoding="utf-8") == "unconfigured: true\n"
    payload.rename(payload.with_name("removed-input"))
    command = home / ".local" / "bin" / "budget-guidance.ps1"
    probe = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(command), "--version"],
        cwd=home, env=environment, capture_output=True, text=True,
        timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert Version(probe.stdout.strip().split()[-1]) == Version(rows[0]["version"])
