import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run(argv, *, cwd=None):
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        argv, cwd=cwd, env=environment, capture_output=True, text=True,
        timeout=30, check=False, **options,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_source_independent_wheel(tmp_path):
    project = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    shutil.copytree(
        project, source,
        ignore=shutil.ignore_patterns("build", "dist", "*.egg-info", "__pycache__",
                                     ".venv", ".pytest_cache"),
    )
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    run([
        sys.executable, "-I", "-c",
        "from setuptools.build_meta import build_wheel; import sys; build_wheel(sys.argv[1])",
        str(wheels),
    ], cwd=source)
    built = list(wheels.glob("agent_chat_service-*.whl"))
    assert len(built) == 1
    uv = shutil.which("uv")
    assert uv is not None, "Artifact qualification requires the repository's uv tool"
    target = tmp_path / "installed"
    run([
        uv, "pip", "install", "--python", sys.executable, "--target", str(target),
        "--no-deps", "--offline", "--no-index", str(built[0]),
    ])
    shutil.rmtree(source)
    assert not source.exists()
    probe = """
import importlib.metadata
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import agent_chat_service as chat
assert Path(chat.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
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
        sys.executable, "-I", "-c", probe, str(target), str(tmp_path / "data"),
    ], cwd=tmp_path))
    assert result["installed"] and result["messages"] == 1 and result["cursor"] == 0
