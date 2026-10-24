from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

import agent_index
from agent_index import config, http_auth


def test_actual_token_command_and_http_consumer(monkeypatch, tmp_path):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.path, self.headers["Authorization"]))
            payload = json.dumps({"available": True, "hits": [{"chunk_id": "example"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/api/index"
    value = {
        "endpoint": endpoint,
        "installation_id": "remote",
        "token_command": [sys.executable, "-c", "print('synthetic-native-credential')"],
    }
    monkeypatch.setattr(config, "_load_yaml", lambda _: {"http_auth": value})
    try:
        remote = http_auth.read_client(endpoint)
        result = remote.search("example", limit=1, source=None, language=None, repo=None)
        assert result["hits"] == [{"chunk_id": "example"}]
        assert received == [
            ("/api/index/search?q=example&limit=1", "".join(
                ["Bear", "er ", "synthetic-native-credential"]
            )),
        ]
        repo = tmp_path / "repo"
        declaration = repo / ".agent-index"
        declaration.mkdir(parents=True)
        (declaration / "config.yaml").write_text(json.dumps({
            "indexer": {"machine": "remote-indexer", "endpoint": endpoint},
        }))
        machine_config = tmp_path / "client.yaml"
        machine_config.write_text(json.dumps({
            "role": "client", "endpoint": endpoint, "http_auth": value,
        }))
        env = dict(os.environ)
        env.update({
            "AGENT_INDEX_CONFIG": str(machine_config),
            "AGENT_INDEX_REPO": str(repo),
            "AGENT_INDEX_MACHINE": "client-machine",
            "PYTHONPATH": str(Path(agent_index.__file__).resolve().parents[1]),
        })
        env.pop("AGENT_INDEX_CONFIG_DATA_B64", None)
        env.pop("AGENT_INDEX_SOURCE_MODE", None)
        result = subprocess.run(
            [sys.executable, "-m", "agent_index", "search", "example", "--limit", "1", "--json"],
            cwd=repo, env=env, capture_output=True, text=True, timeout=20,
            **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == [{"chunk_id": "example"}]
        assert len(received) == 2
        assert received[1] == received[0]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    assert not worker.is_alive()


@pytest.mark.parametrize("script", [
    "import sys; print('synthetic-sensitive-diagnostic', file=sys.stderr); sys.exit(1)",
    "print('invalid credential with spaces')",
    "print('x' * 16385)",
])
def test_actual_credential_errors_never_expose_provider_output(script):
    with pytest.raises(http_auth.HttpAuthError) as error:
        http_auth._credential([sys.executable, "-c", script])
    assert "synthetic-sensitive-diagnostic" not in str(error.value)
    assert "invalid credential with spaces" not in str(error.value)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_actual_unbounded_provider_output_is_stopped(stream):
    script = (
        f"import sys; stream=sys.{stream}.buffer; "
        "exec('while True:\\n stream.write(b\"x\" * 8192); stream.flush()')"
    )
    started = time.monotonic()
    with pytest.raises(http_auth.HttpAuthError, match="http-auth-command-failed"):
        http_auth._credential([sys.executable, "-c", script])
    assert time.monotonic() - started < 15
