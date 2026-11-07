"""Prepare the actual associated applications through the normal Source builders."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys
import time

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from packaging.utils import canonicalize_name

from test_client_distribution import PROJECT, run
from test_gateway_host_distribution import _installed_wheel


def prepare_cohort(tmp_path, monkeypatch, feeds, progress):
    progress("normal Source cohort: inspect prepared dependency inputs")
    root = PROJECT.parent
    monkeypatch.syspath_prepend(str(root / "tools"))
    from test_installer_wheel_acquisition import CERTIFICATE
    import local_install_profile as profiles
    import local_install_source as sources

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
    commit = run(["git", "-C", str(root), "rev-parse", "HEAD"])
    assert commit.returncode == 0, commit.stderr
    progress("normal Source cohort: compose selected Git source")
    record = sources.compose(root, commit.stdout.strip(), None, snapshot)
    progress(f"normal Source cohort materialized: {time.monotonic() - started:.2f}s")
    projects = (
        "standalone:worktree-manager", "agent-worktrees",
        "standalone:fleet-manager", "agent-ssh",
    )
    first_party = set()
    for relative in (
        "worktree-manager", "plugins/agent-worktrees", "fleet-manager", "plugins/agent-ssh",
    ):
        first_party.update(
            profiles.source_inputs.engine._package_name(package)
            for package in profiles.source_inputs.engine._package_roots(snapshot / "source" / relative)
        )
    archives = tmp_path / "registry"
    archives.mkdir()
    distributions = {}
    for distribution in importlib.metadata.distributions(path=sorted(sites)):
        name = canonicalize_name(distribution.metadata["Name"])
        if name not in first_party:
            distributions.setdefault((name, distribution.version), distribution)
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
    outputs = {}
    for key, component, extras in (
        ("worktree-manager", "worktree-manager", ()),
        ("engine-base", "agent-worktrees", ()),
        ("agent-worktrees", "agent-worktrees", ("fleet-ssh",)),
        ("fleet-manager", "fleet-manager", ("gateway-host", "ssh-client")),
    ):
        result = profiles.prepare_registry_lock(
            snapshot, tmp_path / ("profile-" + key), uv=uv,
            deadline=time.monotonic() + 120, prepare_profile=True,
            component=component, extras=extras,
        )
        outputs[key] = Path(result["snapshot"])
        progress(f"normal {key} profile prepared: {time.monotonic() - started:.2f}s")
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
    arguments = parser.parse_args()
    sys.path.insert(0, str(PROJECT.parent / "tools"))
    from test_installer_wheel_acquisition import feeds

    servers = feeds.__wrapped__()
    try:
        with pytest.MonkeyPatch.context() as environment:
            outputs = prepare_cohort(
                arguments.output, environment, next(servers),
                lambda message: print(message, flush=True),
            )
        document = {key: str(value) for key, value in outputs.items()}
        (arguments.output / "cohort.json").write_text(json.dumps(document), encoding="utf-8")
    finally:
        next(servers, None)


if __name__ == "__main__":
    main()
