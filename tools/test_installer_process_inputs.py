"""Closed-wheel children retain their canonical owner without a source graph."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import importlib
from pathlib import Path
import subprocess
import sys
import time

import pytest

from test_installer_closed_wheels import engine as engine  # noqa: PLC0414
from installer_wheel_inputs import WheelError
from installer_wheel_snapshot import WheelInstallInputs, retain_wheel_inputs
from wheel_input_fixture import wheel_bytes


def test_ordinary_child_keeps_frozen_source_and_relative_imports(
    engine, monkeypatch, tmp_path
):
    root = tmp_path / "source-procutil"
    package = root / "src/agent_procutil"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname="agent-procutil"\nversion="1.0"\n', encoding="utf-8"
    )
    (package / "__init__.py").write_text(
        "from .sibling import VALUE\n", encoding="utf-8"
    )
    (package / "sibling.py").write_text("VALUE = 37\n", encoding="utf-8")
    seen = []

    def run(argv, **options):
        module = options["procutil"]
        assert module.VALUE == 37
        assert Path(module.__file__) == package / "__init__.py"
        seen.append(module)
        return subprocess.CompletedProcess(argv, 0, "ordinary", "")

    monkeypatch.setattr(engine, "run_owned", run)
    result = engine._child(
        ["fixture-child"],
        inputs=engine.FrozenInputs(root, (root,), "fixture"),
        descriptor=None,
        timeout=1,
    )
    assert result.stdout == "ordinary" and len(seen) == 1


@contextmanager
def process_inputs(
    engine, tmp_path, *, name="agent-procutil", version="1.0", extra=None
):
    source = tmp_path / "application"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nname="fixture-app"\nversion="1.0"\n'
        '[tool.uv.sources]\nmissing={path="../absent-development-source"}\n',
        encoding="utf-8",
    )
    filename = "agent_procutil-1.0-py3-none-any.whl"
    archive = tmp_path / filename
    package = (
        Path(__file__).resolve().parents[1] / "libs/agent-procutil/src/agent_procutil"
    )
    content = (
        {
            "agent_procutil/" + path.relative_to(package).as_posix(): path.read_bytes()
            for path in package.rglob("*.py")
        }
        if extra is None
        else extra
    )
    archive.write_bytes(
        wheel_bytes(
            name="agent_procutil"
            if "agent_procutil/__init__.py" in content
            else "other",
            prefix="agent_procutil-1.0.dist-info/",
            metadata=f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\n\n".encode(),
            extra=content,
        )
    )
    digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    with retain_wheel_inputs(
        {filename: archive},
        {filename: digest},
        max_wheels=1,
        max_archive_bytes=1 << 20,
        max_total_bytes=1 << 20,
        max_snapshot_bytes=1 << 20,
        deadline=time.monotonic() + 30,
    ) as snapshot:
        wheels = WheelInstallInputs(
            snapshot,
            '{"fixture":true}',
            (("agent-procutil", "1.0", filename, digest),),
        )
        yield engine.FrozenInputs(
            source,
            (source,),
            "fixture",
            wheel_inputs=wheels,
            closed_wheel_source=True,
        )


def test_closed_wheel_failed_import_retires_partial_namespace(
    engine, monkeypatch, tmp_path
):
    before = set(sys.modules)
    monkeypatch.setattr(
        engine, "run_owned", lambda *a, **k: pytest.fail("failed import launched")
    )
    with process_inputs(
        engine,
        tmp_path,
        extra={
            "agent_procutil/__init__.py": b"from . import sibling\nraise RuntimeError('fixture import failure')\n",
            "agent_procutil/sibling.py": b"VALUE = 1\n",
        },
    ) as inputs:
        with pytest.raises(RuntimeError, match="fixture import failure"):
            engine._child(["fixture-child"], inputs=inputs, descriptor=None, timeout=1)
    assert not {
        name
        for name in set(sys.modules) - before
        if name.startswith("_installer_procutil_")
    }


@pytest.mark.parametrize("outcome", ["success", "failure", "timeout"])
def test_closed_wheel_child_retains_real_process_package_until_settlement(
    engine,
    monkeypatch,
    tmp_path,
    outcome,
):
    import installer_engine_process as processes

    owned = []
    failure = {
        "failure": subprocess.CalledProcessError(
            7, ["fixture-child"], "output", "failure"
        ),
        "timeout": subprocess.TimeoutExpired(["fixture-child"], 1, "output", "timeout"),
    }.get(outcome)

    def run(argv, **options):
        module = options["procutil"]
        path = Path(module.__file__)
        owned.append((module.__name__, path))
        assert path.is_file()
        streaming = importlib.import_module(module.__name__ + ".streaming")
        assert Path(streaming.__file__).is_file()
        packet = processes._guardian_packet(
            module,
            Path(engine.runtime.__file__),
            None,
        )
        assert packet
        assert options["construction_fd"] == 19
        assert options["allow_breakaway"] is True
        assert options["deadline"] == inputs.wheel_inputs.deadline
        assert 0 < options["timeout"] <= 1
        if failure is not None:
            raise failure
        return subprocess.CompletedProcess(argv, 0, "output", "")

    monkeypatch.setattr(engine, "run_owned", run)
    monkeypatch.setattr(
        engine,
        "_procutil",
        lambda _inputs: pytest.fail("closed wheels traversed source packages"),
    )
    with process_inputs(engine, tmp_path) as inputs:
        if failure is None:
            result = engine._child(
                ["fixture-child"],
                inputs=inputs,
                descriptor=19,
                timeout=1,
                allow_breakaway=True,
            )
            assert result.stdout == "output"
        else:
            with pytest.raises(type(failure)) as caught:
                engine._child(
                    ["fixture-child"],
                    inputs=inputs,
                    descriptor=19,
                    timeout=1,
                    allow_breakaway=True,
                )
            assert caught.value is failure
        assert inputs.packages == (inputs.plugin,)
    assert len(owned) == 1
    namespace, path = owned[0]
    assert not path.parent.exists()
    assert not any(
        name == namespace or name.startswith(namespace + ".") for name in sys.modules
    )


def test_closed_wheel_retries_share_retained_process_package(
    engine, monkeypatch, tmp_path
):
    paths = []

    def run(argv, **options):
        path = Path(options["procutil"].__file__)
        assert path.is_file()
        paths.append(path)
        if len(paths) == 1:
            raise subprocess.CalledProcessError(1, argv, "", "fixture transient")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(engine, "run_owned", run)
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)
    with process_inputs(engine, tmp_path) as inputs:
        engine._child(
            ["fixture-child"],
            inputs=inputs,
            descriptor=None,
            timeout=1,
            delays=(0,),
            transient="fixture transient",
        )
    assert len(paths) == 2 and paths[0] == paths[1]
    assert not paths[0].exists()


@pytest.mark.parametrize(
    "condition",
    ["missing", "wrong-name", "wrong-version", "changed-binding", "expired"],
)
def test_closed_wheel_owner_refuses_before_launch(
    engine, monkeypatch, tmp_path, condition
):
    changes = {
        "missing": {"extra": {"other/__init__.py": b"VALUE = 1\n"}},
        "wrong-name": {"name": "unrelated"},
        "wrong-version": {"version": "2.0"},
    }.get(condition, {})
    monkeypatch.setattr(
        engine, "run_owned", lambda *a, **k: pytest.fail("invalid owner launched")
    )
    monkeypatch.setattr(
        engine, "_procutil", lambda *a: pytest.fail("invalid owner fell back")
    )
    with process_inputs(engine, tmp_path, **changes) as inputs:
        if condition == "changed-binding":
            name, version, filename, _digest = inputs.wheel_inputs.distributions[0]
            inputs = replace(
                inputs,
                wheel_inputs=replace(
                    inputs.wheel_inputs,
                    distributions=((name, version, filename, "sha256:" + "0" * 64),),
                ),
            )
        if condition == "expired":
            inputs = replace(
                inputs,
                wheel_inputs=replace(
                    inputs.wheel_inputs,
                    snapshot=replace(
                        inputs.wheel_inputs.snapshot, _deadline=time.monotonic() - 1
                    ),
                ),
            )
        with pytest.raises(WheelError):
            engine._child(["fixture-child"], inputs=inputs, descriptor=None, timeout=1)
