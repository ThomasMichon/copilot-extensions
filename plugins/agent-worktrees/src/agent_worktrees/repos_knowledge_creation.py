"""Separately confirmed personal-private creation and safeguard completion."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hmac
import io
import json
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned

from . import inrepo_config_source, knowledge_creation_receipts as receipts, repos
from . import github_knowledge_repository as provider
from . import repository_setup as protocol
from .github_account_observation import SelectedGitHubAccount
from .github_credentials import CredentialObservationError
from .repo_accounts import AccountLookupIndex
from .repos_configuration import target
from .repos_configuration_sources import ConfigurationError, identity
from .repos_knowledge_registration import _token, context_selection
from .repos_registry_transaction import registry_lock


def action_target(project: str) -> dict[str, str]:
    return target(project, "knowledge-creation")


def _selection(project: str, url: str) -> dict:
    canonical = protocol.https_url(url)
    parsed = urlsplit(canonical)
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if (
        parsed.netloc != "github.com" or len(parts) != 2
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", parts[1]) or parts[1] in {".", ".."}
    ):
        raise ConfigurationError("unsupported-creation-target", "Choose an exact personal github.com HTTPS repository.")
    owner = identity(parts[0])
    slug = owner + "/" + parts[1]
    context, registry = context_selection(project)
    index = AccountLookupIndex.from_registry(registry)
    login = identity(repos.account_for_github_slug(slug, registry=registry, index=index), allow_empty=True)
    if not login:
        raise ConfigurationError("creation-account-required", "Configure the named repository, owner or default account first.")
    if owner.casefold() != login.casefold():
        raise ConfigurationError(
            "unsupported-creation-owner", "Only the verified configured user's own personal namespace is supported.",
        )
    entry = index.slugs.get(slug.casefold())
    provenance = "repository" if entry and entry.account else (
        "owner" if index.account_map.get(owner.casefold()) else "default"
    )
    selected = {
        **context, "target": action_target(project), "url": "https://github.com/" + slug,
        "repository": slug, "account": login, "account_source": provenance,
    }
    selected["receipt"] = receipts.reference(project, selected["url"])
    return selected


def _proposal(selected: dict, operation: str, observed: dict) -> dict:
    return {
        "version": 2, "token": _token([selected, operation, observed]),
        "target": selected["target"], "scope": "remote-repository", "operation": operation,
        "summary": "Create a personal private knowledge repository" if operation == "create" else
        "Complete default-branch safeguards for the confirmed creation",
        "effects": [
            f"Remote repository: {selected['url']}; configured account: {selected['account']} "
            f"({selected['account_source']} policy).",
            "Create one private personal repository with the provider's initial README/commit, "
            "then protect its actual default branch." if operation == "create" else
            f"Inspect confirmed repository ID {observed['repository']['id']} and its current default branch.",
            "If absent, add an active default-branch ruleset for deletion and non-fast-forward protection, "
            "with no bypass actors. No PR requirement or feature-branch restriction is added.",
            "Creation and protection are separate remote mutations, not an atomic transaction. "
            "A denial, lost response or cancellation can leave partial or unknown effects.",
            f"Retain native attempt receipt {selected['receipt']}; never automatically replay or delete.",
            "No clone, registration, binding, pair, launch, sign-in or credential/configuration change. "
            "Acquire, register and bind separately after verified completion; New is not ready yet.",
        ],
        "findings": [{
            "code": "remote-effects", "severity": "info",
            "message": "This optional action needs existing configured credentials and provider protection support. "
            "A metadata 404 is not proof of absence; creation never adopts an existing repository.",
        }],
    }


def _receipt(selected: dict, operation: str) -> dict:
    return {
        "status": "incomplete", "operation": operation, "target": selected["target"],
        "receipt": selected["receipt"], "url": selected["url"], "stage": "not-started",
        "creation": "not-attempted", "repository": None, "safeguards": {"status": "not-observed"},
        "protection_attempts": 0,
        "registration_changed": False, "binding_changed": False, "cleanup": "complete",
        "message": "Inspect the native creation receipt read-only before any separately confirmed continuation.",
    }


def _prior(project: str, selected: dict, reference: str) -> dict:
    if reference != selected["receipt"]:
        raise ConfigurationError("creation-receipt-mismatch", "The receipt must belong to this exact selected target.")
    previous = receipts.read(project, reference)
    if (
        previous["target"]["resource"] != selected["target"]["resource"]
        or previous["url"].casefold() != selected["url"].casefold()
    ):
        raise ConfigurationError("creation-receipt-mismatch", "Creation receipt target changed.")
    return previous


def _failure(error, stage: str) -> dict:
    result = {
        "stage": error.stage if isinstance(error, CredentialObservationError) else stage,
        "code": error.code if isinstance(error, (CredentialObservationError, ConfigurationError)) else
        "owner-operation-incomplete",
    }
    if isinstance(error, CredentialObservationError):
        if error.exit_code is not None:
            result["exit_code"] = error.exit_code
        if error.http_status is not None:
            result["http_status"] = error.http_status
    return result


def _inspect(selected, prior, budget, checkpoint, revalidate) -> dict:
    result = {
        **prior, "prior_status": prior["status"], "observation_target": selected["target"],
        "observation_status": "unavailable", "observed_repository": None,
        "observed_safeguards": {"status": "not-observed"},
        "message": "Read-only observation. Unknown prior creation is not promoted into mutation authority.",
    }
    try:
        account = SelectedGitHubAccount(
            "github.com", selected["account"], selected["repository"], budget=budget, checkpoint=checkpoint,
        )
        account.verify()
        result["observed_repository"] = provider.repository(account)
        result["observed_safeguards"] = provider.safeguards(account, result["observed_repository"])
        revalidate()
        result["observation_status"] = "observed"
        if result["observed_safeguards"]["status"] != "verified":
            result["status"] = "incomplete"
    except (CredentialObservationError, ConfigurationError) as error:
        result["status"] = "incomplete"
        result["observation_failure"] = _failure(error, "observation")
    return result


def _worker(request: dict) -> dict:
    project, url, operation = request["project"], request["url"], request["operation"]
    budget = OwnedExecutionBudget(request["deadline"], request["cleanup_reserve"])
    with inrepo_config_source.resolution_budget(deadline=budget.execution_deadline):
        selected = _selection(project, url)

        def checkpoint():
            if time.monotonic() >= budget.execution_deadline:
                raise CredentialObservationError("operation", "timeout")

        def revalidate():
            checkpoint()
            if _selection(project, url) != selected:
                raise ConfigurationError("preview-conflict", "Native selection changed; refresh and preview again.")
            checkpoint()

        prior = _prior(project, selected, request["receipt"]) if operation != "create" else None
        if operation == "inspect":
            return _inspect(selected, prior, budget, checkpoint, revalidate)
        account = SelectedGitHubAccount(
            "github.com", selected["account"], selected["repository"], budget=budget, checkpoint=checkpoint,
        )
        account.verify()
        if operation != "create":
            if prior.get("creation") != "confirmed" or not isinstance(prior.get("repository"), dict):
                raise ConfigurationError("creation-unconfirmed", "Unknown creation requires read-only owner reconciliation first.")
            observed = {
                "repository": provider.repository(account),
                "protection_attempts": prior.get("protection_attempts", 0),
            }
            if observed["repository"] != prior["repository"]:
                raise ConfigurationError("creation-target-changed", "Confirmed repository identity or default branch changed.")
            observed["safeguards"] = provider.safeguards(account, observed["repository"])
        else:
            if receipts.path(project, selected["receipt"]).exists():
                raise ConfigurationError("creation-already-attempted", "Inspect the existing native receipt; do not replay creation.")
            observed = {"availability": provider.create_candidate(account)}
        if request["mode"] == "preview":
            revalidate()
            return _proposal(selected, operation, observed)
        with registry_lock(repos._repos_yaml_path(), budget=budget):
            revalidate()
            if prior is not None and _prior(project, selected, request["receipt"]) != prior:
                raise ConfigurationError("preview-conflict", "Creation receipt changed; inspect and preview again.")
            if not hmac.compare_digest(request["token"], _token([selected, operation, observed])):
                raise ConfigurationError("preview-conflict", "Creation inputs or policy changed; preview again.")
            receipt = _receipt(selected, operation) if prior is None else {
                **prior, "operation": operation, "status": "incomplete", "target": selected["target"],
            }
            receipt["message"] = "Operation remains incomplete until current private identity and safeguards are verified."
            receipt["stage"] = "creation" if operation == "create" else "policy-observation"
            attempted = False
            try:
                if operation == "create":
                    receipt["creation"] = "unknown"
                    receipts.reserve(project, receipt)
                    revalidate()
                    attempted = True
                    receipt["repository"] = provider.create(account)
                    receipt["creation"] = "confirmed"
                    receipts.save(project, receipt)
                    receipt["stage"] = "policy-observation"
                receipt["safeguards"] = provider.safeguards(account, receipt["repository"])
                if receipt["safeguards"]["status"] != "verified":
                    revalidate()
                    if provider.repository(account) != receipt["repository"]:
                        raise CredentialObservationError("protection", "repository-changed")
                    receipt.update(stage="protection", protection_attempt=request["token"])
                    if prior is not None and prior.get("protection_attempt") == request["token"]:
                        raise ConfigurationError("protection-already-attempted", "Inspect prior effects and obtain a new current preview.")
                    receipt["safeguards"] = {"status": "unknown"}
                    receipt["protection_attempts"] = receipt.get("protection_attempts", 0) + 1
                    receipts.save(project, receipt)
                    attempted = True
                    provider.protect(account, receipt["repository"])
                receipt["stage"] = "verification"
                receipt["safeguards"] = provider.safeguards(account, receipt["repository"])
                revalidate()
                if receipt["safeguards"]["status"] == "verified":
                    receipt.update(status="created" if operation == "create" else "protected", stage="complete")
                    receipt.pop("failure", None)
                    receipt["message"] = "Private identity and safeguards verified only. Acquire, register and bind separately."
                receipts.save(project, receipt)
                return receipt
            except OwnedCleanupError:
                raise
            except (CredentialObservationError, ConfigurationError, OSError, RuntimeError,
                    subprocess.SubprocessError, KeyboardInterrupt) as error:
                if not attempted:
                    raise
                receipt["status"] = "incomplete"
                receipt["failure"] = _failure(error, receipt["stage"])
                try:
                    receipts.save(project, receipt)
                except OSError:
                    receipt["receipt_persistence"] = "unconfirmed"
                return receipt


def invoke(project: str, url: str, operation: str, *, token=None, receipt="") -> dict:
    """Bound the complete provider operation and preserve uncertain child results."""
    if operation not in {"create", "protect-default", "inspect"}:
        raise ConfigurationError("invalid-request", "Unsupported knowledge creation operation.")
    if token is not None and not re.fullmatch(r"[a-f0-9]{64}", token):
        raise ConfigurationError("missing-preview", "A fresh remote-operation preview is required.")
    canonical = protocol.https_url(url)
    budget = OwnedExecutionBudget.from_timeout(18 if token is None else 26, cleanup_reserve=2)
    request = {
        "project": project, "url": canonical, "operation": operation,
        "mode": "preview" if token is None else "apply", "token": token, "receipt": receipt,
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
            raise ValueError
        if response.get("status") == "failed":
            raise ConfigurationError(response["code"], response["message"])
        return response
    except (ConfigurationError, OwnedCleanupError):
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt):
        if token is None:
            raise ConfigurationError("creation-observation-unavailable", "Remote observation did not complete.") from None
        return {
            "status": "incomplete", "operation": operation, "target": action_target(project),
            "stage": "worker", "creation": "unknown", "safeguards": {"status": "unknown"},
            "receipt": receipts.reference(project, canonical.removesuffix(".git")),
            "cleanup": "unknown", "registration_changed": False, "binding_changed": False,
            "message": "Completion receipt unavailable. Inspect the native receipt and provider read-only; "
            "do not replay creation or protection.",
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
            "code": error.code if isinstance(error, (ConfigurationError, CredentialObservationError)) else
            "creation-selection-refused",
            "message": str(error) if isinstance(error, ConfigurationError) else
            "Remote creation observation refused; inspect the configured account and native provider.",
        }
    print(json.dumps(result))
