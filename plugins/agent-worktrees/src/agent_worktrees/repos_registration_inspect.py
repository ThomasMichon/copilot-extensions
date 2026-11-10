"""Read-only project identity selection from the native registration owners."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from yaml import YAMLError

from . import config as cfg, installer, projects_registry, repos
from .repos_registry_transaction import read_bytes


CONTRACT = "copilot-extensions.project-registration-inspection"
CONTRACT_VERSION = 1


class RegistrationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _name(value: object) -> str:
    if not isinstance(value, str) or not cfg._PROJECT_NAME_RE.fullmatch(value):
        raise RegistrationError("invalid-project-name", "A registration has an invalid project name.")
    if installer.is_reserved_project_command(value):
        raise RegistrationError("invalid-project-name", "The runtime command is not a project name.")
    return value


def _root(value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise RegistrationError("invalid-project-root", "A registration has no usable absolute root.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise RegistrationError("invalid-project-root", "A registration root is not absolute.")
    return path.resolve()


def _same_root(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except FileNotFoundError:
        return str(left) == str(right)


def _registry_path(path: Path) -> None:
    for candidate in (path, *path.parents):
        try:
            candidate.lstat()
        except FileNotFoundError:
            continue
        resolved = candidate.resolve(strict=True)
        if not (resolved.is_file() if candidate == path else resolved.is_dir()):
            raise RegistrationError("registration-unavailable", "A registration source is not readable.")
        return
    raise RegistrationError("registration-unavailable", "A registration source cannot be resolved.")


def _projects() -> dict:
    content = read_bytes(installer.projects_yaml_path())
    if content is None:
        return {}
    # Migration intentionally removes v1 anchors; inspect the original through
    # the same strict parser before composing canonical repository identity.
    document = projects_registry.load_projects_document(content.decode("utf-8"))
    if document is None:
        return {}
    if not isinstance(document, dict):
        raise ValueError("projects document is not a mapping")
    version = document.get("schema_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError("unsupported projects document")
    projects = document.get("projects", {})
    if projects is None:
        projects = {}
    if not isinstance(projects, dict):
        raise ValueError("projects is not a mapping")
    return projects


def inspect(anchor: str, default_name: str) -> dict:
    target = _root(anchor)
    if not target.is_dir():
        raise RegistrationError("invalid-anchor", "The selected anchor is not an available directory.")
    default_name = _name(default_name)
    _registry_path(repos._repos_yaml_path())
    _registry_path(installer.projects_yaml_path())
    registry = repos.read_registry(strict=True)
    if registry.schema_version not in (None, 1):
        raise ValueError("unsupported repository document")
    projects = _projects()
    names = sorted(set(registry.repos) | set(projects), key=str)
    commands: dict[str, str] = {}
    rows = {}
    for raw_name in names:
        name = _name(raw_name)
        command = name.casefold() if os.name == "nt" else name
        if command in commands and commands[command] != name:
            raise RegistrationError("name-collision", "Registered project command names collide.")
        commands[command] = name
        repository = registry.repos.get(name)
        adoption = projects.get(name, {})
        if not isinstance(adoption, dict):
            raise RegistrationError("registration-unavailable", "A project registration is not a mapping.")
        if "expose_agent" in adoption and type(adoption["expose_agent"]) is not bool:
            raise RegistrationError("invalid-project-policy", "A project exposure policy is not boolean.")
        if "base_repo" in adoption and type(adoption["base_repo"]) is not bool:
            raise RegistrationError("invalid-project-policy", "A project editing mode is not boolean.")
        canonical = repository.local_path() if repository is not None else None
        canonical_root = _root(canonical) if canonical else None
        legacy_root = _root(adoption["anchor"]) if "anchor" in adoption else None
        if canonical_root is not None and legacy_root is not None and not _same_root(canonical_root, legacy_root):
            raise RegistrationError("inconsistent-root", "Repository and project roots disagree.")
        root = canonical_root or legacy_root
        if name in projects and root is None:
            raise RegistrationError("unresolved-project-root", "An adopted project has no known native root.")
        rows[name] = {
            "root": root,
            "agent": repository.agent if repository is not None else adoption.get("expose_agent", True),
            "exposure_source": "repository" if repository is not None else "legacy-project",
            "repository_class": repository.repo_class if repository is not None else (
                "singleton" if adoption.get("base_repo") is True else "worktree"
            ),
        }
    matches = [
        name for name, row in rows.items()
        if row["root"] is not None and _same_root(row["root"], target)
    ]
    if len(matches) > 1:
        raise RegistrationError("ambiguous-anchor", "More than one registered project owns the selected anchor.")
    if matches:
        project = matches[0]
        selected = rows[project]
        return {
            "status": "ready", "project": project, "anchor": str(target), "registered": True,
            "agent": selected["agent"], "exposure_source": selected["exposure_source"],
            "repository_class": selected["repository_class"],
        }
    default_key = default_name.casefold() if os.name == "nt" else default_name
    if default_key in commands:
        raise RegistrationError("name-collision", "The proposed default project name is already registered.")
    return {
        "status": "ready", "project": default_name, "anchor": str(target), "registered": False,
        "agent": True, "exposure_source": "fresh-default", "repository_class": "worktree",
    }


def command(argv: list[str]) -> int:
    if argv and argv[0] == "preflight":
        from .repos_registration_preflight import command as preflight

        return preflight(argv[1:])

    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise RegistrationError("invalid-request", f"Invalid inspection request: {message}")

    parser = Parser(prog="repos registration", description=__doc__, allow_abbrev=False)
    parser.add_argument("action", choices=("inspect",))
    parser.add_argument("--anchor", required=True)
    parser.add_argument("--default-name", required=True)
    parser.add_argument("--contract-version", type=int, default=CONTRACT_VERSION)
    declaration = {
        "contract": CONTRACT, "contract_version": CONTRACT_VERSION,
        "supported_contract_versions": [CONTRACT_VERSION],
    }
    try:
        args = parser.parse_args(argv)
        if args.contract_version != CONTRACT_VERSION:
            raise RegistrationError("unsupported-contract", "Only inspection contract version 1 is supported.")
        result = inspect(args.anchor, args.default_name)
    except RegistrationError as exc:
        result = {"status": "failed", "code": exc.code, "message": str(exc)}
    except (OSError, UnicodeError, ValueError, RuntimeError, YAMLError) as exc:
        result = {
            "status": "failed", "code": "registration-unavailable",
            "message": f"Native registration sources are unreadable or malformed ({type(exc).__name__}).",
        }
    print(json.dumps({**declaration, **result}))
    if result["status"] != "ready":
        print(result["message"], file=sys.stderr)
        return 2
    return 0
