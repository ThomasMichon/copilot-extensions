"""Explicit foreground host; installation, supervision and cutover stay external."""

import argparse
import hmac
import http.server
import json
import logging
import os
from pathlib import Path
import signal
import socket
import socketserver
import sqlite3
import stat
import sys
import threading
import uuid

from .store import ChatError, ChatStore, json_bytes, strict_json


IO_SECONDS = 5.0
MAX_CONNECTIONS = 32
MAX_REQUEST_BYTES = 65536
MAX_RESPONSE_BYTES = 1048576
MAX_HEADER_BYTES = 16384


class HostError(Exception):
    pass


def _absolute(value):
    if (
        not isinstance(value, str) or not value or not value.isprintable()
        or not Path(value).is_absolute()
    ):
        raise HostError("host_configuration_invalid")
    return Path(value)


def _read_regular(path, limit):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise HostError("host_configuration_invalid")
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise HostError("host_configuration_invalid")
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise HostError("host_configuration_invalid")
    return value, info


def load_config(path):
    value, _ = _read_regular(_absolute(path), MAX_REQUEST_BYTES)
    try:
        config = strict_json(value)
    except ChatError:
        raise HostError("host_configuration_invalid") from None
    return _validate_config(config)


def _validate_config(config):
    if (
        not isinstance(config, dict)
        or set(config) != {"schema", "version", "data_dir", "installation_id", "token_file"}
        or config["schema"] != "agent-chat.host"
        or type(config["version"]) is not int or config["version"] != 1
    ):
        raise HostError("host_configuration_invalid")
    _absolute(config["data_dir"])
    _absolute(config["token_file"])
    identity = config["installation_id"]
    if (
        not isinstance(identity, str) or not identity or not identity.isprintable()
        or len(identity.encode("utf-8")) > 128
    ):
        raise HostError("host_configuration_invalid")
    return config


class _BoundedReader:
    def __init__(self, stream):
        self.stream = stream
        self.remaining = MAX_HEADER_BYTES

    def readline(self, limit=-1):
        if self.remaining <= 0:
            raise http.client.LineTooLong("request headers")
        value = self.stream.readline(min(self.remaining + 1, limit) if limit >= 0
                                     else self.remaining + 1)
        self.remaining -= len(value)
        if self.remaining < 0:
            raise http.client.LineTooLong("request headers")
        return value

    def __getattr__(self, name):
        return getattr(self.stream, name)


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "agent-chat"
    sys_version = ""

    def setup(self):
        super().setup()
        self.rfile = _BoundedReader(self.rfile)
        self._timer = None

    def log_message(self, *_):
        return

    def _cancel_deadline(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer.join()
            self._timer = None

    def _deadline(self):
        self._cancel_deadline()
        self.connection.settimeout(IO_SECONDS)
        self._timer = threading.Timer(IO_SECONDS, self._expire)
        self._timer.start()

    def _expire(self):
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.connection.close()

    def handle_one_request(self):
        self._deadline()
        try:
            super().handle_one_request()
        except (OSError, ConnectionError):
            self.close_connection = True
        finally:
            self._cancel_deadline()

    def send_error(self, code, message=None, explain=None):
        self._json(code, {"error": "http_request_invalid"})

    def _json(self, status, value):
        data = json_bytes(value)
        if len(data) > MAX_RESPONSE_BYTES:
            logging.error("chat_host_response_limit")
            status, data = 500, b'{"error":"host_response_limit"}'
        self._deadline()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def _authorized(self):
        values = self.headers.get_all("Authorization", [])
        if len(values) != 1:
            return False
        try:
            supplied = values[0].encode("ascii")
        except UnicodeEncodeError:
            return False
        return hmac.compare_digest(supplied, self.server.authorization)

    def do_GET(self):
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
        elif self.path != "/health":
            self._json(404, {"error": "not_found"})
        else:
            try:
                value = self.server.store.health()
            except ChatError as error:
                self._json(error.status, error.as_dict())
            else:
                value["accepting"] = not self.server.draining
                self._json(503 if self.server.draining else 200, value)

    def do_POST(self):
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        if self.path != "/api/v1/chat":
            self._json(404, {"error": "not_found"})
            return
        lengths = self.headers.get_all("Content-Length", [])
        if (
            len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit()
            or len(lengths[0]) > 6 or self.headers.get_all("Transfer-Encoding")
            or self.headers.get_content_type() != "application/json"
        ):
            self._json(400, {"error": "http_request_invalid"})
            return
        length = int(lengths[0])
        if not 0 < length <= MAX_REQUEST_BYTES:
            self._json(413, {"error": "request_limit"})
            return
        raw = self.rfile.read(length)
        if len(raw) != length:
            self._json(400, {"error": "http_request_invalid"})
            return
        self._cancel_deadline()
        try:
            request = strict_json(raw)
            value = self.server.store.dispatch(request)
            if request["op"] == "members" and len(json_bytes(value)) > MAX_RESPONSE_BYTES:
                raise ChatError("capacity", "Membership reply is too large; use members limit/after.")
        except ChatError as error:
            self._json(error.status, error.as_dict())
        else:
            self._json(200, value)


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = False
    daemon_threads = False
    block_on_close = True
    request_queue_size = MAX_CONNECTIONS

    def __init__(self, address, store, authorization):
        self.store = store
        self.draining = False
        self.authorization = authorization
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(address, _Handler)
        self.timeout = .2

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        logging.error("chat_host_request_failed exception=%s", type(sys.exc_info()[1]).__name__)


class _UnixServer(_Server):
    address_family = getattr(socket, "AF_UNIX", socket.AF_INET)


class HostedChat:
    """One explicitly owned store and local endpoint, with caller-controlled lifetime."""

    def __init__(self, config):
        try:
            from agent_procutil.daemon_instances import admit_daemon_instance
        except ImportError:
            raise HostError("host_service_dependencies_missing") from None
        self._instance_guard = admit_daemon_instance(
            "agent-chat-service", source=__file__, purpose="production",
        )
        try:
            self._initialize(config)
        except BaseException:
            self._instance_guard.close()
            raise

    def _initialize(self, config):
        config = _validate_config(config)
        try:
            from endpoint_rendezvous import clear_endpoint, write_endpoint
            from single_instance_lease import AlreadyRunningError, SingleInstance
        except ImportError:
            raise HostError("host_service_dependencies_missing") from None
        self._clear_endpoint = clear_endpoint
        self._write_endpoint = write_endpoint
        self.store = self.server = self.lease = None
        self._advertised = False
        self._socket_path = None
        self._socket_identity = None
        self.data_dir = _absolute(config["data_dir"])
        token, info = _read_regular(_absolute(config["token_file"]), 1026)
        token = token.strip(b"\r\n")
        if (
            not 32 <= len(token) <= 1024
            or any(byte < 33 or byte > 126 for byte in token)
            or os.name != "nt" and info.st_mode & 0o077
        ):
            raise HostError("host_token_file_invalid")
        prefix = b"Bear" + b"er "
        authorization = prefix + token
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.data_dir.is_symlink() or (
            os.name != "nt" and self.data_dir.stat().st_mode & 0o077
        ):
            raise HostError("host_data_directory_invalid")
        self.run_dir = self.data_dir / "run"
        self.run_dir.mkdir(mode=0o700, exist_ok=True)
        if self.run_dir.is_symlink() or (
            os.name != "nt" and self.run_dir.stat().st_mode & 0o077
        ):
            raise HostError("host_runtime_directory_invalid")
        self.lease = SingleInstance(str(self.data_dir), service="agent-chat")
        try:
            self.lease.acquire()
        except AlreadyRunningError:
            raise HostError("host_already_running") from None
        try:
            self.store = ChatStore(self.data_dir, config["installation_id"])
            if os.name == "nt":
                self.server = _Server(("127.0.0.1", 0), self.store, authorization)
                self.transport = "tcp"
                self.endpoint = f"127.0.0.1:{self.server.server_address[1]}"
            else:
                self._socket_path = self.run_dir / ("c-" + uuid.uuid4().hex[:8] + ".sock")
                if len(os.fsencode(self._socket_path)) > 103:
                    raise HostError("host_socket_path_limit")
                self.server = _UnixServer(str(self._socket_path), self.store, authorization)
                info = self._socket_path.stat()
                self._socket_identity = info.st_dev, info.st_ino
                self.transport, self.endpoint = "unix", str(self._socket_path)
            self._advertised = True
            self._write_endpoint(self.run_dir, self.transport, self.endpoint)
        except BaseException:
            self.close()
            raise

    def serve(self, stop):
        while not stop.is_set():
            self.server.handle_request()

    def drain(self):
        self.server.draining = True
        return self.store.drain()

    def close(self):
        drain_error = None
        if self.server is not None:
            self.server.draining = True
        if self.store is not None:
            try:
                self.store.drain()
            except ChatError as error:
                drain_error = error
            # close waits for the transaction boundary even if timed drain could not.
            self.store.close()
            self.store = None
        if self.server is not None:
            self.server.server_close()
            self.server = None
        if self._advertised:
            self._clear_endpoint(self.run_dir)
            self._advertised = False
        if self._socket_identity is not None:
            info = self._socket_path.lstat()
            if (info.st_dev, info.st_ino) == self._socket_identity and stat.S_ISSOCK(info.st_mode):
                self._socket_path.unlink()
            else:
                raise HostError("host_socket_ownership_changed")
            self._socket_identity = None
        if self.lease is not None:
            self.lease.release()
            self.lease = None
        self._instance_guard.close()
        if drain_error is not None:
            raise drain_error

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        return _run(args.config)
    except HostError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr, flush=True)
        return 1


def _run(config_path):
    try:
        from agent_procutil.daemon_instances import admit_daemon_instance
    except ImportError:
        raise HostError("host_service_dependencies_missing") from None
    with admit_daemon_instance("agent-chat-service", source=__file__, purpose="production"):
        return _serve(config_path)


def _serve(config_path):
    stop = threading.Event()
    previous = {}
    try:
        for name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, name)
            previous[sig] = signal.signal(sig, lambda *_: stop.set())
        with HostedChat(load_config(config_path)) as host:
            print(json.dumps({
                "schema": "agent-chat.host-ready", "version": 1,
                "transport": host.transport, "endpoint": host.endpoint,
                "installation_id": host.store.installation_id,
            }), flush=True)
            host.serve(stop)
        return 0
    except (HostError, ChatError, OSError, sqlite3.Error) as error:
        if isinstance(error, HostError):
            value = {"error": str(error)}
        elif isinstance(error, ChatError):
            value = {"error": "host_contract_invalid", "reason": error.code}
        elif isinstance(error, sqlite3.Error):
            value = {"error": "host_storage_failed",
                     "reason": getattr(error, "sqlite_errorname", type(error).__name__)}
        else:
            value = {"error": "host_os_failed", "errno": error.errno,
                     "winerror": getattr(error, "winerror", None)}
        print(json.dumps(value), file=sys.stderr, flush=True)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
