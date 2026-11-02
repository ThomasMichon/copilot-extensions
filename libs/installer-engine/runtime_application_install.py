"""Slot-local install entry: stage is environment-only; regular install activates."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"application runtime resource is unavailable: {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _read(path: Path, *, limit: int = 65536) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > limit:
        raise RuntimeError(f"application publication target is not an ordinary file: {path}")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise RuntimeError(f"application publication target exceeds its byte bound: {path}")
    return content


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.suffix == ".sh" or not path.suffix:
            temporary.chmod(0o700)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _application(slot: Path) -> dict:
    raw = _read(slot / "application.json", limit=4 << 20)
    if raw is None:
        raise RuntimeError("the copied application has no install declaration")
    record = json.loads(raw)
    fields = {
        "name", "module", "commands", "root_environment", "default_directory",
        "setup", "start", "construction",
    }
    if not isinstance(record, dict) or set(record) != fields:
        raise ValueError("invalid application install declaration")
    if (
        not isinstance(record["module"], str)
        or not re.fullmatch(
            r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?::[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)?",
            record["module"],
        )
        or any(
            not isinstance(record[key], list)
            or any(not isinstance(argument, str) or "\0" in argument for argument in record[key])
            for key in ("setup", "start")
        )
        or not isinstance(record["commands"], list) or not record["commands"]
        or any(not isinstance(name, str) for name in record["commands"])
        or len(set(record["commands"])) != len(record["commands"])
        or record["name"] not in record["commands"]
    ):
        raise ValueError("invalid application command declaration")
    return record


@contextmanager
def _qualified(slot: Path, record: dict):
    runtime_path = slot / "runtime"
    runtime = _load(runtime_path / "versioned_runtime.py", "versioned_runtime")
    sys.path.insert(0, str(runtime_path))
    engine = _load(runtime_path / "installer_engine.py", "_application_engine")
    from installer_wheel_inputs import WheelLimits, inspect_archive
    inputs = record["construction"]
    fields = {
        "identity", "runtime_version", "payload_hash", "source_hash", "wheel_inputs",
        "extras", "local_packages", "excluded_packages",
    }
    if not isinstance(inputs, dict) or set(inputs) != fields:
        raise ValueError("regular install requires the actual Source/profile construction inputs")
    source_bytes = _read(slot / "runtime-source.json")
    declaration = _read(slot / "application.json", limit=4 << 20)
    source = inputs["identity"].get("runtime_source")
    if (
        source_bytes is None or json.loads(source_bytes) != source
        or source["runtime_version"] != engine.runtime_source.runtime_version(
            source["package_version"], source["source_commit"], source["assembly_hash"],
        )
        or inputs["identity"].get("runtime_version") != source["runtime_version"]
        or re.fullmatch(r"[0-9a-f]{64}", inputs["source_hash"]) is None
        or any(
            not isinstance(inputs[key], list)
            or len(set(inputs[key])) != len(inputs[key])
            or any(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None for name in inputs[key])
            for key in ("extras", "local_packages", "excluded_packages")
        )
    ):
        raise ValueError("application construction inputs differ from their Source identity")
    deadline = time.monotonic() + 600
    wheels = engine.WheelReuseInputs(
        slot.name, json.dumps(inputs["wheel_inputs"], sort_keys=True, separators=(",", ":")),
        deadline,
    )
    wheels.verify()
    receipt = wheels.receipt()
    generation = source["runtime_version"] + ".profile." + receipt["lock_id"][7:23]
    if (
        inputs["runtime_version"] != generation
        or re.fullmatch(re.escape(generation) + r"(?:[.+]repair\.[0-9a-f]{32})?", slot.name) is None
        or engine._wheel_construction_hash(inputs["source_hash"], wheels) != inputs["payload_hash"]
        or receipt["binding"].get("release") != {
            "identity": "source:" + source["source_commit"],
            "sha256": "sha256:" + source["assembly_hash"],
        }
        or receipt["binding"].get("component") != source["plugin"]
        or not set(inputs["local_packages"]) <= receipt["distributions"].keys()
        or set(inputs["excluded_packages"]) & receipt["distributions"].keys()
    ):
        raise ValueError("application source/profile selection differs from its version slot")
    origins = {
        name: (slot / "wheels" / filename).as_uri()
        for name, _version, filename, _digest in wheels.distributions
    }
    proof = {
        "schema": "installer-engine.frozen-input-origins", "version": 2,
        "packages": origins, "wheel_inputs": receipt,
        "local_packages": inputs["local_packages"],
        **({"extras": inputs["extras"]} if inputs["extras"] else {}),
    }

    def unchanged():
        if (
            _read(slot / "runtime-source.json") != source_bytes
            or _read(slot / "application.json", limit=4 << 20) != declaration
            or json.loads(_read(slot / ".runtime-input-origins.json", limit=4 << 20)) != proof
        ):
            raise RuntimeError("application construction inputs changed before publication")
        engine.verify_application_wheels(wheels, slot)

    def run(argv):
        result = subprocess.run(
            argv, executable=argv[0], cwd=slot, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=max(0.1, min(180, deadline - time.monotonic())),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode:
            print(result.stderr, file=sys.stderr, end="")
            raise subprocess.CalledProcessError(
                result.returncode, argv, output=result.stdout, stderr=result.stderr,
            )
        return result

    root = slot.parent.parent
    with ExitStack() as retention:
        with runtime.construction_lease(root, slot.name, timeout=180):
            engine._refuse(
                runtime.check_admission(root, slot.name, payload_hash=inputs["payload_hash"]), slot.name,
            )
            complete = runtime.is_complete(root, slot.name, expect_hash=inputs["payload_hash"])
            engine.verify_application_wheels(wheels, slot)
            rows = receipt["binding"].get("wheels")
            if (
                not isinstance(rows, list) or len(rows) != len(wheels.distributions)
                or {row["name"] for row in rows} != receipt["distributions"].keys()
            ):
                raise ValueError("application profile does not cover its complete wheel set")
            limits = WheelLimits(
                max_archive_bytes=128 << 20, max_expanded_bytes=1 << 30,
                max_member_bytes=512 << 20, max_metadata_bytes=16 << 20,
                max_members=100_000, max_tags=10_000, max_metadata_member_bytes=4 << 20,
            )
            expanded = 0
            for row in rows:
                with engine._open(slot / "wheels" / row["filename"]) as archive:
                    facts = inspect_archive(archive, limits=limits, deadline=deadline)
                expanded += facts.unpacked_size
                if (
                    expanded > limits.max_expanded_bytes
                    or re.sub(r"[-_.]+", "-", facts.name).lower() != row["name"]
                    or any(row[key] != receipt["distributions"][row["name"]][key]
                           for key in ("version", "filename", "sha256"))
                    or facts.version != row["version"]
                    or facts.requires_python != row["requires_python"]
                    or list(facts.requires_dist) != row["requires_dist"]
                    or list(facts.provides_extras) != row["provides_extras"]
                ):
                    raise ValueError("application wheel metadata differs from its Source/profile")
            proof_path = slot / ".runtime-input-origins.json"
            previous = _read(proof_path, limit=4 << 20)
            if previous is None and not complete:
                _write(proof_path, json.dumps(proof, indent=2).encode("utf-8") + b"\n")
            elif previous is None or json.loads(previous) != proof:
                raise RuntimeError("application installed origins differ from its construction inputs")
            bootstrap = slot / ".runtime-bootstrap"
            bootstrap.mkdir(exist_ok=True)
            for name in (
                "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
                "versioned_runtime_source.py", "versioned_runtime_construction.py",
                "versioned_runtime_publication.py",
            ):
                content = _read(runtime_path / name, limit=1 << 20)
                previous = _read(bootstrap / name, limit=1 << 20)
                if content is None or previous is not None and previous != content:
                    raise RuntimeError("application retention bootstrap differs from its runtime")
                if previous is None:
                    if complete:
                        raise RuntimeError("completed application is missing its retention bootstrap")
                    _write(bootstrap / name, content)
            module = record["module"].partition(":")[0]
            health_callable = None
            if source["plugin"] in {"worktree-manager", "agent-worktrees"}:
                package = "worktree_manager" if source["plugin"] == "worktree-manager" else "agent_worktrees"
                module, health_callable = package + ".runtime_candidate", "verify_runtime_candidate"
                if not complete:
                    run([
                        sys.executable, "-I", "-X", "utf8", "-B", "-c",
                        f"from {module} import prepare_runtime_candidate; "
                        + ("prepare_runtime_candidate({})" if package == "worktree_manager"
                           else "prepare_runtime_candidate()"),
                    ])
            engine.verify_installed_inputs(
                slot, Path(sys.executable), module, origins=origins,
                extras=tuple(inputs["extras"]), local_packages=tuple(inputs["local_packages"]),
                excluded_packages=inputs["excluded_packages"], wheel_inputs=wheels,
                health_callable=health_callable, run=run,
            )
            unchanged()
            runtime.mark_complete(root, slot.name, payload_hash=inputs["payload_hash"])
            retention.enter_context(runtime.retention_lease(
                root, slot.name, payload_hash=inputs["payload_hash"], timeout=180,
            ))
        yield runtime, unchanged
        unchanged()


def _run(record: dict, command: list[str], root: Path, slot: Path) -> None:
    if not command:
        return
    environment = {**os.environ, record["root_environment"]: str(root)}
    target = (
        [str(slot / (record["name"] + ".py"))] if ":" in record["module"]
        else ["-m", record["module"]]
    )
    result = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", "-B", *target, *command],
        executable=sys.executable, env=environment, timeout=180,
        stdout=sys.stdout, stderr=sys.stderr,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise RuntimeError(f"application command failed with exit code {result.returncode}")


def install(
    slot: Path, *, stage: bool, global_bin: Path | None = None,
    arguments: tuple[str, ...] = (),
) -> dict:
    """UV has synchronized the environment before this entry runs."""
    slot = slot.resolve()
    if (
        not (slot / ".venv" / "pyvenv.cfg").is_file()
        or not Path(sys.prefix).samefile(slot / ".venv")
    ):
            raise RuntimeError(
                "application install must run through its slot-local uv binstub "
                f"(prefix={Path(sys.prefix).resolve()}, expected={(slot / '.venv').resolve()}, "
                f"config={(slot / '.venv' / 'pyvenv.cfg').is_file()})"
            )
    if stage:
        return {"state": "staged", "slot": str(slot)}
    if slot.parent.name != "versions" or not re.fullmatch(r"[0-9][A-Za-z0-9._+-]*", slot.name):
        raise ValueError("regular install requires an owned version-slot layout")
    root = slot.parent.parent
    record = _application(slot)
    if record["name"] == "worktree-manager":
        if arguments:
            raise ValueError("Manager installation does not accept application configuration arguments")
        if global_bin is not None and global_bin != Path.home() / ".local" / "bin":
            raise ValueError("Manager installation requires its owned global command directory")
        _run(record, ["self-install", "--apply"], root, slot)
        return {"state": "installed", "slot": str(slot)}
    launchers = _load(slot / "runtime_application_binstubs.py", "_application_binstubs")
    globals_text = {}
    for name in record["commands"]:
        globals_text.update(launchers.global_binstubs(
            name, record["root_environment"], record["default_directory"], root=root,
        ))
    global_bin = global_bin or Path.home() / ".local" / "bin"
    replacements = {global_bin / name: text.encode("utf-8") for name, text in globals_text.items()}
    previous = {}
    for path, content in replacements.items():
        previous[path] = _read(path)
        if previous[path] is not None and previous[path] != content:
            raise RuntimeError(f"existing launcher requires its owner's migration: {path}")
    selectors = (root / "current-version", root / "last-known-good")
    if arguments and not (record["setup"] or record["start"]):
        raise ValueError("application installation has no configurable setup or startup action")
    setup = [*record["setup"], *arguments] if record["setup"] else []
    start = [*record["start"], *arguments] if record["start"] else []
    with _qualified(slot, record) as (runtime, unchanged), runtime._publication_lease(root):
        _run(record, setup, root, slot)
        unchanged()
        if not runtime.is_complete(root, slot.name, expect_hash=record["construction"]["payload_hash"]):
            raise RuntimeError("application publication requires canonical completed construction")
        selector_before = {path: _read(path) for path in selectors}
        selector_content = (slot.name + "\n").encode("utf-8")
        written = {}
        publishing = False
        try:
            for path, content in replacements.items():
                if _read(path) != previous[path]:
                    raise RuntimeError(f"application launcher changed before publication: {path}")
                if previous[path] != content:
                    _write(path, content)
                    written[path] = content
            publishing = True
            runtime._runtime_component("versioned_runtime_publication").publish_selectors(
                root, slot.name, None,
                lambda home, version: _write(home / "last-known-good", selector_content),
            )
            _run(record, start, root, slot)
            _write(slot / "install-success.json", json.dumps({
                "state": "installed", "version": slot.name,
            }, sort_keys=True).encode("utf-8") + b"\n")
        except BaseException:
            for path, content in written.items():
                if _read(path) != content:
                    raise RuntimeError(f"application publication changed during recovery: {path}")
                if previous[path] is None:
                    path.unlink()
                else:
                    _write(path, previous[path])
            if publishing:
                for path in selectors:
                    content = _read(path)
                    if content == selector_before[path]:
                        continue
                    if content != selector_content:
                        raise RuntimeError(f"application selector changed during recovery: {path}")
                    if selector_before[path] is None:
                        path.unlink(missing_ok=True)
                    else:
                        _write(path, selector_before[path])
            raise
    return {"state": "installed", "slot": str(slot)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--stage", action="store_true")
    parser.add_argument(
        "arguments", nargs=argparse.REMAINDER,
        help="application-owned setup/start arguments after -- (regular installation only)",
    )
    arguments = parser.parse_args(argv)
    forwarded = arguments.arguments
    if forwarded[:1] == ["--"]:
        forwarded = forwarded[1:]
    print(json.dumps(install(
        Path(__file__).parent, stage=arguments.stage, arguments=tuple(forwarded),
    )))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
