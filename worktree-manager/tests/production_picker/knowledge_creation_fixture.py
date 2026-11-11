"""Disposable provider/credential executables, never an operator credential store."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import shlex
import socket
from socketserver import StreamRequestHandler, ThreadingTCPServer
import subprocess
import sys
import threading
import time

from agent_procutil import no_window_kwargs
import pytest


URL = "https://github.com/selected/knowledge"


@pytest.fixture
def private_knowledge_provider(tmp_path, monkeypatch):
    owner_python = os.environ.get("REPOSITORIES_CONFIG_TEST_PYTHON")
    if not owner_python:
        pytest.skip(
            "set REPOSITORIES_CONFIG_TEST_PYTHON to the isolated native engine interpreter"
        )
    state = tmp_path / "private-provider.json"
    state.write_text(
        json.dumps(
            {
                "created": False,
                "protected": False,
                "posts": [],
                "failure": "protection-denied",
                "git_credentials": [],
                "metadata": {
                    "id": 120,
                    "full_name": "selected/knowledge",
                    "private": True,
                    "owner": {"type": "User", "login": "selected"},
                    "permissions": {"admin": True},
                    "archived": False,
                    "disabled": False,
                    "default_branch": "provider-default",
                },
            }
        ),
        encoding="utf-8",
    )
    directory = tmp_path / "private-provider-bin"
    directory.mkdir()
    script = directory / "provider.py"
    script.write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "path=Path(os.environ['CE_CREATION_FIXTURE']); state=json.loads(path.read_text())\n"
        "args=sys.argv[1:]\n"
        "if args[0]=='credential':\n"
        " fields=dict(line.split('=',1) for line in sys.stdin.read().splitlines() if '=' in line)\n"
        " assert os.environ.get('GIT_TERMINAL_PROMPT')=='0'\n"
        " assert os.environ.get('GCM_INTERACTIVE')=='0'\n"
        " assert fields['host']=='github.com' and fields['protocol']=='https'\n"
        " assert fields['username']=='selected' and fields['path'].rstrip('/')=='selected/knowledge'\n"
        " assert state['created'] and state['protected']\n"
        " state['git_credentials'].append(args[-1]);path.write_text(json.dumps(state))\n"
        " if args[-1]=='get':print('username=selected\\npassword=fixture-git-password')\n"
        " sys.exit(0)\n"
        "if args==['auth','token','--user','selected']:\n"
        " print('fixture-token');sys.exit(0)\n"
        "if args==['api','user','--jq','.login']:\n"
        " assert os.environ.get('GH_TOKEN')=='fixture-token'\n"
        " print('selected');sys.exit(0)\n"
        "assert os.environ.get('GH_PROMPT_DISABLED')=='1'\n"
        "if args[:2]==['auth','token']:\n"
        " assert args==['auth','token','--hostname','github.com','--user','selected']\n"
        " assert 'GH_TOKEN' not in os.environ\n"
        " print('fixture-token');sys.exit(0)\n"
        "assert os.environ['GH_TOKEN']=='fixture-token'\n"
        "route=next(arg for arg in args if arg.startswith('https://api.github.com/'))\n"
        "status,body=200,{}\n"
        "if route.endswith('/user'):body={'login':'selected'}\n"
        "elif 'POST' in args:\n"
        " body=json.loads(sys.stdin.read());state['posts'].append([route,body])\n"
        " if route.endswith('/user/repos'):\n"
        "  assert body=={'name':'knowledge','private':True,'auto_init':True}\n"
        "  assert not state['created'];state['created']=True;status,body=201,state['metadata']\n"
        " else:\n"
        "  assert route.endswith('/selected/knowledge/rulesets')\n"
        "  if state['failure']=='protection-denied':status=403\n"
        "  else:state['protected']=True;status,body=201,{'id':7}\n"
        " path.write_text(json.dumps(state))\n"
        "elif route.endswith('/protection'):status=404\n"
        "elif '/rules/branches/' in route:\n"
        " body=[{'type':kind,'ruleset_id':7,'ruleset_source_type':'Repository'} "
        "for kind in ['deletion','non_fast_forward']] if state['protected'] else []\n"
        "elif route.endswith('/rulesets/7'):\n"
        " body={'id':7,'target':'branch','source_type':'Repository','source':'selected/knowledge',"
        "'enforcement':'active','bypass_actors':[],"
        "'rules':[{'type':'deletion'},{'type':'non_fast_forward'}]}\n"
        "elif route.endswith('/repos/selected/knowledge'):\n"
        " status,body=(200,state['metadata']) if state['created'] else (404,{})\n"
        "else:raise AssertionError('unexpected fixture route')\n"
        "print('HTTP/2 '+str(status)+' Response\\r\\n\\r\\n'+json.dumps(body))\n"
        "sys.exit(0 if status in [200,201] else 1)\n",
        encoding="utf-8",
    )
    for name, prefix in (("gh", ""), ("git-credential-manager", "credential ")):
        shim = directory / (name + (".cmd" if sys.platform == "win32" else ""))
        if sys.platform == "win32":
            shim.write_text(
                f'@echo off\n"{owner_python}" "{script}" {prefix}%*\n', encoding="utf-8"
            )
        else:
            shim.write_text(
                f"#!{owner_python}\nimport sys\nsys.argv[1:1]={prefix.split()!r}\n"
                f"exec(compile(open({str(script)!r}).read(),{str(script)!r},'exec'))\n",
                encoding="utf-8",
            )
            shim.chmod(0o700)
    git = subprocess.run(
        ["git", "--exec-path"],
        capture_output=True,
        text=True,
        timeout=5,
        **no_window_kwargs(),
    )
    assert git.returncode == 0, git.stderr
    remote = git.stdout.strip().replace("\\", "/") + "/git-remote-https"
    if sys.platform == "win32":
        remote += ".exe"
    for name, command in (
        (
            "git-credential-manager",
            [owner_python.replace("\\", "/"), str(script).replace("\\", "/"), "credential"],
        ),
        ("git-remote-https", [remote]),
    ):
        executable = directory / name
        executable.write_text(
            "#!/bin/sh\nexec " + " ".join(shlex.quote(arg) for arg in command) + ' "$@"\n',
            encoding="utf-8",
        )
        executable.chmod(0o700)
    # No installed credential helper is reachable through Git's helper lookup.
    monkeypatch.setenv("GIT_EXEC_PATH", str(directory))
    search = [
        value for value in os.environ["PATH"].split(os.pathsep)
        if sys.platform != "win32" or not (Path(value) / "gh.exe").is_file()
    ]
    monkeypatch.setenv("PATH", os.pathsep.join([str(directory), *search]))
    monkeypatch.setenv("CE_CREATION_FIXTURE", str(state))
    for key in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.setenv(key, "hostile-ambient")
    for key in ("GH_HOST", "GH_REPO", "GH_DEBUG"):
        monkeypatch.delenv(key, raising=False)
    return state


@contextmanager
def github_proxy(port):
    """Keep the original HTTPS identity/TLS checks while routing only to the fixture."""

    errors = []

    class Tunnel(StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(5)
            assert self.rfile.readline(4096).split()[:2] == [b"CONNECT", b"github.com:443"]
            for _ in range(32):
                if self.rfile.readline(4096) in (b"\r\n", b"\n", b""):
                    break
            else:
                raise AssertionError("oversized fixture proxy request")
            with socket.create_connection(("127.0.0.1", port), timeout=5) as upstream:
                self.connection.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    ready, _, _ = select.select([self.connection, upstream], [], [], 1)
                    for source in ready:
                        try:
                            data = source.recv(65536)
                            if not data:
                                return
                            (upstream if source is self.connection else self.connection).sendall(data)
                        except ConnectionError:
                            return
                raise TimeoutError("fixture tunnel exceeded its finite budget")

    class Proxy(ThreadingTCPServer):
        def handle_error(self, request, client_address):
            errors.append(type(sys.exc_info()[1]).__name__)
            super().handle_error(request, client_address)

    with Proxy(("127.0.0.1", 0), Tunnel) as proxy:
        worker = threading.Thread(target=proxy.serve_forever)
        worker.start()
        try:
            yield proxy.server_address[1]
        finally:
            proxy.shutdown()
            worker.join(timeout=5)
            assert not worker.is_alive()
            assert errors == []
