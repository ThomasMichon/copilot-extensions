"""Ready-only assembly delegates slot judgment to the existing runtime owner."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from typing import Any

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
from configuration_context.readiness import (  # noqa: E402
    CONTRACT_FILE, PROBE_CONTRACT, PROBE_ENV,
    ProbeContractError, payload_contract, runtime_contract, runtime_contract_path,
)


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


def _probe_contract(path: Path, **fields: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": "copilot-extensions.readiness-isolation", "version": 1,
        "plugin": "agent-worktrees", "module": "agent_worktrees", **fields,
    }), encoding="utf-8")
    return path


@pytest.mark.parametrize("layout", ["bin/python", "Scripts/python.exe", ".venv/bin/python"])
def test_probe_contract_uses_exact_selected_environment(tmp_path: Path, layout: str) -> None:
    payload, root = tmp_path / "new-payload", tmp_path / "runtime"
    _probe_contract(payload / CONTRACT_FILE)
    _probe_contract(root / "deploy-manifest.json")
    _probe_contract(root / runtime_contract_path("agent_worktrees"))
    selected = root / "versions" / "old" / layout
    selected.parent.mkdir(parents=True)
    selected.touch()
    assert payload_contract(payload, "agent-worktrees").module == "agent_worktrees"
    with pytest.raises(ProbeContractError, match="unqualified"):
        runtime_contract(selected, "agent_worktrees")
    descriptor = _probe_contract(
        selected.parent.parent / runtime_contract_path("agent_worktrees"), futureProperty=True,
    )
    admitted = runtime_contract(selected, "agent_worktrees", plugin="agent-worktrees")
    assert admitted.path == descriptor
    assert len(admitted.sha256) == 64
    with pytest.raises(ProbeContractError, match="unqualified"):
        runtime_contract(selected, "agent_containers")
    with pytest.raises(ProbeContractError, match="unqualified"):
        payload_contract(payload, "agent-containers")


@pytest.mark.parametrize("value", [
    '{"schema":"copilot-extensions.readiness-isolation","version":true,'
    '"plugin":"agent-worktrees","module":"agent_worktrees"}',
    '{"schema":"copilot-extensions.readiness-isolation","version":1,"version":1,'
    '"plugin":"agent-worktrees","module":"agent_worktrees"}',
    '{"schema":"copilot-extensions.readiness-isolation","version":2,'
    '"plugin":"agent-worktrees","module":"agent_worktrees"}',
    "[]", "not-json", " " * 4097,
])
def test_probe_contract_refuses_unknown_support_without_execution(tmp_path: Path, value: str) -> None:
    (tmp_path / CONTRACT_FILE).write_text(value, encoding="utf-8")
    with pytest.raises(ProbeContractError, match="update the owning installation"):
        payload_contract(tmp_path, "agent-worktrees")


def test_probe_contract_refuses_linked_descriptor(tmp_path: Path) -> None:
    source = _probe_contract(tmp_path / "source.json")
    try:
        (tmp_path / CONTRACT_FILE).symlink_to(source)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ProbeContractError, match="unqualified"):
        payload_contract(tmp_path, "agent-worktrees")


@pytest.mark.parametrize("module", ["../agent_worktrees", "agent_worktrees/child", "", None])
def test_runtime_contract_path_refuses_non_module_names(module: Any) -> None:
    with pytest.raises(ProbeContractError, match="qualified provider module"):
        runtime_contract_path(module)


def test_probe_prefix_qualification_does_not_block_ordinary_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "versions" / "old" / "bin" / "python"
    monkeypatch.delenv(PROBE_ENV, raising=False)
    assert ready_owner_prefix(tmp_path, "agent_worktrees", resolver=lambda _: selected)
    monkeypatch.setenv(PROBE_ENV, PROBE_CONTRACT)
    with pytest.raises(ProbeContractError, match="unqualified"):
        ready_owner_prefix(tmp_path, "agent_worktrees", resolver=lambda _: selected)
    _probe_contract(selected.parent.parent / runtime_contract_path("agent_worktrees"))
    assert ready_owner_prefix(tmp_path, "agent_worktrees", resolver=lambda _: selected)
    monkeypatch.setenv(PROBE_ENV, "future-contract")
    with pytest.raises(ProbeContractError, match="Unsupported"):
        ready_owner_prefix(tmp_path, "agent_worktrees", resolver=lambda _: selected)


@pytest.mark.parametrize("layout", ["retained", "nested-retained", "payload", "mutable"])
def test_probe_resolver_is_qualified_before_loading_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str,
) -> None:
    from configuration_context.readiness import qualify_resolver

    prefix = tmp_path / "slot"
    if layout == "nested-retained":
        prefix /= ".venv"
    python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    root = prefix.parent if layout == "nested-retained" else prefix
    if layout in {"retained", "nested-retained"}:
        resolver = root / ".runtime-bootstrap" / "versioned_runtime.py"
        descriptor = prefix / runtime_contract_path("agent_worktrees")
    else:
        resolver = tmp_path / "payload" / ("scripts" if layout == "payload" else "bin") / "versioned_runtime.py"
        descriptor = resolver.parent.parent / CONTRACT_FILE
    resolver.parent.mkdir(parents=True)
    resolver.write_text("raise AssertionError('must never import to qualify')\n", encoding="utf-8")
    monkeypatch.delenv(PROBE_ENV, raising=False)
    qualify_resolver(resolver, "agent-worktrees", "agent_worktrees", python=python)
    monkeypatch.setenv(PROBE_ENV, PROBE_CONTRACT)
    with pytest.raises(ProbeContractError):
        qualify_resolver(resolver, "agent-worktrees", "agent_worktrees", python=python)
    _probe_contract(descriptor)
    if layout == "mutable":
        with pytest.raises(ProbeContractError, match="no qualified provenance"):
            qualify_resolver(resolver, "agent-worktrees", "agent_worktrees", python=python)
    else:
        qualify_resolver(resolver, "agent-worktrees", "agent_worktrees", python=python)
        with pytest.raises(ProbeContractError):
            qualify_resolver(resolver, "foreign-plugin", "agent_worktrees", python=python)


@pytest.mark.parametrize("entry", ["prefix", "payload-bootstrap", "nested-payload-bootstrap"])
def test_native_owner_read_preserves_package_and_startup_bytecode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str,
) -> None:
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
    if entry == "nested-payload-bootstrap":
        for package in ("configuration_context", "configuration_layers"):
            shutil.copytree(SOURCE / package, packages / package,
                            ignore=shutil.ignore_patterns("__pycache__"))
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
    installed_marker = tmp_path / "installed-module-executed"
    (module / "__main__.py").write_text(
        (
            "from configuration_context import ready_owner_prefix\n"
            "from configuration_layers import ConfigurationError\n"
            "from pathlib import Path\n"
            "try:\n"
            f"    ready_owner_prefix(Path({str(tmp_path)!r}), 'agent_worktrees', "
            f"resolver=lambda _: Path({str(tmp_path / 'old-peer' / 'bin' / 'python')!r}))\n"
            "except (ConfigurationError, ValueError):\n"
            "    raise RuntimeError('nested compatibility refusal lost its type')\n"
            if entry == "nested-payload-bootstrap" else ""
        ) +
        "import json, os, sys\nfrom . import payload\n"
        "from pathlib import Path\n"
        f"Path({str(installed_marker)!r}).touch()\n"
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
    monkeypatch.setenv(PROBE_ENV, PROBE_CONTRACT)
    with pytest.raises(ProbeContractError, match="unqualified"):
        ready_owner_prefix(tmp_path / "owner", "configuration_owner_fixture",
                           resolver=lambda _: python)
    assert not marker.exists()
    if entry != "prefix":
        from configuration_context import readiness

        payload_root = tmp_path / "new-payload"
        script = payload_root / "scripts" / "versioned_runtime_probe.py"
        script.parent.mkdir(parents=True)
        script.write_bytes(Path(readiness.__file__).read_bytes())
        _probe_contract(
            payload_root / CONTRACT_FILE,
            plugin="configuration-owner-fixture", module="configuration_owner_fixture",
        )
        prefix = [
            str(python), "-I", "-B", "-X", "utf8", str(script),
            "--plugin", "configuration-owner-fixture",
            "--module", "configuration_owner_fixture", "--",
        ]
        result = subprocess.run(
            [*prefix, "state-root", "--configuration", "--json"],
            cwd=project, env=child_env, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 126, result.stdout + result.stderr
        assert "update the owning installation" in result.stderr
        assert not installed_marker.exists()
        assert not marker.exists()
    _probe_contract(
        environment / runtime_contract_path("configuration_owner_fixture"),
        plugin="configuration-owner-fixture", module="configuration_owner_fixture",
    )
    if entry == "prefix":
        prefix = ready_owner_prefix(tmp_path / "owner", "configuration_owner_fixture",
                                    resolver=lambda _: python)
    if entry == "nested-payload-bootstrap":
        with pytest.raises(ProbeContractError, match="update the owning installation"):
            read_owner_context(prefix, project, required=True, environment=child_env)
        assert not installed_marker.exists()
    else:
        assert read_owner_context(prefix, project, required=True, environment=child_env) == report
        assert installed_marker.is_file()
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


@pytest.mark.parametrize("returncode", [1, 126])
def test_only_typed_compatibility_refusal_propagates_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, returncode: int,
) -> None:
    from configuration_context.readiness import REFUSAL

    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, returncode, json.dumps(REFUSAL).encode(), b"private peer diagnostic",
        ),
    )
    expected = ProbeContractError if returncode == 126 else ConfigurationError
    with pytest.raises(expected) as error:
        read_owner_context(["qualified-owner"], tmp_path)
    assert "private peer diagnostic" not in str(error.value)


@pytest.mark.parametrize("selection", ["installed-script", "payload-script", "foreign-script"])
def test_native_script_probe_qualifies_before_executing_payload_code(
    tmp_path: Path, selection: str,
) -> None:
    from configuration_context import readiness

    environment = tmp_path / "installed"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    payload = tmp_path / "payload"
    entry = payload / "scripts" / "versioned_runtime_probe.py"
    entry.parent.mkdir(parents=True)
    entry.write_bytes(Path(readiness.__file__).read_bytes())
    _probe_contract(payload / CONTRACT_FILE)
    script = (tmp_path if selection == "foreign-script" else entry.parent) / "reader.py"
    marker = tmp_path / "payload-script-executed"
    script.write_text(
        "import os, sys\nfrom pathlib import Path\n"
        f"assert os.environ[{PROBE_ENV!r}] == {PROBE_CONTRACT!r}\n"
        "assert (sys.flags.isolated, sys.flags.dont_write_bytecode) == (1, 1)\n"
        f"Path({str(marker)!r}).touch()\n", encoding="utf-8",
    )
    arguments = [
        str(python), "-I", "-B", "-X", "utf8", str(entry),
        "--plugin", "agent-worktrees", "--module", "agent_worktrees", "--script", str(script),
    ]
    if selection != "installed-script":
        arguments.append("--payload-only")
    result = subprocess.run(
        arguments, capture_output=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if selection == "payload-script":
        assert result.returncode == 0, result.stderr
        assert marker.exists()
    else:
        assert result.returncode == 126, result.stderr
        assert readiness.unqualified_output(result.stdout)
        assert not marker.exists()
    if selection == "installed-script":
        _probe_contract(environment / runtime_contract_path("agent_worktrees"))
        admitted = subprocess.run(
            arguments, capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert admitted.returncode == 0, admitted.stderr
        assert marker.exists()


def test_native_origin_probe_qualifies_without_importing_selected_package(tmp_path: Path) -> None:
    from configuration_context import readiness

    environment = tmp_path / "installed"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import site; print(next(p for p in site.getsitepackages() if p.endswith('site-packages')))"],
        capture_output=True, text=True, timeout=15, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    package = Path(site.stdout.strip()) / "agent_worktrees"
    package.mkdir()
    marker = tmp_path / "package-executed"
    origin = package / "__init__.py"
    origin.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8",
    )
    payload = tmp_path / "payload"
    entry = payload / "scripts" / "versioned_runtime_probe.py"
    entry.parent.mkdir(parents=True)
    entry.write_bytes(Path(readiness.__file__).read_bytes())
    _probe_contract(payload / CONTRACT_FILE)
    arguments = [
        str(python), "-I", "-B", "-X", "utf8", str(entry),
        "--plugin", "agent-worktrees", "--module", "agent_worktrees", "--origin",
    ]
    for qualified in (False, True):
        if qualified:
            _probe_contract(environment / runtime_contract_path("agent_worktrees"))
        result = subprocess.run(
            arguments, capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if qualified:
            assert result.returncode == 0, result.stderr
            assert Path(result.stdout.decode().strip()) == origin
        else:
            assert result.returncode == 126, result.stderr
            assert readiness.unqualified_output(result.stdout)
        assert not marker.exists()
    assert not list(environment.rglob("*.pyc"))


def test_standalone_payloads_ship_the_same_canonical_reader() -> None:
    spec = importlib.util.spec_from_file_location(
        "configuration_payload_sync", ROOT / "tools" / "sync-configuration-layers.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for canonical, delivered in module.vendor_pairs():
        assert delivered.read_bytes() == canonical.read_bytes()
