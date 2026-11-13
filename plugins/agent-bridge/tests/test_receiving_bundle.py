"""The portable closure must survive ordinary payload materialization."""

import importlib.util
import io
import json
from pathlib import Path
import posixpath
import re
import sys
import zipfile

import pytest

from agent_bridge import receiving_bundle


def _materializer():
    tools = Path(__file__).resolve().parents[3] / "tools"
    sys.path.insert(0, str(tools))
    try:
        spec = importlib.util.spec_from_file_location("materialize_receiving_assets", tools / "materialize_launch_wrapper_assets.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(tools))


def test_selected_extensions_and_canonical_pane_shell_survive_materialization(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[3]
    bridge = root / "plugins" / "agent-bridge"
    source_bytes, source_identity = receiving_bundle.build_mux_support()
    dest = tmp_path / "bridge"
    dest.mkdir()
    report = _materializer().materialize_into(
        source_consumer_dir=bridge, dest_consumer_dir=dest, canonical_root=root,
    )
    assert report and all(line.startswith("OK ") for line in report), report
    package = dest / "src" / "agent_bridge"
    monkeypatch.setattr(receiving_bundle, "_package_root", lambda: package)
    released_bytes, released_identity = receiving_bundle.build_mux_support()
    assert (released_bytes, released_identity) == (source_bytes, source_identity)
    with zipfile.ZipFile(io.BytesIO(released_bytes)) as archive:
        for name in archive.namelist():
            if name.endswith(".mjs"):
                source = archive.read(name).decode("utf-8")
                for match in re.finditer(r"""(?:\bfrom\s+|\bimport\s*(?:\(\s*)?)["'](\.[^"']+)["']""", source):
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(name), match.group(1)))
                    assert target in archive.namelist(), f"{name} imports missing asset {target}"
        assert archive.read("mux/pane-wrapper.sh") == (root / "worktree-manager/bin/pane-wrapper.sh").read_bytes()
        standalone = root / "plugins" / "agent-remote-driver"
        for name in archive.namelist():
            if name.startswith("plugins/agent-remote-driver/"):
                assert archive.read(name) == (standalone / name.removeprefix("plugins/agent-remote-driver/")).read_bytes()
        metadata = json.loads(archive.read("plugins/agent-bridge/plugin.json"))
        assert metadata["name"] == "agent-bridge"
        assert not {"hooks", "sessionContext", "skills", "installerReadiness"} & metadata.keys()
        prerequisite = "plugins/agent-worktrees/src/agent_worktrees/mux_prerequisite.py"
        assert archive.read(prerequisite) == (root / prerequisite).read_bytes()
        assert not any(
            ("/src/" in name or "/scripts/" in name or "/tests/" in name) and name != prerequisite
            for name in archive.namelist()
        )
    changed = package / "receiving_assets/plugins/agent-remote-driver/extensions/agent-remote-driver/startup-client.mjs"
    changed.write_bytes(changed.read_bytes() + b"\n")
    assert receiving_bundle.build_mux_support()[1] != source_identity


def test_partial_runtime_support_never_falls_back_to_another_payload(tmp_path, monkeypatch):
    package = tmp_path / "agent_bridge"
    (package / "receiving_assets").mkdir(parents=True)
    monkeypatch.setattr(receiving_bundle, "_package_root", lambda: package)
    with pytest.raises(FileNotFoundError):
        receiving_bundle.build_mux_support()


def test_mux_support_never_reads_the_shared_core_cache(tmp_path, monkeypatch):
    import tempfile

    from agent_remote_driver.session_host import bundle

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    cache = tmp_path / "agent-remote-driver-bundles"
    cache.mkdir()
    poison = cache / bundle.bundle_filename()
    poison.write_bytes(b"untrusted shared archive")
    existing = set(tmp_path.iterdir())
    payload, _ = receiving_bundle.build_mux_support()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        with zipfile.ZipFile(io.BytesIO(archive.read("driver.pyz"))) as core:
            assert "agent_remote_driver/headed/launcher.py" in core.namelist()
    assert poison.read_bytes() == b"untrusted shared archive"
    assert set(tmp_path.iterdir()) == existing


def test_mux_launch_retry_preserves_exact_payload_after_core_rebuild(monkeypatch):
    from agent_bridge import portable_policy

    monkeypatch.setattr(portable_policy, "selected_context", lambda: '{"selected":"fixture"}')
    options = {
        "state_root": "/home/operator/.agent-bridge/mux", "scope_id": "workspace",
        "attempt": "11111111-1111-4111-8111-111111111111",
        "session_id": "22222222-2222-4222-8222-222222222222",
    }
    monkeypatch.setattr(zipfile.time, "localtime", lambda _: (2001, 1, 1, 0, 0, 0, 0, 1, -1))
    first = receiving_bundle.build_mux_launch("/workspace/project", **options)
    monkeypatch.setattr(zipfile.time, "localtime", lambda _: (2026, 1, 1, 0, 0, 0, 0, 1, -1))
    assert receiving_bundle.build_mux_launch("/workspace/project", **options) == first


@pytest.mark.parametrize("value", ["../outside", "/absolute", "C:/outside", "a\\b", "a//b"])
def test_receiving_assets_do_not_escape_the_declared_source(tmp_path, value):
    with pytest.raises(ValueError, match="safe relative"):
        receiving_bundle._read(tmp_path, value)


def test_provider_command_carries_selected_policy_and_exact_support(monkeypatch):
    import base64
    import hashlib
    import shlex

    from agent_bridge import portable_policy

    monkeypatch.setattr(portable_policy, "selected_context", lambda: '{"selected":"fixture"}')
    command, payload = receiving_bundle.build_mux_launch(
        "/workspace/project", state_root="/home/operator/.agent-bridge/mux",
        scope_id="workspace", attempt="11111111-1111-4111-8111-111111111111",
        session_id="22222222-2222-4222-8222-222222222222", copilot_args=["--model", "fixture"],
    )
    assert shlex.split(command)[:4] == ["python3", "-I", "-S", "-c"]
    envelope = json.loads(payload)
    assert envelope["request"]["cwd"] == "/workspace/project"
    assert envelope["request"]["policy_context"] == '{"selected":"fixture"}'
    assert hashlib.sha256(base64.b64decode(envelope["support"])).hexdigest() == envelope["request"]["support_sha256"]
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(envelope["support"]))) as archive:
        assert "repository_policy/receiving.py" in archive.namelist()
        assert "receiver.py" in archive.namelist()
        with zipfile.ZipFile(io.BytesIO(archive.read("driver.pyz"))) as core:
            assert "agent_remote_driver/headed/launcher.py" in core.namelist()
            assert "agent_remote_driver/headed/extension.mjs" in core.namelist()
            assert not any(name.startswith("agent_bridge/") for name in core.namelist())


def test_provider_cli_advertises_support_without_touching_a_daemon(capsys, monkeypatch):
    from agent_bridge import __main__

    def forbidden(*args, **kwargs):
        raise AssertionError("support packaging must not start a daemon")

    monkeypatch.setattr(__main__, "_get_client", forbidden)
    __main__.main(["receiving-support", "capabilities"])
    result = json.loads(capsys.readouterr().out)
    assert result["mux"] == {
        "version": 1, "platform": "posix", "transport": "command-stdin",
        "ensure_mux": 1, "registration": 1, "driven_by": 1,
    }
    assert json.loads(result["policy_context"])["schema"] == "agent-bridge.receiving-policy"


@pytest.mark.parametrize("capability", [None, 0, True, 2])
def test_provider_owner_check_refuses_an_unqualified_running_daemon(monkeypatch, capability):
    from agent_bridge import __main__, receiving_cli

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def health(self):
            return {"ready": True, "draining": False, "capabilities": {"portable_mux": capability}}

    def selected(*, ensure):
        assert ensure is False
        return Client()

    monkeypatch.setattr(__main__, "_get_client", selected)
    with pytest.raises(RuntimeError, match="running Bridge"):
        receiving_cli._owner()


def test_provider_owner_capability_never_exposes_registration_credentials(monkeypatch, capsys):
    from agent_bridge import __main__

    class Client:
        _base = "http://127.0.0.1:12345"
        _token = "synthetic-registration-value"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def health(self):
            return {"ready": True, "draining": False, "capabilities": {
                "portable_mux": 1, "portable_inbox": 1, "readonly_plugin_controls": 1,
            }}

    monkeypatch.setattr(__main__, "_get_client", lambda *, ensure: Client())
    __main__.main(["receiving-support", "capabilities", "--owner"])
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["owner_mux"] == 1 and result["daemon_port"] == 12345
    assert "synthetic-registration-value" not in output


def test_provider_owner_uses_the_actual_client_interface(monkeypatch, capsys):
    from agent_bridge import __main__
    from agent_bridge.client import BridgeClient

    client = BridgeClient("http://127.0.0.1:12345", "synthetic-registration-value")
    requests = []

    def request(method, path, *args, **kwargs):
        requests.append((method, path))
        return {"ready": True, "draining": False, "capabilities": {
            "portable_mux": 1, "portable_inbox": 1, "readonly_plugin_controls": 1,
        }}

    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(__main__, "_get_client", lambda *, ensure: client)
    __main__.main(["receiving-support", "capabilities", "--owner"])
    result = json.loads(capsys.readouterr().out)
    assert result["owner_mux"] == 1 and result["daemon_port"] == 12345
    assert requests == [("GET", "/health")]
