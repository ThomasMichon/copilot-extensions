"""Prepare the actual associated applications through the normal Source builders."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable, Iterator, Mapping, Sequence
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any, Protocol, TypeVar, TypedDict, cast

import pytest

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from test_client_distribution import PROJECT
from test_gateway_host_distribution import Progress, _installed_wheel, run


class RequiredDistribution(Protocol):
    @property
    def requires(self) -> list[str] | None: ...


DistributionT = TypeVar("DistributionT", bound=RequiredDistribution)


def original_engine_owner(home: Path) -> Path:
    from prepare_runtime_source import materialize_consumer

    source = PROJECT.parent / "plugins" / "agent-worktrees"
    owner = home / ".copilot" / "installed-plugins" / "copilot-extensions" / "agent-worktrees"
    shutil.copytree(
        source, owner,
        ignore=shutil.ignore_patterns("__pycache__", "build", "dist", "*.egg-info", "tests"),
    )
    materialize_consumer(PROJECT.parent, source, owner, deadline=time.monotonic() + 60)
    (home / ".copilot" / "settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "copilot-extensions": {
                "source": {"source": "github", "repo": "ThomasMichon/copilot-extensions"},
            },
        },
    }), encoding="utf-8")
    return owner.resolve(strict=True)


def source_application_command(
    snapshot: Path, component: str, root: Path, uv: Path,
) -> tuple[list[str], str]:
    import local_install_profile  # noqa: F401 - establishes the normal source-owner import roots
    import install_source

    selected = install_source.select_project(snapshot, component)
    application = install_source._member(snapshot, selected.project["application"])
    declaration = install_source._read(application / "application.json")
    construction = declaration["construction"]
    if component == "worktree-manager":
        return [
            sys.executable, "-I", "-B", str(selected.payload / "scripts" / "runtime_install.py"),
            "--root", str(root), "--install-source", str(snapshot), "--", "--help",
        ], json.dumps(construction["identity"], sort_keys=True)
    arguments = [
        sys.executable, "-I", "-B", str(selected.payload / "scripts" / "install_source.py"),
        "--install-source", str(snapshot), "--component", component,
        "--payload", str(selected.payload), "--install-application", "--root", str(root),
        "--uv", str(uv), "--base-python", sys.executable,
        "--health-module", str(declaration["module"]).partition(":")[0],
        "--expected-runtime-version", str(construction["runtime_version"]),
        "--expected-payload-hash", str(construction["payload_hash"]),
    ]
    return arguments, json.dumps(construction["identity"], sort_keys=True)


class ProfileBuilder(Protocol):
    def prepare_registry_lock(
        self, snapshot: Path, output: Path, *, uv: str, deadline: float,
        prepare_profile: bool, component: str, extras: tuple[str, ...],
    ) -> Mapping[str, Any]: ...


class FeedState(TypedDict):
    origin: str
    routes: dict[str, tuple[int, dict[str, str], bytes]]


class FeedFactory(Protocol):
    def __call__(self, *, certificate: Path) -> FeedState: ...


def _registry_distributions(
    projects: Mapping[str, Any],
    distributions: Mapping[tuple[str, str], DistributionT],
    requirements: Sequence[str],
) -> dict[tuple[str, str], DistributionT]:
    candidates: dict[str, list[DistributionT]] = {}
    for (name, _version), distribution in distributions.items():
        candidates.setdefault(name, []).append(distribution)
    pending = [(Requirement(text), {""}) for text in requirements]
    selected: dict[str, set[str]] = {}
    environment = cast(Mapping[str, str], default_environment())
    while pending:
        requirement, parent_extras = pending.pop()
        if requirement.marker and not any(
            requirement.marker.evaluate({**environment, "extra": extra})
            for extra in parent_extras
        ):
            continue
        name = canonicalize_name(requirement.name)
        extras = {"", *(canonicalize_name(extra) for extra in requirement.extras)}
        if name in selected and extras <= selected[name]:
            continue
        selected.setdefault(name, set()).update(extras)
        if name in projects:
            project = projects[name]["project"]
            children = list(project.get("dependencies", []))
            for extra in selected[name] - {""}:
                children.extend(project.get("optional-dependencies", {}).get(extra, []))
        else:
            assert name in candidates, f"prepared dependency closure is missing {name}"
            children = [
                text for distribution in candidates[name]
                for text in distribution.requires or []
            ]
        pending.extend((Requirement(text), selected[name]) for text in children)
    return {
        key: distribution for key, distribution in distributions.items()
        if key[0] in selected
    }


def _prepare_profiles(
    snapshot: Path, output: Path, profiles: ProfileBuilder, uv: str,
    progress: Progress, started: float,
) -> dict[str, Path]:
    deadline = time.monotonic() + 120

    def prepare(selection: tuple[str, str, tuple[str, ...]]) -> tuple[str, Path, float]:
        key, component, extras = selection
        result = profiles.prepare_registry_lock(
            snapshot, output / ("profile-" + key), uv=uv,
            deadline=deadline, prepare_profile=True,
            component=component, extras=extras,
        )
        return key, Path(result["snapshot"]), time.monotonic() - started

    selections: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        ("worktree-manager", "worktree-manager", ()),
        ("engine-base", "agent-worktrees", ()),
        ("agent-worktrees", "agent-worktrees", ("fleet-ssh",)),
        ("fleet-manager", "fleet-manager", ("gateway-host", "ssh-client")),
    )
    outputs: dict[str, Path] = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(prepare, selection) for selection in selections]
        try:
            for future in as_completed(futures):
                key, path, elapsed = future.result()
                outputs[key] = path
                progress(f"normal {key} profile prepared: {elapsed:.2f}s")
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    return outputs


def _selected_source_commit(root: Path, selected: str | None) -> str:
    if selected is None:
        result = run(["git", "-C", str(root), "rev-parse", "HEAD"])
        assert result.returncode == 0, "Fixture source HEAD is unavailable"
        selected = result.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", selected) is None:
        raise ValueError("Select a full immutable lowercase application Source commit")
    result = run(["git", "-C", str(root), "cat-file", "-e", selected + "^{commit}"])
    assert result.returncode == 0, "Selected application Source commit is unavailable"
    return selected


def prepare_cohort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, feeds: FeedFactory, progress: Progress,
    *, source_commit: str | None = None,
) -> dict[str, Path]:
    progress("normal Source cohort: inspect prepared dependency inputs")
    root = PROJECT.parent
    monkeypatch.syspath_prepend(str(root / "tools"))
    from test_installer_wheel_acquisition import CERTIFICATE
    import local_install_profile as profiles
    import local_install_source as sources
    import installer_engine as engine
    import installer_dependency_policy as dependencies
    import install_source as source_inputs
    from build_toolchain_lock import _LOCKED_TOOLCHAIN_PACKAGES

    progress("normal Source cohort: source owners imported")
    uv = shutil.which("uv")
    assert uv
    prepared = Path(os.environ["COPILOT_EXTENSIONS_TEST_VENV_ROOT"])
    sites: set[str] = set()
    for component in ("agent-worktrees", "worktree-manager", "fleet-manager"):
        python = prepared / component / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        assert python.is_file(), f"prepare the declared {component} test environment first"
        result = run([
            str(python), "-I", "-c",
            "import json,sysconfig;print(json.dumps(sorted({"
            "sysconfig.get_path('purelib'),sysconfig.get_path('platlib')})))",
        ], timeout=15)
        assert result.returncode == 0, result.stderr
        sites.update(json.loads(result.stdout))
        progress("normal Source cohort: inspected " + component)

    started = time.monotonic()
    snapshot = tmp_path / "source"
    commit = _selected_source_commit(root, source_commit)
    projects = (
        "standalone:worktree-manager", "agent-worktrees",
        "standalone:fleet-manager", "agent-ssh",
    )
    progress("normal Source cohort: compose selected Git source " + commit)
    record = sources.compose(root, commit, None, snapshot, projects=projects)
    progress(f"normal Source cohort materialized: {time.monotonic() - started:.2f}s")
    first_party: dict[str, Any] = {}
    for relative in (
        "worktree-manager", "plugins/agent-worktrees", "fleet-manager", "plugins/agent-ssh",
    ):
        for package in engine._package_roots(snapshot / "source" / relative):
            metadata = dependencies._toml(package / "pyproject.toml")
            first_party[canonicalize_name(metadata["project"]["name"])] = metadata
    archives = tmp_path / "registry"
    archives.mkdir()
    distributions: dict[tuple[str, str], importlib.metadata.Distribution] = {}
    for distribution in importlib.metadata.distributions(path=sorted(sites)):
        name: str = canonicalize_name(distribution.metadata["Name"])
        if name not in first_party:
            distributions.setdefault((name, distribution.version), distribution)
    distributions = _registry_distributions(first_party, distributions, [
        *first_party, *_LOCKED_TOOLCHAIN_PACKAGES,
        "agent-worktrees[fleet-ssh]", "fleet-manager[construction,gateway-host,ssh-client]",
        *(
            requirement for metadata in first_party.values()
            for requirement in metadata.get("build-system", {}).get("requires", [])
        ),
    ])
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    files: dict[str, list[dict[str, object]]] = {}
    for (name, _version), distribution in sorted(distributions.items()):
        archive = _installed_wheel(distribution, archives)
        content = archive.read_bytes()
        files.setdefault(name, []).append({
            "filename": archive.name, "url": "/files/" + archive.name,
            "hashes": {"sha256": hashlib.sha256(content).hexdigest()},
            "requires-python": (
                distribution.metadata["Requires-Python"]
                if "Requires-Python" in distribution.metadata else ""
            ),
            "upload-time": "2000-01-01T00:00:00Z", "yanked": False,
        })
        state["routes"]["/files/" + archive.name] = 200, {}, content
    for name, entries in files.items():
        response = 200, {"Content-Type": "application/vnd.pypi.simple.v1+json"}, json.dumps({
            "meta": {"api-version": "1.1"}, "name": name, "files": entries,
        }).encode()
        for prefix in ("simple", "updated/simple"):
            state["routes"][f"/{prefix}/{name}/"] = response
    progress(f"normal Source cohort registry retained: {time.monotonic() - started:.2f}s")

    home = tmp_path / "build-home"
    home.mkdir()
    configuration = tmp_path / "uv.toml"
    for variable in tuple(os.environ):
        if variable.startswith(("UV_", "COPILOT_PLUGIN_")) or variable in {
            "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "SSL_CERT_DIR",
        }:
            monkeypatch.delenv(variable)
    for variable, value in {
        "HOME": str(home), "USERPROFILE": str(home), "AGENT_HOME": str(home),
        "LOCALAPPDATA": str(home / "local"), "XDG_STATE_HOME": str(home / "state"),
        "UV_CONFIG_FILE": str(configuration), "UV_CACHE_DIR": str(tmp_path / "cache"),
        "UV_PYTHON": sys.executable, "UV_PYTHON_DOWNLOADS": "never",
        "SSL_CERT_FILE": str(CERTIFICATE),
    }.items():
        monkeypatch.setenv(variable, value)

    def select_feed(prefix: str) -> None:
        configuration.write_text(
            'system-certs=true\nexclude-newer="7 days"\n[[index]]\n'
            'name="cohort"\ndefault=true\nauthenticate="never"\nurl='
            + json.dumps(state["origin"] + "/" + prefix) + "\n",
            encoding="utf-8",
        )

    select_feed("simple")
    sources.build_projects(snapshot, record, projects=projects)
    progress(f"normal Source cohort built: {time.monotonic() - started:.2f}s")
    outputs = _prepare_profiles(snapshot, tmp_path, profiles, uv, progress, started)
    select_feed("updated/simple")
    updated_profile = profiles.prepare_registry_lock(
        snapshot, tmp_path / "profile-updated", uv=uv,
        deadline=time.monotonic() + 120, prepare_profile=True,
        component="fleet-manager", extras=("gateway-host", "ssh-client"),
    )
    outputs["updated"] = Path(updated_profile["snapshot"])
    for component, output in outputs.items():
        selected = source_inputs.select_project(
            output, {"updated": "fleet-manager", "engine-base": "agent-worktrees"}.get(
                component, component,
            ),
        )
        assert (output / selected.project["application"] / "application.json").is_file()
    progress(f"normal Source cohort profiles complete: {time.monotonic() - started:.2f}s")
    return outputs


def main() -> None:
    import argparse
    import pytest

    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", help="Full immutable application Source commit; default is fixture HEAD")
    arguments = parser.parse_args()
    sys.path.insert(0, str(PROJECT.parent / "tools"))
    from test_installer_wheel_acquisition import feeds

    servers = cast(Callable[[], Iterator[FeedFactory]], inspect.unwrap(feeds))()
    try:
        with pytest.MonkeyPatch.context() as environment:
            outputs = prepare_cohort(
                arguments.output, environment, next(servers),
                lambda message: print(message, flush=True),
                source_commit=arguments.source_commit,
            )
        document = {key: str(value) for key, value in outputs.items()}
        (arguments.output / "cohort.json").write_text(json.dumps(document), encoding="utf-8")
    finally:
        next(servers, None)


if __name__ == "__main__":
    main()
