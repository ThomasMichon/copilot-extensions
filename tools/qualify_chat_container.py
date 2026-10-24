#!/usr/bin/env python3
"""Opt-in disposable Linux image qualification, not release or deployment admission."""

from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent.parent
HOST = ["python", "-I", "-B", "-m", "agent_chat_service.hosting"]
CONFIG = ["--config", "/run/config/chat.json", "--run-dir", "/tmp/chat-run"]

PROBE = """
import json,sys
from agent_chat_service.transport import _http_exchange
from agent_chat_service.store import strict_json
from pathlib import Path
credential=Path('/run/secrets/chat-domain').read_text().strip()
results=[]
for request in json.loads(sys.argv[1]):
    status,data=_http_exchange({
        'endpoint_dir':'/tmp/chat-run','payload':request,
        'credential':credential,'timeout':5,
    })
    assert status==200,(status,data)
    results.append(strict_json(data,max_bytes=1048576))
print(json.dumps(results))
"""


def run(argv, *, cwd=None, timeout=60, check=True):
    result = subprocess.run(
        list(map(str, argv)), cwd=cwd, text=True, capture_output=True, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if check and result.returncode:
        raise RuntimeError(f"command failed: {argv[0]}\n{result.stdout}\n{result.stderr}")
    return result


def wheel_context(root):
    source, context = root / "source", root / "context"
    wheels = context / "wheels"
    wheels.mkdir(parents=True)
    ignore = shutil.ignore_patterns(
        "__pycache__", "*.egg-info", ".pytest_cache", ".ruff_cache", "build", "dist", "tests",
    )
    for relative in (
        "services/chat", "libs/versioned-runtime", "libs/installer-engine",
        "libs/single-instance-lease", "libs/endpoint-rendezvous",
    ):
        shutil.copytree(ROOT / relative, source / relative, ignore=ignore)
    for relative in ("services/chat", "libs/single-instance-lease", "libs/endpoint-rendezvous"):
        run([
            sys.executable, "-I", "-c",
            "from setuptools.build_meta import build_wheel; import sys; build_wheel(sys.argv[1])",
            str(wheels),
        ], cwd=source / relative, timeout=120)
    lines = []
    for path in sorted(wheels.glob("*.whl")):
        with zipfile.ZipFile(path) as archive:
            member, = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            metadata = BytesParser().parsebytes(archive.read(member))
        name, version = metadata["Name"], metadata["Version"]
        extra = "[service]" if name == "agent-chat-service" else ""
        lines.append(
            f"{name}{extra}=={version} --hash=sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        )
    assert len(lines) == 3
    (context / "requirements.lock").write_text("\n".join(lines) + "\n", encoding="utf-8")
    shutil.copyfile(ROOT / "services/chat/container/Dockerfile", context / "Dockerfile")
    shutil.rmtree(source)
    return context


def qualify(base):
    if os.name != "posix" or not sys.platform.startswith("linux"):
        raise RuntimeError("this qualification requires native Linux Docker")
    if not re.fullmatch(r"[a-zA-Z0-9._:/-]+@sha256:[0-9a-f]{64}", base):
        raise ValueError("provide an explicit digest-pinned Python base")
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("Docker is required; native Python is not image qualification")
    identity = "chat-qualification-" + uuid.uuid4().hex
    image, volume, container = identity + ":candidate", identity + "-data", identity + "-host"
    owned = []
    with tempfile.TemporaryDirectory(prefix="chat-container-qualification-") as temporary:
        root = Path(temporary)
        config, secret = root / "config", root / "secret"
        config.mkdir()
        secret.mkdir()
        shutil.copyfile(ROOT / "services/chat/container/host.example.json", config / "chat.json")
        key = secret / "chat-domain"
        key.write_text("synthetic-container-fixture-" + uuid.uuid4().hex, encoding="ascii")
        os.chmod(key, 0o600)
        # These are disposable fixture inputs, not an operator's credential directory.
        context = wheel_context(root)
        started = time.monotonic()
        try:
            run([docker, "build", "--network=none", "--build-arg", "PYTHON_BASE=" + base,
                 "--tag", image, context], timeout=300)
            owned.append(("image", image))
            built = json.loads(run([docker, "image", "inspect", image]).stdout)[0]
            assert built["Config"]["User"] == "65532:65532"
            assert not built["Config"].get("ExposedPorts")
            assert not built["Config"].get("Volumes")
            run([docker, "volume", "create", volume])
            owned.append(("volume", volume))
            mounts = [
                "--mount", f"type=volume,src={volume},dst=/data",
                "--mount", f"type=bind,src={config},dst=/run/config,readonly",
                "--mount", f"type=bind,src={key},dst=/run/secrets/chat-domain,readonly",
            ]
            limits = [
                "--read-only", "--network=none", "--cap-drop=ALL",
                "--security-opt=no-new-privileges", "--pids-limit=64",
                "--memory=128m", "--cpus=1",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m,mode=1777",
            ]
            # Only the explicit fixture-volume preparation runs as root.
            run([docker, "run", "--rm", "--network=none", "--user=0:0", "--entrypoint=python",
                 "--mount", f"type=volume,src={volume},dst=/data",
                 "--mount", f"type=bind,src={secret},dst=/fixture-secret", image, "-I", "-c",
                 "import os; os.chown('/data',65532,65532); os.chmod('/data',0o700); "
                 "os.chown('/fixture-secret/chat-domain',65532,65532)"])
            missing = run([docker, "run", "--rm", *limits, *mounts, image],
                          check=False, timeout=15)
            assert missing.returncode == 1 and '"error"' in missing.stderr
            run([docker, "run", "--rm", *limits, *mounts, image, *CONFIG,
                 "--initialize-storage"])
            repeated = run([docker, "run", "--rm", *limits, *mounts, image, *CONFIG,
                            "--initialize-storage"], check=False)
            assert repeated.returncode == 1 and "host_storage_not_empty" in repeated.stderr
            run([docker, "run", "--detach", "--name", container, "--stop-timeout=10",
                 *limits, *mounts, image])
            owned.append(("container", container))

            def inspect():
                return json.loads(run([docker, "inspect", container]).stdout)[0]

            def healthy():
                begin = time.monotonic()
                while time.monotonic() - begin < 30:
                    state = inspect()["State"]
                    assert state["Running"], run([docker, "logs", container]).stderr
                    if state.get("Health", {}).get("Status") == "healthy":
                        return time.monotonic() - begin
                    time.sleep(.2)
                raise AssertionError("container did not become authenticated healthy")

            startup = healthy()
            state = inspect()
            assert state["HostConfig"]["ReadonlyRootfs"]
            assert state["HostConfig"]["NetworkMode"] == "none"
            assert state["HostConfig"]["Memory"] == 128 * 1024 * 1024
            assert state["HostConfig"]["NanoCpus"] == 1000000000

            def requests(values):
                return json.loads(run([docker, "exec", container, "python", "-I", "-B",
                                       "-c", PROBE, json.dumps(values)]).stdout)

            run([docker, "exec", container, *HOST, *CONFIG, "--health"])
            posted = requests([
                {"op": "join", "channel": "room", "actor": {"codename": "agent-a"}},
                {"op": "join", "channel": "room", "actor": {"codename": "agent-b"}},
                {"op": "post", "channel": "room", "codename": "agent-a",
                 "request_id": "retained-request", "text": "retained container conversation"},
                {"op": "read", "channel": "room", "codename": "agent-b"},
            ])
            event = posted[2]["event"]
            assert posted[3]["messages"] == [event]
            competing = run([docker, "run", "--rm", *limits, *mounts, image],
                            check=False, timeout=15)
            assert competing.returncode == 1 and "host_already_running" in competing.stderr
            shutdown = time.monotonic()
            run([docker, "stop", container], timeout=15)
            shutdown = time.monotonic() - shutdown
            assert shutdown < 10 and inspect()["State"]["ExitCode"] == 0
            run([docker, "start", container])
            restart = healthy()
            retained = requests([
                {"op": "read", "channel": "room", "codename": "agent-b"},
                {"op": "post", "channel": "room", "codename": "agent-a",
                 "request_id": "retained-request", "text": "retained container conversation"},
            ])
            assert retained[0]["messages"] == [event]
            assert retained[1] == {"event": event, "duplicate": True}
            print(json.dumps({
                "schema": "agent-chat.container-qualification", "version": 1,
                "image_id": built["Id"], "base": base,
                "os": built["Os"], "architecture": built["Architecture"],
                "startup_seconds": startup, "restart_seconds": restart,
                "shutdown_seconds": shutdown, "total_seconds": time.monotonic() - started,
                "memory_limit_bytes": 128 * 1024 * 1024, "cpu_limit": 1,
                "scratch_limit_bytes": 16 * 1024 * 1024,
                "release_admitted": False, "gateway_qualified": False,
                "komodo_qualified": False, "status": "passed",
            }), flush=True)
        finally:
            for kind, name in reversed(owned):
                operation = ["rm", "--force", name] if kind == "container" else [kind, "rm", name]
                run([docker, *operation], check=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-base", required=True)
    args = parser.parse_args(argv)
    qualify(args.python_base)


if __name__ == "__main__":
    main()
