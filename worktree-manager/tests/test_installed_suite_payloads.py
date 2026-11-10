"""Source controls; these do not qualify native suite installation."""
from __future__ import annotations

import json
from pathlib import Path
import runpy
import shutil
import sys
from types import SimpleNamespace

import pytest

_SUITE = runpy.run_path(
    str(Path(__file__).parent / "native" / "manager_installed_suite_payloads.py"),
)
_COMMIT = "a" * 40


def _artifact(tmp_path):
    root = tmp_path / "artifact"
    catalog = {
        "name": "example-marketplace",
        "plugins": [
            {"name": name, "source": "plugins/" + name, "version": "1.0"}
            for name in ("agent-worktrees", "agent-machines", "example-guidance")
        ],
    }
    (root / ".github" / "plugin").mkdir(parents=True)
    (root / ".github" / "plugin" / "marketplace.json").write_text(json.dumps(catalog))
    for row in catalog["plugins"]:
        payload = root / "plugins" / row["name"]
        payload.mkdir(parents=True)
        (payload / "plugin.json").write_text(json.dumps({
            "name": row["name"], "version": "1.0",
            "runtimeScope": "none" if row["name"] == "example-guidance" else "universal",
        }))
        (payload / "README.md").write_text("Frozen payload bytes\n")
    (root / "plugins" / "agent-machines" / "MATERIALIZED.json").write_text("{}")
    (root / "worktree-manager").mkdir()
    (root / "worktree-manager" / "pyproject.toml").write_text('[project]\nversion="1.0"\n')
    _seal(root)
    return root


def _seal(root):
    provenance = _SUITE["_PROVENANCE"]
    (root / provenance["RECEIPT"]).write_text(json.dumps({
        "schema": provenance["SCHEMA"], "version": 1, "source_commit": _COMMIT,
        "paths": provenance["content_manifest"](root),
    }))


@pytest.mark.parametrize("defect", ["wrong-commit", "changed-bytes", "missing-payload", "unsafe-source"])
def test_suite_profile_refuses_incomplete_or_substituted_artifact(tmp_path, defect):
    artifact = _artifact(tmp_path)
    commit = _COMMIT
    if defect == "wrong-commit":
        commit = "b" * 40
    elif defect == "changed-bytes":
        (artifact / "plugins" / "agent-machines" / "README.md").write_text("Substitute")
    elif defect == "missing-payload":
        (artifact / "plugins" / "agent-machines" / "plugin.json").unlink()
    else:
        path = artifact / ".github" / "plugin" / "marketplace.json"
        catalog = json.loads(path.read_text())
        catalog["plugins"][-1]["source"] = "../outside"
        path.write_text(json.dumps(catalog))
        _seal(artifact)
    with pytest.raises((ValueError, FileNotFoundError)):
        _SUITE["selected_profile"](artifact, commit, {"stateless": True})


@pytest.mark.parametrize("defect", [None, "partial-discovery", "foreign-payload", "changed-payload",
                                   "changed-nested-receipt",
                                   "changed-artifact", "changed-cli", "reduced-settings", "command-failed"])
def test_suite_acquisition_requires_real_command_closure_and_private_selected_bytes(
    tmp_path, monkeypatch, defect,
):
    artifact = _artifact(tmp_path)
    config = {"project": "example", "stateless": True, "require_application_readiness": True}
    profile = _SUITE["selected_profile"](artifact, _COMMIT, config)
    project = tmp_path / "project"
    settings = project / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps(profile["settings"]))
    if defect == "reduced-settings":
        settings.write_text("{}")
    home = tmp_path / "home"
    copilot_home = home / ".copilot"
    copilot = tmp_path / "actual-cli.exe"
    copilot.write_bytes(b"fixture executable identity")
    calls = []

    def run(argv, environment, cwd, diagnostics, label, *, timeout):
        calls.append(argv)
        if defect == "command-failed":
            raise RuntimeError("actual command refused")
        if argv[0] == copilot:
            if argv[1:4] == ["plugin", "marketplace", "add"]:
                assert argv[-1] == artifact
                return ""
            if argv[1:] == ["plugin", "list", "--json"]:
                return "{}"
            name = argv[-1].split("@")[0]
            shutil.copytree(
                artifact / "plugins" / name,
                copilot_home / "installed-plugins" / "example-marketplace" / name,
            )
            return ""
        names = json.loads(argv[-1])
        selected = {
            name: str(copilot_home / "installed-plugins" / "example-marketplace" / name)
            for name in names
        }
        if defect == "partial-discovery":
            del selected["agent-machines"]
        elif defect == "foreign-payload":
            selected["agent-machines"] = str(artifact / "plugins" / "agent-machines")
        elif defect == "changed-payload":
            (Path(selected["agent-machines"]) / "README.md").write_text("Substitute")
        elif defect == "changed-nested-receipt":
            (Path(selected["agent-machines"]) / "MATERIALIZED.json").write_text("Substitute")
        elif defect == "changed-artifact":
            (artifact / "plugins" / "agent-machines" / "README.md").write_text("Substitute")
        elif defect == "changed-cli":
            copilot.write_bytes(b"replacement CLI")
        return json.dumps(selected)

    monkeypatch.setitem(sys.modules, "manager_suite_fixture", SimpleNamespace(require_venue=lambda: None))
    arguments = dict(
        repository_config=config, copilot=copilot, engine=tmp_path / "engine.exe",
        environment={"USERPROFILE": str(home), "COPILOT_HOME": str(copilot_home)},
        project=project, diagnostics=tmp_path, run=run,
    )
    if defect:
        with pytest.raises((ValueError, RuntimeError)):
            _SUITE["install_payload_closure"](artifact, _COMMIT, **arguments)
        if defect == "reduced-settings":
            assert calls == []
        if defect == "command-failed":
            assert calls == [[copilot, "plugin", "marketplace", "add", artifact]]
    else:
        result = _SUITE["install_payload_closure"](artifact, _COMMIT, **arguments)
        assert calls[0] == [copilot, "plugin", "marketplace", "add", artifact]
        assert [argv[1:] for argv in calls[1:-2]] == [
            ["plugin", "install", name + "@example-marketplace"]
            for name in sorted(profile["inventory"])
        ]
        assert calls[-2] == [copilot, "plugin", "list", "--json"]
        assert set(result["payloads"]) == set(profile["inventory"])
        assert result["profile"]["repository_config"]["stateless"] is True
        assert result["runtime_installation"] == "not-observed"
