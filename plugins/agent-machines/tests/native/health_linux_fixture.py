"""Closed Linux installation and finite calendar inputs for explicit native tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any
import venv

from agent_machines import health_enrollment as enrollment, health_jobs, health_slots

REPO = Path(__file__).resolve().parents[4]
PLUGIN = REPO / "plugins" / "agent-machines"


def _module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def stage_helpers(helpers: Path, name: str) -> Any:
    helpers.mkdir(parents=True)
    for source in (REPO / "libs" / "versioned-runtime").glob("versioned_runtime*.py"):
        shutil.copyfile(source, helpers / source.name)
    shutil.copyfile(
        REPO / "libs" / "installer-engine" / "runtime_launcher.py",
        helpers / "runtime_launcher.py",
    )
    shutil.copyfile(
        PLUGIN / "scripts" / "daemon_instance_runtime.pyz",
        helpers / "daemon_instance_runtime.pyz",
    )
    return _module(helpers / "versioned_runtime.py", name)


def installed(root: Path, version: str) -> enrollment.Installation:
    if sys.platform != "linux":
        raise RuntimeError("Linux installed fixture required")
    inputs = root.parent / ("build-input-" + version)
    suffix = version.replace(".", "_")
    runtime = stage_helpers(inputs / "helpers", "_health_linux_build_" + suffix)
    slot = runtime.slot(root, version)
    environment = slot / ".venv"
    venv.EnvBuilder(with_pip=False).create(environment)
    site = environment / "lib" / (
        f"python{sys.version_info.major}.{sys.version_info.minor}"
    ) / "site-packages"
    assert site.is_dir()
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    modules = {
        "agent_machines": PLUGIN / "src" / "agent_machines",
        "agent_procutil": REPO / "libs" / "agent-procutil" / "src" / "agent_procutil",
    }
    for package in (
        "configuration_context", "configuration_layers", "dropin_registry",
        "plugin_activation", "plugin_resolve", "machine_transport", "remote_login_shell",
        "installer_readiness", "process_telemetry", "single_instance_lease", "yaml", "_yaml",
    ):
        spec = importlib.util.find_spec(package)
        assert spec is not None and spec.origin is not None, package
        modules[package] = Path(spec.origin).parent if spec.submodule_search_locations else Path(spec.origin)
    for name, source in modules.items():
        if source.is_dir():
            shutil.copytree(source, inputs / name, ignore=ignore)
            shutil.copytree(inputs / name, site / name)
        else:
            shutil.copyfile(source, inputs / source.name)
            shutil.copyfile(inputs / source.name, site / source.name)
    for source in (REPO / "libs" / "installation-context").glob("*.py"):
        shutil.copyfile(source, inputs / source.name)
        shutil.copyfile(inputs / source.name, site / source.name)
    runtime.retain_runtime_helpers(slot, complete=False)
    runtime.mark_complete(root, version, payload_hash="fixture-closed-input")
    runtime.activate(root, version, link_free=True)
    runtime = _module(
        slot / ".runtime-bootstrap" / "versioned_runtime.py", "_health_linux_retained_" + suffix,
    )
    launcher = _module(
        slot / ".runtime-bootstrap" / "runtime_launcher.py", "_health_linux_launcher_" + suffix,
    )
    result = enrollment.Installation(
        root, slot, environment / "bin" / "python", "",
        launcher.runtime_identity(slot), runtime, "linux",
    )
    shutil.rmtree(inputs)
    assert not inputs.exists()
    return result


def calendar(now: datetime) -> tuple[tuple[datetime, datetime], datetime]:
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("UTC fixture clock required")
    start = now.replace(microsecond=0) + timedelta(seconds=20)
    return (start, start + timedelta(seconds=30)), start + timedelta(seconds=130)


def stall_collection_child(installation: enrollment.Installation, marker: Path) -> None:
    """Fault only the disposable child's entry; retain the real finite action."""
    entries = list((installation.python.parent.parent / "lib").glob(
        "python3.*/site-packages/agent_machines/__main__.py",
    ))
    assert len(entries) == 1
    entry = entries[0]
    original = entry.read_text(encoding="utf-8")
    declaration = "from __future__ import annotations\n"
    assert original.count(declaration) == 1
    fault = (
        "import os,pathlib,sys,time\n"
        "if sys.argv[1:3] == ['health', 'run'] and '--apply' in sys.argv:\n"
        f"    pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n"
        "    time.sleep(80)\n"
    )
    entry.write_text(
        original.replace(declaration, declaration + fault),
        encoding="utf-8",
    )


def until(
    predicate: Callable[[], dict[str, Any] | None], seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = predicate()
        except health_slots.HealthError as error:
            if str(error) != "native-health-pair-operation-unconfirmed":
                raise
            value = None
        if value:
            return value
        time.sleep(.5)
    raise AssertionError("bounded native Linux fixture deadline")


class FiniteInstallation:
    """Only the native fixture changes cadence; installed code is unmodified."""

    def __init__(
        self, installation: enrollment.Installation,
        times: tuple[datetime, ...], expires: datetime,
    ) -> None:
        if (
            len(times) != 2
            or any(value.tzinfo is None or value.utcoffset() != timedelta(0) for value in (*times, expires))
            or not times[0] < times[1] < expires
            or expires - times[0] > timedelta(minutes=3)
        ):
            raise ValueError("bounded UTC fixture calendar required")
        self.installed, self.times, self.expires = installation, times, expires

    def __getattr__(self, name: str) -> Any:
        return getattr(self.installed, name)

    def definition(
        self, desired: dict[str, Any], owner: str, *, enabled: bool,
    ) -> health_jobs.LinuxDefinition:
        value = self.installed.definition(desired, owner, enabled=enabled)
        assert isinstance(value, dict)
        artifacts = value["artifacts"]
        service, timer = next(name for name in artifacts if name.endswith(".service")), next(
            name for name in artifacts if name.endswith(".timer")
        )
        condition = " ".join(health_jobs._systemd(argument, executable=index == 0) for index, argument in enumerate([
            str(self.python), "-I", "-S", "-c",
            f"import sys,time;sys.exit(0 if time.time() <= {int(self.expires.timestamp())} else 1)",
        ]))
        artifacts[service] = artifacts[service].replace(
            "Type=oneshot\n", "Type=oneshot\nExecCondition=" + condition + "\n",
        )
        scheduled = "".join(
            "OnCalendar=" + value.strftime("%Y-%m-%d %H:%M:%S UTC") + "\n"
            for value in self.times
        )
        artifacts[timer] = artifacts[timer].replace(
            "OnCalendar=*-*-* *:00:15 UTC\n", scheduled,
        )
        return value

    def receipt(self, owner: str) -> dict[str, Any]:
        name = "ce-health-" + owner.rsplit(":", 1)[1][:32]
        return {
            "schema": "agent-machines.health.linux-fixture", "version": 1,
            "account_uid": enrollment.local_uid(), "owner": owner,
            "units": [name + ".service", name + ".timer"],
            "expires": self.expires.isoformat(),
            "calendar": [value.isoformat() for value in self.times],
            "runtime": str(self.root),
        }


class StartupDelayInstallation(FiniteInstallation):
    """Block only a fixture pre-start child under the unchanged manager deadline."""

    def __init__(
        self, installation: enrollment.Installation,
        times: tuple[datetime, ...], expires: datetime, marker: Path,
    ) -> None:
        super().__init__(installation, times, expires)
        self.marker = marker

    def definition(
        self, desired: dict[str, Any], owner: str, *, enabled: bool,
    ) -> health_jobs.LinuxDefinition:
        value = super().definition(desired, owner, enabled=enabled)
        service = next(name for name in value["artifacts"] if name.endswith(".service"))
        command = " ".join(
            health_jobs._systemd(argument, executable=index == 0)
            for index, argument in enumerate([
                str(self.python), "-I", "-S", "-c",
                "import os,pathlib,sys,time;pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(80)",
                str(self.marker),
            ])
        )
        value["artifacts"][service] = value["artifacts"][service].replace(
            "Type=oneshot\n", "Type=oneshot\nExecStartPre=" + command + "\n",
        )
        return value


def write_receipt(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
