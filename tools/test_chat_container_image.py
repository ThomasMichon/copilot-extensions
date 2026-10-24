"""Offline recipe/qualification contracts; real image evidence is a separate opt-in lane."""

import hashlib
import json
from pathlib import Path
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
    assert "STOPSIGNAL SIGTERM" in recipe
    assert '"--require-existing-storage"' in recipe and '"--health"' in recipe
    assert '"--run-dir", "/tmp/chat-run"' in recipe
    assert "EXPOSE" not in recipe and "VOLUME" not in recipe
    assert "COPY ." not in recipe and "agent_worktrees" not in recipe
    config = json.loads((directory / "host.example.json").read_text(encoding="utf-8"))
    assert config["data_dir"] == "/data" and config["token_file"] == "/run/secrets/chat-domain"


def test_dedicated_context_contains_only_three_hashed_wheels_and_recipe(tmp_path, monkeypatch):
    built = []

    def build(argv, *, cwd, timeout):
        wheels = Path(argv[-1])
        name = {
            "chat": "agent-chat-service", "single-instance-lease": "agent-single-instance-lease",
            "endpoint-rendezvous": "agent-endpoint-rendezvous",
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
    assert len(lock.splitlines()) == 3
    assert "agent-chat-service[service]==1.0" in lock
    for wheel in built:
        assert "--hash=sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest() in lock


@pytest.mark.parametrize("base", ["python:3.11", "x@sha256:bad", "x@sha256:" + "z" * 64])
def test_mutable_or_malformed_base_refuses_before_docker(monkeypatch, base):
    monkeypatch.setattr(image.os, "name", "posix")
    monkeypatch.setattr(image.sys, "platform", "linux")
    monkeypatch.setattr(image, "run", lambda *_a, **_k: pytest.fail("executed Docker"))
    with pytest.raises(ValueError, match="digest-pinned"):
        image.qualify(base)
