"""Non-authoritative diagnostics for retained installer children (stdlib only)."""

from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import codecs
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import subprocess
import threading
from datetime import datetime, timedelta, timezone


_FILES = None
_WARNED = False
_ACTIVE = None
_CHECKPOINT_BOUND = 4 * 1024 * 1024


def files():
    global _FILES
    if _FILES is None:
        path = Path(__file__).with_name("installer_diagnostics_files.py")
        ordinary(path)
        spec = importlib.util.spec_from_file_location("_installer_diagnostics_files", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _FILES = module
    return _FILES


def warn():
    global _WARNED
    os.environ["COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING"] = "1"
    try:
        directory = context()
        _record(directory / "missing-diagnostics.json", {"missing_diagnostics": True})
    except (OSError, ValueError, TypeError):
        pass
    if _WARNED:
        return
    _WARNED = True
    try:
        sink = getattr(sys.stderr, "_installer_original", sys.stderr)
        if sink is None:
            return
        print("WARNING: installer diagnostics unavailable; continuing with missing diagnostics.",
              file=sink, flush=True)
    except (OSError, ValueError):
        pass


def ordinary(path: Path):
    for item in (path, *path.parents):
        if not os.path.lexists(item):
            continue
        value = item.lstat()
        if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
            raise ValueError("diagnostic paths must not contain links or reparse points")


def diagnostic_root(environment=None):
    environment = os.environ if environment is None else environment
    raw = environment.get("USERPROFILE" if os.name == "nt" else "HOME")
    if not raw or not Path(raw).is_absolute():
        raise ValueError("diagnostics require an absolute per-user home")
    home = Path(raw)
    root = home / ".copilot-extensions" / "installer-diagnostics"
    ordinary(root)
    return root


def _read_record(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate diagnostic metadata key")
            value[key] = item
        return value

    fd = files().open_file(path)
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        text = handle.read(8193)
    if len(text) > 8192:
        raise ValueError("diagnostic metadata exceeds its bound")
    value = json.loads(text, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("diagnostic metadata must be an object")
    return value


def context(environment=None):
    environment = os.environ if environment is None else environment
    root = diagnostic_root(environment)
    attempt = environment.get("COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT", "")
    if re.fullmatch("[a-f0-9]{32}", attempt) is None:
        raise ValueError("missing installer diagnostic correlation")
    directory = root / attempt
    files().private_directory(root)
    files().private_directory(directory)
    receipt = _read_record(directory / "attempt.json")
    if (receipt.get("schema") != "copilot-extensions.installer-diagnostics"
            or receipt.get("attempt") != attempt
            or receipt.get("plugin") != "agent-worktrees"):
        raise ValueError("installer diagnostics correlation mismatch")
    return directory


def _redactor():
    # Canonical source is packaged beside this standalone helper, not imported
    # through ambient PYTHONPATH or another installed runtime.
    path = Path(__file__).with_name("installer_diagnostics_redact.py")
    ordinary(path)
    spec = importlib.util.spec_from_file_location("_installer_diagnostics_redact", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.redact


def reference():
    try:
        directory = context()
        return {"attempt": directory.name, "directory": str(directory),
                "missing_diagnostics": (os.environ.get("COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING") != "0"
                                        or (directory / "missing-diagnostics.json").exists())}
    except (OSError, ValueError, TypeError):
        return {"missing_diagnostics": True}


def _record(path, document):
    fd = files().open_file(path, create=True)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(document, handle)
        handle.flush()
        os.fsync(handle.fileno())


def reserve_launch(environment):
    """Reserve correlation, not evidence that an installer executed."""
    environment = dict(environment)
    root = diagnostic_root(environment)
    ordinary(root.parent)
    root.parent.mkdir(exist_ok=True)
    files().private_directory(root, create=True)
    attempt = secrets.token_hex(16)
    directory = files().private_directory(root / attempt, create=True)
    _record(directory / "attempt.json", {
        "schema": "copilot-extensions.installer-diagnostics", "attempt": attempt,
        "plugin": "agent-worktrees", "attribution": "UNATTRIBUTED",
        "producer": "launch-request", "started": datetime.now(timezone.utc).isoformat(),
    })
    _record(directory / "launch-request.json", {"attempt": attempt, "kind": "launch-request"})
    environment.update({
        "COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT": attempt,
        "COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY": str(directory),
        "COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING": "0",
    })
    environment.pop("COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER", None)
    return environment


def launch_reference(environment):
    """Read only bounded producer evidence; launch metadata is not entry proof."""
    directory = context(environment)
    result = {
        "attempt": directory.name, "directory": str(directory),
        "attribution": "UNATTRIBUTED", "installer_entry": "missing",
        "status": "missing", "missing_diagnostics": True,
    }
    try:
        writers = []
        for count, path in enumerate(directory.iterdir()):
            if count >= 512:
                raise ValueError("diagnostic writer inventory exceeds its bound")
            if not path.name.endswith(".start.json"):
                continue
            identifier = path.name.removesuffix(".start.json")
            if re.fullmatch("[a-f0-9]{32}", identifier) is None:
                raise ValueError("invalid diagnostic writer name")
            record = _read_record(path)
            if record.get("writer") != identifier:
                raise ValueError("diagnostic writer mismatch")
            result["installer_entry"] = "observed"
            with os.fdopen(files().open_file(directory / f"{identifier}.log"), "rb"):
                pass
            writers.append(identifier)
        if writers:
            result.update(
                status="available", missing_diagnostics=False, writers=sorted(writers),
            )
        if (directory / "missing-diagnostics.json").exists():
            _read_record(directory / "missing-diagnostics.json")
            result.update(status="missing", missing_diagnostics=True)
        association = directory / "installation.json"
        if association.exists():
            record = _read_record(association)
            _validate_association(record, directory)
            result.update(attribution="VALIDATED_INSTALLATION_RECORDED", installation=record)
    except (OSError, ValueError, TypeError):
        result.update(status="unknown", missing_diagnostics=True)
    return result


def _validate_association(record, directory):
    if set(record) != {
        "schema", "attempt", "log_directory", "runtime_root", "runtime_slot",
        "package_version", "runtime_generation", "deployment_manifest_sha256",
    } or (
        record["schema"] != "copilot-extensions.installer-diagnostics.installation"
        or record["attempt"] != directory.name
        or record["log_directory"] != str(directory)
        or not isinstance(record["runtime_root"], str)
        or not Path(record["runtime_root"]).is_absolute()
        or any(not isinstance(record[key], str) or not record[key] or len(record[key]) > 512
               for key in ("runtime_slot", "package_version"))
        or record["runtime_slot"] in {".", ".."}
        or any(char in record["runtime_slot"] for char in "/\\\r\n\0")
        or (record["runtime_generation"] is not None
            and (not isinstance(record["runtime_generation"], str)
                 or len(record["runtime_generation"]) > 512))
        or re.fullmatch("[a-f0-9]{64}", record["deployment_manifest_sha256"]) is None
    ):
        raise ValueError("invalid installer diagnostic installation association")


def associate_installed(installation):
    """Record the lifecycle owner's validated publication, never authorize it."""
    directory = context()
    identifier = os.environ.get("COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER", "")
    if re.fullmatch("[a-f0-9]{32}", identifier) is None:
        raise ValueError("installation association requires its active diagnostic writer")
    if _read_record(directory / f"{identifier}.start.json").get("writer") != identifier:
        raise ValueError("installation association writer mismatch")
    with os.fdopen(files().open_file(directory / f"{identifier}.log"), "rb"):
        pass
    record = {
        "schema": "copilot-extensions.installer-diagnostics.installation",
        "attempt": directory.name, "log_directory": str(directory), **installation,
    }
    _validate_association(record, directory)
    path = directory / "installation.json"
    if path.exists():
        if _read_record(path) != record:
            raise ValueError("installer diagnostics already name another publication")
    else:
        _record(path, record)


def phase(name):
    """Fixed phase labels survive missing presentation without exposing inputs."""
    if re.fullmatch("[a-z][a-z0-9-]{0,63}", name) is None:
        raise ValueError("invalid installer diagnostic phase")
    text = f"Installer phase: {name}"
    if _ACTIVE is not None:
        _ACTIVE.feed("stderr", text + "\n")
    try:
        sink = getattr(sys.stderr, "_installer_original", sys.stderr)
        if sink is not None:
            print(text, file=sink, flush=True)
    except (OSError, ValueError):
        warn()


def complete_launch(environment, outcome, native_exit=None):
    """Persist the caller's outcome without completing any inherited writer."""
    directory = context(environment)
    if outcome not in {"returned", "interrupted", "timeout", "spawn-failed"}:
        raise ValueError("invalid diagnostic launch outcome")
    if native_exit is not None and type(native_exit) is not int:
        raise ValueError("invalid diagnostic native exit")
    result = launch_reference(environment)
    _record(directory / "launch-result.json", {
        **result, "outcome": outcome, "native_exit": native_exit,
        "finished": datetime.now(timezone.utc).isoformat(),
    })
    return result


def _initialize():
    if os.environ.get("COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT"):
        return context(), False
    root = diagnostic_root()
    ordinary(root.parent)
    root.parent.mkdir(exist_ok=True)
    files().private_directory(root, create=True)
    attempt = secrets.token_hex(16)
    directory = files().private_directory(root / attempt, create=True)
    _record(directory / "attempt.json", {
        "schema": "copilot-extensions.installer-diagnostics", "attempt": attempt,
        "plugin": "agent-worktrees", "attribution": "UNATTRIBUTED", "producer": "python",
        "started": datetime.now(timezone.utc).isoformat(),
    })
    os.environ["COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT"] = attempt
    os.environ["COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY"] = str(directory)
    _prune(root, directory)
    return directory, True


def _prune(root, active):
    for directory in root.iterdir():
        if directory == active or re.fullmatch("[a-f0-9]{32}", directory.name) is None:
            continue
        try:
            files().private_directory(directory)
            if not (directory / "completed.json").is_file():
                continue
            receipt = _read_record(directory / "attempt.json")
            if (receipt.get("producer") != "python" or receipt.get("plugin") != "agent-worktrees"
                    or receipt.get("attempt") != directory.name
                    or receipt.get("schema") != "copilot-extensions.installer-diagnostics"):
                continue
            completed = _read_record(directory / "completed.json")
            age = datetime.fromisoformat(completed["completed"]).astimezone(timezone.utc)
            if age >= datetime.now(timezone.utc) - timedelta(days=30):
                continue
            entries = list(directory.iterdir())
            if any(path.is_dir() or path.name.endswith(".pending") for path in entries):
                continue
            if any(path.name.endswith(".start.json") and not path.with_name(
                path.name.replace(".start.json", ".completed.json")).is_file() for path in entries):
                continue
            for path in entries:
                os.close(files().open_file(path))
            for path in entries:
                path.unlink()
            directory.rmdir()
        except (OSError, ValueError, KeyError, TypeError):
            # Foreign, incomplete or unreadable attempts are never repaired.
            continue


class Lines:
    """Finite logical records; incomplete credential prefixes remain together."""

    _PREFIX = re.compile(
        r"(?:authorization\s*:|http\.extraheader\s*=|\b" + "bea" + "rer"
        + r"|\b" + "ba" + "sic" + r")\s*$", re.I,
    )

    def __init__(self, emit):
        self.emit, self.pending, self.credential, self.dropping = emit, "", "", False

    def line(self, line):
        if self.credential:
            line, self.credential = self.credential + "\n" + line, ""
        if len(line) > 65536:
            self.emit("[oversized diagnostic record omitted]")
            self.credential = "Authorization:" if self._PREFIX.search(line) else ""
        elif self._PREFIX.search(line):
            self.credential = line
        else:
            self.emit(line)

    def feed(self, text):
        for offset in range(0, len(text), 8192):
            self.pending += text[offset:offset + 8192]
            while "\n" in self.pending:
                line, self.pending = self.pending.split("\n", 1)
                if not self.dropping:
                    self.line(line.rstrip("\r"))
                self.dropping = False
            if len(self.pending) > 65536:
                if not self.dropping:
                    self.emit("[oversized diagnostic record omitted]")
                # Omitting a long prefix must not expose its next-line value.
                continuation = self._PREFIX.search(self.credential + "\n" + self.pending)
                self.pending = ""
                self.credential = "Authorization:" if continuation else ""
                self.dropping = True

    def finish(self):
        if self.pending and not self.dropping:
            self.line(self.pending)
        if self.credential:
            self.emit(self.credential)
        self.pending, self.credential = "", ""


class Producer:
    def __init__(self, directory, identifier):
        self.failed = False
        self.directory, self.identifier = directory, identifier
        self.checkpoints, self.sequence, self.closed = None, 0, False
        self.binary = {}
        self.redact = _redactor()
        self.lock = threading.RLock()
        self.output = os.fdopen(files().open_file(directory / f"{identifier}.log", create=True),
                                "w", encoding="utf-8", errors="replace", buffering=1)
        self.lines = {name: Lines(lambda line, channel=name: self.emit(channel, line))
                      for name in ("stdout", "stderr", "error")}

    def emit(self, channel, text):
        if not self.failed:
            try:
                self.output.write(f"[{channel}] {_safe(self.redact, text)}\n")
                self.output.flush()
            except (OSError, ValueError, UnicodeError):
                self.failed = True
                warn()

    def feed(self, channel, text):
        with self.lock:
            if not self.failed and not self.closed:
                self.lines[channel].feed(text)

    def checkpoint(self):
        """Persist raw logical state on explicit flush, not completion evidence."""
        with self.lock:
            if self.failed or self.closed:
                return
            channels = {name: {"pending": line.pending, "credential": line.credential,
                               "dropping": line.dropping} for name, line in self.lines.items()}
            binary = {}
            for name, stream in self.binary.items():
                pending, flag = stream.decoder.getstate()
                binary[name] = {"encoding": stream.encoding, "pending": pending.hex(), "flag": flag}
            has_pending = any(state["pending"] or state["credential"] or state["dropping"]
                              for state in channels.values()) or any(state["pending"] for state in binary.values())
            if self.checkpoints is None and not has_pending:
                return
            try:
                self.sequence += 1
                record = {
                    "schema": "copilot-extensions.installer-diagnostics.producer-checkpoint",
                    "writer": self.identifier, "sequence": self.sequence,
                    "channels": channels, "binary": binary,
                }
                payload = json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
                if len(payload) > _CHECKPOINT_BOUND:
                    raise ValueError("producer checkpoint exceeds its record bound")
                if self.checkpoints is None:
                    path = self.directory / f"{self.identifier}.producer.pending"
                    self.checkpoints = os.fdopen(files().open_file(path, create=True), "wb", buffering=0)
                data = memoryview(payload + b"\n")
                while data:
                    written = self.checkpoints.write(data)
                    if not written:
                        raise OSError("producer checkpoint write did not advance")
                    data = data[written:]
                self.checkpoints.flush()
                os.fsync(self.checkpoints.fileno())
                for channel, state in channels.items():
                    preview = _partial_preview(state, binary.get(channel), self.redact)
                    if preview:
                        self.emit("partial " + channel, preview)
            except (OSError, ValueError, UnicodeError):
                self.failed = True
                warn()

    def finish(self):
        with self.lock:
            for lines in self.lines.values():
                lines.finish()
            try:
                self.output.flush()
                os.fsync(self.output.fileno())
                self.output.close()
            except (OSError, ValueError):
                self.failed = True
                warn()
            self.closed = True
            if self.checkpoints is not None:
                try:
                    self.checkpoints.close()
                except OSError:
                    self.failed = True
                    warn()


class BinaryTee:
    def __init__(self, original, producer, channel, text_original):
        self._installer_original = original
        self._installer_text_original = text_original
        self.producer, self.channel = producer, channel
        self.encoding = getattr(text_original, "encoding", None) or "utf-8"
        self.decoder = codecs.getincrementaldecoder(self.encoding)(errors="replace")
        producer.binary[channel] = self

    def write(self, data):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            return self._installer_original.write(data)
        with self.producer.lock:
            encoding = getattr(self._installer_text_original, "encoding", None) or "utf-8"
            if encoding != self.encoding:
                self.finish()
                self.encoding = encoding
                self.decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
            self.producer.feed(self.channel, self.decoder.decode(bytes(data)))
        return self._installer_original.write(data) if self._installer_original is not None else len(data)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def finish(self):
        self.producer.feed(self.channel, self.decoder.decode(b"", final=True))

    def flush(self):
        self.producer.checkpoint()
        if self._installer_original is not None:
            return self._installer_original.flush()

    def fileno(self):
        if self._installer_original is None:
            import io
            raise io.UnsupportedOperation("original stream has no descriptor")
        return self._installer_original.fileno()

    def isatty(self):
        return self._installer_original.isatty() if self._installer_original is not None else False

    def __getattr__(self, name):
        return getattr(self._installer_original, name)


class TextTee:
    def __init__(self, original, producer, channel):
        self._installer_original = original
        self.producer, self.channel = producer, channel
        buffer = getattr(original, "buffer", None)
        self._buffer = BinaryTee(buffer, producer, channel, original)

    @property
    def buffer(self):
        if self._installer_original is not None and not hasattr(self._installer_original, "buffer"):
            raise AttributeError("original stream has no binary buffer")
        return self._buffer

    def write(self, text):
        if not isinstance(text, str):
            return self._installer_original.write(text)
        self.producer.feed(self.channel, text)
        return self._installer_original.write(text) if self._installer_original is not None else len(text)

    def flush(self):
        self.producer.checkpoint()
        if self._installer_original is not None:
            return self._installer_original.flush()

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def fileno(self):
        if self._installer_original is None:
            import io
            raise io.UnsupportedOperation("original stream has no descriptor")
        return self._installer_original.fileno()

    def isatty(self):
        return self._installer_original.isatty() if self._installer_original is not None else False

    @property
    def encoding(self):
        return self._installer_original.encoding if self._installer_original is not None else "utf-8"

    @property
    def errors(self):
        return self._installer_original.errors if self._installer_original is not None else "replace"

    def __getattr__(self, name):
        return getattr(self._installer_original, name)


@contextmanager
def writer(*, announce=False):
    """Record this Python producer in-process without changing subprocess stdio."""
    global _ACTIVE
    if _ACTIVE is not None:
        yield _ACTIVE
        return
    names = ("COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT", "COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER",
             "COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY")
    previous = {name: os.environ.get(name) for name in names}
    directory, identifier, producer, owner = None, secrets.token_hex(16), None, False
    original = sys.stdout, sys.stderr
    tees = ()
    try:
        directory, owner = _initialize()
        parent = previous[names[1]] or ""
        _record(directory / f"{identifier}.start.json", {
            "writer": identifier, "parent": parent if re.fullmatch("[a-f0-9]{32}", parent) else None,
            "kind": "python-installer" if owner else "retained-installer",
        })
        producer = Producer(directory, identifier)
        os.environ[names[1]] = identifier
        os.environ["COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING"] = "0"
        if announce and original[1] is not None and os.environ.get("COPILOT_EXTENSIONS_HUMAN_UTF8") != "1":
            print(f"Installer diagnostics: {directory}", file=original[1], flush=True)
    except (OSError, ValueError, ImportError, TypeError):
        warn()
    if producer is not None:
        _ACTIVE = producer
        tees = TextTee(original[0], producer, "stdout"), TextTee(original[1], producer, "stderr")
        sys.stdout, sys.stderr = tees
    outcome, failure = 0, None
    try:
        yield producer
    except BaseException as error:
        outcome = (0 if error.code is None else error.code if isinstance(error.code, int) else 1) if isinstance(error, SystemExit) else (
            130 if isinstance(error, KeyboardInterrupt) else 1)
        failure = {"error_class": type(error).__name__}
        if producer is not None and isinstance(error, SystemExit) and error.code is not None and not isinstance(error.code, int):
            producer.feed("error", str(error.code) + "\n")
        if producer is not None and not isinstance(error, SystemExit):
            if isinstance(error, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
                failure["native_exit"] = getattr(error, "returncode", None)
                producer.feed("error", "native child failed\n")
                for channel in ("stdout", "stderr"):
                    text = getattr(error, channel, None)
                    if text is None and channel == "stdout":
                        text = getattr(error, "output", None)
                    if text is not None:
                        producer.feed("error", text.decode("utf-8", "replace") if isinstance(text, bytes) else str(text))
                        producer.feed("error", "\n")
            else:
                producer.feed("error", str(error) + "\n")
        raise
    finally:
        if producer is not None:
            for tee in tees:
                tee._buffer.finish()
            producer.finish()
        sys.stdout, sys.stderr = original
        _ACTIVE = None
        if directory is not None and producer is not None:
            try:
                result = {"exit": outcome, "completed": datetime.now(timezone.utc).isoformat(),
                          "missing_diagnostics": producer.failed, **(failure or {})}
                _record(directory / f"{identifier}.completed.json", result)
                if owner:
                    _record(directory / "completed.json", result)
            except (OSError, ValueError):
                warn()
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _safe(redact, text):
    text = re.sub(r"(?i)(error:\s*unrecognized arguments:).*", r"\1 [arguments omitted]", text)
    text = re.sub(r"(?i)(error:\s*argument\s+[^:]+:).*", r"\1 [value omitted]", text)
    if any(marker in text for marker in (
        "UPDATE_DRIVER_WITNESS", "--governance-generation", "COPILOT_EXTENSIONS_CONTEXT",
    )):
        return "[sensitive diagnostic record omitted]"
    if re.search(r"""(?i)(?:"?(?:argv|environ(?:ment)?)"?\s*[:=]|\bCommand\s+[\['"])""", text):
        return "[invocation diagnostic record omitted]"
    schemes = "(?:" + "bea" + "rer|ba" + "sic)"
    # Consume the complete credential before erasing its header/scheme prefix.
    text = re.sub(
        r"(?i)(http\.extraheader\s*=)\s*(?:authorization\s*:\s*)*(?:" + schemes + r"\s+)*[^\r\n]*",
        r"\1***", text,
    )
    text = re.sub(
        r"(?i)(authorization\s*:)\s*(?:" + schemes + r"\s+)*[^\r\n]*",
        r"\1 ***", text,
    )
    return redact(text)


def _partial_preview(state, binary, redact):
    if state["dropping"]:
        return "[oversized incomplete record; credential context retained]"
    text = state["credential"]
    if state["pending"]:
        text += ("\n" if text else "") + state["pending"]
    if not text and not (binary and binary["pending"]):
        return ""
    # A final token is not yet a complete logical value. Mask known partial
    # token shapes and URL authorities before showing any incomplete preview.
    text = re.sub(r"\b(?:gh[pousr]_|github_pat_)\w*", "***", text)
    text = re.sub(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s]*", "[incomplete URL authority]", text)
    text = _safe(redact, text)
    text = re.sub(r"\S+\s*$", "[incomplete token omitted]", text)
    if binary and binary["pending"]:
        text += " [incomplete encoded unit retained privately]"
    return text


def _validate_checkpoint(record, identifier):
    if (set(record) != {"schema", "writer", "sequence", "channels", "binary"}
            or record["schema"] != "copilot-extensions.installer-diagnostics.producer-checkpoint"
            or record["writer"] != identifier or type(record["sequence"]) is not int
            or record["sequence"] < 1 or not isinstance(record["channels"], dict)
            or set(record["channels"]) != {"stdout", "stderr", "error"}
            or not isinstance(record["binary"], dict)
            or not set(record["binary"]) <= {"stdout", "stderr"}):
        raise ValueError("invalid producer checkpoint identity")
    for state in record["channels"].values():
        if (not isinstance(state, dict) or set(state) != {"pending", "credential", "dropping"}
                or type(state["dropping"]) is not bool
                or any(not isinstance(state[key], str) or len(state[key]) > 65536
                       for key in ("pending", "credential"))):
            raise ValueError("invalid producer logical state")
    for state in record["binary"].values():
        if (not isinstance(state, dict) or set(state) != {"encoding", "pending", "flag"}
                or not isinstance(state["encoding"], str) or len(state["encoding"]) > 128
                or not isinstance(state["pending"], str) or len(state["pending"]) > 131072
                or len(state["pending"]) % 2 or re.fullmatch("[a-f0-9]*", state["pending"]) is None
                or type(state["flag"]) is not int):
            raise ValueError("invalid producer binary state")


def recover_producer_checkpoint(environment, identifier):
    """Recover a fixed private snapshot; never assert quiescence or retire raw."""
    if re.fullmatch("[a-f0-9]{32}", identifier) is None:
        raise ValueError("invalid producer writer")
    directory = context(environment)
    if _read_record(directory / f"{identifier}.start.json").get("writer") != identifier:
        raise ValueError("producer writer identity mismatch")
    path = directory / f"{identifier}.producer.pending"
    with os.fdopen(files().open_file(path), "rb") as source:
        length = os.fstat(source.fileno()).st_size
        start = max(0, length - (2 * _CHECKPOINT_BOUND + 2))
        source.seek(start)
        data = source.read(length - start)
    if len(data) != length - start:
        raise ValueError("producer checkpoint changed during snapshot; raw retained")
    if start:
        data = data.partition(b"\n")[2]
    complete = data.split(b"\n")[:-1]
    if not complete:
        raise ValueError("no complete producer checkpoint; raw retained")
    payload = complete[-1]
    if len(payload) > _CHECKPOINT_BOUND:
        raise ValueError("producer checkpoint exceeds its record bound")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate checkpoint key")
            result[key] = value
        return result

    record = json.loads(payload.decode("utf-8"), object_pairs_hook=unique)
    if not isinstance(record, dict):
        raise ValueError("producer checkpoint must be an object")
    _validate_checkpoint(record, identifier)
    output = directory / f"{identifier}.recovered.{secrets.token_hex(8)}.log"
    redact = _redactor()
    with os.fdopen(files().open_file(output, create=True), "w", encoding="utf-8") as destination:
        for channel, state in record["channels"].items():
            preview = _partial_preview(state, record["binary"].get(channel), redact)
            if preview:
                destination.write(f"[partial {channel}] {preview}\n")
        destination.flush()
        os.fsync(destination.fileno())
    return {
        "status": "snapshot-recovered", "writer": identifier, "source_bytes": length,
        "checkpoint_sequence": record["sequence"], "truncated_tail": not data.endswith(b"\n"),
        "recovered_log": str(output), "raw_retained": True, "quiescence": "not-asserted",
    }


def archive_snapshot(path, destination, redact):
    """Bounded logical records from a fixed byte count, with split credentials."""
    ordinary(path)
    with os.fdopen(files().open_file(path), "rb") as handle:
        remaining = os.fstat(handle.fileno()).st_size
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        lines = Lines(lambda line: destination.write(_safe(redact, line) + "\n"))

        while remaining:
            data = handle.read(min(remaining, 8192))
            if not data:
                break
            remaining -= len(data)
            lines.feed(decoder.decode(data))
        lines.feed(decoder.decode(b"", final=True))
        lines.finish()


@contextmanager
def capture():
    """Explicit regular-file destinations; no pipes and no descendant EOF wait.

    Raw private spools and their pending marker intentionally survive even
    successful direct-child exit: that is not proof all inherited writers ended.
    """
    streams = []
    directory = None
    writer = secrets.token_hex(16)
    try:
        directory = context()
        redact = _redactor()
        for channel in ("stdout", "stderr"):
            path = directory / f"{writer}.{channel}.raw"
            fd = files().open_file(path, create=True)
            streams.append(os.fdopen(fd, "wb", buffering=0))
        marker = directory / f"{writer}.pending"
        with os.fdopen(files().open_file(marker, create=True), "w", encoding="utf-8") as handle:
            handle.write("private raw transport; inherited writers may remain\n")
    except (OSError, ValueError, TypeError, ImportError):
        for stream in streams:
            try:
                stream.close()
            except (OSError, ValueError):
                warn()
        streams = []
        warn()
    try:
        yield {"stdout": streams[0], "stderr": streams[1]} if streams else {}
    finally:
        for stream in streams:
            try:
                stream.close()
            except (OSError, ValueError):
                warn()
        if streams:
            try:
                # Snapshot lengths, not EOF: retained descendants may append.
                path = directory / f"{writer}.log"
                with os.fdopen(files().open_file(path, create=True), "w", encoding="utf-8") as handle:
                    for channel in ("stdout", "stderr"):
                        handle.write(f"[{channel}]\n")
                        archive_snapshot(directory / f"{writer}.{channel}.raw", handle, redact)
                    handle.flush()
                    os.fsync(handle.fileno())
            except (OSError, ValueError, TypeError):
                warn()
