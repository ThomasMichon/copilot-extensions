"""Offline recipe/qualification contracts; real image evidence is a separate opt-in lane."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

import qualify_chat_container as image


def test_recipe_has_only_offline_wheel_inputs_and_foreground_nonroot_host():
    directory = image.ROOT / "services" / "chat" / "container"
    recipe = (directory / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM ${PYTHON_BASE}" in recipe and "@sha256:" in recipe
    assert "--no-index --find-links=" in recipe
    assert "--only-binary=:all: --require-hashes" in recipe
    assert "USER 65532:65532" in recipe
    assert "COPILOT_EXTENSIONS_DAEMON_REGISTRY=/tmp/chat-instances" in recipe
    assert "STOPSIGNAL SIGTERM" in recipe
    assert '"--require-existing-storage"' in recipe and '"--health"' in recipe
    assert '"--run-dir", "/tmp/chat-run"' in recipe
    assert "EXPOSE" not in recipe and "VOLUME" not in recipe
    assert "COPY ." not in recipe and "agent_worktrees" not in recipe
    config = json.loads((directory / "host.example.json").read_text(encoding="utf-8"))
    assert config["data_dir"] == "/data" and config["token_file"] == "/run/secrets/chat-domain"


def test_image_lane_resolves_the_existing_runner_managed_interpreter():
    workflow = (image.ROOT / ".github/workflows/chat-container-qualification.yml").read_text(
        encoding="utf-8",
    )
    assert 'runpy.run_path("tools/run-standalone-tests.py")' in workflow
    assert 'runner["default_python"]("services/chat")' in workflow
    assert ".test-venvs/linux/services/chat/bin/python" not in workflow


@pytest.mark.parametrize("timeout_probe", [0, 1], ids=["volume-preparation", "missing-store"])
def test_timed_out_container_probe_is_named_and_force_removed(monkeypatch, timeout_probe):
    probes, removed = [], []
    monkeypatch.setattr(image, "os", SimpleNamespace(name="posix", chmod=os.chmod))
    monkeypatch.setattr(image.sys, "platform", "linux")
    monkeypatch.setattr(image.shutil, "which", lambda _name: "docker")
    monkeypatch.setattr(image, "wheel_context", lambda root: root)

    def docker(argv, **kwargs):
        if argv[1:3] == ["image", "inspect"]:
            return SimpleNamespace(stdout=json.dumps([{"Config": {"User": "65532:65532"}}]))
        if argv[1] == "run":
            name = argv[argv.index("--name") + 1]
            probes.append(name)
            if len(probes) - 1 == timeout_probe:
                raise subprocess.TimeoutExpired(argv, 15)
        if argv[1:3] == ["rm", "--force"]:
            removed.append(argv[3])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(image, "run", docker)
    with pytest.raises(subprocess.TimeoutExpired):
        image.qualify("example@sha256:" + "a" * 64)
    assert len(probes) == len(set(probes)) == timeout_probe + 1
    assert set(probes) <= set(removed)


def test_dedicated_context_contains_only_service_closure_wheels_and_recipe(tmp_path, monkeypatch):
    built = []

    def build(argv, *, cwd, timeout):
        wheels = Path(argv[-1])
        name = {
            "chat": "agent-chat-service", "single-instance-lease": "agent-single-instance-lease",
            "endpoint-rendezvous": "agent-endpoint-rendezvous", "agent-procutil": "agent-procutil",
        }[cwd.name]
        path = wheels / (name.replace("-", "_") + "-1.0-py3-none-any.whl")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(name + "-1.0.dist-info/METADATA", f"Name: {name}\nVersion: 1.0\n")
        built.append(path)

    monkeypatch.setattr(image, "run", build)
    context = image.wheel_context(tmp_path)
    assert not (tmp_path / "source").exists()
    assert {path.name for path in context.iterdir()} == {
        "wheels", "requirements.lock", "Dockerfile",
    }
    lock = (context / "requirements.lock").read_text(encoding="utf-8")
    assert len(lock.splitlines()) == 4
    assert "agent-chat-service[service]==1.0" in lock
    assert "agent-procutil==1.0" in lock
    for wheel in built:
        assert "--hash=sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest() in lock


@pytest.mark.parametrize("base", ["python:3.11", "x@sha256:bad", "x@sha256:" + "z" * 64])
def test_mutable_or_malformed_base_refuses_before_docker(monkeypatch, base):
    monkeypatch.setattr(image.os, "name", "posix")
    monkeypatch.setattr(image.sys, "platform", "linux")
    monkeypatch.setattr(image, "run", lambda *_a, **_k: pytest.fail("executed Docker"))
    with pytest.raises(ValueError, match="digest-pinned"):
        image.qualify(base)
