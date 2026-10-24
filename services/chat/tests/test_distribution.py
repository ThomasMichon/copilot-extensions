import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest


def test_source_removed_installed_transport_enforces_raw_budgets_and_strict_text(
    tmp_path, installed_chat,
):
    probe = r"""
import json,sys
from agent_chat_service.client import ClientError
from agent_chat_service.transport import run_command
argv=[sys.executable,"-I","-c"]
assert run_command(
    [*argv,"import sys; data=sys.stdin.read(); sys.stderr.write('diagnostic'); print(data,end='')"],
    timeout=5,payload="Unicode \u2603".encode(),
).decode()=="Unicode \u2603"
checks=[]
for stream in ("stdout","stderr"):
    try:
        run_command(
            [*argv,f"import sys,time; sys.{stream}.buffer.write(b'private'*200000); "
             f"sys.{stream}.flush(); time.sleep(30)"],timeout=5,
        )
    except ClientError as error:
        assert error.code=="invalid_response" and stream in error.message
        assert "private" not in error.message
        checks.append(stream)
    else:
        raise AssertionError("installed transport accepted unbounded output")
try:
    run_command([*argv,"import sys; sys.stdout.buffer.write(b'\\r\\n'*33)"],
                timeout=5,max_capture=64)
except ClientError as error:
    assert error.code=="invalid_response"
    checks.append("raw-before-newline-normalization")
else:
    raise AssertionError("raw-byte limit was checked only after decoding")
try:
    run_command([*argv,"import sys; sys.stdout.buffer.write(b'\\xff')"],timeout=5)
except ClientError as error:
    assert error.code=="provider_unavailable" and "Unicode" in error.message
    checks.append("strict-utf8")
else:
    raise AssertionError("installed transport accepted invalid UTF-8")
print(json.dumps({"installed_transport":checks}),flush=True)
"""
    result = json.loads(installed_chat.run(
        [str(installed_chat.interpreter), "-I", "-c", probe], cwd=tmp_path,
    ))
    assert result == {"installed_transport": [
        "stdout", "stderr", "raw-before-newline-normalization", "strict-utf8",
    ]}


def test_source_independent_wheel(tmp_path, installed_chat):
    run = installed_chat.run
    probe = """
import importlib.metadata
import importlib.util
import json
import sys
from pathlib import Path
import agent_chat_service as chat
assert Path(chat.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
assert importlib.util.find_spec("agent_procutil") is None
assert importlib.util.find_spec("single_instance_lease") is None
assert importlib.util.find_spec("endpoint_rendezvous") is None
assert importlib.metadata.version("agent-chat-service") == chat.__version__.replace("-dev", ".dev")
store = chat.ChatStore(sys.argv[2], "example-installation")
try:
    store.dispatch({"op": "join", "channel": "room", "actor": {"codename": "agent-a"}})
    request = {"op": "post", "channel": "room", "codename": "agent-a",
               "request_id": "artifact-request", "text": "artifact conversation"}
    first = store.dispatch(request)
    assert store.dispatch(request) == {"event": first["event"], "duplicate": True}
    page = store.dispatch({"op": "read", "channel": "room", "codename": "agent-a"})
    assert page["messages"] == [first["event"]] and page["cursor"] == 0
    feed = store.dispatch({"op": "scan", "codename": "agent-a", "epoch": None,
                          "after": [{"channel": "room", "event_id": 0}], "timeout": 0})
    assert "text" not in feed["events"][0]
    assert store.dispatch({"op": "read", "channel": "room", "codename": "agent-a"})["cursor"] == 0
    assert store.drain()["state"] == "drained"
    try:
        store.dispatch(request)
    except chat.ChatError as error:
        assert error.code == "draining" and error.status == 503
    else:
        raise AssertionError("drained installed core accepted work")
    assert store.undrain()["state"] == "active"
    assert store.dispatch(request) == {"event": first["event"], "duplicate": True}
    print(json.dumps({"installed": True, "messages": len(page["messages"]),
                      "cursor": page["cursor"], "version": store.health()["version"]}))
finally:
    store.close()
"""
    result = json.loads(run([
        str(installed_chat.core_python), "-I", "-c", probe,
        str(installed_chat.core_target), str(tmp_path / "data"),
    ], cwd=tmp_path))
    assert result["installed"] and result["messages"] == 1 and result["cursor"] == 0
    console = installed_chat.console
    assert run([str(console), "--version"], cwd=tmp_path).strip() == result["version"]
    assert json.loads(run([str(console), "scan", "--describe"], cwd=tmp_path))["version"] == 1
    catalog = json.loads(run([str(console), "--config", str(tmp_path / "missing"), "catalog"],
                             cwd=tmp_path))
    assert catalog["schema"] == "agent-chat.client-catalog" and catalog["version"] == 1
    assert catalog["evidence"] == "local-installed-resource-record"
    assert not any(catalog[key] for key in (
        "release_selection_verified", "remote_verified",
        "native_listening_verified", "mutations_performed",
    ))
    exact = catalog["commands"][0]
    assert exact == {
        "id": "agent-chat",
        "argv": [str(installed_chat.interpreter), "-I", "-m", "agent_chat_service.cli"],
        "shell": "direct", "availability": "ready",
    }
    assert len(catalog["resource_sha256s"]) == 18
    assert "hosting.py" in catalog["resource_sha256s"]
    assert "workspace.py" in catalog["resource_sha256s"]
    assert "collaboration.py" in catalog["resource_sha256s"]
    offline = """
from contextlib import redirect_stdout
import io,json
from agent_chat_service.cli import main
def invoke(args):
    output=io.StringIO()
    with redirect_stdout(output):
        try:
            code=main(args)
        except SystemExit as error:
            code=error.code
    assert code==0
    return output.getvalue()
print(json.dumps({
    "version":invoke(["--version"]).strip(),
    "catalog":json.loads(invoke(["catalog"])),
    "capability":json.loads(invoke(["scan","--describe"])),
}),flush=True)
"""
    outputs = json.loads(run([exact["argv"][0], "-I", "-c", offline], cwd=tmp_path))
    assert outputs["version"] == result["version"]
    assert outputs["catalog"] == catalog
    assert outputs["capability"]["version"] == 1


def test_wheel_and_independent_sdist_deliver_exact_canonical_construction(
    tmp_path, installed_chat,
):
    import hashlib

    root = Path(__file__).resolve().parents[3]
    runtime = root / "libs" / "versioned-runtime"
    installer = root / "libs" / "installer-engine"
    expected = {
        "_versioned_runtime.py": runtime / "versioned_runtime.py",
        **{
            name: runtime / name for name in (
                "versioned_runtime_leases.py", "versioned_runtime_construction.py",
                "versioned_runtime_publication.py", "versioned_runtime_source.py",
            )
        },
        **{
            name: installer / name for name in (
                "installer_engine.py", "installer_engine_process.py",
                "installer_dependency_policy.py", "installer_dependency_config.py",
            )
        },
    }
    catalog = json.loads(installed_chat.run([
        str(installed_chat.console), "catalog",
    ], cwd=tmp_path))
    with tarfile.open(installed_chat.sdist, "r:gz") as archive:
        members = archive.getmembers()
        for name, source in expected.items():
            member, = (
                item for item in members if item.name.endswith("/src/agent_chat_service/" + name)
            )
            with archive.extractfile(member) as stream:
                data = stream.read()
            assert data == source.read_bytes()
            assert catalog["resource_sha256s"][name] == hashlib.sha256(data).hexdigest()
        helper, = (item for item in members if item.name.endswith("/_standalone_runtime_build.py"))
        with archive.extractfile(helper) as stream:
            assert stream.read() == (runtime / "standalone_build.py").read_bytes()
    assert not catalog["release_selection_verified"]


@pytest.mark.parametrize("shadow_source", ["cwd", "pythonpath"])
def test_installed_catalog_worker_preserves_isolation(
    tmp_path, rpc_endpoint, installed_chat, shadow_source,
):
    shadow = tmp_path / "shadow"
    package = shadow / "agent_chat_service"
    package.mkdir(parents=True)
    marker = tmp_path / "untrusted-worker-loaded"
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "transport.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise SystemExit(9)\n",
        encoding="utf-8",
    )
    config = tmp_path / "client.json"
    config.write_text(json.dumps({
        "schema": "agent-chat.client", "version": 1,
        "endpoint": rpc_endpoint.endpoint, "state_dir": str(tmp_path / "client-state"),
        "actor": {"codename": "isolated-agent"},
        "token_command": [str(installed_chat.interpreter), "-I", "-c",
                          "print('synthetic-credential')"],
    }), encoding="utf-8")
    catalog = json.loads(installed_chat.run([str(installed_chat.console), "catalog"], cwd=tmp_path))
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    if shadow_source == "pythonpath":
        environment["PYTHONPATH"] = str(shadow)
    result = subprocess.run(
        [*catalog["commands"][0]["argv"], "--config", str(config), "join", "room"],
        cwd=shadow if shadow_source == "cwd" else tmp_path,
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert not marker.exists(), "HTTP worker imported the shadow package"
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["actor"]["codename"] == "isolated-agent"
    assert rpc_endpoint.state.authenticated == 1
