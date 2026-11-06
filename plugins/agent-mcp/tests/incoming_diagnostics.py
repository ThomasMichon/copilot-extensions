"""Fixture-only comparison of exact matcher inputs; never shipped as runtime code."""

from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import threading
from weakref import WeakKeyDictionary

FIELD = "_fixture_context"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_PATH = re.compile(r"/[A-Za-z0-9_/~.-]*\Z")


class Diagnostics:
    def __init__(self, sink):
        self.sink = sink
        self.snapshot = ContextVar("fixture-context", default=None)
        self.direct = ContextVar("fixture-direct", default=False)
        self.seen = WeakKeyDictionary()

    @staticmethod
    def _mac(key, domain, value):
        data = json.dumps([domain, value], sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(bytes.fromhex(key), data, hashlib.sha256).hexdigest()

    def capture(self, data, key):
        """Observe the actual bytes consumed by the original identity function."""
        try:
            source = json.loads(data)
            config = {}

            def walk(value, path):
                if isinstance(value, dict) and value:
                    for name, child in value.items():
                        walk(child, path + "/" + name.replace("~", "~0").replace("/", "~1"))
                elif isinstance(value, list) and value:
                    for index, child in enumerate(value):
                        walk(child, path + "/" + str(index))
                else:
                    config[path] = self._mac(key, path, value)

            walk(source["config"], "/config")
            observed = {
                "key_tag": self._mac(key, "fixture-key", None),
                "config": config,
                "environment": {name: self._mac(key, name, value)
                                for name, value in source["environment"].items()},
                "cwd": self._mac(key, "cwd", source["cwd"]),
            }
            self.snapshot.set(observed if len(json.dumps(observed)) <= 32768 else None)
        except Exception:
            self.snapshot.set(None)

    @staticmethod
    def _valid(value):
        if not isinstance(value, dict) or set(value) != {"key_tag", "config", "environment", "cwd"}:
            return False
        if not isinstance(value["config"], dict) or not isinstance(value["environment"], dict):
            return False
        if len(value["config"]) + len(value["environment"]) > 4096:
            return False
        pairs = [("key_tag", value["key_tag"]), ("cwd", value["cwd"])]
        for group, pattern in (("config", _PATH), ("environment", _NAME)):
            for name, digest in value[group].items():
                if not isinstance(name, str) or len(name) > 256 or not pattern.fullmatch(name):
                    return False
                pairs.append((name, digest))
        return all(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
                   for _, digest in pairs)

    def compare(self, remote):
        local = self.snapshot.get()
        if not self._valid(local) or not self._valid(remote) or local["key_tag"] != remote["key_tag"]:
            return {"comparison": "unknown", "cwd_equal": None,
                    "different_environment": [], "different_config": []}
        return {
            "comparison": "compared", "cwd_equal": local["cwd"] == remote["cwd"],
            "different_environment": sorted(name for name in local["environment"].keys() | remote["environment"].keys()
                                            if local["environment"].get(name) != remote["environment"].get(name)),
            "different_config": sorted(name for name in local["config"].keys() | remote["config"].keys()
                                       if local["config"].get(name) != remote["config"].get(name)),
        }

    def emit(self, event, **detail):
        try:
            self.sink({"event": event, **detail})
        except Exception:
            pass  # Missing diagnostic delivery is unknown, never a changed decision.

    def fields(self, original):
        @wraps(original)
        def wrapped(*args, **kwargs):
            token = self.snapshot.set(None)
            try:
                result = original(*args, **kwargs)
                observed = self.snapshot.get()
                if self._valid(observed):
                    return {**result, FIELD: observed}
                return result
            finally:
                self.snapshot.reset(token)
        return wrapped

    def config(self, original, refused):
        @wraps(original)
        def wrapped(bridge, request, loader, *, repository_root):
            token = self.snapshot.set(None)
            try:
                result = original(bridge, request, loader, repository_root=repository_root)
            except refused:
                self.emit("context_refused", **self.compare(request.get(FIELD)))
                raise
            else:
                self.emit("context_accepted", **self.compare(request.get(FIELD)))
                return result
            finally:
                self.snapshot.reset(token)
        return wrapped

    def warm(self, original, refused, unknown):
        @wraps(original)
        async def wrapped(*args, **kwargs):
            try:
                result = await original(*args, **kwargs)
            except refused:
                self.emit("warm_refused")
                raise
            except unknown:
                self.emit("warm_unknown_outcome")
                raise
            except OSError:
                self.emit("warm_unavailable")
                raise
            except Exception:
                self.emit("warm_other_failure")
                raise
            else:
                self.emit("warm_accepted" if isinstance(result, dict) and result.get("ok") is True
                          else "warm_error_response")
                return result
        return wrapped

    def attempt(self, original):
        @wraps(original)
        def wrapped(*args, **kwargs):
            self.direct.set(False)
            result = original(*args, **kwargs)
            self.direct.set(result is None and not kwargs.get("no_serve", False))
            self.emit("direct_selected" if result is None else "warm_path_returned")
            return result
        return wrapped

    def call(self, original):
        @wraps(original)
        async def wrapped(*args, **kwargs):
            self.emit("direct_fallback" if self.direct.get() else "direct_execution")
            return await original(*args, **kwargs)
        return wrapped

    def pool(self, original):
        @wraps(original)
        async def wrapped(pool, key, cfg):
            before = pool._entries.get(key)
            entry = await original(pool, key, cfg)
            seen = self.seen.setdefault(pool, set())
            event = ("pool_reuse" if before is entry else
                     "pool_reopen" if before is not None or key in seen else "pool_open")
            if len(seen) < 128:
                seen.add(key)
            else:
                event = "pool_unknown"
            self.emit(event)
            return entry
        return wrapped


def file_sink(directory, phase):
    if phase not in {"old", "new", "unit"}:
        raise ValueError("unknown fixture phase")
    directory = Path(directory)
    lock = threading.Lock()
    count = 0

    def write(record):
        nonlocal count
        allowed = {"event", "comparison", "cwd_equal", "different_environment", "different_config"}
        events = {
            "context_refused", "context_accepted", "warm_refused", "warm_unknown_outcome",
            "warm_unavailable", "warm_other_failure", "warm_accepted", "warm_error_response", "direct_selected",
            "warm_path_returned", "direct_fallback", "direct_execution",
            "pool_reuse", "pool_reopen", "pool_open", "pool_unknown",
        }
        if not isinstance(record, dict) or set(record) - allowed or record.get("event") not in events:
            return
        for name, pattern in (("different_environment", _NAME), ("different_config", _PATH)):
            if name in record and (
                not isinstance(record[name], list)
                or any(not isinstance(value, str) or len(value) > 256 or not pattern.fullmatch(value)
                       for value in record[name])
            ):
                return
        if "cwd_equal" in record and record["cwd_equal"] is not None and type(record["cwd_equal"]) is not bool:
            return
        if "comparison" in record and record["comparison"] not in {"compared", "unknown"}:
            return
        role = "server" if record["event"].startswith(("context_", "pool_")) else "caller"
        data = json.dumps({"phase": phase, "role": role, **record}, separators=(",", ":")).encode() + b"\n"
        if len(data) > 16384:
            return
        with lock:
            if count >= 128:
                return
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / f"{phase}-{os.getpid()}.jsonl").open("ab") as stream:
                stream.write(data)
            count += 1
    return write


def read_records(directory):
    records = []
    for path in sorted(Path(directory).glob("*.jsonl")):
        with path.open("rb") as stream:
            for _ in range(128):
                line = stream.readline(16385)
                if not line:
                    break
                if len(line) > 16384:
                    return [{"event": "diagnostic_unknown"}]
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError):
                    return [{"event": "diagnostic_unknown"}]
                records.append(record)
                if len(json.dumps(records)) > 65536:
                    return [{"event": "diagnostic_unknown"}]
    return records or [{"event": "diagnostic_unknown"}]


def actor_environment(environment, flag):
    return {name: value for name, value in environment.items() if name.casefold() != flag.casefold()}


def instrument_payload(payload, directory, phase):
    """Patch only the private copy, before its canonical producer refresh."""
    package = Path(payload) / "src" / "agent_mcp"
    (package / "_fixture_diagnostics.py").write_bytes(Path(__file__).read_bytes())
    context = package / "execution_context.py"
    source = context.read_text(encoding="utf-8")
    assert "_fixture_diagnostics.capture" not in source
    anchor = "    return hmac.new(bytes.fromhex(key), data, hashlib.sha256).hexdigest()"
    assert source.count(anchor) == 1
    source = source.replace(anchor, "    _fixture_diagnostics.capture(data, key)\n" + anchor)
    source += (
        "\nfrom ._fixture_diagnostics import Diagnostics, file_sink\n"
        f"_fixture_diagnostics = Diagnostics(file_sink({str(directory)!r}, {phase!r}))\n"
        "request_fields = _fixture_diagnostics.fields(request_fields)\n"
        "load_config = _fixture_diagnostics.config(load_config, SharingUnavailable)\n"
    )
    context.write_text(source, encoding="utf-8")
    additions = {
        "ipc.py": "\nfrom . import execution_context as _fixture_context, incoming as _fixture_incoming\n"
                  "call_via_socket = _fixture_context._fixture_diagnostics.warm("
                  "call_via_socket, _fixture_context.SharingUnavailable, _fixture_incoming.OutcomeUnknown)\n",
        "serve.py": "\nWarmPool._entry_for_locked = execution_context._fixture_diagnostics.pool(WarmPool._entry_for_locked)\n",
        "__main__.py": "\nfrom . import execution_context as _fixture_context\n"
                       "_try_serve_call = _fixture_context._fixture_diagnostics.attempt(_try_serve_call)\n"
                       "_run_call = _fixture_context._fixture_diagnostics.call(_run_call)\n",
    }
    for name, addition in additions.items():
        path = package / name
        text = path.read_text(encoding="utf-8")
        # CLI's terminal main() call must see the fixture wrappers.
        if name == "__main__.py":
            anchor = 'if __name__ == "__main__":'
            assert text.count(anchor) == 1
            text = text.replace(anchor, addition + "\n" + anchor)
        else:
            text += addition
        path.write_text(text, encoding="utf-8")
