"""Provider admission does not select or activate an application."""
import time
import ast
import json
import os
from pathlib import Path
import stat
import sys
import sysconfig
import tomllib
from types import SimpleNamespace
import venv

import pytest

from worktree_manager import runtime_application_inputs as provider


@pytest.fixture
def provider_files(tmp_path, monkeypatch):
    root = tmp_path / "package" / "_runtime_assets" / "scripts" / "runtime-input-provider"
    tools = root / "tools"
    tools.mkdir(parents=True)
    source = Path(__file__).resolve().parents[2] / "tools" / "manager_application_provider.json"
    declaration = tools / source.name
    declaration.write_bytes(source.read_bytes())
    entry = tools / "prepare_manager_application.py"
    entry.write_text("raise AssertionError('linked entry executed')\n", encoding="utf-8")
    monkeypatch.setattr(provider, "__file__", str(root.parents[2] / "runtime_application_inputs.py"))
    return root, tools, declaration, entry


@pytest.mark.parametrize("target", ["tools", "declaration", "entry"])
def test_provider_refuses_descendant_reparse_metadata_before_use(
    provider_files, tmp_path, monkeypatch, target,
):
    root, tools, declaration, entry = provider_files
    selected = {"tools": tools, "declaration": declaration, "entry": entry}[target]
    original = Path.lstat

    def reparse(path, *args, **kwargs):
        observed = original(path, *args, **kwargs)
        if path == selected:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR if target == "tools" else stat.S_IFREG,
                st_file_attributes=getattr(observed, "st_file_attributes", 0) | 0x400,
            )
        return observed

    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(ValueError, match="must not be linked"):
        if target != "entry":
            provider.provider_root()
        else:
            executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
            provider.prepare_application_inputs(
                tmp_path, tmp_path / "output", identity={}, python=executable,
                tooling_python=executable, uv=executable, deadline=time.monotonic() + 10,
            )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("target", ["tools", "declaration", "entry"])
def test_provider_refuses_actual_descendant_links(provider_files, tmp_path, target):
    root, tools, declaration, entry = provider_files
    selected = {"tools": tools, "declaration": declaration, "entry": entry}[target]
    retained = tmp_path / ("retained-" + target)
    selected.rename(retained)
    try:
        selected.symlink_to(retained, target_is_directory=target == "tools")
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) in (5, 1314):
            pytest.skip("Windows link creation unavailable; portable reparse-admission controls still run")
        raise
    with pytest.raises(ValueError, match="must not be linked"):
        if target != "entry":
            provider.provider_root()
        else:
            executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
            provider.prepare_application_inputs(
                tmp_path, tmp_path / "output", identity={}, python=executable,
                tooling_python=executable, uv=executable, deadline=time.monotonic() + 10,
            )


def test_provider_child_receives_no_mixed_case_python_overrides(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(repo / "tools"))
    import materialize_launch_wrapper_assets

    package = tmp_path / "package"
    package.mkdir()
    report = materialize_launch_wrapper_assets.materialize_into(
        source_consumer_dir=repo / "worktree-manager",
        dest_consumer_dir=package / "_runtime_assets", canonical_root=repo, dest_root=package,
    )
    assert report and all(row.startswith("OK ") for row in report), report
    monkeypatch.setattr(provider, "__file__", str(package / "runtime_application_inputs.py"))
    root = provider.provider_root()
    entry = root / "tools" / "prepare_manager_application.py"
    canonical = entry.with_name("_canonical_prepare_manager_application.py")
    canonical.write_bytes(entry.read_bytes())
    # Observe the real child, then execute the untouched canonical worker and its admission.
    entry.write_text(
        "import json,os,pathlib,runpy,sys\n"
        "output=pathlib.Path(sys.argv[sys.argv.index('--output')+1])\n"
        "blocked={'PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','__PYVENV_LAUNCHER__'}\n"
        "(output.parent/'environment.json').write_text(json.dumps({\n"
        " 'blocked':[name for name in os.environ if name.upper() in blocked],\n"
        " 'kept':os.environ.get('PROVIDER_KEEP')}))\n"
        "runpy.run_path(str(pathlib.Path(__file__).with_name("
        "'_canonical_prepare_manager_application.py')),run_name='__main__')\n",
        encoding="utf-8",
    )
    tooling = tmp_path / "tooling"
    venv.EnvBuilder(with_pip=False, symlinks=False).create(tooling)
    executable = tooling / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = Path(sysconfig.get_paths(
        scheme="venv", vars={"base": str(tooling), "platbase": str(tooling)},
    )["purelib"])
    site.mkdir(parents=True, exist_ok=True)
    (site / "declared-test-tooling.pth").write_text(
        sysconfig.get_path("purelib") + "\n", encoding="utf-8",
    )
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"}
    }
    environment.update({
        "pYtHoNpAtH": str(tmp_path / "foreign"), "pYtHoNhOmE": str(tmp_path / "foreign"),
        "vIrTuAl_EnV": str(tmp_path / "foreign"),
        "__pYvEnV_lAuNcHeR__": str(tmp_path / "foreign"), "PROVIDER_KEEP": "preserved",
    })
    monkeypatch.setattr(provider.os, "environ", environment)
    output = tmp_path / "output"
    with pytest.raises(RuntimeError, match="install-source.json"):
        provider.prepare_application_inputs(
            tmp_path, output, identity={}, python=executable, tooling_python=executable,
            uv=executable, deadline=time.monotonic() + 20,
        )
    assert not output.exists()
    assert json.loads((tmp_path / "environment.json").read_text()) == {
        "blocked": [], "kept": "preserved",
    }


def test_provider_requester_preserves_normal_procutil_identity(provider_files, tmp_path, monkeypatch):
    import agent_procutil

    root, _tools, _declaration, _entry = provider_files
    output = tmp_path / "output"
    output.mkdir()
    identity = {"fixture": "admitted-source"}
    loads = []

    def run_owned(_command, **options):
        assert options["procutil"] is agent_procutil
        return SimpleNamespace(stdout=json.dumps({"snapshot": str(output), "identity": identity}))

    def load(path, namespace):
        loads.append(path)
        assert namespace.endswith("._process")
        return SimpleNamespace(run_owned=run_owned)

    monkeypatch.setattr(provider, "_load", load)
    executable = Path(sys.executable).resolve()
    record = provider.prepare_application_inputs(
        tmp_path, output, identity=identity, python=executable, tooling_python=executable,
        uv=executable, deadline=time.monotonic() + 10,
    )
    assert record["identity"] == identity
    assert loads == [root / "libs/installer-engine/installer_engine_process.py"]


@pytest.mark.parametrize("deadline", [True, float("nan"), 0])
def test_provider_refuses_invalid_or_expired_budget_before_resource_loading(
    tmp_path, monkeypatch, deadline,
):
    monkeypatch.setattr(provider, "provider_root", lambda: pytest.fail("invalid request loaded resources"))
    with pytest.raises(ValueError, match="deadline"):
        provider.prepare_application_inputs(
            tmp_path, tmp_path / "output", identity={}, python=tmp_path / "python",
            tooling_python=tmp_path / "tools-python", uv=tmp_path / "uv", deadline=deadline,
        )
    assert not (tmp_path / "output").exists()


def test_provider_does_not_substitute_path_for_missing_selected_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(provider, "provider_root", lambda: pytest.fail("missing executable loaded resources"))
    with pytest.raises(ValueError, match="executable is unavailable"):
        provider.prepare_application_inputs(
            tmp_path, tmp_path / "output", identity={}, python=tmp_path / "python",
            tooling_python=tmp_path / "tools-python", uv=tmp_path / "uv",
            deadline=time.monotonic() + 10,
        )


def test_missing_installed_provider_does_not_follow_checkout_tools(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "tools").mkdir(parents=True)
    (source / ".git").mkdir()
    (source / "tools" / "prepare_manager_application.py").write_text("raise AssertionError('foreign source')")
    monkeypatch.setattr(provider, "__file__", str(source / "worktree_manager" / "runtime_application_inputs.py"))
    with pytest.raises(ValueError, match="unavailable; repair authentic"):
        provider.provider_root()


def test_provider_refuses_parent_traversal_before_loading_resources(tmp_path, monkeypatch):
    monkeypatch.setattr(provider, "provider_root", lambda: pytest.fail("invalid output loaded resources"))
    with pytest.raises(ValueError, match="parent traversal"):
        provider.prepare_application_inputs(
            tmp_path, tmp_path / ".." / "outside", identity={}, python=tmp_path / "python",
            tooling_python=tmp_path / "tools-python", uv=tmp_path / "uv",
            deadline=time.monotonic() + 10,
        )


def test_provider_projection_carries_registered_primitives_and_package_resources():
    root = Path(__file__).resolve().parents[2]
    record = json.loads((root / "worktree-manager/packaged-assets.json").read_text(encoding="utf-8"))
    groups = {item["canonicalDir"]: set(item["files"]) for item in record["groups"]}

    def declaration(path, name):
        return next(
            ast.literal_eval(node.value) for node in ast.parse(path.read_text(encoding="utf-8")).body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
        )

    fragments = declaration(root / "libs/installation-context/installation_context.py", "_FRAGMENT_FILES")
    assert {"installation_context.py", *fragments} <= groups["libs/installation-context"]
    owner = root / "tools/installer_engine_ref.py"
    assert {
        *declaration(owner, "PYTHON_FILES"), *declaration(owner, "SUPPORT_FILES"),
        "runtime_application_install.py", "runtime_application_binstubs.py",
    } <= groups["libs/installer-engine"]
    package = root / "libs/agent-procutil"
    data = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))
    resources = data["tool"]["setuptools"]["package-data"]["agent_procutil"]
    assert {
        *(path.name for path in (package / "src/agent_procutil").glob("*.py")), *resources,
    } <= groups["libs/agent-procutil/src/agent_procutil"]
