import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

import pytest

from test_installers import BASH, PWSH, _copy_payload, _fake_uv_environment


@pytest.mark.parametrize("platform", ["posix", "powershell"])
@pytest.mark.parametrize("failure", [0, 9])
def test_dependency_artifacts_are_cleaned_before_and_after_each_build_attempt(tmp_path, platform, failure):
    if platform == "posix" and (BASH is None or os.name == "nt"):
        pytest.skip("POSIX installer coverage")
    if platform == "powershell" and (PWSH is None or os.name == "nt"):
        pytest.skip("portable PowerShell fixture uses a POSIX fake uv executable")
    _, env = _fake_uv_environment(tmp_path)
    payload = tmp_path / "budget-guidance"
    _copy_payload(payload)
    procutil = payload / "libs" / "agent-procutil"
    for source in (payload, procutil):
        (source / "build" / "lib").mkdir(parents=True, exist_ok=True)
        (source / "seed.egg-info").mkdir()
        (source / "src" / "seed.egg-info").mkdir()
    env["TEST_BUILD_ARTIFACT_PROBE"] = "1"
    env["TEST_INSTALL_EXIT_CODE"] = str(failure)
    attempts = tmp_path / "attempts.jsonl"
    env["TEST_INSTALL_ATTEMPTS_LOG"] = str(attempts)
    install_dir = Path(env["HOME"]) / ".budget-guidance"
    command = (
        [BASH, str(payload / "scripts" / "install.sh"), "install", "--install-dir", str(install_dir)]
        if platform == "posix" else
        [PWSH, "-NoProfile", "-File", str(payload / "scripts" / "install.ps1"),
         "-Action", "install", "-InstallDir", str(install_dir)]
    )
    result = subprocess.run(
        command, cwd=tmp_path, env=env, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=120, check=False,
    )
    assert (result.returncode != 0) is bool(failure), result.stdout
    assert [json.loads(line) for line in attempts.read_text().splitlines()] == [
        {"engine": "uv", "sources": 2},
    ]
    for source in (payload, procutil):
        assert not (source / "build").exists()
        assert not list(source.glob("*.egg-info"))
        assert not list((source / "src").glob("*.egg-info"))
    if failure:
        assert not (install_dir / "current-version").exists()
        assert not (install_dir / "versions" / tomllib.loads(
            (payload / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"]).exists()


@pytest.mark.parametrize("platform", ["posix", "powershell"])
def test_canonical_dependency_metadata_is_part_of_admission_hash(tmp_path, platform):
    if platform == "posix" and (BASH is None or os.name == "nt"):
        pytest.skip("POSIX installer coverage")
    if platform == "powershell" and PWSH is None:
        pytest.skip("PowerShell is unavailable")
    from test_installers import PLUGIN

    payload = tmp_path / "repo" / "plugins" / "budget-guidance"
    payload.mkdir(parents=True)
    (payload / "pyproject.toml").write_text('[project]\nname="budget-guidance"\nversion="1"\n')
    dependency = tmp_path / "repo" / "libs" / "agent-procutil"
    dependency.mkdir(parents=True)
    project = dependency / "pyproject.toml"
    project.write_text('[project]\nname="agent-procutil"\nversion="1"\n')
    if platform == "powershell":
        source = (PLUGIN / "scripts" / "install.ps1").read_text()
        functions = "\n".join(re.search(
            rf"(?ms)^function {name} \{{.*?^\}}", source,
        ).group(0) for name in ("Get-BudgetProcutilSource", "Get-PayloadHash"))
        script = (
            f"$PluginDir = '{str(payload).replace(chr(39), chr(39) * 2)}'\n"
            + functions + '\nWrite-Host "H0=$(Get-PayloadHash)"\n'
            + f"Add-Content -LiteralPath '{str(project).replace(chr(39), chr(39) * 2)}' '# changed'\n"
            + 'Write-Host "H1=$(Get-PayloadHash)"\n'
        )
        command = [PWSH, "-NoProfile", "-NonInteractive", "-Command", script]
    else:
        import shlex

        source = (PLUGIN / "scripts" / "install.sh").read_text()
        functions = "\n".join(re.search(
            rf"(?ms)^{name}\(\) \{{.*?^\}}", source,
        ).group(0) for name in ("_budget_procutil_source", "_payload_hash"))
        script = (
            f"set -euo pipefail\nPLUGIN_DIR={shlex.quote(str(payload))}\n"
            + functions + '\necho "H0=$(_payload_hash)"\n'
            + f"printf '\\n# changed\\n' >> {shlex.quote(str(project))}\n"
            + 'echo "H1=$(_payload_hash)"\n'
        )
        command = [BASH, "-c", script]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    hashes = dict(line.split("=", 1) for line in result.stdout.splitlines() if line.startswith("H"))
    assert len(hashes["H0"]) == len(hashes["H1"]) == 64
    assert hashes["H0"] != hashes["H1"]


@pytest.mark.parametrize("platform", ["posix", "powershell"])
def test_development_stamp_projects_own_snapshot_library_paths(tmp_path, platform):
    if platform == "posix" and (BASH is None or os.name == "nt"):
        pytest.skip("POSIX installer coverage")
    if platform == "powershell" and PWSH is None:
        pytest.skip("PowerShell is unavailable")
    from test_installers import PLUGIN

    _, env = _fake_uv_environment(tmp_path)
    repository = tmp_path / "repo"
    payload = repository / "plugins" / "budget-guidance"
    shutil.copytree(PLUGIN, payload)
    shutil.copytree(PLUGIN.parents[1] / "libs" / "agent-procutil", repository / "libs" / "agent-procutil")
    before = (payload / "pyproject.toml").read_bytes()
    version = tomllib.loads(before.decode())["project"]["version"]
    root = Path(env["HOME"]) / ".budget-guidance"
    command = (
        [BASH, str(payload / "scripts" / "install.sh"), "stamp", "--install-dir", str(root)]
        if platform == "posix" else
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(payload / "scripts" / "install.ps1"),
         "-Action", "stamp", "-InstallDir", str(root)]
    )
    result = subprocess.run(
        command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    snapshot = root / "snapshots" / version
    source = tomllib.loads((snapshot / "pyproject.toml").read_text())
    assert source["tool"]["uv"]["sources"]["agent-procutil"] == {"path": "libs/agent-procutil"}
    assert (snapshot / "libs" / "agent-procutil" / "pyproject.toml").is_file()
    assert (payload / "pyproject.toml").read_bytes() == before
    assert not (root / "versions").exists()
