"""Native repository preference preview/apply API, independent of the Manager."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import hashlib
import hmac
import io
import json
from pathlib import Path
import re
import socket
import sys

import yaml

from . import registry_paths, repos
from .repos_configuration_sources import (
    ConfigurationError, PreferenceDefaults, defaults, fingerprint, identity, mapping, resource_name,
)
from .repos_registry_reader import RepositoryResolutionError
from .repos_registry_transaction import registry_lock, write_checked

FUTURE_ONLY = (
    "Preference for future launches only. No login, credential access, global account "
    "switch, session launch or existing-worker change."
)


def target(project: str, name: str) -> dict[str, str]:
    context = registry_paths.installation_context()
    installation = json.dumps({
        "context": context, "registry": str(registry_paths.registry_root().resolve()),
        "runtime": str(Path(__file__).resolve().parent),
    }, sort_keys=True)
    return {
        "installation": "agent-worktrees:" + hashlib.sha256(installation.encode()).hexdigest(),
        "machine": socket.gethostname().lower(),
        "resource": f"{project}/{name}",
    }


def preference(project: str, name: str) -> dict:
    project, name = resource_name(project), resource_name(name)
    registry = repos.read_registry(strict=True)
    fingerprint([repos._repos_yaml_path()])
    return compose_preference(project, name, registry, defaults(project))


def compose_preference(
    project: str, name: str, registry: repos.ReposRegistry, inherited: PreferenceDefaults,
) -> dict:
    if registry._baseline is None:
        raise ConfigurationError("missing-revision", "Native registration must be read before composition.")
    if project not in registry.repos:
        raise ConfigurationError("context-not-registered", "The selected project is not registered.")
    entry = registry.repos.get(name)
    if entry is None:
        raise ConfigurationError("not-registered", "The repository is not registered.")
    explicit = identity(entry.copilot_account, allow_empty=True)
    sources = [dict(source) for source in inherited.sources]
    if explicit:
        sources.append({
            "id": "repository-local-override", "kind": "machine-local",
            "setting": "copilot_account", "value": explicit,
            "precedence": len(sources), "revision": registry._baseline[1],
        })
    for index, source in enumerate(sources):
        source["wins"] = index == len(sources) - 1
        source["masked"] = not source["wins"]
        source["redacted"] = False
    baseline = hashlib.sha256(json.dumps({
        "target": target(project, name), "registry": registry._baseline,
        "defaults": inherited.revision,
    }, sort_keys=True).encode()).hexdigest()
    return {
        "target": target(project, name), "revision": baseline,
        "effective": explicit or inherited.login or "(ambient)",
        "override": explicit, "inherited": inherited.login or "(ambient)",
        "switch_enabled": inherited.switch_enabled, "sources": sources,
        "scope": "machine-local", "observed": "unknown",
        "editable_keys": ["copilot_account"], "supported_operations": ["set", "reset"],
        "local_destination": "native machine-local repository registry",
        "constraints": [FUTURE_ONLY],
    }


def preview(project: str, name: str, operation: str, login: str = "") -> dict:
    if operation not in ("set", "reset"):
        raise ConfigurationError("unsupported-operation", "Only preference set/reset is supported.")
    normalized = identity(login) if operation == "set" else ""
    if operation == "reset" and login:
        raise ConfigurationError("invalid-reset", "Reset cannot supply a replacement login.")
    state = preference(project, name)
    token = hashlib.sha256(json.dumps({
        "protocol": 1, "target": state["target"], "revision": state["revision"],
        "operation": operation, "login": normalized,
    }, sort_keys=True).encode()).hexdigest()
    effective = normalized if operation == "set" else state["inherited"]
    return {
        "version": 1, "token": token, "target": state["target"], "scope": "machine-local",
        "operation": operation, "summary": f"{operation.title()} the local Copilot preference",
        "effects": [
            f"Effective preference: {state['effective']} -> {effective}",
            "Write only copilot_account in the native local repository registration."
            if operation == "set" else
            "Remove only the local copilot_account override; resume current native inheritance.",
            FUTURE_ONLY, "Repository, knowledge, plugin and preset sources remain read-only.",
        ],
        "findings": [{
            "code": "future-launches-only", "severity": "info", "message": FUTURE_ONLY,
        }],
    }


def apply(project: str, name: str, operation: str, login: str, token: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{64}", token):
        raise ConfigurationError("missing-preview", "A valid fresh provider preview is required.")
    if not hmac.compare_digest(token, preview(project, name, operation, login)["token"]):
        raise ConfigurationError("preview-conflict", "Configuration changed; refresh and preview again.")
    with registry_lock(repos._repos_yaml_path()):
        proposed = preview(project, name, operation, login)
        if not hmac.compare_digest(token, proposed["token"]):
            raise ConfigurationError("preview-conflict", "Configuration changed; refresh and preview again.")
        registry = repos.read_registry(strict=True)
        state = preference(project, name)
        raw = mapping(repos._repos_yaml_path())
        entry = registry.repos.get(name)
        if entry is None or not isinstance(raw.get("repos", {}).get(name), dict):
            raise ConfigurationError("not-registered", "The repository is not registered.")
        raw["repos"] = dict(raw["repos"])
        raw["repos"][name] = dict(raw["repos"][name])
        normalized = identity(login) if operation == "set" else ""
        if operation == "set":
            raw["repos"][name]["copilot_account"] = normalized
        else:
            raw["repos"][name].pop("copilot_account", None)
        # Recheck composition immediately before the only allowed write.
        if preview(project, name, operation, login)["token"] != token:
            raise ConfigurationError("preview-conflict", "Configuration changed; refresh and preview again.")
        registry.repos[name] = replace(entry, copilot_account=normalized)
        write_checked(
            repos._repos_yaml_path(), registry,
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        )
        return {
            "status": "persisted", "operation": operation, "target": proposed["target"],
            "effective": normalized or state["inherited"],
            "effects": [FUTURE_ONLY],
        }


def command(argv: list[str]) -> int:
    class SafeParser(argparse.ArgumentParser):
        def error(self, message):
            raise ConfigurationError(
                "invalid-request", "Invalid Configuration request; use repos configuration --help."
            )

    parser = SafeParser(prog="repos configuration")
    parser.add_argument("command", choices=("catalog", "preview", "apply"))
    parser.add_argument("--context-project", required=True)
    parser.add_argument("--repository")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--operation", choices=("set", "reset"), default="set")
    parser.add_argument("--login", default="")
    parser.add_argument("--preview-token", default="")
    try:
        args = parser.parse_args(argv)
        # Native loaders may warn about source content. Return sanitized findings,
        # never those raw diagnostics through provider rows, stderr or receipts.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            project = resource_name(args.context_project)
            if args.command == "catalog":
                from .repos_configuration_catalog import catalog

                result = catalog(project, workspace=args.workspace)
            else:
                name = resource_name(args.repository)
                result = (
                    preview(project, name, args.operation, args.login)
                    if args.command == "preview"
                    else apply(project, name, args.operation, args.login, args.preview_token)
                )
    except (ValueError, TypeError, RepositoryResolutionError, OSError, TimeoutError, yaml.YAMLError) as exc:
        code = exc.code if isinstance(exc, ConfigurationError) else "owner-read-or-write-failed"
        message = str(exc) if isinstance(exc, ConfigurationError) else (
            "Native configuration is unavailable or changed; inspect its owner and preview again."
        )
        print(json.dumps({"status": "failed", "code": code, "message": message}))
        print(f"Configuration failed ({code}): {message}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0
