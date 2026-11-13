"""Complete prepared application inputs for the existing runtime update owner."""

from __future__ import annotations

import ast
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from agent_procutil import no_window_kwargs

from .installation_probe import strict_json_object

if TYPE_CHECKING:
    from .update_runtime_plan import RuntimeUpdateStep


def plan_step(step: RuntimeUpdateStep, *, force: bool) -> RuntimeUpdateStep:
    """Build the canonical application call without replacing the update executor."""
    from . import update_runtime as runtime

    try:
        assert step.source is not None and step.source.application is not None
        assert step.runtime_root is not None
        return dataclasses.replace(
            step,
            argv=step.source.application.argv(step.runtime_root, repair=force),
            deadline_s=max(
                600, runtime._installer_timeout(step.name, step.environment, step.artifact)
            ),
        )
    except (OSError, ValueError) as error:
        return dataclasses.replace(
            step, status=f"application installation inputs invalid: {error}"
        )


def _read(path: Path) -> dict:
    info = path.lstat()
    if (
        path.is_symlink()
        or getattr(info, "st_file_attributes", 0) & 0x400
        or not path.is_file()
        or info.st_size > 4 << 20
    ):
        raise ValueError("Application input must be a bounded ordinary document")
    with path.open("rb") as stream:
        content = stream.read((4 << 20) + 1)
    if len(content) > 4 << 20:
        raise ValueError("Application input exceeds its document bound")
    value = json.loads(content, object_pairs_hook=strict_json_object)
    if not isinstance(value, dict):
        raise ValueError("Application input must be an object")
    return value


def _member(root: Path, value: str) -> Path:
    if not isinstance(value, str):
        raise ValueError("Application input requires a relative member")
    relative = PurePosixPath(value)
    if (
        not relative.parts
        or relative.is_absolute()
        or ".." in relative.parts
        or "\\" in value
        or ":" in value
    ):
        raise ValueError("Application input requires a portable relative member")
    path = root
    for part in relative.parts:
        path /= part
        info = path.lstat()
        if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Application input member must not be linked")
    return path


def _selector(root: Path, name: str) -> str:
    path = _member(root, name)
    if not path.is_file() or path.stat().st_size > 4096:
        raise ValueError("Application selector must be a bounded ordinary file")
    return path.read_text(encoding="utf-8-sig").strip()


def selected_version(root: Path, component: str) -> str | None:
    """Observe an application selector, without asserting completion or serving health."""
    try:
        selected = _selector(root, "current-version")
        if selected == "dev" or re.fullmatch(r"dev-(?:[0-9a-f]{32}|[0-9a-f]{64})", selected):
            return None
        if re.fullmatch(r"[0-9][A-Za-z0-9._+-]*", selected) is None:
            raise ValueError("Invalid selected application version")
        path = _member(root, "versions/" + selected + "/application.json")
    except FileNotFoundError:
        return None
    if _read(path).get("name") != component:
        raise ValueError("Selected application belongs to another component")
    return selected


@dataclasses.dataclass(frozen=True)
class PreparedApplication:
    snapshot: Path
    component: str
    profile: str
    payload: Path = dataclasses.field(init=False)
    identity: str = dataclasses.field(init=False)
    selection: str = dataclasses.field(init=False)
    declaration: str = dataclasses.field(init=False)
    association: str = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        if not self.snapshot.is_absolute() or self.snapshot.is_symlink():
            raise ValueError("Prepared application requires an absolute ordinary snapshot")
        snapshot = self.snapshot.resolve(strict=True)
        record = _read(snapshot / "install-source.json")
        projects = record.get("projects")
        composition = record.get("composition")
        kind = record.get("kind")
        if kind == "main":
            valid_composition = composition is None
        else:
            valid_composition = (
                isinstance(composition, dict)
                and set(composition) == {"main_seed", "tree"}
                and composition["main_seed"] == record.get("main_head")
                and all(
                    isinstance(value, str)
                    and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is not None
                    for value in composition.values()
                )
            )
        if (
            record.get("schema") != "copilot-extensions.install-source"
            or type(record.get("version")) is not int
            or record["version"] != 1
            or kind not in ("main", "source")
            or not isinstance(projects, dict)
            or self.component not in projects
            or not isinstance(projects[self.component], dict)
            or not isinstance(self.profile, str)
            or not isinstance(record.get("main_head"), str)
            or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", record["main_head"]) is None
            or not valid_composition
        ):
            raise ValueError("Prepared application requires a complete source snapshot")
        project = projects[self.component]
        if (
            not {"source", "payload", "profile", "wheel_inputs", "artifact", "application"}
            <= project.keys()
        ):
            raise ValueError(
                "Prepared application requires complete wheel/profile/application inputs"
            )
        payload = _member(snapshot, project["payload"])
        declaration = _read(_member(snapshot, project["application"]) / "application.json")
        construction = declaration.get("construction")
        if (
            not isinstance(construction, dict)
            or not {"identity", "runtime_version", "payload_hash", "extras", "local_packages"}
            <= construction.keys()
            or not isinstance(project["source"], dict)
            or construction["identity"]
            != {
                "runtime_source": project["source"],
                "runtime_version": project["source"].get("runtime_version"),
            }
            or project["source"].get("plugin") != self.component
            or project["source"].get("source_commit") != record.get("source_commit")
            or project["source"].get("source_tree") != record.get("source_tree")
            or declaration.get("name") != self.component
            or not isinstance(declaration.get("module"), str)
            or re.fullmatch(
                r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?::\w+(?:\.\w+)*)?", declaration["module"]
            )
            is None
            or any(
                not isinstance(construction.get(key), str)
                or re.fullmatch(pattern, construction[key]) is None
                for key, pattern in (
                    ("runtime_version", r"[0-9][A-Za-z0-9._+-]*"),
                    ("payload_hash", r"[0-9a-f]{64}"),
                )
            )
        ):
            raise ValueError("Prepared application differs from its source selection")
        for key in ("extras", "local_packages"):
            values = construction[key]
            if (
                not isinstance(values, list)
                or len(values) > 256
                or any(
                    not isinstance(value, str)
                    or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) is None
                    for value in values
                )
                or len(set(values)) != len(values)
            ):
                raise ValueError("Prepared application has invalid construction names")
        selection = {
            key: construction[key]
            for key in (
                "identity",
                "runtime_version",
                "payload_hash",
                "extras",
                "local_packages",
            )
        }
        selection["source_descriptor"] = record.get("source_descriptor")
        object.__setattr__(self, "snapshot", snapshot)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "identity", json.dumps(construction["identity"], sort_keys=True))
        object.__setattr__(self, "selection", json.dumps(selection, sort_keys=True))
        object.__setattr__(self, "declaration", json.dumps(declaration, sort_keys=True))
        object.__setattr__(self, "association", json.dumps(record, sort_keys=True))

    @property
    def version(self) -> str:
        return json.loads(self.selection)["runtime_version"]

    def validate(self, owner: Path, root: Path, environment: Mapping[str, str]) -> None:
        from .update_runtime_target import application_profile

        observed = PreparedApplication(self.snapshot, self.component, self.profile)
        if observed != self:
            raise ValueError("Prepared application inputs changed after selection")
        profile = application_profile(self.component, owner, root, environment)
        selected = json.loads(self.selection)
        if json.dumps(profile, sort_keys=True) != self.profile or selected["extras"] != (
            profile["extras"] if profile is not None else []
        ):
            raise ValueError("Application profile changed after preparation")
        helper = self.payload / "scripts" / "install_source.py"
        supported = False
        for node in ast.parse(helper.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "APPLICATION_INSTALL_CONTRACT"
                for target in node.targets
            ):
                capability = ast.literal_eval(node.value)
                supported = (
                    isinstance(capability, dict)
                    and set(capability) == {"schema", "version"}
                    and capability["schema"] == "copilot-extensions.prepared-application-install"
                    and type(capability["version"]) is int
                    and capability["version"] == 1
                )
        if not supported:
            raise ValueError("Selected source lacks canonical application installation support")
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(helper),
                "--install-source",
                str(self.snapshot),
                "--component",
                self.component,
                "--payload",
                str(self.payload),
                "--native-inputs",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            env=dict(environment),
            check=False,
            **no_window_kwargs(),
        )
        if result.returncode:
            detail = result.stderr.strip() or result.returncode
            raise ValueError(f"Prepared application validation failed: {detail}")
        if (
            len(result.stdout.encode("utf-8")) > 4 << 20
            or json.dumps(
                json.loads(
                    result.stdout,
                    object_pairs_hook=strict_json_object,
                ),
                sort_keys=True,
            )
            != self.selection
        ):
            raise ValueError("Prepared application validator returned a different selection")

    def argv(self, root: Path, *, repair: bool) -> tuple[str, ...]:
        uv = shutil.which("uv")
        if uv is None:
            raise ValueError("uv is required for prepared application installation")
        selected = json.loads(self.selection)
        return (
            sys.executable,
            "-I",
            "-B",
            str(self.payload / "scripts" / "install_source.py"),
            "--install-source",
            str(self.snapshot),
            "--component",
            self.component,
            "--payload",
            str(self.payload),
            "--install-application",
            "--root",
            str(root),
            "--uv",
            os.path.abspath(uv),
            "--base-python",
            sys.executable,
            "--health-module",
            json.loads(self.declaration)["module"].partition(":")[0],
            "--expected-runtime-version",
            self.version,
            "--expected-payload-hash",
            selected["payload_hash"],
            *(("--repair",) if repair else ()),
        )

    def deployed_version(self, root: Path, owner: Path) -> str | None:
        try:
            selected = _selector(root, "current-version")
            if (
                re.fullmatch(re.escape(self.version) + r"(?:\.repair\.[0-9a-f]{32})?", selected)
                is None
            ):
                return None
            slot = root / "versions" / selected
            if slot.is_symlink():
                raise ValueError("Application slot must not be linked")
            interpreter = _member(
                slot,
                ".venv/" + ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
            )
            if not interpreter.is_file():
                return None
            for name in ("current-version", "last-known-good"):
                if _selector(root, name) != selected:
                    return None
            if (
                _read(slot / "install-success.json") != {"state": "installed", "version": selected}
                or json.dumps(_read(slot / "application.json"), sort_keys=True) != self.declaration
                or json.dumps(_read(slot / "runtime-source.json"), sort_keys=True)
                != json.dumps(json.loads(self.identity)["runtime_source"], sort_keys=True)
            ):
                return None
        except (FileNotFoundError, UnicodeError, ValueError):
            return None
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(owner / "scripts" / "versioned_runtime.py"),
                    "--root",
                    str(root),
                    "--json",
                    "is-complete",
                    selected,
                    "--expect-hash",
                    json.loads(self.selection)["payload_hash"],
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                **no_window_kwargs(),
            )
        except subprocess.SubprocessError as error:
            raise ValueError(f"Application completion probe failed: {error}") from error
        if result.returncode not in (0, 1):
            detail = result.stderr.strip() or result.returncode
            raise ValueError(f"Application completion probe failed: {detail}")
        complete = json.loads(result.stdout, object_pairs_hook=strict_json_object)
        if (
            not isinstance(complete, dict)
            or complete.get("version") != selected
            or type(complete.get("complete")) is not bool
            or result.returncode != (0 if complete["complete"] else 1)
        ):
            raise ValueError("Application completion probe returned an invalid result")
        return self.version if complete["complete"] else None
