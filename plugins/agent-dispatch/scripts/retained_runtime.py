"""Capture and restore an immutable Dispatch runtime using the current controller."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any

SCHEMA = "copilot-extensions.dispatch-retained-runtime"
OWNER_ENV = "_AGENT_DISPATCH_RESTORE_OWNER"
SCRIPT_DIR = Path(__file__).resolve().parent


def runtime_manager() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_dispatch_retained_versioned_runtime", SCRIPT_DIR / "versioned_runtime.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Retained restoration requires the runtime manager")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate retained-runtime field: {key}")
        result[key] = value
    return result


def read_object(text: str) -> dict[str, Any]:
    result = json.loads(text, object_pairs_hook=unique_object)
    if not isinstance(result, dict):
        raise ValueError("Retained-runtime metadata must be an object")
    return result


def plain_directory(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    if os.path.normcase(str(path.resolve())) != os.path.normcase(str(path)) or not path.is_dir():
        raise ValueError(f"Retained-runtime directory is missing or indirect: {path}")
    return path


def tree_hash(root: Path) -> str:
    """Ignore disposable bytecode, not installed code, metadata or link identity."""
    digest = hashlib.sha256()

    def fail(error: OSError) -> None:
        raise error

    for directory, dirs, files in os.walk(root, onerror=fail, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in ("__pycache__", ".git"))
        entries = sorted(files + [name for name in dirs if (Path(directory) / name).is_symlink()])
        for name in entries:
            path = Path(directory) / name
            info = path.lstat()
            label = path.relative_to(root).as_posix().encode("utf-8")
            if stat.S_ISLNK(info.st_mode):
                content = b"link:" + os.fsencode(os.readlink(path))
            elif stat.S_ISREG(info.st_mode):
                content = b"file:" + path.read_bytes()
            else:
                raise ValueError(f"Unexpected retained-runtime object: {path}")
            for item in (label, str(stat.S_IMODE(info.st_mode)).encode(), content):
                digest.update(len(item).to_bytes(8, "big"))
                digest.update(item)
    return digest.hexdigest()


def assembly(root: Path, manifest_text: str, manager: Any) -> tuple[str, Path, Path]:
    manifest = read_object(manifest_text)
    source = manifest.get("source")
    if (manifest.get("schema_version") != 3 or manifest.get("service") != "agent-dispatch"
            or not isinstance(source, dict) or source.get("plugin") != "agent-dispatch"
            or source.get("kind") != "marketplace" or source.get("dirty") is not False
            or manifest.get("runtime") != "python" or manifest.get("runtime_source")):
        raise ValueError("Restoration requires a retained immutable marketplace assembly")
    version = source.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9][A-Za-z0-9._+-]*", version):
        raise ValueError("Invalid retained runtime version")
    slot = plain_directory(root / "versions" / version)
    if manifest.get("venv") != str(slot) or not manager.is_complete(root, version):
        raise ValueError("Retained manifest and completed runtime slot do not agree")
    payload_value = source.get("path")
    if not isinstance(payload_value, str) or not Path(payload_value).is_absolute():
        raise ValueError("Retained payload path must be absolute")
    payload = plain_directory(Path(payload_value))
    if (payload / "runtime-source.json").exists():
        raise ValueError("Source-managed snapshots are not marketplace restoration targets")
    plugin = read_object((payload / "plugin.json").read_text(encoding="utf-8"))
    if plugin.get("name") != "agent-dispatch" or plugin.get("version") != version:
        raise ValueError("Retained payload version does not match the manifest")
    marker = manager.read_marker(root, version)
    if not marker or not source.get("content_hash") or marker.get("payload_hash") != source["content_hash"]:
        raise ValueError("Retained completion evidence does not match the manifest")
    for path in slot.glob("**/*.dist-info/direct_url.json"):
        directory = read_object(path.read_text(encoding="utf-8")).get("dir_info", {})
        if not isinstance(directory, dict) or directory.get("editable"):
            raise ValueError("An editable runtime cannot be retained as an immutable assembly")
    return version, payload, slot


def capture(root: Path, manager: Any) -> dict[str, Any]:
    root = plain_directory(root)
    text = (root / "deploy-manifest.json").read_text(encoding="utf-8")
    version, payload, slot = assembly(root, text, manager)
    with manager.retention_lease(root, version):
        if manager.current_version(root) != version:
            raise ValueError("Capture requires the manifest's runtime to be selected")
        result = {
            "schema": SCHEMA, "version": 1, "root": str(root), "manifest": text,
            "payload_hash": tree_hash(payload), "slot_hash": tree_hash(slot),
        }
        if (root / "deploy-manifest.json").read_text(encoding="utf-8") != text:
            raise ValueError("Deployment manifest changed during capture")
        validate(root, result, manager)
        return result


def validate(root: Path, record: dict[str, Any], manager: Any) -> tuple[str, Path, Path]:
    root = plain_directory(root)
    if (set(record) != {"schema", "version", "root", "manifest", "payload_hash", "slot_hash"}
            or record["schema"] != SCHEMA or type(record["version"]) is not int
            or record["version"] != 1 or record["root"] != str(root)
            or not isinstance(record["manifest"], str)):
        raise ValueError("Retained-runtime record does not bind this installation")
    version, payload, slot = assembly(root, record["manifest"], manager)
    if tree_hash(payload) != record["payload_hash"] or tree_hash(slot) != record["slot_hash"]:
        raise ValueError("Retained payload or runtime bytes changed; restoration refused")
    return version, payload, slot


def restore(
    root: Path, record_path: Path, record: dict[str, Any], manager: Any,
    shell: str, executable: str, record_bytes: bytes | None = None,
) -> int:
    if record_bytes is not None and read_object(record_bytes.decode("utf-8")) != record:
        raise ValueError("Retained restoration bytes do not match the retained assembly")
    version, _payload, _slot = validate(root, record, manager)
    with manager.retention_lease(root, version), tempfile.TemporaryDirectory(
        prefix="dispatch-retained-runtime-",
    ) as workspace:
        validate(root, record, manager)
        # Pin the input as well as its slot: a caller replacing the supplied
        # record must not redirect the child to an unleased generation.
        record_path = Path(workspace) / "retained-runtime.json"
        record_bytes = record_bytes if record_bytes is not None else json.dumps(record).encode("utf-8")
        record_path.write_bytes(record_bytes)
        installer = SCRIPT_DIR / f"install.{shell}"
        command = (
            [executable, str(installer), "restore", "--retained-runtime", str(record_path),
             "--install-dir", str(root)]
            if shell == "sh" else
            [executable, "-NoProfile", "-NonInteractive", "-File", str(installer),
             "-Action", "restore", "-RetainedRuntime", str(record_path), "-InstallDir", str(root)]
        )
        if shell == "ps1":
            command.extend(["-RetainedRuntimeSha256", hashlib.sha256(record_bytes).hexdigest()])
        env = dict(os.environ, **{OWNER_ENV: str(os.getpid())})
        # This is a synchronous installer child, not a detached service. The
        # installer's existing bounded engine owns its lifecycle and diagnostics.
        return subprocess.call(
            command, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "inspect", "manifest", "restore"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--record", type=Path)
    parser.add_argument("--expected-record-sha256")
    parser.add_argument("--shell", choices=("sh", "ps1"))
    parser.add_argument("--shell-executable")
    args = parser.parse_args()
    try:
        manager = runtime_manager()
        if args.action == "capture":
            print(json.dumps(capture(args.root, manager), sort_keys=True))
            return 0
        if args.record is None:
            raise ValueError("--record is required")
        record_path = args.record.absolute()
        with record_path.open("rb") as stream:
            record_bytes = stream.read(1048577)
        if len(record_bytes) > 1048576:
            raise ValueError("Retained restoration record is over budget")
        if args.expected_record_sha256 and hashlib.sha256(record_bytes).hexdigest() != args.expected_record_sha256:
            raise ValueError("Retained restoration record changed after admission")
        record = read_object(record_bytes.decode("utf-8"))
        version, payload, slot = validate(args.root, record, manager)
        if args.action == "inspect":
            print(json.dumps({"version": version, "payload": str(payload), "slot": str(slot)}))
        elif args.action == "manifest":
            sys.stdout.write(record["manifest"])
        else:
            if not args.shell or not args.shell_executable:
                raise ValueError("Restoration requires the controller's shell executable")
            return restore(
                args.root, record_path, record, manager, args.shell, args.shell_executable, record_bytes,
            )
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Retained-runtime restoration refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
