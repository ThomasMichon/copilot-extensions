"""Prepare the actual associated applications through the normal Source builders."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from test_client_distribution import PROJECT, run
from test_gateway_host_distribution import _installed_wheel


def _registry_distributions(projects, distributions, requirements):
    candidates = {}
    for (name, _version), distribution in distributions.items():
        candidates.setdefault(name, []).append(distribution)
    pending = [(Requirement(text), {""}) for text in requirements]
    selected = {}
    environment = default_environment()
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


def _prepare_profiles(snapshot, output, profiles, uv, progress, started):
    deadline = time.monotonic() + 120

    def prepare(selection):
        key, component, extras = selection
        result = profiles.prepare_registry_lock(
            snapshot, output / ("profile-" + key), uv=uv,
            deadline=deadline, prepare_profile=True,
            component=component, extras=extras,
        )
        return key, Path(result["snapshot"]), time.monotonic() - started

    selections = (
        ("worktree-manager", "worktree-manager", ()),
        ("engine-base", "agent-worktrees", ()),
        ("agent-worktrees", "agent-worktrees", ("fleet-ssh",)),
        ("fleet-manager", "fleet-manager", ("gateway-host", "ssh-client")),
    )
    outputs = {}
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


def _selected_source_commit(root, selected):
    if selected is None:
        result = run(["git", "-C", str(root), "rev-parse", "HEAD"])
        assert result.returncode == 0, "Fixture source HEAD is unavailable"
        selected = result.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", selected) is None:
        raise ValueError("Select a full immutable lowercase application Source commit")
    result = run(["git", "-C", str(root), "cat-file", "-e", selected + "^{commit}"])
    assert result.returncode == 0, "Selected application Source commit is unavailable"
    return selected


def prepare_cohort(tmp_path, monkeypatch, feeds, progress, *, source_commit=None):
    progress("normal Source cohort: inspect prepared dependency inputs")
    root = PROJECT.parent
    monkeypatch.syspath_prepend(str(root / "tools"))
    from test_installer_wheel_acquisition import CERTIFICATE
    import local_install_profile as profiles
    import local_install_source as sources
    from build_toolchain_lock import _LOCKED_TOOLCHAIN_PACKAGES

    progress("normal Source cohort: source owners imported")
    uv = shutil.which("uv")
    assert uv
    prepared = Path(os.environ["COPILOT_EXTENSIONS_TEST_VENV_ROOT"])
    sites = set()
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
    progress("normal Source cohort: compose selected Git source " + commit)
    record = sources.compose(root, commit, None, snapshot)
    progress(f"normal Source cohort materialized: {time.monotonic() - started:.2f}s")
    projects = (
        "standalone:worktree-manager", "agent-worktrees",
        "standalone:fleet-manager", "agent-ssh",
    )
    first_party = {}
    for relative in (
        "worktree-manager", "plugins/agent-worktrees", "fleet-manager", "plugins/agent-ssh",
    ):
        for package in profiles.source_inputs.engine._package_roots(snapshot / "source" / relative):
            metadata = profiles.dependencies._toml(package / "pyproject.toml")
            first_party[canonicalize_name(metadata["project"]["name"])] = metadata
    archives = tmp_path / "registry"
    archives.mkdir()
    distributions = {}
    for distribution in importlib.metadata.distributions(path=sorted(sites)):
        name = canonicalize_name(distribution.metadata["Name"])
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
    files = {}
    for (name, _version), distribution in sorted(distributions.items()):
        archive = _installed_wheel(distribution, archives)
        content = archive.read_bytes()
        files.setdefault(name, []).append({
            "filename": archive.name, "url": "/files/" + archive.name,
            "hashes": {"sha256": hashlib.sha256(content).hexdigest()},
            "requires-python": distribution.metadata.get("Requires-Python", ""),
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
    for name in tuple(os.environ):
        if name.startswith(("UV_", "COPILOT_PLUGIN_")) or name in {
            "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "SSL_CERT_DIR",
        }:
            monkeypatch.delenv(name)
    for name, value in {
        "HOME": str(home), "USERPROFILE": str(home), "AGENT_HOME": str(home),
        "LOCALAPPDATA": str(home / "local"), "XDG_STATE_HOME": str(home / "state"),
        "UV_CONFIG_FILE": str(configuration), "UV_CACHE_DIR": str(tmp_path / "cache"),
        "UV_PYTHON": sys.executable, "UV_PYTHON_DOWNLOADS": "never",
        "SSL_CERT_FILE": str(CERTIFICATE),
    }.items():
        monkeypatch.setenv(name, value)

    def select_feed(prefix):
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
    result = profiles.prepare_registry_lock(
        snapshot, tmp_path / "profile-updated", uv=uv,
        deadline=time.monotonic() + 120, prepare_profile=True,
        component="fleet-manager", extras=("gateway-host", "ssh-client"),
    )
    outputs["updated"] = Path(result["snapshot"])
    for component, output in outputs.items():
        selected = profiles.source_inputs.select_project(
            output, {"updated": "fleet-manager", "engine-base": "agent-worktrees"}.get(
                component, component,
            ),
        )
        assert (output / selected.project["application"] / "application.json").is_file()
    progress(f"normal Source cohort profiles complete: {time.monotonic() - started:.2f}s")
    return outputs


def main():
    import argparse
    import pytest

    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", help="Full immutable application Source commit; default is fixture HEAD")
    arguments = parser.parse_args()
    sys.path.insert(0, str(PROJECT.parent / "tools"))
    from test_installer_wheel_acquisition import feeds

    servers = feeds.__wrapped__()
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
