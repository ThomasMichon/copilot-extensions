"""Actor-free consumer controls for callback and qualification clock ownership."""
from __future__ import annotations

import ast
from contextlib import nullcontext
from dataclasses import dataclass
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any, Callable, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs" / "installer-engine"))
import runtime_application_install as application


@dataclass(frozen=True)
class ClockEvidence:
    deadline: float
    clock: list[float]

    def verify(self) -> None:
        if self.clock[0] >= self.deadline:
            raise TimeoutError("wheel evidence deadline expired")


@pytest.mark.parametrize("scenario", ["legacy", "extended", "qualification", "changed"])
def test_qualification_and_post_callback_evidence_have_separate_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str,
) -> None:
    slot = tmp_path / "versions" / "1.0"
    runtime_path, bootstrap = slot / "runtime", slot / ".runtime-bootstrap"
    runtime_path.mkdir(parents=True)
    bootstrap.mkdir()
    for name in (
        "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_source.py", "versioned_runtime_construction.py",
        "versioned_runtime_publication.py", "versioned_runtime_cache.py",
        "daemon_instance_runtime.pyz",
    ):
        (runtime_path / name).write_bytes(b"fixture")
        (bootstrap / name).write_bytes(b"fixture")
    record: dict[str, Any] = {"module": "fixture_app", "setup": [], "start": ["serve"]}
    if scenario != "legacy":
        record["callback_timeout_seconds"] = {"start": 900}
    declaration = json.dumps(record).encode()
    (slot / "application.json").write_bytes(declaration)
    (slot / "runtime-source.json").write_bytes(b"{}")
    (slot / ".runtime-input-origins.json").write_bytes(b"{}")
    clock = [100.0]
    monkeypatch.setattr(application, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    association = SimpleNamespace(
        inputs={"payload_hash": "fixture", "extras": [], "local_packages": [], "excluded_packages": []},
        source={"plugin": "fixture-app"}, source_bytes=b"{}", declaration=declaration,
        wheels=ClockEvidence(700, clock), receipt={}, origins={}, proof={},
    )
    runtime, engine = ModuleType("fixture_runtime"), ModuleType("fixture_engine")
    setattr(runtime, "construction_lease", lambda *_a, **_kw: nullcontext())
    setattr(runtime, "retention_lease", lambda *_a, **_kw: nullcontext())
    setattr(runtime, "check_admission", lambda *_a, **_kw: None)
    setattr(runtime, "is_complete", lambda *_a, **_kw: True)
    setattr(engine, "_refuse", lambda *_a, **_kw: None)
    observed: list[float] = []

    def verify(wheels: ClockEvidence, _slot: Path) -> None:
        observed.append(wheels.deadline)
        wheels.verify()

    def qualify(*_args: object, **kwargs: Any) -> None:
        if scenario == "qualification":
            clock[0] = 801
        cast(ClockEvidence, kwargs["wheel_inputs"]).verify()

    setattr(engine, "verify_application_wheels", verify)
    setattr(engine, "verify_installed_inputs", qualify)
    monkeypatch.setattr(application, "_resources", lambda _slot: (runtime, engine))
    monkeypatch.setattr(application, "_construction_inputs", lambda *_args: association)
    monkeypatch.setattr(application, "_inspect_application_wheels", lambda *_args: None)
    expected = TimeoutError if scenario in ("legacy", "qualification") else RuntimeError
    failure = pytest.raises(expected) if scenario != "extended" else nullcontext()
    with failure:
        with application._qualified(slot, record) as (_runtime, unchanged):
            clock[0] = 801
            if scenario == "changed":
                (slot / "application.json").write_bytes(b"{}")
            unchanged()
    if scenario == "extended":
        assert observed[0] == 700
        assert all(deadline == 1660 for deadline in observed[1:])
    elif scenario == "qualification":
        assert association.wheels.deadline == 700


@pytest.mark.parametrize("extend_base", [False, True])
def test_existing_ssh_receiver_accepts_the_actual_unchanged_base_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extend_base: bool,
) -> None:
    source = ROOT / "libs" / "installer-engine" / "install_source.py"
    declarations = {
        target.id: ast.literal_eval(node.value)
        for node in ast.parse(source.read_bytes()).body if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name)
        and target.id in {"APPLICATION_INSTALL_CONTRACT", "APPLICATION_INSTALL_CAPABILITIES"}
    }
    contract = declarations["APPLICATION_INSTALL_CONTRACT"]
    if extend_base:
        contract = {**contract, "capabilities": ["callback-timeouts/v1"]}
    owner = tmp_path / "owner"
    helper = owner / "_runtime_assets" / "scripts" / "install_source.py"
    helper.parent.mkdir(parents=True)
    helper.write_text(
        f"APPLICATION_INSTALL_CONTRACT = {contract!r}\n"
        f"APPLICATION_INSTALL_CAPABILITIES = {declarations['APPLICATION_INSTALL_CAPABILITIES']!r}\n"
        "def install_application():\n    pass\n",
    )
    receiver = ROOT / "plugins" / "agent-worktrees" / "src" / "agent_worktrees" / "repository_ssh_setup.py"
    body: list[ast.stmt] = [
        node for node in ast.parse(receiver.read_bytes()).body
        if isinstance(node, ast.FunctionDef) and node.name == "_installer"
    ]
    assert len(body) == 1

    def owner_path(filename: str) -> Path:
        assert filename == str(receiver)
        return owner / "repository_ssh_setup.py"

    namespace: dict[str, Any] = {
        "Path": owner_path, "importlib": importlib, "sys": sys,
        "__name__": "unit_ssh_contract_receiver", "__file__": str(receiver),
        "protocol": SimpleNamespace(_plain=lambda _path: None, SetupError=RuntimeError),
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), str(receiver), "exec"), namespace)
    monkeypatch.setitem(
        sys.modules, "_repository_ssh_source_installer", ModuleType("prior_source_installer"),
    )
    probe = cast(Callable[[], ModuleType], namespace["_installer"])
    if extend_base:
        with pytest.raises(RuntimeError, match="prepared-application-install v1"):
            probe()
    else:
        assert Path(probe().__file__ or "") == helper


@pytest.mark.parametrize("support", ["current", "old", "malformed"])
def test_publication_selection_reads_actual_helper_without_executing(
    tmp_path: Path, support: str,
) -> None:
    helper = tmp_path / "install.py"
    helper.write_text(
        "APPLICATION_INSTALL_CAPABILITIES = (\n" if support == "malformed"
        else "pass\n" if support == "old"
        else "APPLICATION_INSTALL_CAPABILITIES = ('global-bin/v1',)\n"
             "raise AssertionError('metadata discovery must not execute helpers')\n",
    )
    if support == "current":
        application.require_publication_selection(helper)
    else:
        with pytest.raises(ValueError, match="selected application helper lacks global-bin/v1"):
            application.require_publication_selection(helper)


def test_publication_cli_keeps_callback_separator_and_refuses_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def install(_slot: Path, **options: Any) -> dict[str, str]:
        calls.append(options)
        return {"state": "installed"}

    monkeypatch.setattr(application, "install", install)
    selected = tmp_path / "commands"
    assert application.main([
        "--global-bin", str(selected), "--", "--global-bin", "callback-value",
    ]) == 0
    assert calls == [{
        "stage": False, "global_bin": selected,
        "arguments": ("--global-bin", "callback-value"),
    }]
    with pytest.raises(SystemExit) as error:
        application.main(["--global-bin", str(selected), "--stage"])
    assert error.value.code == 2 and len(calls) == 1
    assert not selected.exists()


@pytest.mark.parametrize("generation", ["2.0", "1.0"])
def test_publication_directory_preserves_current_and_retained_slots(
    tmp_path: Path, generation: str,
) -> None:
    directory = tmp_path / "versions" / generation
    directory.mkdir(parents=True)
    launcher = directory / "fixture-app.sh"
    launcher.write_bytes(b"owned slot-local launcher\n")
    with pytest.raises(ValueError, match="cannot overlap runtime version slots"):
        application.publication_directory(directory, root=tmp_path)
    assert launcher.read_bytes() == b"owned slot-local launcher\n"
    assert application.publication_directory(tmp_path / "commands", root=tmp_path) == tmp_path / "commands"
