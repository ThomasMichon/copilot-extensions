"""Optional source embedding of the existing Worktrees GitHub report owner."""

from __future__ import annotations

import json
from inspect import signature
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned
from fleet_manager.gateway_service_channel import BODY_BYTES

from .report_read import ReportBinding, ReportUnavailable, report_scope, response_budget


def _settle(error):
    logging.getLogger(__name__).warning("native report cleanup delayed; original custody retained")
    while True:
        try:
            error.retry_cleanup(deadline=time.monotonic() + 2)
            break
        except Exception:
            time.sleep(1)
    raise ReportUnavailable("native report cleanup was delayed") from None


class NativeGitHubReport:
    """Trusted hosts supply a config owner and exact scope, never HTTP overrides.

    Every resolve creates an independent finite observation. This is not an
    installed-host loader, workspace exporter or alternate dispatch path.
    """

    def __init__(self, *, config_path: Path, scope, context_id):
        from .owner_read import _label

        self.scope = report_scope(scope)
        if self.scope["provider"] != "github" or self.scope["endpoint"] != "github.com":
            raise ValueError("native report adapter only supports GitHub.com")
        if not isinstance(config_path, Path) or not config_path.is_absolute():
            raise ValueError("native report needs its trusted owner's absolute config path")
        self.config_path, self.context_id = config_path, _label(context_id)

    @staticmethod
    def check_support() -> None:
        """Check selected executing APIs without resolving policy or credentials."""
        try:
            from agent_worktrees.github_account_observation import SelectedGitHubAccount
            from agent_worktrees.github_credentials import CredentialObservationError
            from agent_worktrees.pr_report_target import worker_main
            from agent_worktrees.pr_diff_report import diff_report
            from agent_worktrees.pr_thread_report import thread_report
            from agent_worktrees.providers.github import GitHubProvider

            signature(SelectedGitHubAccount).bind(
                "github.com", "selected", "example/project", budget=None, checkpoint=None,
            )
            signature(SelectedGitHubAccount.capture).bind(
                None, "report", [], binary=False, limit=BODY_BYTES,
            )
            signature(SelectedGitHubAccount.verify).bind(None)
            if not issubclass(CredentialObservationError, Exception):
                raise ValueError("native credential error contract unavailable")
            signature(GitHubProvider).bind(report_capture=None)
            signature(worker_main).bind()
            for report in (diff_report, thread_report):
                signature(report).bind(None, "example/project", 1, api_base="https://api.github.com")
        except (ImportError, AttributeError, TypeError, ValueError):
            raise ValueError("selected native report APIs unsupported") from None

    def resolve(self, remaining, *, response_limit=BODY_BYTES):
        """Compose actual selection, credential, provider and caller-owned seams."""
        response_budget(response_limit)
        try:
            import agent_worktrees
            from agent_worktrees.github_account_observation import SelectedGitHubAccount
            from agent_worktrees.github_credentials import CredentialObservationError
            from agent_worktrees.pr_diff_report import diff_report
            from agent_worktrees.pr_thread_report import thread_report
            from agent_worktrees.providers.github import GitHubProvider
        except ImportError:
            raise ReportUnavailable("native report owner is not available") from None
        started = time.monotonic()
        seconds = remaining()
        budget = OwnedExecutionBudget(started + seconds, min(.25, seconds / 4))
        paths = json.dumps([
            str(Path(agent_worktrees.__file__).resolve().parents[1]),
            *(str(Path(path).absolute()) for path in sys.path),
        ])
        environment = {
            key: value for key, value in os.environ.items()
            if key.upper() not in {
                "PYTHONPATH", "PYTHONHOME", "GH_TOKEN", "GITHUB_TOKEN",
                "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_HOST", "GH_REPO",
                "GH_DEBUG", "GH_FORCE_TTY", "GH_HTTP_UNIX_SOCKET",
            }
        }
        cleanup_delayed = False

        def selection():
            nonlocal cleanup_delayed
            remaining()
            timeout = budget.execution_deadline - time.monotonic()
            if timeout <= 0:
                raise ReportUnavailable("native report deadline exhausted")
            try:
                result = run_caller_owned([
                    sys.executable, "-I", "-B", "-c",
                    "import json,sys;sys.path[:]=json.loads(sys.argv.pop(1));"
                    "from agent_worktrees.pr_report_target import worker_main;worker_main()",
                    paths, str(self.config_path), self.scope["repo"],
                ], timeout=timeout, budget=budget, env=environment,
                    max_stdout_bytes=65536, max_stderr_bytes=8192)
                remaining()
                selected = json.loads(result.stdout)
                if result.returncode or not isinstance(selected, dict) or set(selected) != {
                    "target", "provider", "endpoint", "account", "repo",
                }:
                    raise ValueError
                if not isinstance(selected["account"], str):
                    raise ValueError
                observed = {key: selected[key] for key in self.scope}
                if observed["account"].casefold() == self.scope["account"].casefold():
                    observed["account"] = self.scope["account"]
                if observed != self.scope:
                    raise ValueError
                return selected
            except OwnedCleanupError as error:
                cleanup_delayed = True
                _settle(error)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                raise ReportUnavailable("native report target unavailable or changed") from None

        original = selection()

        def current():
            remaining()
            selected = selection()
            if selected["account"].casefold() == original["account"].casefold():
                selected["account"] = original["account"]
            if selected != original:
                raise ReportUnavailable("native report target changed")
            remaining()

        account = SelectedGitHubAccount(
            "github.com", original["account"], self.scope["repo"], budget=budget, checkpoint=current,
        )
        try:
            account.verify()
        except OwnedCleanupError as error:
            _settle(error)
        except Exception:
            raise ReportUnavailable("native report selected account unavailable") from None

        def collect(operation, number, checkpoint):
            transport_unavailable = False

            def capture(arguments, *, binary=False):
                nonlocal cleanup_delayed, transport_unavailable
                try:
                    checkpoint()
                    return account.capture("report", arguments, binary=binary, limit=response_limit)
                except OwnedCleanupError as error:
                    cleanup_delayed = True
                    _settle(error)
                except CredentialObservationError as error:
                    if error.code != "access-unconfirmed":
                        transport_unavailable = True
                    raise
                except Exception:
                    transport_unavailable = True
                    raise
            provider = GitHubProvider(report_capture=capture)
            checkpoint()
            try:
                report = (thread_report if operation == "threads" else diff_report)(
                    provider, self.scope["repo"], number, api_base="https://api.github.com",
                )
            except Exception:
                raise ReportUnavailable("native report acquisition unavailable") from None
            checkpoint()
            if cleanup_delayed:
                raise ReportUnavailable("native report cleanup was delayed")
            if transport_unavailable:
                raise ReportUnavailable("native report transport unavailable")
            return int(bool(report["error"] or not report["supported"])), json.dumps(report, allow_nan=False)

        current()
        def live():
            remaining()
        return ReportBinding(self.scope, self.context_id, current, collect, final_checkpoint=live,
                             response_limit=response_limit)
