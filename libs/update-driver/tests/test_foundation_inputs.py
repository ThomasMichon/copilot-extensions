"""Fixed closure admission and canonical construction participant contracts."""

import importlib.util
import json
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from update_driver import RequestSpec, TrustedRunner, drain, enqueue, foundation_state as fs
from update_driver.foundation_inputs import (
    PACKAGES, SUPPORT, FoundationInputError, check_health, inspect,
)


@pytest.fixture
def source(area):
    common = area / "source"
    for directory, (name, dependencies) in PACKAGES.items():
        root = common / directory
        root.mkdir(parents=True)
        lines = [
            "[project]", f"name = {json.dumps(name)}", 'version = "1.0.0"',
            "dependencies = " + json.dumps(sorted(dependencies)), "[tool.uv.sources]",
        ]
        for dependency in sorted(dependencies):
            target = next(key for key, value in PACKAGES.items() if value[0] == dependency)
            lines.append(f'{dependency} = {{ path = "../{target}", editable = true }}')
        (root / "pyproject.toml").write_text("\n".join(lines), encoding="utf-8")
        (root / "source.py").write_text("pass\n", encoding="utf-8")
    plugin = common / "update-driver"
    (plugin / "scripts").mkdir()
    for name in SUPPORT:
        (plugin / "scripts" / name).write_text("# fixture\n", encoding="utf-8")
    return plugin


def test_fixed_closure_is_location_independent_and_ignores_generated_cruft(source, area):
    first = inspect(source)
    copied = area / "copied"
    shutil.copytree(source.parent, copied)
    assert inspect(copied / "update-driver").digest == first.digest
    cache = source / "__pycache__"
    cache.mkdir()
    (cache / "ignored.pyc").write_bytes(b"generated")
    assert inspect(source) == first
    (source / "source.py").write_text("changed\n")
    assert inspect(source).digest != first.digest


@pytest.mark.parametrize("directory", list(PACKAGES))
def test_no_additional_runtime_dependency_can_enter_the_recipe(source, directory):
    path = source.parent / directory / "pyproject.toml"
    text = path.read_text()
    text = text.replace("dependencies = [", 'dependencies = ["unexpected", ', 1)
    path.write_text(text)
    with pytest.raises(FoundationInputError, match="fixed recipe"):
        inspect(source)


@pytest.mark.parametrize("replacement", ["../../foreign", "../single-instance-lease"])
def test_changed_local_source_refuses_even_when_the_other_package_exists(source, replacement):
    path = source / "pyproject.toml"
    path.write_text(path.read_text().replace("../agent-procutil", replacement))
    with pytest.raises(FoundationInputError, match="fixed sibling"):
        inspect(source)


def test_missing_canonical_support_is_not_an_ambient_helper_fallback(source):
    (source / "scripts" / SUPPORT[0]).unlink()
    with pytest.raises(FileNotFoundError):
        inspect(source)


def test_health_is_read_only_and_checks_packaged_native_assets():
    check_health()


@pytest.fixture
def builder(monkeypatch, source):
    path = Path(__file__).parents[1] / "scripts" / "foundation-build.py"
    spec = importlib.util.spec_from_file_location("foundation_build_fixture", path)
    module = importlib.util.module_from_spec(spec)
    original = sys.path[:]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = original
    monkeypatch.setattr(module, "SOURCE", source)
    return module


@pytest.fixture
def construction(monkeypatch, source):
    events = []

    def freeze(plugin, destination):
        events.append("freeze")
        shutil.copytree(plugin.parent, destination)
        return SimpleNamespace(plugin=destination / "update-driver")

    @contextmanager
    def candidate(root, version, **kwargs):
        events.append(("candidate", version, kwargs))
        yield SimpleNamespace(slot=root / "versions" / version)
        events.append("released")

    def activate(root, version, **kwargs):
        events.append(("selected", version, kwargs))

    engine = SimpleNamespace(
        _package_roots=lambda plugin: inspect(plugin).packages,
        freeze_inputs=freeze, numbered_candidate=candidate,
    )
    monkeypatch.setitem(sys.modules, "installer_engine", engine)
    monkeypatch.setitem(sys.modules, "versioned_runtime", SimpleNamespace(activate=activate))
    return engine, events


def execute(builder, source, area, monkeypatch):
    root = area / "state"
    queued = enqueue(
        RequestSpec("foundation-builder-fixture"), root=root,
        runner=TrustedRunner((sys.executable, "-c", "pass"), str(area)),
    )
    result = []

    def executor(transaction):
        digest = inspect(source).digest
        operation = fs.begin(root, digest, transaction._lock)
        monkeypatch.setenv("UPDATE_DRIVER_WITNESS", transaction.participant_env()["UPDATE_DRIVER_WITNESS"])
        result.append(builder.construct(
            root=root, source_hash=digest, uv=area / "uv", base_python=Path(sys.executable),
        ))
        fs.finish(root, operation, transaction._lock, exit_code=0)
        return 0

    drain(root=root, executor=executor)
    return queued, result[0]


def test_builder_without_custody_never_stages_anything(builder, source, area, monkeypatch):
    monkeypatch.delenv("UPDATE_DRIVER_WITNESS", raising=False)
    root = area / "absent"
    with pytest.raises(FoundationInputError, match="live installation custody"):
        builder.construct(root=root, source_hash=inspect(source).digest, uv=area / "uv",
                          base_python=Path(sys.executable))
    assert not root.exists()


def test_builder_uses_fixed_retained_canonical_recipe_under_borrowed_custody(
    builder, construction, source, area, monkeypatch,
):
    _, events = construction
    _, slot = execute(builder, source, area, monkeypatch)
    version = "foundation-" + inspect(source).digest
    assert slot == area / "state" / "foundation" / "versions" / version
    assert events[0] == "freeze" and events[-1] == "released"
    candidate = next(event for event in events if isinstance(event, tuple) and event[0] == "candidate")
    assert candidate[1] == version
    assert candidate[2]["process_retention"] is True
    assert candidate[2]["health_module"] == "update_driver.foundation_inputs"
    assert candidate[2]["health_callable"] == "check_health"
    assert ("selected", version, {"link_free": True}) in events
    assert not list((area / "state" / "foundation").glob("inputs-*"))


@pytest.mark.parametrize("phase", ["freeze", "construct"])
def test_source_substitution_never_selects_a_candidate(
    builder, construction, source, area, monkeypatch, phase,
):
    engine, events = construction
    original = engine.freeze_inputs if phase == "freeze" else engine.numbered_candidate

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        (source / "source.py").write_text("substituted")
        return result

    monkeypatch.setattr(engine, "freeze_inputs" if phase == "freeze" else "numbered_candidate", changed)
    with pytest.raises(FoundationInputError, match="source changed|admission changed"):
        execute(builder, source, area, monkeypatch)
    assert not any(isinstance(event, tuple) and event[0] == "selected" for event in events)
    assert not list((area / "state" / "foundation").glob("inputs-*"))
