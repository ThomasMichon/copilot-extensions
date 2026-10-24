"""Earn platform-specific standalone baselines using the native installed runner.

This is deliberate full validation, never a per-change selector or a new
baseline authority. Publication uses the existing promotion/Release pointers.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

from tools.coverage_guided_selection.ancestor_resolution import _git  # noqa: E402
from tools.coverage_guided_selection.baseline import (  # noqa: E402
    BASELINE_SCHEMA_VERSION, BaselineCollectionError, _DRIVER_SCRIPT,
)
from tools.coverage_guided_selection import child_stream, child_stream_parent, child_audit  # noqa: E402
from plugin_test_containment import Limits, isolated_environment, run_contained  # noqa: E402
from seasoned_uv import run_uv_install  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_standalone_baseline_runner", REPO / "tools" / "run-standalone-tests.py"
)
assert _spec is not None and _spec.loader is not None
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

COMPONENT = "fleet-manager"
PACKAGE = "fleet_manager"
SOURCE = f"{COMPONENT}/src/{PACKAGE}"
PLATFORMS = {"linux": "linux", "win32": "windows"}
COLLECTION_SCHEMA = "copilot-extensions.standalone-coverage-collection.v1"
COVERAGE_REQUIREMENT = "coverage>=7"
COVERAGE_CAPABILITY_PROBE = (
    "import coverage, pytest_jsonreport; "
    "assert callable(coverage.Coverage.switch_context); "
    "assert callable(coverage.CoverageData.contexts_by_lineno)"
)


def baseline_identity(platform: str) -> str:
    try:
        return f"{COMPONENT}-{PLATFORMS[platform]}"
    except KeyError as exc:
        raise ValueError("standalone baselines currently support native Linux and Windows") from exc


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_revision(repo: Path, measured_commit: str) -> None:
    """Coverage inputs must really be the named commit, not a modified checkout."""
    if _git(["rev-parse", "HEAD"], cwd=repo).strip() != measured_commit:
        raise ValueError("measured_commit must be this checkout's exact HEAD")
    inputs = [COMPONENT, "libs/versioned-runtime"]
    if _git(["status", "--porcelain", "--untracked-files=all", "--", *inputs], cwd=repo).strip():
        raise ValueError("standalone source/build/test inputs differ from measured_commit")


def runtime_resource_mapping(repo: Path) -> dict[str, Path]:
    root = repo / "libs/versioned-runtime"
    tree = ast.parse((root / "standalone_build.py").read_text(encoding="utf-8"))
    declarations = [
        ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "RUNTIME_RESOURCES" for target in node.targets)
    ]
    if len(declarations) != 1 or not isinstance(declarations[0], dict) or not declarations[0]:
        raise ValueError("expected one literal runtime resource declaration")
    resources = declarations[0]
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.py", value)
        for pair in resources.items() for value in pair
    ) or len(set(resources.values())) != len(resources):
        raise ValueError("invalid or ambiguous runtime resource declaration")
    return {destination: root / name for name, destination in resources.items()}


def verified_source_mapping(
    repo: Path, installed: Path, environment: Path, *, with_inventory: bool = False,
) -> tuple:
    """Only byte-identical installed modules earn repository line coordinates."""
    installed = installed.resolve()
    if not installed.is_relative_to(environment.resolve()):
        raise ValueError("package is not installed inside the leased environment")
    source = repo / SOURCE
    expected = {p.relative_to(source).as_posix(): p for p in source.rglob("*.py")}
    expected.update(runtime_resource_mapping(repo))
    actual = {p.relative_to(installed).as_posix(): p for p in installed.rglob("*.py")}
    if actual.keys() != expected.keys():
        raise ValueError("installed/source Python module inventories differ")
    mapping, hashes, contents = {}, {}, {}
    for relative, canonical in expected.items():
        path = actual[relative]
        if path.is_symlink() or not path.resolve().is_relative_to(installed):
            raise ValueError("installed module must not be linked outside its package")
        actual_bytes, canonical_bytes = path.read_bytes(), canonical.read_bytes()
        if actual_bytes != canonical_bytes:
            raise ValueError(f"installed source differs from canonical module: {relative}")
        key = canonical.relative_to(repo).as_posix()
        mapping[str(path.resolve())] = key
        hashes[key] = hashlib.sha256(canonical_bytes).hexdigest()
        contents[str(path.resolve())] = actual_bytes
    if with_inventory:
        inventory = {
            identifier: {
                "installed": file, "canonical": canonical, "sha256": hashes[canonical],
                "lines": len(contents[file].splitlines()),
            }
            for identifier, (file, canonical) in enumerate(sorted(mapping.items()), 1)
        }
        return mapping, hashes, inventory
    return mapping, hashes


def build_baseline(repo: Path, result: dict, measured_commit: str, hashes: dict) -> dict:
    summary = result["summary"]
    collected = summary.get("collected", 0)
    durations = result["durations"]
    if (
        not collected or summary.get("deselected", 0) or summary.get("failed", 0)
        or summary.get("error", 0) or summary.get("errors", 0)
        or len(durations) != collected
    ):
        raise ValueError("baseline requires a complete, successful, non-deselected test universe")
    universe = set(durations)
    for per_line in result["coverage"].values():
        for nodes in per_line.values():
            if not set(nodes) <= universe:
                raise ValueError("coverage attribution names an uncollected test")
    if not result["coverage"]:
        raise ValueError("installed baseline has no attributed source coverage")
    children = result.get("children") or {}
    if (
        children.get("audit_complete") is not True
        or children.get("child_streams") != children.get("audited_producers")
        or len(children.get("prelude_seconds", [])) != children.get("child_streams")
    ):
        raise ValueError("standalone baseline requires complete audited child evidence")
    tests = {
        node: {
            "duration_s": duration, "portfolio_tier": result["tiers"].get(node),
            "markers": result.get("markers", {}).get(node, []),
        }
        for node, duration in durations.items()
    }
    test_inputs = sorted((repo / COMPONENT / "tests").rglob("*.py"))
    test_inputs += [repo / COMPONENT / name for name in ("pyproject.toml", "setup.py")]
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "plugin": baseline_identity(sys.platform),
        "cov_source": SOURCE,
        "measured_commit": measured_commit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tests": tests,
        "coverage": result["coverage"],
        "collection": {
            "schema": COLLECTION_SCHEMA,
            "component": COMPONENT,
            "platform": sys.platform,
            "python_version": result["python_version"],
            "installed_package": True,
            "host_contained": True,
            "source_sha256": hashes,
            "test_input_sha256": {p.relative_to(repo).as_posix(): digest(p) for p in test_inputs},
            "collector_sha256": {
                "baseline_driver": hashlib.sha256(_DRIVER_SCRIPT.encode()).hexdigest(),
                "standalone_collector": digest(Path(__file__)),
            },
            "test_universe_sha256": hashlib.sha256(
                json.dumps(sorted(universe), separators=(",", ":")).encode()
            ).hexdigest(),
            "summary": summary,
            "child_context_strategy": "leased-venv-pth/inherited-audited-manifest",
            "child_backend": child_stream.VERSION,
            "child_audit": child_audit.VERSION,
            "unsupported_child_lifecycles": ["fork", "forkpty", "exec", "system", "unregistered-native"],
            "child_persistence": "unbuffered-sparse-writes",
            "coverage_version": result["coverage_version"],
            "children": result["children"],
            "child_backend_sha256": {
                name: digest(Path(__file__).with_name(name + ".py"))
                for name in ("child_stream", "child_stream_parent", "child_audit")
            },
            "temporary_path_strategy": "compact-node-digest" if os.name == "nt" else "pytest-default",
        },
    }


def _prepare_instrumentation(python: Path, *, repo: Path, prepare: bool) -> None:
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    if prepare:
        runner.prepare(COMPONENT, python)
    # Instrumentation is explicitly provisioned by the baseline collector,
    # just as baseline.py provisions these tools in its ephemeral environment.
    probe = [str(python), "-I", "-c", COVERAGE_CAPABILITY_PROBE]
    check = subprocess.run(probe, capture_output=True, text=True, timeout=30, **flags)
    if check.returncode:
        run_uv_install(
            "uv", ["--python", str(python),
             COVERAGE_REQUIREMENT, "pytest-json-report"],
            trusted_source_roots=(repo,),
            cwd=repo, check=True, capture_output=True, text=True, timeout=300, **flags,
        )
        checked = subprocess.run(probe, capture_output=True, text=True, timeout=30, **flags)
        if checked.returncode:
            raise BaselineCollectionError(
                checked.returncode, checked.stdout,
                "coverage lacks required parent dynamic-context capability:\n" + checked.stderr,
            )


def coverage_files(sandbox: Path, mapping: dict, *, inventory=None) -> tuple[str, dict]:
    """Lease-scoped stdlib children plus coverage.py parent, without spawn wrappers."""
    control = sandbox / "child-lines"
    control.mkdir(mode=0o700)
    for name in ("hits", "audit", "manifests"):
        (control / name).mkdir(mode=0o700)
    (control / "status").write_bytes(b"R")
    nonce = secrets.token_hex(32)
    (control / "run").write_text(nonce, encoding="ascii")
    if inventory is not None and {
        row["installed"]: row["canonical"] for row in inventory.values()
    } != mapping:
        raise ValueError("verified source inventory does not match source mapping")
    options = {
        "child_stream_control": str(control), "child_stream_nonce": nonce,
        "child_stream_inventory": child_stream_parent.source_inventory(mapping) if inventory is None else inventory,
        "child_stream_helpers": {},
    }
    for name in ("child_stream", "child_stream_parent", "child_audit"):
        target = sandbox / ("_cgs_" + name + ".py")
        shutil.copyfile(Path(__file__).with_name(name + ".py"), target)
        options["_cgs_" + name] = str(target)
        options["child_stream_helpers"][str(target)] = digest(target)
    body = (
        "import sys,time\n"
        "os.environ['_CGS_PRELUDE_NS']=str(time.perf_counter_ns())\n"
        "try:\n"
        f" sys.path.insert(0,{str(sandbox)!r})\n"
        " try:\n"
        "  import _cgs_child_audit,_cgs_child_stream\n"
        f"  _cgs_child_stream.start({str(control)!r})\n"
        " finally:\n"
        f"  sys.path.remove({str(sandbox)!r})\n"
        "except BaseException as exc:\n"
        f" fd=os.open({str(control / 'status')!r},os.O_WRONLY)\n"
        " os.write(fd,b'E');os.close(fd)\n"
        " try:\n"
        f"  _cgs_child_audit.report_bootstrap_error({str(control)!r},exc)\n"
        " except BaseException:\n"
        "  import json\n"
        "  token=os.path.basename(os.environ.get('_CGS_CHILD_MANIFEST','')).removesuffix('.manifest')\n"
        "  token=token if len(token)==64 and all(c in '0123456789abcdef' for c in token) else 'unknown'\n"
        f"  path=os.path.join({str(control / 'audit')!r},str(os.getpid())+'-'+os.urandom(16).hex()+'.error')\n"
        "  record={'schema':'CGS-ERROR/1','context_digest':token,'producer_pid':os.getpid(),"
        "'observed_ns':time.monotonic_ns(),'reason':'bootstrap import failed: '+type(exc).__name__}\n"
        "  fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)\n"
        "  os.write(fd,json.dumps(record).encode()[:4096]);os.close(fd)\n"
    )
    hook = "import os; os.getenv('_CGS_CHILD_MANIFEST') and exec(" + repr(body) + ")\n"
    return hook, options


def collect_standalone(
    *, measured_commit: str, prepare: bool = False, timeout_s: float = 600,
    admission_wait: float = 60,
) -> dict:
    baseline_identity(sys.platform)
    verify_revision(REPO, measured_commit)
    python = runner.default_python(COMPONENT)
    environment = python.parent.parent
    if prepare:
        environment.mkdir(parents=True, exist_ok=True)
    elif not python.is_file():
        raise ValueError("prepared standalone interpreter required; use --prepare")
    lease = runner.acquire(admission_wait, environment)
    temporary = None
    hook = None
    try:
        if python.is_file() and not (environment / "pyvenv.cfg").is_file():
            raise ValueError("standalone baseline requires a standard managed venv")
        _prepare_instrumentation(python, repo=REPO, prepare=prepare)
        scratch = REPO / ".pytmp"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="c", dir=scratch)
        sandbox = Path(temporary.name)
        env = isolated_environment(os.environ, sandbox)
        for name in (
            "PYTEST_ADDOPTS", "PYTHONPATH", "COVERAGE_PROCESS_START", "COVERAGE_FILE",
            "COVERAGE_PROCESS_CONFIG", "_CGS_CHILD_MANIFEST", "_CGS_CHILD_AUDITOR",
        ):
            env.pop(name, None)
        probe = sandbox / "installed.json"
        code = (
            "import importlib.util,json,sysconfig; from pathlib import Path; "
            f"package=Path(importlib.util.find_spec('{PACKAGE}').origin).parent; "
            f"Path({str(probe)!r}).write_text(json.dumps([str(package),sysconfig.get_path('purelib')]))"
        )
        status = run_contained(
            [str(python), "-I", "-c", code], cwd=REPO / COMPONENT,
            env=env, sandbox=sandbox, limits=Limits(wall_seconds=30),
        )
        if status:
            raise BaselineCollectionError(status, "", "installed-package probe failed")
        installed, site = map(Path, json.loads(probe.read_text()))
        if not site.resolve().is_relative_to(environment.resolve()):
            raise ValueError("instrumentation site must belong to the leased environment")
        mapping, hashes = verified_source_mapping(REPO, installed, environment)
        hook_text, coverage_options = coverage_files(sandbox, mapping)
        candidate_hook = site / "_cgs_standalone.pth"
        with candidate_hook.open("x", encoding="utf-8") as stream:
            hook = candidate_hook
            stream.write(hook_text)
        driver = sandbox / "driver.py"
        driver.write_text(_DRIVER_SCRIPT, encoding="utf-8")
        result_path = sandbox / "result.json"
        basetemp = str(sandbox / "p")
        status = run_contained(
            [
                str(python), "-I", str(driver), json.dumps(["tests"]), SOURCE,
                str(REPO), str(sandbox / ".coverage"), str(sandbox / "report.json"),
                str(result_path), basetemp,
                json.dumps({
                    "installed_source": str(installed.resolve()),
                    "source_mapping": mapping, **coverage_options,
                    "compact_tmp_paths": os.name == "nt",
                }),
            ],
            cwd=REPO / COMPONENT, env=env, sandbox=sandbox, limits=Limits(wall_seconds=timeout_s),
        )
        if status:
            raise BaselineCollectionError(status, "", "contained standalone full coverage run failed")
        verify_revision(REPO, measured_commit)
        if verified_source_mapping(REPO, installed, environment) != (mapping, hashes):
            raise ValueError("installed source changed during baseline collection")
        result = json.loads(result_path.read_text())
        child_stream_parent.verify_final(
            result, control=coverage_options["child_stream_control"],
            nonce=coverage_options["child_stream_nonce"],
            inventory=coverage_options["child_stream_inventory"],
            helpers=coverage_options["child_stream_helpers"],
        )
        return build_baseline(REPO, result, measured_commit, hashes)
    finally:
        try:
            if hook is not None:
                hook.unlink()
        finally:
            try:
                if temporary is not None:
                    runner.cleanup_sandbox(temporary)
            finally:
                lease.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=[COMPONENT])
    parser.add_argument("--measured-commit", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--admission-wait", type=float, default=60)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    if not math.isfinite(args.admission_wait) or args.admission_wait < 0:
        parser.error("--admission-wait must be finite and non-negative")
    try:
        baseline = collect_standalone(
            measured_commit=args.measured_commit, prepare=args.prepare,
            timeout_s=args.timeout, admission_wait=args.admission_wait,
        )
        args.out.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"standalone coverage: {exc}", file=sys.stderr)
        return 1
    print(
        f"earned {baseline['plugin']} baseline: {len(baseline['tests'])} tests, "
        f"{len(baseline['coverage'])} source files; measured_commit={baseline['measured_commit']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
