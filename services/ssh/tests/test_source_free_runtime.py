"""Installed SSH runtime closure, without checkout imports or startup .pth files."""

import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
from typing import Any, TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    import services.ssh.tests.test_machine_consumer as consumer
else:
    import test_machine_consumer as consumer

CHECKOUT = Path(__file__).resolve().parents[3]
FLAGS = {"creationflags": cast(Any, subprocess).CREATE_NO_WINDOW} if os.name == "nt" else {}
PROBE = r"""
import json, os, runpy, sys
from pathlib import Path

context = json.loads(sys.argv.pop(1))
checkout = Path(context["checkout"]).resolve()
runtime = Path(context["runtime"]).resolve()
fixture = Path(context["fixture"]).resolve() if context.get("fixture") else None
assert fixture is None or (
    not checkout.is_relative_to(fixture) and not runtime.is_relative_to(fixture)
)

def deny_source(event, arguments):
    if event in {"open", "os.listdir", "os.scandir"} and arguments:
        value = arguments[0]
        if isinstance(value, (str, bytes, os.PathLike)):
            selected = Path(os.fsdecode(value)).resolve()
            fixture_data = (
                fixture is not None and selected.is_relative_to(fixture)
                and selected.suffix.lower() not in {".py", ".pyw", ".pyc", ".pyo", ".pyd"}
            )
            if (selected.is_relative_to(checkout) and not selected.is_relative_to(runtime)
                    and not fixture_data):
                raise PermissionError("source-free probe refused checkout access")
sys.addaudithook(deny_source)
if sys.argv[1:] == ["--source-import-control"]:
    if fixture is not None:
        assert (fixture / "fixture-data.txt").read_text("utf-8") == "fixture data"
        for suffix in (".py", ".PY", ".pyc"):
            try:
                (fixture / ("fixture-source" + suffix)).read_bytes()
            except PermissionError:
                pass
            else:
                raise AssertionError("fixture source read was not refused")
    try:
        runpy.run_path(str(checkout / "fleet-manager" / "src" / "fleet_manager" / "ssh_machine.py"))
    except PermissionError as error:
        assert str(error) == "source-free probe refused checkout access"
        print("SOURCE_IMPORT_REFUSED")
    else:
        raise AssertionError("checkout import control was not refused")
else:
    # -S prevents editable .pth startup from populating sys.modules before the guard.
    for directory in context["sites"]:
        selected = Path(directory).resolve()
        assert selected.is_relative_to(runtime)
        sys.path.append(str(selected))
    from importlib import metadata
    for name in ("fleet-manager", "agent-ssh-service", "agent-ssh",
                 "agent-single-instance-lease", "agent-ssh-manager"):
        assert metadata.distribution(name).files, "required runtime distribution is absent"
    for distribution in metadata.distributions():
        assert Path(distribution.locate_file("")).resolve().is_relative_to(runtime), (
            "distribution metadata escaped the installed runtime"
        )
        direct = distribution.read_text("direct_url.json")
        assert not direct or not json.loads(direct).get("dir_info", {}).get("editable"), (
            "runtime closure still contains an editable distribution"
        )
        assert distribution.files, "installed distribution has no file inventory"
        for entry in distribution.files:
            assert Path(distribution.locate_file(entry)).resolve().is_relative_to(runtime), (
                "distribution file escaped the installed runtime"
            )
    sys.argv[0] = "fleet-manager"
    runpy.run_module("fleet_manager", run_name="__main__")
"""


def probe_command(*arguments: str, fixture_root: Path | None = None) -> list[str]:
    context = {
        "checkout": str(CHECKOUT),
        "runtime": sys.prefix,
        "sites": sorted({sysconfig.get_path("purelib"), sysconfig.get_path("platlib")}),
        "fixture": str(fixture_root) if fixture_root is not None else None,
    }
    return [sys.executable, "-I", "-S", "-c", PROBE, json.dumps(context), *arguments]


def test_source_import_control_is_physically_refused(tmp_path: Path) -> None:
    result = subprocess.run(
        probe_command("--source-import-control"), cwd=tmp_path, capture_output=True,
        text=True, timeout=15, **FLAGS,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "SOURCE_IMPORT_REFUSED"


def test_checkout_contained_fixture_data_does_not_permit_source_reads(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "fixture-data.txt").write_text("fixture data", encoding="utf-8")
    for suffix in (".py", ".PY", ".pyc"):
        (fixture / ("fixture-source" + suffix)).write_text("raise AssertionError", encoding="utf-8")
    context = {
        "checkout": str(tmp_path), "runtime": sys.prefix, "sites": [],
        "fixture": str(fixture),
    }
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", PROBE, json.dumps(context),
         "--source-import-control"],
        cwd=fixture, capture_output=True, text=True, timeout=15, **FLAGS,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "SOURCE_IMPORT_REFUSED"


@pytest.mark.parametrize("disconnected", [False, True])
async def test_source_free_fleet_role_converges_and_catches_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disconnected: bool,
) -> None:
    actual = subprocess.Popen
    launches: list[tuple[str, ...]] = []

    def launch(command: list[str], **options: Any) -> subprocess.Popen[bytes]:
        if command[:4] == [sys.executable, "-I", "-m", "fleet_manager"]:
            launches.append(tuple(command))
            command = probe_command(*command[4:], fixture_root=tmp_path)
        return actual(command, **options)

    monkeypatch.setattr(consumer.subprocess, "Popen", launch)
    await consumer.test_installed_fleet_role_converges_live_and_catches_up_after_restart(
        tmp_path, disconnected,
    )
    assert len(launches) == (2 if disconnected else 1)
