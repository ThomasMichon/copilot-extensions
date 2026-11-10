"""Bounded Configuration consumer of acquisition-only repository bootstrap."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hmac
import io
import json
from pathlib import Path
import re
import subprocess
import sys

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned

from . import inrepo_config_source, repos, repository_bootstrap as bootstrap, repository_setup as protocol
from .repos_configuration import target
from .repos_configuration_sources import ConfigurationError, identity
from .repos_knowledge_registration import _token, context_selection
from .repos_registry_transaction import registry_lock


def action_target(project: str) -> dict[str, str]:
    return target(project, "knowledge-acquisition")


def _selection(project: str, url: str, path: str) -> dict:
    from .repos_knowledge_configuration import binding_names_supported

    if not Path(path).is_absolute():
        raise ConfigurationError("invalid-destination", "Select an explicit absolute new checkout path.")
    context, _ = context_selection(project)
    canonical = protocol.https_url(url)
    name, checkout, account = bootstrap.acquisition_selection(canonical, path)
    account = identity(account, allow_empty=True)
    if not binding_names_supported(project, name):
        raise ConfigurationError("invalid-registration-name", "The remote name is unsupported by local knowledge registration.")
    info = checkout.parent.stat()
    return {
        **context, "target": action_target(project), "name": name, "url": canonical,
        "path": str(checkout), "account": account,
        "parent_identity": [info.st_dev, info.st_ino],
    }


def _proposal(selected: dict) -> dict:
    return {
        "version": 1, "token": _token(selected), "target": selected["target"],
        "scope": "machine-local", "operation": "set",
        "summary": "Acquire a remote knowledge checkout only",
        "effects": [
            f"Download {selected['url']} to the new destination {selected['path']}.",
            f"Configured noninteractive account: {selected['account'] or '(native credential inheritance)'}.",
            "No registration, binding, credential pin, sign-in, repository setup, pair, worktree or launch.",
            "A failed or timed-out acquisition may leave a partial target; it will not be deleted or replayed.",
            "Refresh, then separately register the local checkout with branch/account metadata and confirm binding.",
            "This bounded Configuration action may refuse large or slow acquisitions. It does not make New ready.",
        ],
        "findings": [{
            "code": "acquisition-only", "severity": "info",
            "message": "Provider access is unobserved. Only the configured noninteractive credential route is used.",
        }],
    }


def _present(path: str) -> bool | None:
    try:
        Path(path).lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return True


def _incomplete(project: str, path: str, stage: str, code: str, *, cleanup="complete") -> dict:
    return {
        "status": "incomplete", "operation": "set", "target": action_target(project),
        "code": code, "stage": stage, "checkout_path": path, "target_exists": _present(path),
        "cleanup": cleanup, "registration_changed": False, "binding_changed": False,
        "message": "Acquisition is incomplete; any target was preserved. Inspect local ownership read-only "
        "with repos registration inspect --anchor <destination> --default-name <intended-name>. "
        "This does not qualify source completeness. Do not retry into an existing target.",
    }


def _worker(request: dict) -> dict:
    project, url, path = request["project"], request["url"], request["path"]
    budget = OwnedExecutionBudget(request["deadline"], request["cleanup_reserve"])
    with inrepo_config_source.resolution_budget(deadline=budget.execution_deadline):
        if request["operation"] == "preview":
            return _proposal(_selection(project, url, path))
        with registry_lock(repos._repos_yaml_path(), budget=budget):
            selected = _selection(project, url, path)
            if not hmac.compare_digest(request["token"], _token(selected)):
                raise ConfigurationError("preview-conflict", "Acquisition inputs changed; refresh and preview again.")
            stage = "acquisition"
            try:
                def validate():
                    if not hmac.compare_digest(request["token"], _token(_selection(project, url, path))):
                        raise ConfigurationError("preview-conflict", "Acquisition inputs changed before download.")

                source = bootstrap.acquire(
                    selected["name"], Path(selected["path"]), selected["url"], register=False, validate=validate,
                )
                stage = "final-qualification"
                context, _ = context_selection(project)
                if any(context[key] != selected[key] for key in context):
                    raise ConfigurationError("preview-conflict", "Native inputs changed during acquisition.")
                parent = source.path.parent.stat()
                if [parent.st_dev, parent.st_ino] != selected["parent_identity"]:
                    raise ConfigurationError("preview-conflict", "Destination parent changed during acquisition.")
                if bootstrap._account(repos.read_registry(strict=True), selected["name"], source.url) != selected["account"]:
                    raise ConfigurationError("preview-conflict", "Account selection changed during acquisition.")
                if protocol.qualify(source.path, source.url) != source:
                    raise ConfigurationError("preview-conflict", "Source changed during final qualification.")
                return {
                    "status": "acquired", "operation": "set", "target": selected["target"],
                    "checkout_path": str(source.path), "commit": source.commit, "url": source.url,
                    "registration_changed": False, "binding_changed": False,
                    "message": "Acquired only. Refresh, separately register the local checkout with explicit "
                    "branch/account metadata, then confirm binding. New is not ready yet.",
                }
            except OwnedCleanupError:
                raise
            except (ValueError, OSError, RuntimeError, subprocess.SubprocessError, repos.RepositoryResolutionError) as error:
                receipt = _incomplete(project, path, stage, "acquisition-incomplete")
                if isinstance(error, (ConfigurationError, protocol.SetupError)):
                    receipt["reason"] = str(error)
                return receipt


def _invoke(project: str, url: str, path: str, *, token: str | None = None) -> dict:
    if token is not None and not re.fullmatch(r"[a-f0-9]{64}", token):
        raise ConfigurationError("missing-preview", "A fresh acquisition preview is required.")
    canonical = protocol.https_url(url)
    budget = OwnedExecutionBudget.from_timeout(18 if token is None else 26, cleanup_reserve=2)
    request = {
        "project": project, "url": canonical, "path": path, "token": token,
        "operation": "preview" if token is None else "apply",
        "deadline": budget.deadline, "cleanup_reserve": budget.cleanup_reserve,
    }
    try:
        result = run_caller_owned(
            [sys.executable, "-B", "-m", __name__], input_text=json.dumps(request),
            timeout=26, max_stdout_bytes=32768, max_stderr_bytes=2048,
            env=protocol._environment(attended=False), budget=budget,
        )
        response = json.loads(result.stdout)
        if not isinstance(response, dict) or result.returncode:
            raise ValueError("Invalid acquisition receipt.")
        if response.get("status") == "failed":
            raise ConfigurationError(response["code"], response["message"])
        return response
    except ConfigurationError:
        raise
    except OwnedCleanupError:
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt):
        if token is None:
            raise ConfigurationError("acquisition-preview-unavailable", "Acquisition preview did not complete.") from None
        return _incomplete(project, path, "worker", "acquisition-receipt-unavailable", cleanup="unknown")


def preview(project: str, url: str, path: str) -> dict:
    """Preview acquisition without any network, credential or filesystem effect."""
    return _invoke(project, url, path)


def apply(project: str, url: str, path: str, token: str) -> dict:
    """Acquire only after revalidating this installation's exact proposal."""
    return _invoke(project, url, path, token=token)


def action_row(project: str) -> dict:
    return {
        "id": "action:knowledge-acquisition", "title": "Acquire a remote knowledge checkout",
        "context_project": project, "registration": "setup action", "participation": "not bound",
        "effective": "Choose HTTPS source / new path", "provenance": "native owner",
        "editable": "false", "resettable": "false", "knowledge_bindable": "false",
        "preferred_knowledge_bindable": "false", "knowledge_acquisition_available": "true",
        "configuration": {"target": action_target(project)},
        "card": {
            "title": "Acquire, then separately register and bind",
            "body": "Download an existing HTTPS repository to an absent absolute destination. "
            "No sign-in, repository setup, registration or binding is performed.",
        },
        "knowledge_acquisition_fields": [
            {"name": "url", "type": "text", "label": "Credential-free HTTPS Git URL", "required": True},
            {"name": "path", "type": "text", "label": "Absolute new checkout path", "required": True},
        ],
    }


if __name__ == "__main__":
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = _worker(json.loads(sys.stdin.read(16385)))
    except OwnedCleanupError:
        raise
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, subprocess.SubprocessError,
            repos.RepositoryResolutionError) as error:
        result = {
            "status": "failed",
            "code": error.code if isinstance(error, ConfigurationError) else "acquisition-selection-refused",
            "message": str(error) if isinstance(error, (ConfigurationError, protocol.SetupError)) else
            "Acquisition selection is unavailable; inspect the native owner and preview again.",
        }
    print(json.dumps(result))
