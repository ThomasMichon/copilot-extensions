"""Incoming inspection composes construction without installed-runtime admission."""

from contextlib import contextmanager
import importlib.util
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def incoming(monkeypatch, tmp_path):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "incoming_registration_inspection", scripts / "inspect_registration.py",
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    anchor = tmp_path / "selected anchor"
    anchor.mkdir()
    uv = tmp_path / "uv"
    uv.write_bytes(b"selected tool")
    payload = tmp_path / "incoming"
    payload.mkdir()
    (payload / "pyproject.toml").write_text(
        '[project]\nname = "agent-worktrees"\nversion = "1.2.3"\n', encoding="utf-8",
    )
    monkeypatch.setattr(module.sources, "read_source_identity", lambda *args, **kwargs: None)
    return module, anchor, uv, payload


def test_incoming_control_retains_child_then_cleans_up_without_publishing(incoming, monkeypatch):
    module, anchor, uv, payload = incoming
    observed = {}

    @contextmanager
    def candidate(root, version, **options):
        observed.update(root=root, version=version, options=options)
        assert root.is_dir()
        yield SimpleNamespace(
            slot=root / "versions" / version, frozen_plugin=payload,
            payload_hash="a" * 64, descriptor=23,
        )
        observed["settled"] = True

    monkeypatch.setattr(module.engine, "numbered_candidate", candidate)
    monkeypatch.setattr(module.engine.runtime, "slot_python", lambda *args: Path(sys.executable))
    monkeypatch.setattr(module.engine, "_package_roots", lambda path: (path,))
    monkeypatch.setattr(module.engine, "_procutil", lambda inputs: "owned-process-tools")
    monkeypatch.setenv("PYTHONPATH", "must-not-reach-reader")
    monkeypatch.setenv("PYTHONHOME", "must-not-reach-reader")
    monkeypatch.setenv("VIRTUAL_ENV", "must-not-reach-reader")
    monkeypatch.setenv("AGENT_HOME", str(anchor))

    def run(argv, **options):
        assert not observed.get("settled")
        observed.update(argv=argv, child=options)
        return subprocess.CompletedProcess(argv, 0, '{"status":"ready"}\n', "")

    monkeypatch.setattr(module.engine, "run_owned", run)
    result = module.inspect_registration(payload, anchor, "custom-control", uv=uv)
    assert result.stdout == '{"status":"ready"}\n'
    assert observed["settled"] and not observed["root"].exists()
    assert observed["version"] == "control"
    assert observed["options"] == {
        "plugin": payload, "uv": uv, "base_python": Path(sys.executable).resolve(),
        "health_module": "agent_worktrees.repos_registration_inspect",
        "expected_source_identity": {},
    }
    assert observed["argv"][-7:] == [
        "inspect", "--anchor", str(anchor), "--default-name", "custom-control",
        "--contract-version", "1",
    ]
    assert observed["child"]["construction_fd"] == 23
    assert observed["child"]["runtime_root"] == observed["root"]
    assert observed["child"]["env"]["AGENT_HOME"] == str(anchor)
    assert not {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"} & observed["child"]["env"].keys()


def test_reader_refusal_keeps_its_original_output_and_exit_after_cleanup(incoming, monkeypatch):
    module, anchor, uv, payload = incoming
    root = []

    @contextmanager
    def candidate(path, *args, **kwargs):
        root.append(path)
        yield SimpleNamespace(
            slot=path / "versions" / "control", frozen_plugin=payload,
            payload_hash="a" * 64, descriptor=23,
        )

    monkeypatch.setattr(module.engine, "numbered_candidate", candidate)
    monkeypatch.setattr(module.engine.runtime, "slot_python", lambda *args: Path(sys.executable))
    monkeypatch.setattr(module.engine, "_package_roots", lambda path: (path,))
    monkeypatch.setattr(module.engine, "_procutil", lambda inputs: None)

    def refused(argv, **kwargs):
        raise subprocess.CalledProcessError(
            2, argv, output='{"status":"failed","code":"ambiguous-anchor"}\n',
            stderr="More than one registered project owns the selected anchor.\n",
        )

    monkeypatch.setattr(module.engine, "run_owned", refused)
    result = module.inspect_registration(payload, anchor, "control", uv=uv)
    assert result.returncode == 2 and '"ambiguous-anchor"' in result.stdout
    assert result.stderr.startswith("More than one")
    assert not root[0].exists()


def test_failed_incoming_construction_never_runs_reader(incoming, monkeypatch):
    module, anchor, uv, payload = incoming
    root = []

    @contextmanager
    def failed(path, *args, **kwargs):
        root.append(path)
        raise RuntimeError("actual incoming health failure")
        yield

    monkeypatch.setattr(module.engine, "numbered_candidate", failed)
    monkeypatch.setattr(module.engine, "run_owned", lambda *args, **kwargs: pytest.fail("reader ran"))
    with pytest.raises(RuntimeError, match="incoming health"):
        module.inspect_registration(payload, anchor, "control", uv=uv)
    assert not root[0].exists()


def test_closed_source_retains_native_inputs_and_application_through_reader(incoming, monkeypatch):
    module, anchor, uv, payload = incoming
    snapshot = payload.parent / "prepared"
    identity = SimpleNamespace(package_version="1.2.3")
    selected = SimpleNamespace(
        payload=payload, identity=identity, project={"application": "application"},
    )
    inputs = SimpleNamespace(deadline=time.monotonic() + 600)
    held = []
    selection = {
        "extras": ["cli"], "local_packages": ["agent-worktrees"],
        "identity": {"runtime_source": "selected"},
    }

    @contextmanager
    def project_inputs(path, component, *, deadline):
        assert path == snapshot and component == "agent-worktrees"
        assert deadline > time.monotonic()
        held.append("inputs")
        yield selected, inputs
        assert held == ["inputs"]
        held.pop()

    monkeypatch.setitem(sys.modules, "install_source", SimpleNamespace(
        project_inputs=project_inputs, native_selection=lambda *args: selection,
        _member=lambda path, member: path / member,
    ))
    monkeypatch.setattr(module.sources, "read_source_identity", lambda *args, **kwargs: identity)

    @contextmanager
    def candidate(root, version, **options):
        assert held == ["inputs"]
        assert options["application"] == snapshot / "application"
        assert options["wheel_inputs"] is inputs
        assert options["local_packages"] == ("agent-worktrees",)
        assert options["expected_source_identity"] == selection["identity"]
        held.append("candidate")
        yield SimpleNamespace(
            slot=root / "versions" / version, frozen_plugin=payload,
            payload_hash="a" * 64, descriptor=23,
        )
        assert held.pop() == "candidate"

    monkeypatch.setattr(module.engine, "numbered_candidate", candidate)
    monkeypatch.setattr(module.engine.runtime, "slot_python", lambda *args: Path(sys.executable))
    monkeypatch.setattr(module.engine, "_package_roots", lambda path: (path,))
    monkeypatch.setattr(module.engine, "_procutil", lambda inputs: None)

    def run(argv, **options):
        assert held == ["inputs", "candidate"]
        assert options["deadline"] == inputs.deadline
        return subprocess.CompletedProcess(argv, 0, '{"status":"ready"}\n', "")

    monkeypatch.setattr(module.engine, "run_owned", run)
    assert module.inspect_registration(
        payload, anchor, "control", uv=uv, install_source=snapshot,
    ).returncode == 0
    assert held == []


@pytest.mark.parametrize("invalid", ["relative-anchor", "missing-anchor", "relative-uv", "missing-uv"])
def test_invalid_preparation_inputs_fail_before_construction(incoming, monkeypatch, invalid):
    module, anchor, uv, payload = incoming
    monkeypatch.setattr(module.engine, "numbered_candidate", lambda *args, **kwargs: pytest.fail("constructed"))
    if invalid == "relative-anchor":
        anchor = Path("relative")
    elif invalid == "missing-anchor":
        anchor = anchor / "missing"
    elif invalid == "relative-uv":
        uv = Path("uv")
    else:
        uv = uv.with_name("missing")
    with pytest.raises(ValueError):
        module.inspect_registration(payload, anchor, "control", uv=uv)


def test_bootstrap_typo_or_unsupported_contract_never_constructs(incoming, monkeypatch):
    module, anchor, uv, _payload = incoming
    monkeypatch.setattr(
        module, "inspect_registration", lambda *args, **kwargs: pytest.fail("constructed"),
    )
    for extra in (["--misspelled"], ["--contract-version", "2"]):
        with pytest.raises(SystemExit) as result:
            module.main(["--anchor", str(anchor), "--default-name", "control", "--uv", str(uv), *extra])
        assert result.value.code == 2
