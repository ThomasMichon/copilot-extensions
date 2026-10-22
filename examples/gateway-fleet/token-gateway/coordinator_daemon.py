"""Experimental reference implementation; see the parent README."""


from __future__ import annotations

import json
import logging
import os
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

RUN_DIR = Path(os.environ.get("HGF_RUN_DIR", str(Path.home() / "harness-gateway-fleet" / "run")))
SOCKET_PATH = RUN_DIR / "coordinator.sock"
LOG_PATH = Path(
    os.environ.get("HGF_LOG_PATH", str(Path.home() / "harness-gateway-fleet" / "coordinator.log"))
)
CONFIG_PATH = Path(
    os.environ.get("HGF_CONFIG_PATH", str(Path(__file__).parent / "coordinator_config.json"))
)


VERSION = "0.2.1"


_lock = threading.Lock()
_state: dict = {"token": None, "expires_epoch": None, "received_at": None, "generation": 0}

log = logging.getLogger("hgf.coordinator")


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.addHandler(logging.StreamHandler(sys.stderr))
    log.setLevel(logging.INFO)


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {"consumers": []}


def _status() -> dict:
    with _lock:
        if _state["token"] is None or _state["expires_epoch"] is None:
            return {"has_token": False, "remaining_seconds": None}
        remaining = _state["expires_epoch"] - time.time()
        return {"has_token": remaining > 0, "remaining_seconds": max(remaining, 0.0)}


def _ingest(token: str, expires_epoch: float) -> dict:
    with _lock:
        current_expiry = _state.get("expires_epoch")


        if current_expiry is not None and expires_epoch <= current_expiry:
            log.info(
                "ingest: stale push ignored (incoming_exp=%.0f <= current_exp=%.0f)",
                expires_epoch,
                current_expiry,
            )
            return {"ok": True, "accepted": False, "reason": "not-newer"}
        _state["token"] = token
        _state["expires_epoch"] = expires_epoch
        _state["received_at"] = time.time()
        _state["generation"] += 1
        generation = _state["generation"]
    log.info(
        "ingest: accepted token len=%d expires_in=%.0fs generation=%d",
        len(token),
        expires_epoch - time.time(),
        generation,
    )
    threading.Thread(
        target=_redistribute, args=(token, expires_epoch, generation), daemon=True
    ).start()
    return {"ok": True, "accepted": True, "generation": generation}


def _redistribute(token: str, expires_epoch: float, generation: int) -> None:
    config = _load_config()
    payload = json.dumps({"token": token, "expires_epoch": expires_epoch, "generation": generation})
    for consumer in config.get("consumers", []):
        host = consumer["ssh_target"]
        remote_python = consumer.get("remote_python", "python3")
        remote_script = consumer["remote_script"]
        cache_dir = consumer.get("cache_dir", "~/.agent-vault/cache")
        vendor_path = consumer.get("vendor_path", "~/harness-gateway-fleet/vendor")
        ssh_key = consumer.get("ssh_key", "~/.ssh/coordinator_ed25519")
        remote_cmd = (
            f"AGENT_VAULT_CACHE=1 AGENT_VAULT_CACHE_DIR={cache_dir} "
            f"PYTHONPATH={vendor_path} {remote_python} {remote_script}"
        )
        cmd = [
            "ssh",
            "-i",
            os.path.expanduser(ssh_key),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=accept-new",
            host,
            remote_cmd,
        ]
        try:
            result = subprocess.run(
                cmd, input=payload, capture_output=True, text=True, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                log.info("redistribute: %s ok (%s)", host, result.stdout.strip())
            else:
                log.error(
                    "redistribute: %s FAILED rc=%d stderr=%s",
                    host,
                    result.returncode,
                    result.stderr.strip(),
                )
        except Exception as exc:
            log.error("redistribute: %s EXCEPTION %s", host, exc)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            line = self.rfile.readline()
            if not line:
                return
            request = json.loads(line.decode("utf-8"))
            cmd = request.get("cmd")
            if cmd == "status":
                response = _status()
            elif cmd == "ingest":
                response = _ingest(request["token"], float(request["expires_epoch"]))
            else:
                response = {"ok": False, "error": f"unknown cmd {cmd!r}"}
        except Exception as exc:
            log.error("handler error: %s", exc)
            response = {"ok": False, "error": str(exc)}
        self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))


class _Server(socketserver.ThreadingUnixStreamServer):
    allow_reuse_address = True


def main() -> None:
    _setup_logging()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()
    server = _Server(str(SOCKET_PATH), _Handler)
    os.chmod(SOCKET_PATH, 0o600)
    log.info(
        "coordinator daemon v%s listening on %s (pid=%d)", VERSION, SOCKET_PATH, os.getpid()
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("coordinator daemon stopping")
    finally:
        server.server_close()
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()


if __name__ == "__main__":
    main()
