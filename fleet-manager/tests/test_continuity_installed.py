"""Noneditable SDK closure through the existing real TLS continuity fixture."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from agent_procutil import no_window_kwargs
import pytest

import test_continuity_bootstrap as consumer

REPOSITORY = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.client_distribution_full,
    pytest.mark.skipif(sys.platform != "linux", reason="Linux SDK qualification; native lanes are separate"),
]
PROBE = r"""
import json, os, runpy, sys
from pathlib import Path

context = json.loads(sys.argv.pop(1))
checkout = Path(context["checkout"]).resolve()
runtime = Path(context["runtime"]).resolve()
def deny_source(event, arguments):
    if event in {"open", "os.listdir", "os.scandir"} and arguments:
        value = arguments[0]
        if isinstance(value, (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(value)).resolve()
            if path.is_relative_to(checkout) and not path.is_relative_to(runtime):
                raise PermissionError("continuity SDK refused checkout access")
sys.addaudithook(deny_source)
if sys.argv[1:] == ["--source-import-control"]:
    try:
        runpy.run_path(str(checkout / "fleet-manager/src/fleet_manager/continuity_bootstrap.py"))
    except PermissionError as error:
        assert str(error) == "continuity SDK refused checkout access"
        print("SOURCE_IMPORT_REFUSED")
    else:
        raise AssertionError("source access control did not refuse")
else:
    import hashlib
    for site in context["sites"]:
        path = Path(site).resolve()
        assert path.is_relative_to(runtime)
        sys.path.append(str(path))
    from importlib import metadata
    for name in ("fleet-manager", "agent-procutil", "aiohttp", "PyJWT",
                 "cryptography", "jwcrypto", "joserfc"):
        assert metadata.distribution(name).files, "required SDK distribution is absent"
    for distribution in metadata.distributions():
        assert Path(distribution.locate_file("")).resolve().is_relative_to(runtime)
        direct = distribution.read_text("direct_url.json")
        assert not direct or not json.loads(direct).get("dir_info", {}).get("editable"), (
            "SDK closure contains an editable distribution"
        )
        assert distribution.files, "SDK distribution has no file inventory"
        for entry in distribution.files:
            assert Path(distribution.locate_file(entry)).resolve().is_relative_to(runtime), (
                "SDK distribution file escaped its runtime"
            )
    import fleet_manager, agent_procutil
    for module in (fleet_manager, agent_procutil):
        assert Path(module.__file__).resolve().is_relative_to(runtime)
    package = Path(fleet_manager.__file__).parent
    for name, digest in context["expected"].items():
        assert hashlib.sha256((package / name).read_bytes()).hexdigest() == digest, (
            "installed SDK differs from selected source"
        )
    sys.argv[0] = "fleet-manager"
    runpy.run_module("fleet_manager", run_name="__main__")
"""


@pytest.fixture(scope="module")
def sdk_runtime(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[str]]:
    runtime = tmp_path_factory.mktemp("continuity-installed") / "runtime"
    uv = shutil.which("uv")
    assert uv, "installed SDK qualification requires the maintained uv build path"
    subprocess.run(
        [uv, "venv", "--python", sys.executable, str(runtime)],
        check=True, capture_output=True, text=True,
        timeout=60, **no_window_kwargs())
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    previous = sys.path[:]
    sys.path.insert(0, str(REPOSITORY / "tools"))
    try:
        from seasoned_uv import run_uv_install
        from standalone_consumers import standalone_build_resources

        run_uv_install(
            uv, ["--python", str(python), "--no-editable", ".[service-gateway]"],
            cwd=REPOSITORY / "fleet-manager", trusted_source_roots=(REPOSITORY,),
            retained_resource_roots=standalone_build_resources(
                "fleet-manager", source_root=REPOSITORY),
            check=True, capture_output=True, text=True, timeout=180,
            **no_window_kwargs())
    finally:
        sys.path[:] = previous
    sites = subprocess.run(
        [str(python), "-I", "-c",
         "import json,sysconfig;print(json.dumps(sorted({"
         "sysconfig.get_path('purelib'),sysconfig.get_path('platlib')})))"],
        check=True, capture_output=True, text=True, timeout=15, **no_window_kwargs())
    context = {
        "checkout": str(REPOSITORY), "runtime": str(runtime),
        "sites": json.loads(sites.stdout),
        "expected": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (REPOSITORY / "fleet-manager/src/fleet_manager").glob("*.py")},
    }
    return python, [str(python), "-I", "-S", "-c", PROBE, json.dumps(context)]


def test_source_import_control_is_refused(
    tmp_path: Path, sdk_runtime: tuple[Path, list[str]],
) -> None:
    result = subprocess.run(
        [*sdk_runtime[1], "--source-import-control"], cwd=tmp_path,
        capture_output=True, text=True, timeout=15, **no_window_kwargs())
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "SOURCE_IMPORT_REFUSED"


@pytest.mark.parametrize("selected", ["fleet-cli", "manager-engine"])
def test_installed_sdk_first_touch_and_original_key_recovery(
    tmp_path: Path, sdk_runtime: tuple[Path, list[str]], selected: str,
) -> None:
    consumer.test_real_cli_first_touch_check_restart_and_same_private_key(
        tmp_path, selected, sdk_runtime=sdk_runtime)
