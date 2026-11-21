"""Provider selection is user state, not permission to mutate a completed runtime."""

from __future__ import annotations

from collections.abc import Callable
import json
import importlib.util
from importlib import import_module
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from typing import Any

import pytest
from agent_procutil import no_window_kwargs


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import installer_provider_roots

    return installer_provider_roots


@pytest.fixture
def vault_profile(monkeypatch: pytest.MonkeyPatch, providers: ModuleType) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "runtime_profile.py"
    spec = importlib.util.spec_from_file_location("vault_provider_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "runtime_profile", module)
    spec.loader.exec_module(module)
    return module


def test_provider_desire_survives_core_reconstruction(providers: ModuleType, tmp_path: Path) -> None:
    assert providers.read(tmp_path) is None
    providers.persist(tmp_path, ["Example_Provider[Feature_B,feature-a]"])
    path = tmp_path / providers.FILE
    before = path.read_bytes(), path.stat().st_mtime_ns
    for version in ("1.2.3", "1.2.4"):
        roots = [f"agent-vault[ssh]=={version}", "example-provider[feature-a,feature-b]==0.1.0"]
        receipt = {"binding": {"roots": roots, "wheels": [
            {"name": "agent-vault", "selected_extras": ["ssh"]},
            {"name": "example-provider", "selected_extras": ["feature-a", "feature-b"]},
        ]}}
        providers.check_construction(tmp_path, receipt, core="agent-vault")
        providers.check_construction(
            tmp_path, {"binding": {"profile": receipt["binding"]}}, core="agent-vault",
        )
    providers.persist(tmp_path, ["example-provider[feature-b,feature-a]"])
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    with pytest.raises(ValueError, match="complete native wheel profile"):
        providers.check_construction(tmp_path, None, core="agent-vault")
    for roots in (["agent-vault==1.2.4"], ["agent-vault", "example-provider[feature-a]"]):
        with pytest.raises(ValueError, match="changes selected"):
            providers.check_construction(
                tmp_path, {"binding": {**receipt["binding"], "roots": roots}}, core="agent-vault",
            )
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("roots", [
    "example-provider", ["provider", "Provider"], ["provider[x,x]"], ["provider==1"],
    ["provider; os_name=='nt'"], ["provider @ https://example.invalid/a.whl"],
])
def test_invalid_desire_is_rejected(providers: ModuleType, tmp_path: Path, roots: object) -> None:
    with pytest.raises(ValueError):
        providers.persist(tmp_path, roots)
    assert not (tmp_path / providers.FILE).exists()


def test_malformed_desire_is_not_replaced(providers: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / providers.FILE
    body = '{"schema":"copilot-extensions.runtime-provider-roots","version":1,"roots":[],"roots":["a"]}'
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        providers.persist(tmp_path, [])
    assert path.read_text(encoding="utf-8") == body


def test_explicit_removal_does_not_change_runtime(providers: ModuleType, tmp_path: Path) -> None:
    marker = tmp_path / "current-version"
    marker.write_text("retained-generation", encoding="utf-8")
    providers.persist(tmp_path, ["example-provider"])
    providers.persist(tmp_path, [])
    providers.check_construction(tmp_path, None, core="agent-vault")
    providers.check_construction(
        tmp_path, {"binding": {"roots": ["agent-vault==2"], "wheels": []}}, core="agent-vault",
    )
    with pytest.raises(ValueError, match="changes selected"):
        providers.check_construction(
            tmp_path, {"binding": {"roots": ["agent-vault", "example-provider"], "wheels": [
                {"name": "example-provider", "selected_extras": []},
            ]}},
            core="agent-vault",
        )
    assert marker.read_text(encoding="utf-8") == "retained-generation"


def test_constructor_refuses_provider_loss_before_creating_candidate(
    providers: ModuleType, tmp_path: Path,
) -> None:
    import installer_engine as engine
    import sys

    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "pyproject.toml").write_text(
        '[project]\nname="agent-vault"\nversion="1.2.4"\n', encoding="utf-8",
    )
    root = tmp_path / "runtime"
    providers.persist(root, ["example-provider"])
    original = (root / providers.FILE).read_bytes()
    with pytest.raises(ValueError, match="complete native wheel profile"):
        with engine.numbered_candidate(
            root, "1.2.4", plugin=payload, uv=Path("unused-uv"),
            base_python=Path(sys.executable), health_module="agent_vault",
        ):
            pytest.fail("unqualified candidate escaped constructor admission")
    assert sorted(path.name for path in root.iterdir()) == [providers.FILE]
    assert (root / providers.FILE).read_bytes() == original


def test_currentness_does_not_hide_an_unapplied_provider_selection(
    providers: ModuleType, vault_profile: ModuleType, tmp_path: Path,
) -> None:
    import runtime_source_target as target

    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "pyproject.toml").write_text(
        '[project]\nname="agent-vault"\nversion="1.2.4"\n', encoding="utf-8",
    )
    root = tmp_path / "runtime"
    slot = root / "versions" / "1.2.4"
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"interpreter metadata fixture")
    (root / "current-version").write_text("1.2.4", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(
        json.dumps({"source": {"version": "1.2.4"}}), encoding="utf-8",
    )
    assert target.current(root, payload, "1.2.4")
    providers.persist(root, [])
    assert target.current(root, payload, "1.2.4")
    providers.persist(root, ["example-provider"])
    assert not target.current(root, payload, "1.2.4")
    origins = slot / ".runtime-input-origins.json"
    origins.write_text(json.dumps({
        "wheel_inputs": {"binding": {"roots": ["agent-vault==1.2.4"], "wheels": []}},
    }), encoding="utf-8")
    assert not target.current(root, payload, "1.2.4")
    origins.write_text(json.dumps({
        "wheel_inputs": {"binding": {
            "roots": ["agent-vault==1.2.4", "example-provider==0.1"],
            "wheels": [{"name": "example-provider", "selected_extras": []}],
        }},
    }), encoding="utf-8")
    assert target.current(root, payload, "1.2.4")
    providers.persist(root, [])
    assert not target.current(root, payload, "1.2.4")
    origins.unlink()
    assert target.current(root, payload, "1.2.4")


def test_provider_only_selection_bypasses_unchanged_base_fast_exit(tmp_path: Path) -> None:
    assert __package__ is not None
    bootstrap = import_module(".test_bootstrap_driver", __package__)
    fixtures = import_module(".test_bootstrap_check_reconcile_always_on", __package__)
    make_plugin: Callable[[Path, str], Path] = fixtures._make_fake_plugin
    make_install: Callable[[Path, str], Path] = fixtures._make_fake_install
    clean_env: Callable[[dict[str, str]], dict[str, str]] = fixtures._clean_env
    native_command: Callable[[Path, Path, Path], list[str]] = bootstrap.native_bootstrap
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    plugin = make_plugin(tmp_path, "agent-vault")
    installation = make_install(home, "agent-vault")
    (installation / ".venv").mkdir()
    (installation / "deploy-manifest.json").write_text(
        '{"source":{"version":"1.0.1"}}', encoding="utf-8",
    )
    (installation / "runtime-provider-roots.json").write_text(
        '{"schema":"copilot-extensions.runtime-provider-roots","version":1,"roots":["example-provider"]}',
        encoding="utf-8",
    )
    (plugin / "scripts" / "runtime_profile.py").write_text(
        "import pathlib,sys\n"
        "assert sys.argv[-3:] == ['current','--package-version','1.0.1']\n"
        "pathlib.Path(sys.argv[2], 'provider-inspected').write_text('yes')\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    env = clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })
    result = subprocess.run(
        native_command(plugin, project, home), env=env, cwd=project,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0 and json.loads(result.stdout) == {}, result.stderr
    assert (installation / "provider-inspected").read_text() == "yes"
    assert (project / "handoff").read_text(encoding="utf-8-sig").splitlines()[4] == "provision"


def test_provider_cli_requires_explicit_persistence_and_excludes_core(
    providers: ModuleType, vault_profile: ModuleType, tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = vault_profile
    arguments = ["--root", str(tmp_path), "providers"]
    assert module.main(arguments + ["--roots", '["example-provider"]']) == 0
    assert providers.read(tmp_path) is None
    assert module.main(arguments + ["--roots", '["example-provider"]', "--persist"]) == 0
    assert providers.read(tmp_path) == ["example-provider"]
    assert module.main(arguments) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {"roots": ["example-provider"]}
    with pytest.raises(ValueError, match="owning core"):
        module.main(arguments + ["--roots", '["agent-vault"]', "--persist"])
    assert providers.read(tmp_path) == ["example-provider"]


def test_inactive_roots_and_unselected_extras_do_not_satisfy_provider_desire(
    providers: ModuleType, tmp_path: Path,
) -> None:
    providers.persist(tmp_path, ["example-provider"])
    profile: dict[str, Any] = {
        "roots": ['agent-vault', 'example-provider; python_version < "3.0"'],
        "wheels": [{"name": "agent-vault", "selected_extras": []}],
    }
    active = providers.from_profile(profile, core="agent-vault")
    assert active == []
    with pytest.raises(ValueError, match="changes selected"):
        providers.select(tmp_path, active, write=True)
    with pytest.raises(ValueError, match="changes selected"):
        providers.check_construction(tmp_path, {"binding": profile}, core="agent-vault")
    assert providers.read(tmp_path) == ["example-provider"]
    profile["roots"] = ["agent-vault", "example-provider[feature]"]
    profile["wheels"].append({"name": "example-provider", "selected_extras": []})
    assert providers.from_profile(profile, core="agent-vault") == []
    with pytest.raises(ValueError, match="changes selected"):
        providers.check_construction(tmp_path, {"binding": profile}, core="agent-vault")


def test_repeated_profile_roots_union_only_inspected_extras(providers: ModuleType) -> None:
    profile: dict[str, Any] = {
        "roots": [
            "agent-vault", "example-provider[a]==1", "Example_Provider[b]==1",
            'example-provider[inactive]; python_version < "3.0"',
        ],
        "wheels": [{"name": "example-provider", "selected_extras": ["a", "b"]}],
    }
    assert providers.from_profile(profile, core="agent-vault") == ["example-provider[a,b]"]
    profile["roots"] = ["example-provider==1; os_name=='nt'", "example-provider==1; os_name!='nt'"]
    profile["wheels"][0]["selected_extras"] = []
    assert providers.from_profile(profile, core="agent-vault") == []


@pytest.mark.parametrize("marker", ['python_version < "3.0"', 'python_version >= "3.0"'])
def test_conditional_transitive_roots_do_not_add_unconditional_provider_intent(
    providers: ModuleType, tmp_path: Path, marker: str,
) -> None:
    profile: dict[str, Any] = {
        "roots": ["agent-vault", "example-provider", "core-dependency; " + marker],
        "wheels": [
            {"name": name, "selected_extras": []}
            for name in ("agent-vault", "example-provider", "core-dependency")
        ],
    }
    providers.persist(tmp_path, ["example-provider"])
    roots = providers.from_profile(profile, core="agent-vault")
    assert roots == ["example-provider"]
    providers.select(tmp_path, roots, write=True)
    assert providers.read(tmp_path) == roots
