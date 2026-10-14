"""Disposable two-consumer measurement, not an installer or artifact authority."""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def run(argv, **kwargs):
    started = time.perf_counter()
    completed = subprocess.run(
        list(map(str, argv)), capture_output=True, text=True, timeout=180,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        **kwargs,
    )
    if completed.returncode:
        raise RuntimeError(
            f"{argv[0]} exited {completed.returncode}: {completed.stderr[-4000:]}"
        )
    return completed.stdout, time.perf_counter() - started


def allocated(path):
    if os.name != "nt":
        return path.stat().st_blocks * 512
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class StandardInfo(ctypes.Structure):
        _fields_ = [
            ("allocation", ctypes.c_longlong), ("size", ctypes.c_longlong),
            ("links", ctypes.c_ulong), ("pending", ctypes.c_ubyte),
            ("directory", ctypes.c_ubyte),
        ]

    create = kernel.CreateFileW
    create.argtypes = [
        ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
        ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
    ]
    create.restype = ctypes.c_void_p
    query = kernel.GetFileInformationByHandleEx
    query.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    query.restype = ctypes.c_int
    close = kernel.CloseHandle
    close.argtypes = [ctypes.c_void_p]
    close.restype = ctypes.c_int
    handle = create(str(path), 0x80, 7, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = StandardInfo()
        if not query(handle, 1, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        require(info.size == path.stat().st_size, "allocation probe size changed")
        return info.allocation
    finally:
        close(handle)


def inventory(roots):
    seen = set()
    logical = physical = files = duplicated = 0
    for root in roots:
        for path in root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            info = path.stat()
            files += 1
            logical += info.st_size
            identity = (info.st_dev, info.st_ino)
            if identity in seen:
                duplicated += 1
            else:
                seen.add(identity)
                physical += allocated(path)
    return dict(
        files=files, logical_bytes=logical, unique_allocated_bytes=physical,
        duplicate_file_links=duplicated,
    )


def sources(repo, project):
    selected = set()

    def visit(path):
        path = path.resolve()
        if not path.is_relative_to(repo):
            raise ValueError("source dependency escapes the measured repository")
        if path in selected:
            return
        selected.add(path)
        data = tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))
        for definition in data.get("tool", {}).get("uv", {}).get("sources", {}).values():
            if isinstance(definition, dict) and "path" in definition:
                visit(path / definition["path"])

    visit(project)
    return sorted(selected)


def python_at(root):
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def source_modules(repo, project):
    modules = set()
    for source in sources(repo, project):
        src = source / "src"
        names = {
            path.parent.name for path in src.glob("*/__init__.py")
        } | {path.stem for path in src.glob("*.py")}
        require(bool(names), "no supported first-party modules: " + str(source))
        require(all(name.isidentifier() for name in names), "invalid first-party module name")
        modules.update(names)
    return sorted(modules)


PACKAGE_PROBE = """
from importlib.metadata import distributions
from importlib.util import find_spec
import json
from pathlib import Path
import sys
root = Path(sys.prefix).resolve()
for module in json.loads(sys.argv[1]):
    spec = find_spec(module)
    if spec is None or spec.origin is None or not Path(spec.origin).resolve().is_relative_to(root):
        raise RuntimeError("required module escapes runtime: " + module)
packages = []
for dist in distributions():
    if not Path(dist.locate_file("")).resolve().is_relative_to(root):
        raise RuntimeError("distribution escapes runtime: " + dist.metadata["Name"])
    paths = [dist.locate_file(item).resolve() for item in dist.files or []]
    if any(not path.is_relative_to(root) for path in paths):
        raise RuntimeError("recorded payload escapes runtime: " + dist.metadata["Name"])
    paths = [path for path in paths if path.is_file()]
    packages.append(dict(name=dist.metadata["Name"], version=dist.version,
                         files=len(paths), logical_bytes=sum(path.stat().st_size for path in paths)))
print(json.dumps(sorted(packages, key=lambda item: item["name"])))
"""


def packages(root, modules=()):
    output, _ = run([
        python_at(root), "-I", "-B", "-c", PACKAGE_PROBE, json.dumps(list(modules)),
    ])
    return json.loads(output)


def allocation_proof(root):
    probe = root / "allocation-proof"
    probe.mkdir()
    first = probe / "first.bin"
    first.write_bytes(os.urandom(4097))
    before = inventory([probe])
    os.link(first, probe / "linked.bin")
    linked = inventory([probe])
    require(linked["unique_allocated_bytes"] == before["unique_allocated_bytes"] > 0,
            "hardlink changed unique allocation")
    require(linked["logical_bytes"] == 2 * before["logical_bytes"],
            "hardlink logical accounting differs")
    require(linked["duplicate_file_links"] == 1, "hardlink identity was not shared")
    (probe / "copied.bin").write_bytes(first.read_bytes())
    copied = inventory([probe])
    require(copied["unique_allocated_bytes"] == 2 * before["unique_allocated_bytes"],
            "independent copy allocation differs")
    return dict(single=before, hardlink=linked, independent_copy=copied)


def wheel_requirements(repo, project, wheels):
    extras = {}
    for source in sources(repo, project):
        metadata = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
        for requirement in metadata["project"].get("dependencies", []):
            match = re.match(r"^([A-Za-z0-9_.-]+)(?:\[([^\]]+)\])?", requirement)
            require(match is not None, "invalid requirement: " + requirement)
            if match[2]:
                extras.setdefault(match[1], set()).update(match[2].split(","))
    constraints = []
    for package, wheel in wheels.items():
        suffix = "[" + ",".join(sorted(extras[package])) + "]" if package in extras else ""
        constraints.append(f"{package}{suffix} @ {wheel.as_uri()}\n")
    return "".join(constraints)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True,
                        help="New disposable directory; existing paths are refused")
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--artifacts", action="store_true")
    parser.add_argument("--reference-report", type=Path)
    parser.add_argument("--reuse-wheels", type=Path)
    args = parser.parse_args()
    repo, root = args.repo.resolve(), args.root.resolve()
    if args.reuse_wheels and not args.artifacts:
        parser.error("--reuse-wheels requires --artifacts")
    root.mkdir(parents=True, exist_ok=False)
    cache = root / "uv-cache"
    env = {
        key: value for key, value in os.environ.items()
        if key not in ("UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")
    }
    uv = [args.uv, "--cache-dir", cache]
    report = dict(
        python=sys.version,
        source_commit=run(["git", "-C", repo, "rev-parse", "HEAD"])[0].strip(),
        allocation="unique file-stream allocation; excludes filesystem metadata and external interpreter",
        consumers=[],
    )
    report["allocation_proof"] = allocation_proof(root)
    roots = [cache]
    consumers = ("agent-containers", "agent-bridge")
    wheels = {}
    if args.artifacts:
        wheelhouse = (args.reuse_wheels or root / "wheels").resolve()
        if not args.reuse_wheels:
            wheelhouse.mkdir()
        libraries = set().union(*(
            set(sources(repo, repo / "plugins" / name)) for name in consumers
        ))
        libraries = sorted(path for path in libraries if path.parent == repo / "libs")
        report["artifacts"] = []
        for library in libraries:
            metadata = tomllib.loads((library / "pyproject.toml").read_text(encoding="utf-8"))
            name = metadata["project"]["name"]
            if args.reuse_wheels:
                wheel, = wheelhouse.glob(name.replace("-", "_") + "-*.whl")
                elapsed = None
            else:
                before = set(wheelhouse.glob("*.whl"))
                _, elapsed = run([
                    *uv, "build", "--wheel", "--no-sources",
                    "--out-dir", wheelhouse, library,
                ], env=env)
                wheel, = set(wheelhouse.glob("*.whl")) - before
            wheels[name] = wheel
            report["artifacts"].append(dict(
                library=library.relative_to(repo).as_posix(), build_seconds=elapsed,
                bytes=wheel.stat().st_size,
                sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
            ))
        roots.append(wheelhouse)
    runtime_roots = []
    for name in consumers:
        project = repo / "plugins" / name
        modules = source_modules(repo, project)
        runtime = root / name
        before = inventory(roots)
        override = []
        if args.artifacts:
            constraints_path = root / f"{name}-overrides.txt"
            constraints_path.write_text(
                wheel_requirements(repo, project, wheels), encoding="utf-8"
            )
            override = ["--override", constraints_path]
        _, create = run([*uv, "venv", "--python", sys.executable, runtime], env=env)
        _, install = run([
            *uv, "pip", "install", "--python", python_at(runtime),
            "--link-mode", "hardlink", "--no-sources", *override,
            *([project] if args.artifacts else sources(repo, project)),
        ], env=env)
        output, cold_help = run(
            [python_at(runtime), "-I", "-B", "-m", name.replace("-", "_"), "--help"],
            cwd=root, env=env,
        )
        require("usage" in output.lower(), "actual CLI did not return help")
        roots.append(runtime)
        runtime_roots.append(runtime)
        after = inventory(roots)
        report["consumers"].append(dict(
            name=name, create_seconds=create, install_seconds=install,
            cold_help_seconds=cold_help,
            libraries=[path.relative_to(repo).as_posix() for path in sources(repo, project)
                       if path != project],
            required_modules=modules, packages=packages(runtime, modules),
            runtime_files=inventory([runtime]),
            cache_and_runtimes=after,
            incremental_allocated_bytes=after["unique_allocated_bytes"] - before["unique_allocated_bytes"],
        ))
    report["two_runtime_union"] = inventory(runtime_roots)
    report["independent_runtime_allocated_sum"] = sum(
        consumer["runtime_files"]["unique_allocated_bytes"] for consumer in report["consumers"]
    )
    report["cross_runtime_hardlink_savings"] = (
        report["independent_runtime_allocated_sum"] -
        report["two_runtime_union"]["unique_allocated_bytes"]
    )
    if args.reference_report:
        reference = json.loads(args.reference_report.read_text(encoding="utf-8"))
        for current, previous in zip(report["consumers"], reference["consumers"], strict=True):
            require(current["name"] == previous["name"], "reference consumer mismatch")
            require([(item["name"], item["version"]) for item in current["packages"]] == [
                (item["name"], item["version"]) for item in previous["packages"]
            ], "reference distribution closure differs: " + current["name"])
        report["same_distribution_closures_as_reference"] = True
    (root / "closure-results.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
