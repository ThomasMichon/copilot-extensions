"""Opt-in portable uv environment projects; no app install or lifecycle effects."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import time
import tomllib
from pathlib import Path

import construction_inventory as ci
import uv_editable_ref as uer


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def metadata(project: Path) -> dict:
    return tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8-sig"))


def inline(value) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {inline(v)}" for k, v in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(inline(item) for item in value) + "]"
    raise ValueError(f"unsupported uv source value: {type(value).__name__}")


def source_table(project: Path, sources: dict) -> None:
    path = project / "pyproject.toml"
    text = path.read_text(encoding="utf-8-sig")
    span = uer.uv_sources_table_span(text)
    body = "".join(
        f"{json.dumps(name)} = {inline(value)}\n" for name, value in sources.items()
    ) + "\n"
    text = (
        text[:span[0]] + "\n" + body + text[span[1]:] if span
        else text.rstrip() + "\n\n[tool.uv.sources]\n" + body
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def copy_project(source: Path, destination: Path) -> None:
    shutil.copytree(
        source, destination,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", "*.egg-info", "uv.lock",
            ".pytest_cache", ".ruff_cache", "build", "dist", ".test-runs", ".runs",
        ),
    )


def runtime_resources(project: Path, original: Path, root: Path) -> None:
    """Deliver the existing standalone sdist fallback resources before building."""
    setup = original / "setup.py"
    if not setup.exists():
        return
    text = setup.read_text(encoding="utf-8")
    if "_standalone_runtime_build.py" not in text and not (
        original / "_build_runtime_resource.py"
    ).exists():
        return
    canonical = root / "libs" / "versioned-runtime"
    helper = canonical / "standalone_build.py"
    declarations = [
        node.value for node in ast.parse(helper.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "RUNTIME_RESOURCES"
            for target in node.targets
        )
    ]
    resources = ast.literal_eval(declarations[0])
    package = metadata(original)["project"]["name"].replace("-", "_")
    target = project / "src" / package
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(helper, project / "_standalone_runtime_build.py")
    for source, destination in resources.items():
        shutil.copyfile(canonical / source, target / destination)
    companions = {}
    if package == "fleet_manager":
        companions = {
            "_runtime_launcher.py": root / "libs/installer-engine/runtime_launcher.py",
            "_installer_process.py": root / "libs/installer-engine/installer_engine_process.py",
            "_construction_procutil.py": root / "libs/agent-procutil/src/agent_procutil/__init__.py",
        }
    elif package == "agent_chat_service":
        companions = {
            name: root / "libs" / "installer-engine" / name
            for name in (
                "installer_engine.py", "installer_engine_process.py",
                "installer_dependency_policy.py", "installer_dependency_config.py",
                "installer_wheel_inputs.py", "installer_wheel_snapshot.py", "runtime_launcher.py",
            )
        }
    for name, source in companions.items():
        shutil.copyfile(source, target / name)


def close_project(original: Path, destination: Path, catalog: dict, root: Path) -> None:
    root_name = normalized(metadata(original)["project"]["name"])
    located = {root_name: destination}
    visited = set()

    def visit(source: Path, project: Path):
        name = normalized(metadata(source)["project"]["name"])
        if name in visited:
            return
        visited.add(name)
        runtime_resources(project, source, root)
        data = metadata(project)
        sources = dict(data.get("tool", {}).get("uv", {}).get("sources", {}))
        requirements = list(data["project"].get("dependencies", []))
        for values in data["project"].get("optional-dependencies", {}).values():
            requirements.extend(values)
        for values in data.get("dependency-groups", {}).values():
            requirements.extend(item for item in values if isinstance(item, str))
        requirements.extend(data.get("build-system", {}).get("requires", []))
        names = {normalized(re.match(r"[\w.-]+", value).group()) for value in requirements}
        names.update(normalized(value) for value in sources)
        for dependency in sorted(names):
            if dependency not in catalog:
                continue
            if dependency == root_name:
                # uv already owns the root identity; a path override conflicts with it.
                for key in list(sources):
                    if normalized(key) == dependency:
                        sources.pop(key)
                continue
            if dependency not in located:
                target = destination / "libs" / dependency
                if target.exists():
                    shutil.rmtree(target)
                copy_project(catalog[dependency], target)
                located[dependency] = target
            target = located[dependency]
            key = next((key for key in sources if normalized(key) == dependency), dependency)
            sources[key] = {"path": os.path.relpath(target, project).replace("\\", "/")}
            visit(catalog[dependency], target)
        if sources:
            source_table(project, sources)

    visit(original, destination)


def portable(project: Path) -> list[str]:
    problems = []
    for path in project.rglob("pyproject.toml"):
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        for name, source in data.get("tool", {}).get("uv", {}).get("sources", {}).items():
            if isinstance(source, dict) and "path" in source:
                value = source["path"]
                if re.match(r"^(?:[A-Za-z]:|[/\\])", value) or not (
                    path.parent / value
                ).resolve().is_relative_to(project.resolve()):
                    problems.append(f"{path.relative_to(project)}: escaping source {name}")
    lock = project / "uv.lock"
    if lock.exists():
        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"editable", "directory", "path", "virtual"} and isinstance(item, str):
                        if re.match(r"^(?:[A-Za-z]:|[/\\]|file:)", item):
                            problems.append(f"uv.lock: absolute local {key}")
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(tomllib.loads(lock.read_text(encoding="utf-8")))
    return problems


def assemble(destination: Path, source: Path, *, index: str = "https://pypi.org/simple") -> list[str]:
    """Compose all declared owner projects, recording every lock outcome."""
    declaration = ci.inventory(source, ci.tracked_projects(source))
    owners = declaration["owners"]
    catalog = {}
    for entry in owners:
        catalog[normalized(entry["distribution"])] = destination / Path(entry["project"]).parent
    for entry in declaration["shared_library_inputs"]:
        project = destination / Path(entry).parent
        catalog[normalized(metadata(project)["project"]["name"])] = project
    output = destination / "install-source" / "uv-projects"
    output.mkdir(parents=True)
    report = {
        "qualification": "environment preparation only; no install commands or services",
        "selected_profile": "base", "index": index,
        "lock_resolution": "uv universal resolution; optional groups locked but not selected for init",
        "projects": [],
    }
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("the opt-in suite requires an available uv")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("UV_")}
    environment["UV_CACHE_DIR"] = str(destination.parent / "uv-lock-cache")
    for owner in owners:
        relative = Path(owner["project"]).parent
        original = destination / relative
        project = output / relative.as_posix().replace("/", "--")
        row = {
            **owner, "folder": project.relative_to(destination).as_posix(), "profile": "base",
            "optional_profiles_not_initialized": [
                name for name in owner["optional_dependency_groups"] if name not in {"dev", "test"}
            ],
        }
        started = time.monotonic()
        try:
            copy_project(original, project)
            close_project(original, project, catalog, destination)
            ignore = project / ".gitignore"
            text = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
            ignore.write_text(
                text.rstrip() + "\n.venv/\nuv-lock.log\n!uv.lock\n", encoding="utf-8", newline="\n",
            )
            result = subprocess.run(
                [uv, "lock", "--no-config", "--project", str(project),
                 "--default-index", index, "--exclude-newer", "7 days"],
                env=environment, capture_output=True, text=True, timeout=180,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            (project / "uv-lock.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            problems = portable(project)
            row.update(
                status="locked" if result.returncode == 0 and not problems else "blocked",
                error=(result.stderr if result.returncode else "") or "; ".join(problems),
                relative_source_and_lock_paths=not problems,
            )
        except Exception as error:
            row.update(status="blocked", error=repr(error))
        row["seconds"] = round(time.monotonic() - started, 3)
        report["projects"].append(row)
        (output / "environment-projects.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8",
        )
        print(f"uv project {owner['distribution']}: {row['status']} ({row['seconds']}s)", flush=True)
    return [
        ("OK   " if row["status"] == "locked" else "SKIP ") + row["distribution"] + ": " + row["status"]
        for row in report["projects"]
    ]
