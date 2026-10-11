"""Exercise the shipped inert template through config and Docker argv owners."""

from pathlib import Path
from types import SimpleNamespace

from agent_containers import config, fleet


def test_ci_repair_template_real_config_and_provisioning(monkeypatch):
    template = Path(__file__).resolve().parents[1] / "templates" / "ci-repair"
    monkeypatch.setattr(config, "_config_path", lambda: template / "containers.yaml")
    loaded = config.load_config(strict=True)
    selected = loaded.fleets["ci-repair"]
    assert loaded.credentials_for(selected) == (False, False)
    assert selected.restricted and selected.network == "none"
    assert "--allow-all" not in loaded.acp_command_for(selected)
    calls = []
    monkeypatch.setattr(
        fleet,
        "_docker",
        lambda args, **kwargs: (
            calls.append(args) or SimpleNamespace(returncode=0, stdout="fake-id", stderr="")
        ),
    )
    monkeypatch.setattr(fleet, "_image_user", lambda *a, **k: (1000, 1000, "/home/vscode"))
    monkeypatch.setattr(fleet, "_image_id", lambda *a: "sha256:fake-image")
    monkeypatch.setattr(fleet, "_validate_restricted_network", lambda *a: None)
    assert (
        fleet._image_run(
            "ci-repair", selected, "ci-repair-1", workspace_folder="/workspace", exec_user="vscode"
        )
        == "ci-repair-1"
    )
    argv = calls[0]
    assert "--read-only" in argv and "--cap-drop=ALL" in argv
    assert argv[argv.index("--network") + 1] == "none"
    assert "--volume" not in argv and "--mount" not in argv
    assert not any("TOKEN" in part or "host.docker.internal" in part for part in argv)
    dockerfile = (template / "Dockerfile").read_text()
    assert "ARG COPILOT_VERSION" in dockerfile
    assert "USER vscode" in dockerfile
    assert "gh auth" not in dockerfile and "TOKEN" not in dockerfile
