"""Ready-only assembly delegates slot judgment to the existing runtime owner."""

from __future__ import annotations

import importlib.util
import os
import sys
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))
from configuration_context import (  # noqa: E402
    RepositoryContextRequired,
    read_owner_context,
    ready_owner_prefix,
)
from configuration_layers import ConfigurationError  # noqa: E402


def test_absent_runtime_uses_actual_read_only_resolver_without_creating_it(tmp_path: Path) -> None:
    path = ROOT / "libs" / "versioned-runtime" / "versioned_runtime.py"
    spec = importlib.util.spec_from_file_location("configuration_runtime_owner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    missing = tmp_path / "absent-owner"
    assert ready_owner_prefix(missing, "agent_worktrees", resolver=module.resolve_python) == []
    assert not missing.exists()


def test_ready_prefix_retains_qualified_interpreter_and_module(tmp_path: Path) -> None:
    selected = tmp_path / "versions" / "complete" / "python"
    roots: list[Path] = []

    def resolve(root: Path) -> Path:
        roots.append(root)
        return selected

    assert ready_owner_prefix(tmp_path, "agent_worktrees", resolver=resolve) == [
        str(selected), "-I", "-B", "-X", "utf8", "-m", "agent_worktrees",
    ]
    assert roots == [tmp_path]
    assert not selected.exists()


def test_readiness_failure_never_becomes_optional_absence(tmp_path: Path) -> None:
    def refuse(root: Path) -> Path:
        raise ValueError("unconfirmed selection")

    with pytest.raises(ConfigurationError, match="readiness read failed"):
        ready_owner_prefix(tmp_path, "agent_worktrees", resolver=refuse)
    with pytest.raises(ConfigurationError, match="relative interpreter"):
        ready_owner_prefix(tmp_path, "agent_worktrees", resolver=lambda root: Path("python"))


def test_native_owner_read_preserves_package_and_startup_bytecode(tmp_path: Path) -> None:
    environment = tmp_path / "owner" / ".venv"
    venv.EnvBuilder(with_pip=False).create(environment)
    if os.name == "nt":
        python = environment / "Scripts" / "python.exe"
        packages = environment / "Lib" / "site-packages"
    else:
        python = environment / "bin" / "python"
        packages = environment / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    module = packages / "configuration_owner_fixture"
    module.mkdir()
    (module / "__init__.py").write_text("", encoding="utf-8")
    (module / "payload.py").write_text("VALUE = 'native-owner'\n", encoding="utf-8")
    (packages / "startup_witness.py").write_text("VALUE = 'startup'\n", encoding="utf-8")
    (packages / "sitecustomize.py").write_text("import startup_witness\n", encoding="utf-8")
    project = tmp_path / "selected-project"
    project.mkdir()
    marker = tmp_path / "checkout-import-executed"
    for name in ("configuration_owner_fixture", "json", "startup_witness"):
        (project / f"{name}.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
            "raise RuntimeError('checkout module executed during configuration read')\n",
            encoding="utf-8",
        )
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "fixture-repo", "execution_machine": "fixture-worker",
            "installation_cell": "fixture-cell", "binding": "unbound",
            "base_root": str(project),
        },
    }
    (module / "__main__.py").write_text(
        "import json, os, sys\nfrom . import payload\n"
        "assert sys.argv[1:] == ['state-root', '--configuration', '--json']\n"
        "assert (sys.flags.isolated, sys.flags.dont_write_bytecode, sys.flags.utf8_mode) == (1, 1, 1)\n"
        "assert not sys.flags.no_site\n"
        f"assert os.getcwd() == {str(project)!r}\n"
        "assert payload.VALUE == 'native-owner'\n"
        f"print(json.dumps({report!r}))\n",
        encoding="utf-8",
    )
    before = {path.relative_to(packages): path.read_bytes()
              for path in packages.rglob("*") if path.is_file()}
    child_env = {key: value for key, value in os.environ.items()
                 if key not in {"PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH"}}
    child_env["PYTHONPATH"] = str(project)
    prefix = ready_owner_prefix(tmp_path / "owner", "configuration_owner_fixture",
                                resolver=lambda _: python)
    assert read_owner_context(prefix, project, required=True, environment=child_env) == report
    assert not marker.exists()
    assert not list(project.rglob("*.pyc"))
    after = {path.relative_to(packages): path.read_bytes()
             for path in packages.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_missing_repository_has_actionable_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: str,
) -> None:
    import subprocess

    output = {"stdout": b"", "stderr": b""}
    output[stream] = (
        b"Could not resolve a project for 'state-root'. "
        b"private-owner-detail-that-must-not-be-echoed"
    )
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, **output),
    )
    with pytest.raises(RepositoryContextRequired, match="Repository context is missing") as error:
        read_owner_context(["qualified-owner"], tmp_path)
    assert "<repo> <subject>" in str(error.value)
    assert "private-owner-detail" not in str(error.value)


def test_other_owner_refusals_do_not_claim_repository_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 126, b"", b"binding is unresolved",
        ),
    )
    with pytest.raises(ConfigurationError, match="refused the selected context") as error:
        read_owner_context(["qualified-owner"], tmp_path)
    assert not isinstance(error.value, RepositoryContextRequired)


def test_standalone_payloads_ship_the_same_canonical_reader() -> None:
    spec = importlib.util.spec_from_file_location(
        "configuration_payload_sync", ROOT / "tools" / "sync-configuration-layers.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for canonical, delivered in module.vendor_pairs():
        assert delivered.read_bytes() == canonical.read_bytes()
