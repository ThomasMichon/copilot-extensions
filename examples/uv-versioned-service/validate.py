"""Deterministic local Tier-P witness; artifacts stay in the example's .runs."""

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


def execute(
    argv: list[str], log: Path, *, cwd: Path | None = None, env: dict | None = None,
) -> str:
    started = time.monotonic()
    try:
        result = subprocess.run(
            argv, cwd=cwd, env=env, capture_output=True, timeout=240, **spike.child_options()
        )
    except subprocess.TimeoutExpired as error:
        log.write_bytes((error.stdout or b"") + b"\nSTDERR:\n" + (error.stderr or b""))
        raise
    log.write_bytes(result.stdout + b"\nSTDERR:\n" + result.stderr)
    if result.returncode:
        raise RuntimeError(f"exit {result.returncode}: {argv[0]}; details: {log}")
    print(f"{log.stem}: {time.monotonic() - started:.3f}s", flush=True)
    return result.stdout.decode("utf-8", errors="replace").strip()


def git_fixture(root: Path, log: Path) -> str:
    for args in (
        ["init", str(root)],
        ["-C", str(root), "add", "--force", "."],
        ["-C", str(root), "-c", "user.name=Example", "-c", "user.email=example@example.com",
         "-c", "core.hooksPath=", "commit", "-m", "Synthetic assembled example"],
    ):
        execute(["git", *args], log)
    return execute(["git", "-C", str(root), "rev-parse", "HEAD"], log)


def concurrent_tools(project: Path, run: Path) -> dict:
    home = run / "concurrent-home"
    boot = spike.copy_candidate(project, home, "tool")
    assert not Path(boot["slot"], ".venv").exists()
    assert not (home / "cache").exists()
    processes = []
    started = time.monotonic()
    environment = spike.environment(home)
    try:
        for index in range(2):
            caller = run / f"caller {index}"
            caller.mkdir()
            args = [f"space argument {index}", "--literal-flag"]
            process = subprocess.Popen(
                spike.uv_command(Path(boot["slot"]), "tool", *args), cwd=caller,
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **spike.child_options(),
            )
            processes.append((process, caller, args))
        outputs = []
        for index, (process, caller, args) in enumerate(processes):
            stdout, stderr = process.communicate(timeout=120)
            (run / f"concurrent-{index}.stdout").write_bytes(stdout)
            (run / f"concurrent-{index}.stderr").write_bytes(stderr)
            assert process.returncode == 0, stderr.decode(errors="replace")
            output = json.loads(stdout)
            assert Path(output["cwd"]) == caller and output["args"] == args
            assert Path(output["module"]).is_relative_to(Path(boot["slot"]) / ".venv")
            assert output["service_started"] is False
            outputs.append(output)
        assert not (home / "services").exists()
        assert not (home / "bin").exists()
        assert not (Path(boot["slot"]).parents[1] / "current-version").exists()
        assert not (Path(boot["slot"]) / ".install-complete").exists()
        return {
            "initial_environment": "absent", "initial_cache": "absent",
            "concurrent_invocations": 2, "outputs": outputs,
            "seconds": round(time.monotonic() - started, 3),
            "all_launchers_exited": True, "globals_unpublished": True,
            "invocation": "direct candidate uv run, read-only tool command",
        }
    finally:
        for process, _caller, _args in processes:
            if process.poll() is None:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True, timeout=10, **spike.child_options(),
                    )
                else:
                    process.terminate()
                process.communicate(timeout=10)


def warm_tool_overhead(boot: dict, home: Path, run: Path, caller: Path) -> dict:
    timings = {"direct_uv": [], "outer_binstub": []}
    for index in range(3):
        for label, argv in (
            ("direct_uv", spike.uv_command(Path(boot["slot"]), "tool", "probe")),
            ("outer_binstub", spike.command(Path(boot["binstub"])) + ["probe"]),
        ):
            started = time.monotonic()
            result = subprocess.run(
                argv, cwd=caller, env=spike.environment(home), capture_output=True,
                timeout=30, check=True, **spike.child_options(),
            )
            timings[label].append(round(time.monotonic() - started, 6))
            (run / f"warm-{label}-{index}.stderr").write_bytes(result.stderr)
            output = json.loads(result.stdout)
            assert output["args"] == ["probe"] and Path(output["cwd"]) == caller
    direct = statistics.median(timings["direct_uv"])
    outer = statistics.median(timings["outer_binstub"])
    return {
        **timings, "direct_uv_median_seconds": direct, "outer_binstub_median_seconds": outer,
        "observed_median_difference_seconds": round(outer - direct, 6),
        "environment_and_cache": "warm", "samples_per_path": 3,
    }


def wrapper_matrix(boot: dict, home: Path, run: Path) -> dict:
    results = {}
    slot = Path(boot["slot"])
    for extension in ("ps1", "cmd", "sh"):
        binary = Path(boot["binstub"]).with_suffix("." + extension)
        assert binary.is_file() and (slot / "bin" / ("invoke." + extension)).is_file()
        global_body = binary.read_text(encoding="utf-8")
        assert "uv run" not in global_body and "UV_CACHE_DIR" not in global_body
        try:
            argv = spike.command(binary)
        except FileNotFoundError as error:
            results[extension] = {"status": "unavailable", "reason": str(error)}
            continue
        caller = run / ("shell caller " + extension)
        caller.mkdir()
        args = ["space argument", "--literal-flag"]
        success = subprocess.run(
            argv + args, cwd=caller, env=spike.environment(home), capture_output=True,
            timeout=30, check=True, **spike.child_options(),
        )
        output = json.loads(success.stdout)
        assert Path(output["cwd"]) == caller and output["args"] == args
        assert Path(output["module"]).is_relative_to(slot / ".venv")
        failed = subprocess.run(
            argv + ["install", "--fail"], cwd=caller, env=spike.environment(home),
            capture_output=True, timeout=30, **spike.child_options(),
        )
        (run / ("shell-" + extension + ".stderr")).write_bytes(success.stderr + failed.stderr)
        assert failed.returncode == 17, (extension, failed.returncode, failed.stderr)
        results[extension] = {"status": "passed", "cwd_and_args": True, "exit_code": 17}
    return results


def validate(source_commit: str, *, real_home: bool = False) -> Path:
    run = HERE / ".runs" / ("validation-" + uuid.uuid4().hex)
    run.mkdir(parents=True)
    report = {
        "tier": "P", "status": "running", "source_commit": source_commit,
        "source_lane": "local committed candidate, not remote main",
        "run_directory": str(run), "commands": [], "checks": {},
        "toolchain": {
            "python": sys.version, "python_executable": sys.executable,
            "python_warmness": "already installed host interpreter; no fresh Python provisioning",
            "uv": shutil.which("uv"),
            "candidate_environment_and_cache": "absent before candidate first uv run",
        },
    }

    def invoke(
        argv: list[str], label: str, *, cwd: Path | None = None, env: dict | None = None,
    ) -> str:
        report["commands"].append(argv)
        return execute(argv, run / (label + ".log"), cwd=cwd, env=env)

    def record(label: str, value):
        report["checks"][label] = value
        (run / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(label + ": " + json.dumps(value), flush=True)

    try:
        dev, main = run / "dev", run / "main"
        spike.stage(
            REPO.as_uri(), source_commit, dev,
            paths=("/tools/", "/examples/uv-versioned-service/"),
        )
        invoke(
            ["git", "-C", str(dev), "sparse-checkout", "set", "--no-cone",
             "/tools/", "/examples/uv-versioned-service/"], "example-source-selection",
        )
        record("shallow_dev", invoke(
            ["git", "-C", str(dev), "rev-parse", "--is-shallow-repository"], "shallow-dev"
        ) == "true")
        assert report["checks"]["shallow_dev"]
        invoke(
            [sys.executable, "-B", str(dev / "tools" / "materialize_main.py"),
             "--uv-example", "--dest", str(main)], "materialize",
        )
        ready = main / "examples" / "uv-versioned-service" / "install-source"
        service = ready / "services" / "example-service"
        tool = ready / "tools" / "example-tool"
        assert (service / "uv.lock").exists() and (tool / "uv.lock").exists()
        assert not (service / ".venv").exists() and not (tool / ".venv").exists()
        tool_home = run / "tool home"
        tool_result = spike.bootstrap(tool, tool_home, "tool")
        assert not (tool_home / "services").exists()
        assert Path(tool_result["slot"], ".venv").exists()
        record("tool_install_no_daemon_then_publish", tool_result)
        for kind, project in (("service", service), ("tool", tool)):
            failure_home = run / ("first-failure-" + kind)
            try:
                spike.bootstrap(project, failure_home, kind, fail=True)
            except subprocess.CalledProcessError as error:
                (run / ("failed-first-" + kind + ".stderr")).write_text(
                    error.stderr or "", encoding="utf-8"
                )
            else:
                raise AssertionError("injected first install unexpectedly succeeded")
            assert not (failure_home / "bin").exists()
            owner = failure_home / spike.KINDS[kind] / spike.identity(project)[0]
            assert not (owner / "current-version").exists()
            assert all(
                not (attempt / ".install-complete").exists()
                for attempt in (owner / "versions").iterdir()
            )
            record("failed_first_install_no_publication_" + kind, True)
        home = run / "isolated-home"
        candidate = spike.copy_candidate(service, home, "service")
        assert not candidate["environment_present"]
        assert not (home / "bin").exists()
        assert not (Path(candidate["slot"]).parents[1] / "current-version").exists()
        assert not Path(candidate["slot"], ".install-complete").exists()
        assert not (home / "cache" / "uv").exists()
        record("candidate_plain_copy_before_uv", candidate)

        relocated_main, relocated_dev = run / "relocated-main", run / "relocated-dev"
        main.rename(relocated_main)
        dev.rename(relocated_dev)
        ready = relocated_main / "examples" / "uv-versioned-service" / "install-source"
        service, tool = ready / "services" / "example-service", ready / "tools" / "example-tool"
        initial = spike.install_candidate(candidate, home, "service")
        assert initial["environment_present"]
        record("cold_uv_init_and_install_then_publication", initial)
        cold = spike.run_service(home, initial["name"], run_dir=run / "cold")
        assert cold["library_links"] > 1, "uv did not hardlink the installed library"
        if sys.platform == "win32":
            assert cold["hardlink"].get("same_file"), "no cache hardlink identity observed"
            assert cold["service_handle_signaled"]
        slot = Path(initial["slot"])
        assert Path(cold["module"]).is_relative_to(slot / ".venv")
        assert Path(cold["library"]).is_relative_to(slot / ".venv")
        assert Path(cold["cwd"]) == run / "cold"
        record("first_published_binstub_reuses_environment", cold)
        stamp = (slot / "pyproject.toml").stat().st_mtime_ns
        warm_copy = spike.bootstrap(service, home, "service")
        assert not warm_copy["copied"] and warm_copy["environment_present"]
        assert (slot / "pyproject.toml").stat().st_mtime_ns == stamp
        record("warm_setup_no_copy", warm_copy)
        record("warm_default_uv_sync", spike.run_service(
            home, initial["name"], run_dir=run / "warm"
        ))

        new_manifest = (
            relocated_dev / "examples" / "uv-versioned-service" / "dev"
            / "services" / "example-service" / "pyproject.toml"
        )
        new_manifest.write_text(
            new_manifest.read_text().replace('version = "0.1.0"', 'version = "0.1.1"'),
            encoding="utf-8",
        )
        v2_main = run / "main-v2"
        invoke(
            [sys.executable, "-B", str(relocated_dev / "tools" / "materialize_main.py"),
             "--uv-example", "--dest", str(v2_main)], "materialize-v2",
        )
        v2 = (
            v2_main / "examples" / "uv-versioned-service" / "install-source"
            / "services" / "example-service"
        )
        owner = home / "services" / initial["name"]
        binary = Path(initial["binstub"])
        marker = slot / ".install-complete"
        previous = tuple(
            binary.with_suffix("." + extension).read_bytes() for extension in ("ps1", "cmd", "sh")
        ) + ((owner / "current-version").read_bytes(), marker.read_bytes())
        try:
            spike.bootstrap(v2, home, "service", fail=True)
        except subprocess.CalledProcessError as error:
            (run / "failed-upgrade.stderr").write_text(error.stderr or "", encoding="utf-8")
            record("expected_install_failure", str(error))
        else:
            raise AssertionError("injected upgrade install unexpectedly succeeded")
        assert previous == tuple(
            binary.with_suffix("." + extension).read_bytes() for extension in ("ps1", "cmd", "sh")
        ) + ((owner / "current-version").read_bytes(), marker.read_bytes())
        assert (owner / "current-version").read_text().strip() == "0.1.0"
        failed = list((owner / "versions").glob("0.1.1.failed.*"))
        assert len(failed) == 1 and not (owner / "versions" / "0.1.1").exists()
        assert not (failed[0] / ".install-complete").exists()
        record("failed_attempt_previous_publication_unchanged", {
            "attempt": str(failed[0]), "current": "0.1.0", "previous_bytes_unchanged": True,
        })
        retry = spike.bootstrap(v2, home, "service")
        assert retry["copied"] and retry["environment_present"]
        record("retry_copy_install_and_publication", retry)
        record("fresh_retry_new_version", spike.run_service(
            home, initial["name"], run_dir=run / "retry"
        ))
        caller = run / "published tool caller"
        caller.mkdir()
        environment = os.environ.copy()
        environment.update(
            UV_NO_SYNC="1", UV_FROZEN="1", UV_WORKING_DIR=str(tool),
            UV_PROJECT_ENVIRONMENT=str(run / "wrong-environment"),
        )
        args = ["space argument", "--literal-flag"]
        output = json.loads(invoke(
            spike.command(Path(tool_result["binstub"])) + args, "first-tool",
            cwd=caller, env=environment,
        ))
        assert output["service_started"] is False
        assert Path(output["cwd"]) == caller and output["args"] == args
        assert not (run / "wrong-environment").exists()
        record("published_tool_preserves_cwd_args_no_service_start", output)
        record("two_layer_wrapper_matrix", wrapper_matrix(tool_result, tool_home, run))
        record("warm_direct_uv_vs_outer_binstub", warm_tool_overhead(
            tool_result, tool_home, run, caller,
        ))
        record("concurrent_cold_candidate_uv", concurrent_tools(tool, run))

        fixture = run / "main-source-fixture"
        shutil.copytree(ready, fixture / "install-source")
        fixture_commit = git_fixture(fixture, run / "fixture-git.log")
        main_stage = run / "staged-main"
        spike.stage(fixture.as_uri(), fixture_commit, main_stage)
        main_home = run / "main-style-home"
        main_install = spike.bootstrap(
            main_stage / "install-source" / "services" / "example-service", main_home, "service"
        )
        assert main_install["environment_present"]
        record("main_style_copy_install_publish_no_transform", main_install)
        record("main_style_copy_without_materialization", spike.run_service(
            main_home, initial["name"], run_dir=run / "main-style"
        ))
        if real_home:
            actual = Path.home() / ".copilot-extensions"
            service_owner = actual / "services" / initial["name"]
            binary = actual / "bin" / Path(initial["binstub"]).name
            if service_owner.exists() or binary.exists():
                record("actual_home", {"status": "skipped", "reason": "synthetic name already exists"})
            else:
                try:
                    cache_preexisted = (actual / "cache" / "uv").exists()
                    actual_install = spike.bootstrap(service, actual, "service")
                    assert actual_install["environment_present"]
                    record("actual_home", {
                        "cache_preexisted": cache_preexisted, "setup": actual_install,
                        "service": spike.run_service(
                            actual, initial["name"], run_dir=run / "actual-home",
                        ),
                    })
                finally:
                    if service_owner.exists():
                        shutil.rmtree(service_owner)
                    for extension in ("ps1", "cmd", "sh"):
                        binary.with_suffix("." + extension).unlink(missing_ok=True)
                record("actual_home_owned_slot_and_binstub_removed", True)
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = repr(error)
        raise
    finally:
        (run / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("RESULTS " + str(run / "results.json"), flush=True)
    return run


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--real-home", action="store_true")
    args = parser.parse_args()
    validate(args.source_commit, real_home=args.real_home)
