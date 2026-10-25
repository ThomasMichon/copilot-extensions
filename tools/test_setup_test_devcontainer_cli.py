"""The shared Linux CI setup preserves locked seasoning before execution."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import setup_test_devcontainer_cli as setup


@pytest.mark.parametrize(
    "failure",
    [None, "registry", "audit", "install", "mutated", "version", "version-command"],
)
def test_locked_cli_admission_precedes_install_and_version_probe(tmp_path, monkeypatch, failure):
    source = tmp_path / "tools/devcontainer-cli"
    source.mkdir(parents=True)
    (source / "package.json").write_text(json.dumps({
        "dependencies": {"@devcontainers/cli": "0.80.0"},
    }))
    (source / "package-lock.json").write_text('{"fixture": true}')
    project = tmp_path / "installed"
    events = []

    def audit(lock, metadata, *, replace_default_registry):
        events.append("audit")
        assert lock == {"fixture": True} and replace_default_registry
        assert metadata("fixture", "1") == {"reviewed": True}
        if failure == "audit":
            raise ValueError("unseasoned dependency")
        return 1

    admission = SimpleNamespace(
        npm_command=lambda: ["node", "npm"],
        audit_lock=audit,
        read_metadata=lambda command, directory, name, version: {"reviewed": True},
    )
    loader = SimpleNamespace(exec_module=lambda module: None)
    monkeypatch.setattr(setup.importlib.util, "spec_from_file_location", lambda *args: SimpleNamespace(loader=loader))
    monkeypatch.setattr(setup.importlib.util, "module_from_spec", lambda spec: admission)

    def run(argv, **kwargs):
        assert kwargs["check"] is True and kwargs["timeout"] <= 120
        if argv[2:] == ["config", "get", "replace-registry-host"]:
            return SimpleNamespace(stdout="npmjs\n")
        if argv[2:] == ["config", "get", "registry"]:
            return SimpleNamespace(stdout="" if failure == "registry" else "https://registry.npmjs.org/\n")
        if argv[2:3] == ["ci"]:
            events.append("install")
            assert kwargs["cwd"] == project
            assert argv[3:] == ["--ignore-scripts", "--strict-peer-deps", "--no-audit", "--fund=false"]
            assert kwargs["env"]["npm_config_registry"] == "https://registry.npmjs.org/"
            if failure == "install":
                raise setup.subprocess.CalledProcessError(23, argv)
            if failure == "mutated":
                (project / "package-lock.json").write_text("{}")
            return SimpleNamespace(returncode=0)
        events.append("version")
        assert argv == [str(project / "node_modules/.bin/devcontainer"), "--version"]
        if failure == "version-command":
            raise setup.subprocess.CalledProcessError(29, argv)
        return SimpleNamespace(stdout="wrong" if failure == "version" else "0.80.0\n")

    monkeypatch.setattr(setup.subprocess, "run", run)
    if failure:
        with pytest.raises((RuntimeError, ValueError, setup.subprocess.CalledProcessError)):
            setup.provision(tmp_path, project)
    else:
        assert setup.provision(tmp_path, project) == project / "node_modules/.bin/devcontainer"
    assert events == (
        [] if failure == "registry" else
        ["audit"] if failure == "audit" else
        ["audit", "install"] if failure in {"install", "mutated"} else
        ["audit", "install", "version"]
    )


def test_only_verified_cli_is_added_to_github_path(tmp_path, monkeypatch):
    destination = tmp_path / "github-path"
    destination.write_text("retained\n")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_PATH", str(destination))
    binary = tmp_path / "verified/bin/devcontainer"

    def provision(root, project):
        assert root == Path(__file__).resolve().parents[1]
        assert project == tmp_path / "periodic-devcontainer-cli"
        return binary

    monkeypatch.setattr(setup, "provision", provision)
    setup.main()
    assert destination.read_text().splitlines() == ["retained", str(binary.parent)]
    monkeypatch.setattr(setup, "provision", lambda *args: (_ for _ in ()).throw(RuntimeError("refused")))
    with pytest.raises(RuntimeError, match="refused"):
        setup.main()
    assert destination.read_text().splitlines() == ["retained", str(binary.parent)]
