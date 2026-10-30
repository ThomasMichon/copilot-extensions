"""Environment-only suite witness: no application install commands or services."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

import spike

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PROBE = (
    "import importlib.metadata as m,json,sys; "
    "d=m.distribution(sys.argv[1]); "
    "f=min((f for f in d.files if str(f).endswith('.py')),key=str); "
    "p=d.locate_file(f); compile(p.read_bytes(),str(p),'exec'); "
    "print(json.dumps({'distribution':d.metadata['Name'],'version':d.version,"
    "'python':sys.version,'platform':sys.platform,"
    "'installed_code':str(p),'probe':'compile installed Python; no app imports or startup'}))"
)


def captured(argv: list[str], log: Path, *, timeout: int = 180, env=None) -> tuple[int, str]:
    try:
        result = subprocess.run(
            argv, capture_output=True, timeout=timeout, env=env, **spike.child_options()
        )
        log.write_bytes(result.stdout + b"\nSTDERR:\n" + result.stderr)
        return result.returncode, result.stdout.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired as error:
        log.write_bytes((error.stdout or b"") + b"\nSTDERR:\n" + (error.stderr or b""))
        return 124, "bounded command timeout"


def git(args: list[str], log: Path) -> str:
    code, output = captured(["git", "-c", "core.longpaths=true", *args], log)
    if code:
        raise RuntimeError(f"Git failed ({code}); see {log}")
    return output.strip()


def initialize(row, project, slot, uv, index, run, environment, number):
    started = time.monotonic()
    shutil.copytree(project, slot, ignore=shutil.ignore_patterns(".venv"))
    row["copy_seconds"] = round(time.monotonic() - started, 3)
    row["environment_initially_absent"] = not (slot / ".venv").exists()
    cache = Path(environment["UV_CACHE_DIR"])
    row["cache_before"] = "shared warm" if cache.exists() else "absent"
    argv = [
        uv, "run", "--no-config", "--locked", "--no-dev", "--no-editable",
        "--link-mode", "hardlink", "--project", str(slot),
        "--default-index", index, "--exclude-newer", "7 days",
        "python", "-c", PROBE, row["distribution"],
    ]
    log = f"{project.name}.pass-{number}.cold.log"
    started = time.monotonic()
    code, output = captured(argv, run / log, env=environment)
    row["uv_init_and_probe_seconds"] = round(time.monotonic() - started, 3)
    if code:
        row.update(status="blocked-init", exit_code=code, error_log=log)
    else:
        probe = json.loads(output)
        assert Path(probe["installed_code"]).is_relative_to(slot / ".venv")
        row.update(status="qualified-environment", probe=probe)
        try:
            row["hardlink"] = spike.hardlink_evidence(Path(probe["installed_code"]), cache)
        except OSError as error:
            row["hardlink_observation_error"] = repr(error)
    return argv


def post_checks(row, slot, argv, uv, index, run, environment):
    name = slot.parents[1].name
    started = time.monotonic()
    code, _ = captured(argv, run / (name + ".warm.log"), env=environment)
    row["warm_probe_seconds"] = round(time.monotonic() - started, 3)
    if code:
        row.update(status="blocked-warm-probe", exit_code=code)
    linux = [
        uv, "sync", "--no-config", "--locked", "--no-dev", "--no-editable",
        "--dry-run", "--python-platform", "x86_64-unknown-linux-gnu",
        "--project", str(slot), "--default-index", index, "--exclude-newer", "7 days",
    ]
    code, _ = captured(linux, run / (name + ".linux-resolution.log"), env=environment)
    row["linux_resolution"] = "resolved-dry-run" if code == 0 else "blocked-see-linux-resolution-log"
    if code:
        row.update(status="blocked-linux-resolution", linux_exit_code=code)


def retire_version_slots(slots, root, cache):
    """Remove only exact, recorded version leaves; never remove a cache or parent."""
    for slot in slots:
        relative = slot.resolve().relative_to(root.resolve())
        if len(relative.parts) != 3 or relative.parts[1] != "versions":
            raise ValueError(f"not an exact test-owned version leaf: {slot}")
    if not cache.is_dir():
        raise ValueError("the runtime cache must exist before slot retirement")
    for slot in slots:
        shutil.rmtree(slot)
        assert not slot.exists()
    return {"deleted_version_slots": [str(slot) for slot in slots], "cache_preserved": cache.is_dir()}


def compare_rebuilds(first, second):
    paired = []
    for before, after in zip(first, second, strict=True):
        assert before["distribution"] == after["distribution"]
        cold = before["uv_init_and_probe_seconds"]
        rebuilt = after["uv_init_and_probe_seconds"]
        paired.append({
            "distribution": before["distribution"], "pass1_seconds": cold,
            "pass2_seconds": rebuilt, "speedup_ratio": round(cold / rebuilt, 3),
            "percent_reduction": round(100 * (cold - rebuilt) / cold, 2),
            "root_cache_artifact_reused": after.get("root_cache_artifact_reused", False),
        })
    cold = sum(row["uv_init_and_probe_seconds"] for row in first)
    rebuilt = sum(row["uv_init_and_probe_seconds"] for row in second)
    copy1 = sum(row["copy_seconds"] for row in first)
    copy2 = sum(row["copy_seconds"] for row in second)
    return {
        "pass1_init_total_seconds": round(cold, 3),
        "pass2_init_total_seconds": round(rebuilt, 3),
        "pass1_copy_total_seconds": round(copy1, 3),
        "pass2_copy_total_seconds": round(copy2, 3),
        "aggregate_init_speedup_ratio": round(cold / rebuilt, 3),
        "aggregate_init_percent_reduction": round(100 * (cold - rebuilt) / cold, 2),
        "copy_plus_init_speedup_ratio": round((cold + copy1) / (rebuilt + copy2), 3),
        "median_project_speedup_ratio": statistics.median(row["speedup_ratio"] for row in paired),
        "regressions": [row for row in paired if row["speedup_ratio"] < 1],
        "per_project": paired,
        "interpretation": "one paired trial; OS/network effects are not independently isolated",
        "network": "not disabled; no no-network claim",
    }


def validate(commit: str, *, two_pass: bool = False) -> Path:
    run = HERE / ".runs" / ("environments-" + uuid.uuid4().hex)
    run.mkdir(parents=True)
    report = {
        "status": "running", "source_commit": commit, "projects": [],
        "qualification": "base environments only; no install commands, globals or services",
        "python": sys.version, "python_executable": sys.executable,
        "toolchain_warmness": "preinstalled Python/uv; fresh candidate envs and initial runtime cache",
        "source_lane": "local committed source; no remote-release claim",
        "requested_passes": 2 if two_pass else 1,
    }

    def save():
        (run / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    try:
        dev, main = run / "dev", run / "main"
        spike.stage(REPO.as_uri(), commit, dev)
        code, _ = captured(
            [sys.executable, "-B", str(dev / "tools/materialize_main.py"),
             "--uv-suite", "--dest", str(main)],
            run / "materialize.log", timeout=1500,
        )
        report["materializer_exit"] = code
        prepared = main / "install-source" / "uv-projects"
        inventory = json.loads((prepared / "environment-projects.json").read_text(encoding="utf-8"))
        fixture = run / "git-source"
        shutil.copytree(
            prepared, fixture,
            ignore=shutil.ignore_patterns("uv-lock.log", ".venv", "__pycache__", "*.egg-info"),
        )
        git(["init", str(fixture)], run / "git-init.log")
        git(["-C", str(fixture), "add", "-A"], run / "git-add.log")
        tracked = git(["-C", str(fixture), "ls-files"], run / "git-tracked.log").splitlines()
        expected = sum(row["status"] == "locked" for row in inventory["projects"])
        actual = sum(path.endswith("/uv.lock") for path in tracked)
        report["git_lock_retention"] = {"expected_locked": expected, "tracked_locks": actual}
        if actual != expected:
            raise RuntimeError("generated uv locks were not retained by ordinary Git add")
        git([
            "-C", str(fixture), "-c", "user.name=Example", "-c", "user.email=example@example.com",
            "-c", "core.hooksPath=", "commit", "--quiet", "-m", "Portable uv environment fixtures",
        ], run / "git-commit.log")
        fixture_commit = git(["-C", str(fixture), "rev-parse", "HEAD"], run / "git-head.log")
        relocated = run / "independent-checkout"
        spike.stage(fixture.as_uri(), fixture_commit, relocated)
        report["independent_git_checkout"] = True
        dev.rename(run / "unavailable-dev")
        main.rename(run / "unavailable-main")
        fixture.rename(run / "unavailable-git-source")
        report["original_input_paths_removed_before_initialization"] = True
        cache = run / "runtime-cache"
        assert not cache.exists()
        environment = {key: value for key, value in os.environ.items() if not key.startswith("UV_")}
        environment.pop("PYTHONPATH", None)
        environment.pop("VIRTUAL_ENV", None)
        environment["UV_CACHE_DIR"] = str(cache)
        uv = shutil.which("uv")
        entries = sorted(
            inventory["projects"], key=lambda row: row["distribution"] == "agent-index-engine",
        )
        prepared_rows = {}
        probe_commands = {}
        for source in entries:
            row = {
                "distribution": source["distribution"], "project": source["project"],
                "profile": "base", "optional_profiles_not_initialized": source[
                    "optional_profiles_not_initialized"
                ], "lock_status": source["status"],
            }
            if source["status"] != "locked":
                row.update(status="blocked-lock", error=source.get("error"))
                report["projects"].append(row)
                save()
                continue
            project = relocated / Path(source["folder"]).name
            version = __import__("tomllib").loads(
                (project / "pyproject.toml").read_text(encoding="utf-8")
            )["project"]["version"]
            slot = run / "slots" / project.name / "versions" / version
            prepared_rows[row["distribution"]] = (project, slot)
            argv = initialize(row, project, slot, uv, inventory["index"], run, environment, 1)
            probe_commands[row["distribution"]] = argv
            if not two_pass and row["status"] == "qualified-environment":
                post_checks(row, slot, argv, uv, inventory["index"], run, environment)
            report["projects"].append(row)
            save()
            print(f"{row['distribution']}: {row['status']}", flush=True)
        if two_pass and all(row["status"] == "qualified-environment" for row in report["projects"]):
            first = report["projects"]
            snapshots = {
                row["distribution"]: Path(row["hardlink"]["cache_path"]).stat()
                for row in first if row.get("hardlink", {}).get("same_file")
            }
            report["passes"] = [{"number": 1, "projects": first}]
            report["slot_retirement"] = retire_version_slots(
                [slot for _project, slot in prepared_rows.values()], run / "slots", cache,
            )
            report["projects"] = []
            report["passes"].append({"number": 2, "projects": report["projects"]})
            save()
            for before in first:
                row = {key: before[key] for key in (
                    "distribution", "project", "profile", "optional_profiles_not_initialized",
                    "lock_status",
                )}
                project, slot = prepared_rows[row["distribution"]]
                argv = initialize(row, project, slot, uv, inventory["index"], run, environment, 2)
                probe_commands[row["distribution"]] = argv
                if row.get("hardlink", {}).get("same_file") and row["distribution"] in snapshots:
                    row["root_cache_artifact_reused"] = os.path.samestat(
                        snapshots[row["distribution"]], Path(row["hardlink"]["cache_path"]).stat(),
                    )
                report["projects"].append(row)
                save()
                print(f"rebuild {row['distribution']}: {row['status']}", flush=True)
            if all(row["status"] == "qualified-environment" for row in report["projects"]):
                report["rebuild_comparison"] = compare_rebuilds(first, report["projects"])
                for row in report["projects"]:
                    _project, slot = prepared_rows[row["distribution"]]
                    post_checks(
                        row, slot, probe_commands[row["distribution"]], uv,
                        inventory["index"], run, environment,
                    )
        assert len(report["projects"]) == len(inventory["projects"])
        report["summary"] = {
            "project_count": len(report["projects"]),
            "qualified_environment_count": sum(
                row["status"] == "qualified-environment" for row in report["projects"]
            ),
            "native_cache_hardlink_count": sum(
                row.get("hardlink", {}).get("same_file", False) for row in report["projects"]
            ),
            "linux_dry_run_resolution_count": sum(
                row.get("linux_resolution") == "resolved-dry-run" for row in report["projects"]
            ),
            "tracked_uv_lock_count": actual,
            "root_cache_artifact_reuse_count": sum(
                row.get("root_cache_artifact_reused", False) for row in report["projects"]
            ),
        }
        report["status"] = (
            "passed" if all(row["status"] == "qualified-environment" for row in report["projects"])
            else "incomplete"
        )
    except Exception as error:
        report.update(status="failed", error=repr(error))
        raise
    finally:
        save()
        print("RESULTS " + str(run / "results.json"), flush=True)
    return run


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--two-pass", action="store_true")
    args = parser.parse_args()
    result = validate(args.source_commit, two_pass=args.two_pass)
    raise SystemExit(0 if json.loads((result / "results.json").read_text())["status"] == "passed" else 1)
