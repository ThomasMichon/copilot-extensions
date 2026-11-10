"""Admission precedes policy and lifecycle, independent of global installation."""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest
from agent_procutil import no_window_kwargs

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(f"admission_test_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("kind", ["preToolUse", "postToolUse", "sessionStart", "sessionEnd"])
def test_unmanaged_event_never_dispatches(kind, tmp_path, monkeypatch, capsys):
    client = load("hook_client")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("COPILOT_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "sessionId": "unmanaged", "cwd": str(tmp_path), "toolName": "edit",
        "toolArgs": {"path": "source.py"},
    })))

    def forbidden(*args, **kwargs):
        pytest.fail("unmanaged event reached policy or lifecycle activation")

    monkeypatch.setattr(client, "decide", forbidden)
    monkeypatch.setattr(client, "_enrich_session_payload", forbidden)
    monkeypatch.setattr(client, "_enrich_session_end_payload", forbidden)
    assert client.main([kind]) == 0
    output = capsys.readouterr()
    assert output.out.strip() in {"", "{}"}
    assert not output.err
    assert not (tmp_path / ".copilot").exists()


def fixture_project(tmp_path):
    anchor = tmp_path / "anchor-\u00e9"
    git_dir = anchor / ".git"
    for folder in ("objects", "refs"):
        (git_dir / folder).mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    registry = tmp_path / ".agent-worktrees"
    registry.mkdir()
    (registry / "projects.yaml").write_text('projects:\n  example:\n    config_dir: "~/.example"\n')
    (registry / "repos.yaml").write_text(
        "repos:\n  example:\n    class: worktree\n"
        + f"    {'windows' if os.name == 'nt' else 'linux'}: {json.dumps(str(anchor))}\n"
    )
    return anchor, registry


def fixture_linked_worktree(tmp_path, anchor, *, relative=False):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    admin = anchor / ".git" / "worktrees" / "example-worktree"
    admin.mkdir(parents=True)
    (admin / "HEAD").write_text("ref: refs/heads/work\n")
    (admin / "commondir").write_text("../..\n")
    (admin / "gitdir").write_text(
        (os.path.relpath(worktree / ".git", admin) if relative else str(worktree / ".git")) + "\n",
        encoding="utf-8",
    )
    (worktree / ".git").write_text("gitdir: " + str(admin) + "\n", encoding="utf-8")
    return worktree, admin


def fixture_checkout(path):
    for folder in ("objects", "refs"):
        (path / ".git" / folder).mkdir(parents=True)
    (path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    return path


def test_positive_membership_is_cached_but_registration_alone_is_not(tmp_path, monkeypatch):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, registry = fixture_project(tmp_path)
    payload = {"sessionId": "owned", "cwd": str(anchor)}
    assert module.admitted(payload, home=tmp_path)
    original = module._entries
    monkeypatch.setattr(module, "_entries", lambda *args: pytest.fail("warm membership reparsed registries"))
    assert module.admitted(payload, home=tmp_path)
    monkeypatch.setattr(module, "_entries", original)
    (registry / "projects.yaml").write_text("projects: {}\n")
    assert not module.admitted(payload, home=tmp_path)
    (registry / "projects.yaml").write_text("projects:\n  example: {}\n")
    assert module.admitted(payload, home=tmp_path)
    assert not module.admitted({**payload, "cwd": str(tmp_path)}, home=tmp_path)
    assert not module.admitted({**payload, "sessionId": "../bad"}, home=tmp_path)


def test_native_session_association_avoids_warm_git_traversal(tmp_path, monkeypatch):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, registry = fixture_project(tmp_path)
    session_dir = tmp_path / ".copilot" / "session-state" / "owned"
    session_dir.mkdir(parents=True)
    (session_dir / "agent-worktrees.json").write_text(json.dumps({
        "version": 2, "session_id": "owned",
        "relations": [{"role": "controller", "project": "example"}],
    }))
    unrelated = fixture_checkout(tmp_path / "unrelated")
    assert not module.admitted({"sessionId": "owned", "cwd": str(unrelated)}, home=tmp_path)
    payload = {"sessionId": "owned", "cwd": str(anchor)}
    assert module.admitted(payload, home=tmp_path)
    marker = session_dir / "agent-worktrees" / "managed-session.json"
    assert marker.is_file()
    original = module.repository_identity
    monkeypatch.setattr(module, "repository_identity", lambda *a, **k: pytest.fail("warm Git traversal"))
    assert module.admitted(payload, home=tmp_path)
    (registry / "projects.yaml").write_text("projects: {}\n")
    monkeypatch.setattr(module, "repository_identity", original)
    assert not module.admitted(payload, home=tmp_path)


@pytest.mark.parametrize("relative", [False, True])
def test_linked_worktree_requires_the_adopted_common_directory(tmp_path, monkeypatch, relative):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, _ = fixture_project(tmp_path)
    worktree, admin = fixture_linked_worktree(tmp_path, anchor, relative=relative)
    nested = worktree / "src"
    nested.mkdir()
    payload = {"sessionId": "owned", "cwd": str(nested)}
    assert module.admitted(payload, home=tmp_path)
    (admin / "commondir").write_text(str(tmp_path / "foreign") + "\n")
    assert not module.admitted(payload, home=tmp_path)


@pytest.mark.parametrize("tags", ["[selected, local]", "\n    - selected\n    - local"])
def test_generated_registry_admission_needs_no_yaml_install(tmp_path, monkeypatch, tags):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setitem(sys.modules, "yaml", None)
    anchor, registry = fixture_project(tmp_path)
    with (registry / "repos.yaml").open("a") as stream:
        stream.write(
            f"    tags: {tags}\n"
            "    metadata:\n      nested: [ignored]\n"
            f"  unrelated:\n    class: reference\n    tags: {tags}\n"
        )
    assert module.admitted({"sessionId": "fresh", "cwd": str(anchor)}, home=tmp_path)


@pytest.mark.parametrize("container", [False, True])
@pytest.mark.parametrize("implementation", ["standalone", "runtime"])
def test_admission_shares_kernel_and_container_platform_detection(
    tmp_path, monkeypatch, container, implementation,
):
    from types import SimpleNamespace

    module = load("managed_session")
    if implementation == "runtime":
        from machine_transport import detect_platform

        monkeypatch.setattr(module, "detect_platform", detect_platform)
    detector = sys.modules[module.detect_platform.__module__]
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    if container:
        monkeypatch.setenv("WSL_DISTRO_NAME", "inherited")
    else:
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.setattr(detector, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(detector, "os", SimpleNamespace(
        uname=lambda: SimpleNamespace(release="6.6-microsoft-standard-WSL2"),
    ))

    class Probe:
        def __init__(self, path):
            self.path = path

        def is_file(self):
            return False

        def read_text(self, **kwargs):
            return "0::/docker/example" if container else "0::/user.slice"

    monkeypatch.setattr(detector, "Path", Probe)
    anchor, registry = fixture_project(tmp_path)
    other = tmp_path / "other"
    selected = "linux" if container else "wsl"
    paths = {name: str(anchor if name == selected else other) for name in ("windows", "linux", "wsl")}
    (registry / "repos.yaml").write_text(json.dumps({"repos": {"example": paths}}))
    assert module.admitted({"sessionId": "classified", "cwd": str(anchor)}, home=tmp_path)


def test_sdk_cell_cache_revokes_changed_resolver_ownership(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import test_registry_paths as fixtures

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    native_run = subprocess.run
    monkeypatch.setattr(fixtures, "subprocess", SimpleNamespace(
        run=lambda *args, **kwargs: native_run(*args, **(kwargs | no_window_kwargs())),
    ))
    anchor, legacy = fixture_project(tmp_path)
    payload = fixtures._payload(tmp_path, "selected")
    for name in ("managed_session.py", "execution_platform.py", "managed_project_identity.py",
                 "remote_identity.py", "guarded_anchor_map.py",
                 "hook_policy_process.py", "hook_process_identity.py"):
        shutil.copyfile(SCRIPTS / name, payload / "scripts" / name)
    context, registry = fixtures._stamp(
        tmp_path / "durable", payload, marketplace="selected", repository="example/selected",
    )
    for name in ("projects.yaml", "repos.yaml"):
        shutil.copyfile(legacy / name, registry / name)
    namespace = json.loads(context.read_text())["namespaceReceipt"]
    driver = tmp_path / "cell-cache.mjs"
    driver.write_text("""
import assert from "node:assert/strict";
import {readFileSync, unlinkSync, writeFileSync} from "node:fs";
import {pathToFileURL} from "node:url";
const [module, scriptsRoot, python, workingDirectory, namespace, manifest] = process.argv.slice(2);
const {createManagedSessionAdmission} = await import(pathToFileURL(module));
const check = createManagedSessionAdmission({scriptsRoot, python});
const input = {sessionId: "cell-cache", workingDirectory};
assert.equal(await check(input), true);
for (const path of [namespace, manifest]) {
  const saved = readFileSync(path);
  for (const damage of ["remove", "corrupt"]) {
    if (damage === "remove") unlinkSync(path);
    else writeFileSync(path, "{}");
    assert.equal(check.current(input), false, "resolver ownership invalidates warm admission");
    assert.equal(await check(input), false);
    writeFileSync(path, saved);
    assert.equal(await check(input), true, "restored ownership can admit the same session");
  }
}
""")
    env = environment(tmp_path, anchor)
    env.update(COPILOT_EXTENSIONS_CONTEXT=str(context), COPILOT_PLUGIN_ROOT=str(payload),
               AGENT_WORKTREES_PAYLOAD_ROOT=str(payload))
    result = subprocess.run(
        [node, str(driver), str(SCRIPTS.parent / "extensions" / "agent-worktrees" / "managed-session.mjs"),
         str(payload / "scripts"), sys.executable, str(anchor), namespace, str(payload / "plugin.json")],
        capture_output=True, text=True, timeout=15, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_linked_admission_cache_tracks_failed_owner_and_common_metadata(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    anchor, registry = fixture_project(tmp_path)
    worktree, admin = fixture_linked_worktree(tmp_path, anchor)
    unrelated = tmp_path / "unrelated" / ".git"
    for folder in ("objects", "refs"):
        (unrelated / folder).mkdir(parents=True)
    (unrelated / "HEAD").write_text("ref: refs/heads/main\n")
    with (registry / "projects.yaml").open("a") as stream:
        stream.write(f"  other:\n    anchor: {json.dumps(str(unrelated.parent))}\n")
    driver = tmp_path / "linked-cache.mjs"
    driver.write_text("""
import assert from "node:assert/strict";
import {mkdirSync, readFileSync, rmdirSync, unlinkSync, writeFileSync} from "node:fs";
import {join} from "node:path";
import {pathToFileURL} from "node:url";
const [module, scriptsRoot, python, workingDirectory, anchor, admin] = process.argv.slice(2);
const {createManagedSessionAdmission} = await import(pathToFileURL(module));
const check = createManagedSessionAdmission({scriptsRoot, python});
const input = {sessionId: "linked-cache", workingDirectory};
const head = join(anchor, ".git", "HEAD");
const text = readFileSync(head);
unlinkSync(head);
assert.equal(await check(input), false);
writeFileSync(head, text);
assert.equal(await check(input), true, "repairing a failed anchor probe invalidates a negative");
assert.equal(check.current(input), true);
for (const name of ["objects", "refs"]) {
  const path = join(anchor, ".git", name);
  rmdirSync(path);
  assert.equal(check.current(input), false);
  assert.equal(await check(input), false, "shared metadata removal revokes admission");
  mkdirSync(path);
  assert.equal(await check(input), true, "restoring shared metadata invalidates a negative");
}
const backlink = join(admin, "gitdir");
const linkText = readFileSync(backlink);
unlinkSync(backlink);
assert.equal(await check(input), false);
writeFileSync(backlink, linkText);
assert.equal(await check(input), true, "repairing a failed backlink probe invalidates a negative");
unlinkSync(head);
assert.equal(check.current(input), false);
assert.equal(await check(input), false, "anchor HEAD remains a live ownership input");
""")
    result = subprocess.run(
        [node, str(driver), str(SCRIPTS.parent / "extensions" / "agent-worktrees" / "managed-session.mjs"),
         str(SCRIPTS), sys.executable, str(worktree), str(anchor), str(admin)],
        capture_output=True, text=True, timeout=15,
        env=environment(tmp_path, worktree), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_cache_is_scoped_to_session_and_selected_registry(tmp_path, monkeypatch):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, registry = fixture_project(tmp_path)
    payload = {"sessionId": "first", "cwd": str(anchor)}
    assert module.admitted(payload, home=tmp_path)
    calls = []
    original = module._entries
    monkeypatch.setattr(module, "_entries", lambda *args: calls.append(args) or original(*args))
    assert module.admitted({**payload, "sessionId": "second"}, home=tmp_path)
    assert len(calls) == 2
    other = tmp_path / "other-installation"
    other.mkdir()
    (other / "projects.yaml").write_text("projects: {}\n")
    monkeypatch.setattr(module, "resolve_registry_root", lambda **kwargs: other)
    assert not module.admitted(payload, home=tmp_path)
    assert not (other / "cache").exists()
    assert (registry / "cache").is_dir()


def test_persistent_membership_does_not_cross_execution_platforms(tmp_path, monkeypatch):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, registry = fixture_project(tmp_path)
    (registry / "repos.yaml").write_text(json.dumps({
        "repos": {"example": {"wsl": str(anchor), "linux": str(tmp_path / "other")}},
    }))
    payload = {"sessionId": "same-session", "cwd": str(anchor)}
    monkeypatch.setattr(module, "detect_platform", lambda: "wsl")
    assert module.admitted(payload, home=tmp_path)
    monkeypatch.setattr(module, "detect_platform", lambda: "linux")
    assert not module.admitted(payload, home=tmp_path)
    monkeypatch.setattr(module, "detect_platform", lambda: "wsl")
    assert module.admitted(payload, home=tmp_path)


@pytest.mark.parametrize("syntax", ["json", "yaml", "quoted-yaml"])
def test_machine_anchor_override_precedes_registered_anchor_and_invalidates_reuse(tmp_path, monkeypatch, syntax):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setitem(sys.modules, "yaml", None)
    anchor, _ = fixture_project(tmp_path)
    override = fixture_checkout(tmp_path / "override")
    config = tmp_path / ".example" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        json.dumps({"repos": {"example": {"anchor": str(override)}}}) if syntax == "json" else
        (
            f'"repos":\n  example:\n    "anchor": {json.dumps(str(override))}\n'
            if syntax == "quoted-yaml" else
            f"repos:\n  example:\n    anchor: {json.dumps(str(override))}\n"
        )
    )
    payload = {"sessionId": "override", "cwd": str(anchor)}
    assert not module.admitted(payload, home=tmp_path)
    payload["cwd"] = str(override)
    assert module.admitted(payload, home=tmp_path)
    config.write_text("repos: null\n")
    assert not module.admitted(payload, home=tmp_path)
    payload["cwd"] = str(anchor)
    assert module.admitted(payload, home=tmp_path)


@pytest.mark.parametrize("source", ["registry", "machine"])
@pytest.mark.parametrize("quote", ['"', "'"])
@pytest.mark.parametrize("indent", [2, 4])
def test_commented_yaml_ownership_paths_preserve_quotes_hashes_and_indentation(
    tmp_path, monkeypatch, source, quote, indent,
):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setitem(sys.modules, "yaml", None)
    stale, registry = fixture_project(tmp_path)
    current = fixture_checkout(tmp_path / "current # retained")
    if source == "registry":
        path, field = registry / "repos.yaml", "windows" if os.name == "nt" else "linux"
    else:
        path, field = tmp_path / ".example" / "config.yaml", "anchor"
        path.parent.mkdir()
    value = json.dumps(str(current)) if quote == '"' else "'" + str(current).replace("'", "''") + "'"
    path.write_text(
        "repos: # ownership\n" + " " * indent + "example: # project\n"
        + " " * (indent * 2) + field + ": " + value + " # operator comment\n"
    )
    payload = {"sessionId": "commented-ownership", "cwd": str(stale)}
    assert not module.admitted(payload, home=tmp_path)
    assert module.admitted({**payload, "cwd": str(current)}, home=tmp_path)


def test_quoted_yaml_platform_override_precedes_execution_platform_without_yaml(tmp_path, monkeypatch):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setitem(sys.modules, "yaml", None)
    monkeypatch.setattr(module, "detect_platform", lambda: "linux")
    stale, registry = fixture_project(tmp_path)
    current = fixture_checkout(tmp_path / "current")
    (registry / "repos.yaml").write_text(json.dumps({
        "repos": {"example": {"linux": str(stale), "wsl": str(current)}},
    }))
    config = tmp_path / ".example" / "config.yaml"
    config.parent.mkdir()
    config.write_text('"platform": wsl\n')
    payload = {"sessionId": "quoted-platform", "cwd": str(stale)}
    assert not module.admitted(payload, home=tmp_path)
    assert module.admitted({**payload, "cwd": str(current)}, home=tmp_path)


@pytest.mark.parametrize("scalar", ["null", "~", "Null", "NULL", "", "json"])
def test_per_anchor_null_clears_to_registry_without_yaml(tmp_path, monkeypatch, scalar):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setitem(sys.modules, "yaml", None)
    anchor, _ = fixture_project(tmp_path)
    config = tmp_path / ".example" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        json.dumps({"repos": {"example": {"anchor": None}}}) if scalar == "json" else
        f"repos:\n  example:\n    anchor: {scalar}\n"
    )
    assert module.admitted({"sessionId": "cleared", "cwd": str(anchor)}, home=tmp_path)


def test_wsl_does_not_inherit_linux_only_registration(tmp_path, monkeypatch):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(module, "detect_platform", lambda: "wsl")
    anchor, registry = fixture_project(tmp_path)
    (registry / "repos.yaml").write_text(json.dumps({
        "repos": {"example": {"linux": str(anchor)}},
    }))
    payload = {"sessionId": "wsl-only", "cwd": str(anchor)}
    assert not module.admitted(payload, home=tmp_path)
    config = tmp_path / ".example" / "config.yaml"
    config.parent.mkdir()
    config.write_text(json.dumps({"repos": {"example": {"anchor": str(anchor)}}}))
    assert module.admitted(payload, home=tmp_path)


@pytest.mark.parametrize("without_yaml", [False, True])
@pytest.mark.parametrize("platform", ["windows", "linux", "wsl"])
def test_stale_project_anchor_never_supplies_registry_ownership(
    tmp_path, monkeypatch, without_yaml, platform,
):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    if without_yaml:
        monkeypatch.setitem(sys.modules, "yaml", None)
    monkeypatch.setattr(module, "detect_platform", lambda: platform)
    stale, registry = fixture_project(tmp_path)
    current = fixture_checkout(tmp_path / "current")
    (registry / "projects.yaml").write_text(json.dumps({
        "projects": {"example": {"anchor": str(stale)}},
    }))
    repos = registry / "repos.yaml"
    repos.write_text(json.dumps({"repos": {"example": {}}}))
    payload = {"sessionId": "stale-anchor", "cwd": str(stale)}
    assert not module.admitted(payload, home=tmp_path)
    repos.write_text(json.dumps({"repos": {"example": {platform: str(current)}}}))
    assert not module.admitted(payload, home=tmp_path)
    assert module.admitted({**payload, "cwd": str(current)}, home=tmp_path)
    repos.write_text(json.dumps({"repos": {"example": {}}}))
    assert not module.admitted({**payload, "cwd": str(current)}, home=tmp_path)
    assert not module.admitted(payload, home=tmp_path)


def test_namespaced_anchor_override_uses_canonical_repository_identity(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import test_registry_paths as fixtures
    from agent_worktrees import project_state

    native_run = subprocess.run
    monkeypatch.setattr(fixtures, "subprocess", SimpleNamespace(
        run=lambda *args, **kwargs: native_run(*args, **(kwargs | no_window_kwargs())),
    ))
    module = load("managed_session")
    anchor, legacy = fixture_project(tmp_path)
    override = fixture_checkout(tmp_path / "override")
    payload_root = fixtures._payload(tmp_path, "selected")
    context, registry = fixtures._stamp(
        tmp_path / "durable", payload_root, marketplace="selected", repository="example/selected",
    )
    (registry / "projects.yaml").write_text((legacy / "projects.yaml").read_text())
    remote = "https://github.com:443/Example/Repository.git"
    (registry / "repos.yaml").write_text(json.dumps({
        "repos": {"example": {"remote": remote,
                              "windows" if os.name == "nt" else "linux": str(anchor)}},
    }))
    fixtures._select(monkeypatch, context, payload_root)
    identity = project_state._identity("example", remote=remote)
    generated = module.project_identity("example", remote, module.resolve_registry_context())
    assert generated.state_root == identity.state_root
    identity.state_root.mkdir(parents=True)
    receipt = identity.repository_root / "identity.json"
    value = project_state._receipt(identity)
    receipt.write_text(json.dumps(value))
    (identity.state_root / "config.yaml").write_text(json.dumps({
        "repos": {"example": {"anchor": str(override)}},
    }))
    payload = {"sessionId": "cell-override", "cwd": str(anchor)}
    assert not module.admitted(payload)
    payload["cwd"] = str(override)
    assert module.admitted(payload)
    receipt.write_text(json.dumps({**value, "normalizedRemote": "network:example.invalid/other"}))
    assert not module.admitted(payload)
    receipt.write_text(json.dumps(value))
    assert module.admitted(payload)


def test_sdk_rechecks_new_ownership_fragments_and_explicit_precedence(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    anchor, _ = fixture_project(tmp_path)
    other = fixture_checkout(tmp_path / "other")
    config = tmp_path / ".example" / "config.yaml"
    fragments = config.parent / "config.d"
    fragments.mkdir(parents=True)
    driver = tmp_path / "config-cache.mjs"
    driver.write_text("""
import assert from "node:assert/strict";
import {unlinkSync, writeFileSync} from "node:fs";
import {join} from "node:path";
import {pathToFileURL} from "node:url";
const [module, scriptsRoot, python, workingDirectory, other, config, fragments] = process.argv.slice(2);
const {createManagedSessionAdmission} = await import(pathToFileURL(module));
const check = createManagedSessionAdmission({scriptsRoot, python});
const input = {sessionId: "config-cache", workingDirectory};
assert.equal(await check(input), true);
const fragment = join(fragments, "operator.yaml");
writeFileSync(fragment, JSON.stringify({repos: {example: {anchor: other}}}));
assert.equal(check.current(input), false);
assert.equal(await check(input), false, "conditional ownership is not guessed before activation");
assert.equal(await check({...input, workingDirectory: other}), false);
writeFileSync(config, JSON.stringify({repos: {example: {anchor: other}}}));
assert.equal(await check({...input, workingDirectory: other}), true,
  "the explicit machine configuration wins");
unlinkSync(config);
unlinkSync(fragment);
assert.equal(await check(input), true);
""")
    result = subprocess.run(
        [node, str(driver), str(SCRIPTS.parent / "extensions" / "agent-worktrees" / "managed-session.mjs"),
         str(SCRIPTS), sys.executable, str(anchor), str(other), str(config), str(fragments)],
        capture_output=True, text=True, timeout=15,
        env=environment(tmp_path, anchor), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_installation_cells_do_not_share_admission_or_fall_back(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import test_registry_paths as fixtures

    native_run = subprocess.run
    monkeypatch.setattr(fixtures, "subprocess", SimpleNamespace(
        run=lambda *args, **kwargs: native_run(*args, **(kwargs | no_window_kwargs())),
    ))
    anchor, legacy = fixture_project(tmp_path)
    cells = []
    for label in ("first", "second"):
        payload = fixtures._payload(tmp_path, label)
        for name in ("managed_session.py", "execution_platform.py", "managed_project_identity.py",
                     "remote_identity.py", "guarded_anchor_map.py"):
            shutil.copyfile(SCRIPTS / name, payload / "scripts" / name)
        context, registry = fixtures._stamp(
            tmp_path / "durable", payload,
            marketplace=label, repository=f"example/{label}",
        )
        (registry / "projects.yaml").write_text(
            (legacy / "projects.yaml").read_text() if label == "first" else "projects: {}\n"
        )
        (registry / "repos.yaml").write_text((legacy / "repos.yaml").read_text())
        cells.append((payload, context, registry))
    for index in (0, 1, 0):
        payload, context, registry = cells[index]
        env = environment(tmp_path, anchor)
        env.update(
            COPILOT_PLUGIN_ROOT=str(payload), AGENT_WORKTREES_PAYLOAD_ROOT=str(payload),
            COPILOT_EXTENSIONS_CONTEXT=str(context),
        )
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(payload / "scripts" / "managed_session.py")],
            input=json.dumps({"sessionId": "same-session", "cwd": str(anchor)}),
            text=True, capture_output=True, timeout=10, env=env, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["admitted"] is (index == 0), result.stdout
        assert (registry / "cache" / "managed-sessions").exists() is (index == 0)
        if index == 0:
            association = json.loads(next((registry / "cache" / "managed-sessions").glob("*.json")).read_text())
            view = association["installation"]
            assert view["version"] == 1
            assert view["pluginRoot"] == str(registry)
            assert set(view["files"]) == {
                view["namespaceReceipt"], view["installReceipt"], str(payload / "plugin.json"),
            }
    assert not (legacy / "cache").exists()


def environment(home, anchor):
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("COPILOT_", "AGENT_WORKTREES_", "WORKTREE_")):
            env.pop(key)
    env.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        COPILOT_PROJECT_DIR=str(anchor), COPILOT_PLUGIN_ROOT=str(SCRIPTS.parent),
        PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
    )
    return env


@pytest.mark.skipif(os.name != "nt", reason="Native Windows prepared membership")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("linked", [False, True])
def test_prepared_native_membership_reuses_original_association(tmp_path, monkeypatch, capsys, shell, linked):
    executable = shutil.which(shell)
    if not executable:
        if os.environ.get("AGENT_WORKTREES_REQUIRE_NATIVE_MEMBERSHIP") == "1":
            pytest.fail(f"Required native membership shell {shell} is unavailable")
        pytest.skip(f"{shell} is unavailable")
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, _ = fixture_project(tmp_path)
    workspace = fixture_linked_worktree(tmp_path, anchor)[0] if linked else anchor
    workspace = workspace / "src"
    workspace.mkdir()
    session = tmp_path / ".copilot" / "session-state" / "prepared"
    session.mkdir(parents=True)
    payload = {"sessionId": "prepared", "cwd": str(workspace), "toolName": "view"}
    assert module.admitted(payload, home=tmp_path)
    phases = tmp_path / "native-admission-phases"
    command = (
        "function Write-AdmissionPhase([string]$Name) { "
        "[IO.File]::AppendAllText($env:PREPARED_MEMBERSHIP_PHASES, "
        "[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds().ToString("
        "[Globalization.CultureInfo]::InvariantCulture) + ' ' + $Name + [Environment]::NewLine) }; "
        "Write-AdmissionPhase 'command-entry'; "
        ". $env:PREPARED_MEMBERSHIP_SCRIPT; Write-AdmissionPhase 'helper-loaded'; "
        "function Get-ManagedWorktreesPython { throw 'interpreter lookup is forbidden' }; "
        "$raw = [Console]::In.ReadToEnd(); Write-AdmissionPhase 'input-complete'; "
        "$admitted = Test-ManagedWorktreesSession -Raw $raw; Write-AdmissionPhase 'admission-complete'; "
        "[Console]::Out.Write($admitted.ToString()); Write-AdmissionPhase 'output-complete'"
    )
    env = environment(tmp_path, workspace)
    env["PREPARED_MEMBERSHIP_SCRIPT"] = str(SCRIPTS / "managed-session.ps1")
    env["PREPARED_MEMBERSHIP_PHASES"] = str(phases)
    started_ms = time.time_ns() // 1_000_000
    try:
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", command],
            input=json.dumps(payload), text=True, capture_output=True, timeout=10,
            cwd=workspace, env=env, **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as error:
        progress = phases.read_text() if phases.exists() else "no command entry"
        raise AssertionError(
            f"{shell} membership exceeded the original 10s child limit; start_ms={started_ms}; "
            f"phases={progress!r}; stdout={repr(error.stdout)[:512]}; stderr={repr(error.stderr)[:512]}"
        ) from error
    assert result.returncode == 0, result.stderr
    assert result.stdout == "True", result.stdout + result.stderr
    with capsys.disabled():
        print(json.dumps({
            "shell": shell, "linked": linked, "start_ms": started_ms,
            "phases": phases.read_text().splitlines(),
        }))


@pytest.mark.skipif(os.name != "nt", reason="Native Windows registered pre-tool consumer")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", ["read", "write", "revoked", "cold-read", "cold-write", "read-batch"])
def test_registered_pre_tool_entry_reuses_prepared_membership(tmp_path, monkeypatch, shell, case):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, registry = fixture_project(tmp_path)
    workspace = fixture_linked_worktree(tmp_path, anchor)[0]
    session = tmp_path / ".copilot" / "session-state" / "pre-tool"
    session.mkdir(parents=True)
    payload = {
        "sessionId": "pre-tool", "cwd": str(workspace),
        "toolName": "view" if case in {"read", "cold-read", "read-batch"} else "edit",
        "toolArgs": {"path": "non-ascii-\u00e9.txt", "text": "quote'\"\\\n\t"},
    }
    if case == "read-batch":
        payload["toolCalls"] = [{"name": "edit", "args": payload["toolArgs"]}]
    if not case.startswith("cold-"):
        assert module.admitted(payload, home=tmp_path)
    plugin = tmp_path / "payload"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    if case.startswith("cold-"):
        shutil.copytree(SCRIPTS, scripts, dirs_exist_ok=True)
    for name in (
        "pre-tool-hook.ps1", "managed-session.ps1", "hook-read-policy.json",
        "statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py",
        "pr_supersede_guard.py",
    ):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    observed = tmp_path / "policy-input"
    (scripts / "resolve-runtime.ps1").write_text(
        "$AwPy='" + sys.executable.replace("'", "''") + "'\n",
        encoding="utf-8",
    )
    shutil.copyfile(SCRIPTS / "hook_policy_process.py", scripts / "hook_policy_process_original.py")
    (scripts / "hook_policy_process.py").write_text(
        "import base64,json,sys\nfrom pathlib import Path\n"
        + "# Re-export the real admission helper's caller-lifetime watcher without executing policy on import.\n"
        + "from hook_policy_process_original import _watch_caller\n"
        + "if __name__ == '__main__':\n"
        + f"    with Path({str(observed)!r}).open('xb') as output: output.write(base64.b64decode(sys.stdin.readline()))\n"
        + "    print(json.dumps({'permissionDecision':'deny','permissionDecisionReason':'fixture policy'}))\n",
        encoding="utf-8",
    )
    if case == "revoked":
        (registry / "projects.yaml").write_text("projects: {}\n", encoding="utf-8")
    env = environment(tmp_path, workspace)
    env["COPILOT_PLUGIN_ROOT"] = str(plugin)
    env.pop("AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION", None)
    entry = json.loads((SCRIPTS.parent / "hooks.json").read_text(encoding="utf-8"))["hooks"]["preToolUse"][0]
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    if case.startswith("cold-"):
        assert not (registry / "cache").exists()
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", entry["powershell"]],
        input=raw.encode("utf-8"), capture_output=True, timeout=10,
        cwd=workspace, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == b""
    if case.startswith("cold-"):
        assert (session / "agent-worktrees" / "managed-session.json").is_file()
    if case in {"write", "cold-write", "read-batch"}:
        assert observed.read_bytes() == raw.encode("utf-8")
        assert json.loads(result.stdout) == {
            "permissionDecision": "deny", "permissionDecisionReason": "fixture policy",
        }
    else:
        assert not observed.exists()
        assert result.stdout == b""


@pytest.mark.skipif(os.name != "nt", reason="Native Windows registered prepared-cell pre-tool consumer")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", ["view", "rg", "glob", "guard-drift", "incomplete", "receipt", "write", "read-batch"])
def test_registered_prepared_cell_membership_read_requires_complete_native_proof(tmp_path, monkeypatch, shell, case):
    from types import SimpleNamespace
    import test_registry_paths as fixtures

    executable = shutil.which(shell)
    if not executable:
        if os.environ.get("AGENT_WORKTREES_REQUIRE_NATIVE_MEMBERSHIP") == "1":
            pytest.fail(f"{shell} is required")
        pytest.skip(f"{shell} is unavailable")
    native_run = subprocess.run
    monkeypatch.setattr(fixtures, "subprocess", SimpleNamespace(
        run=lambda *args, **kwargs: native_run(
            *args, **(kwargs | no_window_kwargs() | {"timeout": 10}),
        ),
    ))
    anchor, legacy = fixture_project(tmp_path)
    workspace = fixture_linked_worktree(tmp_path, anchor)[0]
    plugin = fixtures._payload(tmp_path, "selected")
    scripts = plugin / "scripts"
    for name in (
        "pre-tool-hook.ps1", "managed-session.ps1", "managed_session.py",
        "execution_platform.py", "managed_project_identity.py", "remote_identity.py",
        "guarded_anchor_map.py", "hook_policy_process.py", "hook-read-policy.json",
        "statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py", "pr_supersede_guard.py",
    ):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    context, registry = fixtures._stamp(
        tmp_path / "durable", plugin, marketplace="selected", repository="example/selected",
    )
    for name in ("projects.yaml", "repos.yaml"):
        (registry / name).write_text((legacy / name).read_text())
    fixtures._select(monkeypatch, context, plugin)
    session = tmp_path / ".copilot" / "session-state" / "pre-cell"
    session.mkdir(parents=True)
    event = {
        "sessionId": "pre-cell", "cwd": str(workspace),
        "toolName": case if case in {"view", "rg", "glob"} else "edit" if case == "write" else "view",
        "toolArgs": {"path": "non-ascii-\u00e9.txt", "text": "quote'\"\\\n\t"},
    }
    if case == "read-batch":
        event["toolCalls"] = [{"name": "edit", "args": event["toolArgs"]}]
    assert load("managed_session").admitted(event, home=tmp_path)
    marker = session / "agent-worktrees" / "managed-session.json"
    if case == "incomplete":
        value = json.loads(marker.read_text())
        value.pop("installation")
        marker.write_text(json.dumps(value))
    elif case == "receipt":
        context.write_text(context.read_text() + "\n")
    elif case == "guard-drift":
        with (scripts / "anchor_write_guard.py").open("a", encoding="utf-8") as output:
            output.write("\n# changed guard implementation\n")
    membership = tmp_path / "membership-interpreter"
    validation = tmp_path / "context-validator"
    policy = tmp_path / "policy-input"
    with (scripts / "managed-session.ps1").open("a", encoding="utf-8") as output:
        output.write(
            "\nfunction Get-ManagedWorktreesPython { "
            + "[IO.File]::WriteAllText('" + str(membership).replace("'", "''") + "', 'called'); "
            + "return '" + sys.executable.replace("'", "''") + "' }\n"
        )
    (scripts / "installation-context" / "installation-context.ps1").write_text(
        "[IO.File]::WriteAllText('" + str(validation).replace("'", "''") + "', 'called')\n"
        + "Write-Output '" + json.dumps({"pluginRoot": str(registry)}).replace("'", "''") + "'\n"
        + "$global:LASTEXITCODE = 0\n",
        encoding="utf-8",
    )
    (scripts / "resolve-runtime.ps1").write_text(
        "$AwPy='" + sys.executable.replace("'", "''") + "'\n", encoding="utf-8",
    )
    shutil.copyfile(scripts / "hook_policy_process.py", scripts / "hook_policy_process_original.py")
    (scripts / "hook_policy_process.py").write_text(
        "import base64,json,sys\nfrom pathlib import Path\n"
        + "from hook_policy_process_original import _watch_caller\n"
        + "if __name__ == '__main__':\n"
        + f"    Path({str(policy)!r}).write_bytes(base64.b64decode(sys.stdin.readline()))\n"
        + "    print(json.dumps({'permissionDecision':'deny','permissionDecisionReason':'fixture policy'}))\n",
        encoding="utf-8",
    )
    flag = tmp_path / "default-stop.json"
    flag.write_text('{"active":true}', encoding="utf-8")
    env = environment(tmp_path, workspace)
    env.update(
        COPILOT_EXTENSIONS_CONTEXT=str(context), COPILOT_PLUGIN_ROOT=str(plugin),
        AGENT_WORKTREES_PAYLOAD_ROOT=str(plugin), PRETOOL_KILLSWITCH_STATE_FILE=str(flag),
    )
    env.pop("AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION", None)
    entry = json.loads((SCRIPTS.parent / "hooks.json").read_text(encoding="utf-8"))["hooks"]["preToolUse"][0]
    raw = json.dumps(event, ensure_ascii=False, indent=2).encode("utf-8")
    result = native_run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", entry["powershell"]],
        input=raw, capture_output=True, timeout=10,
        cwd=workspace, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert membership.exists() is (case in {"incomplete", "receipt"})
    policy_required = case not in {"view", "rg", "glob"}
    assert validation.exists() is policy_required, (result.stdout, result.stderr)
    assert policy.exists() is policy_required
    if policy_required:
        assert policy.read_bytes() == raw
        assert json.loads(result.stdout)["permissionDecision"] == "deny"
    else:
        assert result.stdout == b""
    assert not (legacy / "cache").exists()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows registered file-local write consumer")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", [
    "ordinary", "create", "path-alias", "json-args", "personal-state", "anchor-cwd",
    "anchor-target", "foreign", "nested", "batch", "structured", "drive-relative",
    "missing-map", "partial-map", "wrong-scope", "array-schema", "oversized-config",
    "guard-drift", "reader-drift", "forms-drift", "missing-dependencies", "git-pointer-aba",
    "delegated", "invalid-elevation", "noncanonical-map", "dot-segment-map", "real-policy", "old-admission",
    "cell-ordinary", "cell-create", "cell-personal-state", "cell-guard-drift",
    "personal-state-cr", "personal-state-crlf", "personal-state-dot-prefix",
    "personal-state-dot-file", "personal-state-dot-assignments",
    "personal-state-control-space", "ordinary-nested-dot-file",
    "ordinary-unrelated-inventory",
    "batch-mixed", "batch-json-args", "batch-protected", "batch-foreign",
    "batch-multiple-targets", "batch-structured", "batch-missing-args",
    "batch-null-args", "batch-extra-key", "batch-unknown", "batch-empty",
    "batch-not-list", "batch-limit", "batch-oversized", "batch-read-only",
    "batch-working-directory", "batch-real-policy", "batch-malformed",
    "cell-batch-mixed", "cell-batch-protected",
])
def test_registered_file_local_write_reuses_original_guard_facts(tmp_path, monkeypatch, shell, case):
    from agent_worktrees import guarded_anchors

    cell = case.startswith("cell-")
    if cell:
        case = case.removeprefix("cell-")
    executable = shutil.which(shell)
    if not executable:
        if os.environ.get("AGENT_WORKTREES_REQUIRE_NATIVE_MEMBERSHIP") == "1":
            pytest.fail(f"{shell} is required")
        pytest.skip(f"{shell} is unavailable")
    anchor, registry = fixture_project(tmp_path)
    workspace, admin = fixture_linked_worktree(tmp_path, anchor)
    if case == "anchor-cwd":
        workspace = anchor
    config = workspace / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    config.write_text("stateless: false\n" if case == "anchor-cwd" else "stateless: true\n", encoding="utf-8")
    if case in {"personal-state-cr", "personal-state-crlf"}:
        newline = "\r" if case == "personal-state-cr" else "\r\n"
        config.write_bytes(f"default_branch: main{newline}stateless: true{newline}".encode("utf-8"))
    session = tmp_path / ".copilot" / "session-state" / "pre-write"
    session.mkdir(parents=True)
    event = {
        "sessionId": "pre-write", "cwd": str(workspace), "toolName": "edit",
        "toolArgs": {
            "path": {
                "personal-state-dot-prefix": ".efforts/private.md",
                "personal-state-dot-file": ".ownership.yml",
                "personal-state-dot-assignments": "..dev-assignments.yml",
                "ordinary-nested-dot-file": "source/.ownership.yml",
            }.get(case, "efforts/private.md" if case.startswith("personal-state") else "source-\u00e9.py"),
            "text": "quote'\"\\\n\t",
        },
    }
    if case == "create":
        event["toolName"] = "create"
    elif case == "path-alias":
        event["toolArgs"]["file_path"] = event["toolArgs"].pop("path")
    elif case == "json-args":
        event["toolArgs"] = json.dumps(event["toolArgs"])
    elif case == "anchor-target":
        event["toolArgs"]["path"] = str(anchor / "source.py")
    elif case == "foreign":
        event["toolArgs"]["path"] = str(fixture_checkout(tmp_path / "foreign") / "source.py")
    elif case == "nested":
        event["toolArgs"]["path"] = str(fixture_checkout(workspace / "nested") / "source.py")
    elif case == "batch":
        event["toolCalls"] = [{"name": "edit", "args": event["toolArgs"]}]
    elif case == "structured":
        event["toolArgs"]["edits"] = [{"path": "other.py"}]
    elif case == "drive-relative":
        event["toolArgs"]["path"] = workspace.drive + "source.py"
    if case.startswith("batch-"):
        first = {"name": "create", "args": event["toolArgs"]}
        last = {"name": "edit", "args": {"path": "second.py"}}
        event["toolCalls"] = [first, {"name": "view", "args": {"path": "read.txt"}}, last]
        # A top-level read must not hide a later write or supply its arguments.
        event["toolName"] = "view"
        event["toolArgs"] = {"path": "ignored.py"}
        event["tool_input"] = {"path": "efforts/ignored.md"}
        if case == "batch-json-args":
            last["args"] = json.dumps(last["args"])
        elif case == "batch-protected":
            last["args"]["path"] = "efforts/private.md"
        elif case == "batch-foreign":
            last["args"]["path"] = str(fixture_checkout(tmp_path / "foreign") / "source.py")
        elif case == "batch-multiple-targets":
            last["args"]["file_path"] = "efforts/private.md"
        elif case == "batch-structured":
            last["args"]["edits"] = [{"path": "efforts/private.md"}]
        elif case == "batch-missing-args":
            last.pop("args")
        elif case == "batch-null-args":
            last["args"] = None
        elif case == "batch-extra-key":
            last["cwd"] = str(workspace)
        elif case == "batch-unknown":
            last["name"] = "unknown"
        elif case == "batch-malformed":
            event["toolCalls"][-1] = None
        elif case == "batch-empty":
            event["toolCalls"] = []
        elif case == "batch-not-list":
            event["toolCalls"] = {"name": "edit", "args": {"path": "source.py"}}
        elif case in {"batch-limit", "batch-oversized"}:
            event["toolCalls"] = [first] * (128 if case == "batch-limit" else 129)
        elif case == "batch-read-only":
            event["toolCalls"] = [{"name": "view", "args": {}}]
        elif case == "batch-working-directory":
            event["workingDirectory"] = event.pop("cwd")
    env = environment(tmp_path, workspace)
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_WORKTREES_PAYLOAD_ROOT"):
        monkeypatch.delenv(key, raising=False)
    for key in ("HOME", "USERPROFILE", "AGENT_HOME"):
        monkeypatch.setenv(key, str(tmp_path))
    plugin = tmp_path / "payload"
    if cell:
        from types import SimpleNamespace
        import test_registry_paths as fixtures

        native_run = subprocess.run
        monkeypatch.setattr(fixtures, "subprocess", SimpleNamespace(
            run=lambda *args, **kwargs: native_run(
                *args, **(kwargs | no_window_kwargs() | {"timeout": 10}),
            ),
        ))
        plugin = fixtures._payload(tmp_path, "selected")
        with (registry / "repos.yaml").open("a", encoding="utf-8") as output:
            output.write('    remote: "https://github.com/example/project.git"\n')
        context, selected_registry = fixtures._stamp(
            tmp_path / "durable", plugin, marketplace="selected", repository="example/selected",
        )
        for name in ("projects.yaml", "repos.yaml"):
            (selected_registry / name).write_bytes((registry / name).read_bytes())
        registry = selected_registry
        fixtures._select(monkeypatch, context, plugin)
        from agent_worktrees import project_state

        project_state.ensure_project_state("example")
        env.update(
            COPILOT_EXTENSIONS_CONTEXT=str(context), AGENT_WORKTREES_PAYLOAD_ROOT=str(plugin),
            COPILOT_PLUGIN_ROOT=str(plugin),
        )
    if case == "delegated":
        from agent_worktrees import related

        related.write_related(workspace, related.RelatedConfig(
            related={"example": related.RelatedEntry(name="example", delegate="agent-bridge")},
        ))
    assert guarded_anchors.refresh([workspace], registry_root=registry)["published"] == 1
    reader = load("guarded_anchor_map")
    facts = reader.read_map(workspace, registry)
    assert facts.status == "ready" and not facts.posture_errors
    if case == "personal-state-control-space":
        config.write_bytes(b"default_branch: main\n\x1cstateless: true\n")
    assert load("managed_session").admitted(event, home=tmp_path)
    try:
        guard_events = load("tool_call_forms").tool_events(event)
    except ValueError:
        guard_events = []
    decisions = [
        load(name).decide(guard_event, env=env, home=tmp_path)
        for guard_event in guard_events
        for name in ("statelessness_guard", "cross_repo_guard", "anchor_write_guard", "pr_supersede_guard")
    ]
    assert any(value and value.get("permissionDecision") == "deny" for value in decisions) is (
        case.startswith("personal-state") or case in {
            "anchor-cwd", "anchor-target", "delegated",
            "batch-protected", "batch-multiple-targets", "batch-structured",
        }
    ), decisions
    map_path = reader.map_path(workspace, registry)
    if case == "ordinary-unrelated-inventory":
        value = json.loads(map_path.read_text(encoding="utf-8"))
        for index in range(128):
            root = f"z:\\inventory-{index}"
            value["postures"].append({
                "name": f"inventory-{index}", "delegate": "none", "path": root,
                "posture": "worktree", "elevated": False,
                "identity": {"root": root, "git_dir": root + "\\.git", "common_dir": root + "\\.git"},
            })
        map_path.write_text(json.dumps(value), encoding="utf-8")
        assert reader.read_map(workspace, registry).status == "ready"
        assert load("cross_repo_guard").decide(event, env=env, home=tmp_path) is None
    elif case == "missing-map":
        map_path.unlink()
    elif case in {
        "partial-map", "wrong-scope", "array-schema", "invalid-elevation", "noncanonical-map", "dot-segment-map",
    }:
        value = json.loads(map_path.read_text(encoding="utf-8"))
        if case == "partial-map":
            value["posture_authority"] = "partial"
        elif case == "wrong-scope":
            value["source_root"] = str(anchor)
        elif case == "array-schema":
            value["schema"] = [value["schema"]]
        elif case == "invalid-elevation":
            value["postures"][0]["elevated"] = 0
        elif case == "noncanonical-map":
            value["postures"][0]["identity"]["common_dir"] = str(anchor / ".git").upper()
        else:
            value["postures"][0]["identity"]["common_dir"] += "\\."
        map_path.write_text(json.dumps(value), encoding="utf-8")
    elif case == "oversized-config":
        config.write_text("#" * 16385, encoding="utf-8")
    elif case == "git-pointer-aba":
        alternate = admin.with_name("example-worktrex")
        shutil.copytree(admin, alternate)
        marker = workspace / ".git"
        before = marker.stat()
        text = marker.read_text(encoding="utf-8")
        marker.write_text(text.replace("example-worktree", "example-worktrex"), encoding="utf-8")
        os.utime(marker, ns=(before.st_atime_ns, before.st_mtime_ns))

    scripts = plugin / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    if case in {"real-policy", "batch-real-policy"}:
        for path in SCRIPTS.glob("*.py"):
            shutil.copyfile(path, scripts / path.name)
    for name in (
        "pre-tool-hook.ps1", "managed-session.ps1", "hook-read-policy.json",
        "statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py",
        "pr_supersede_guard.py", "guarded_anchor_map.py", "tool_call_forms.py",
    ):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    drift = {
        "guard-drift": "anchor_write_guard.py", "reader-drift": "guarded_anchor_map.py",
        "forms-drift": "tool_call_forms.py",
    }.get(case)
    if drift:
        with (scripts / drift).open("a", encoding="utf-8") as output:
            output.write("\n# changed source\n")
    elif case == "missing-dependencies":
        policy = scripts / "hook-read-policy.json"
        value = json.loads(policy.read_text(encoding="utf-8"))
        value.pop("nativeWriteDependencies", None)
        policy.write_text(json.dumps(value), encoding="utf-8")
    observed = tmp_path / "policy-input"
    (scripts / "resolve-runtime.ps1").write_text(
        "$AwPy='" + sys.executable.replace("'", "''") + "'\n", encoding="utf-8",
    )
    if cell:
        (scripts / "installation-context" / "installation-context.ps1").write_text(
            "Write-Output '" + json.dumps({"pluginRoot": str(registry)}).replace("'", "''") + "'\n"
            + "$global:LASTEXITCODE = 0\n", encoding="utf-8",
        )
    if case not in {"real-policy", "batch-real-policy"}:
        (scripts / "hook_policy_process.py").write_text(
            "import base64,json,sys\nfrom pathlib import Path\n"
            + f"Path({str(observed)!r}).write_bytes(base64.b64decode(sys.stdin.readline()))\n"
            + "print(json.dumps({'permissionDecision':'deny','permissionDecisionReason':'fixture policy'}))\n",
            encoding="utf-8",
        )
    membership = tmp_path / "membership-interpreter"
    inventory_probe = tmp_path / "inventory-probe"
    native_membership = tmp_path / "native-membership"
    if case == "ordinary-unrelated-inventory":
        admission = scripts / "managed-session.ps1"
        admission.write_text(admission.read_text(encoding="utf-8").replace(
            "return $hasGitMarker",
            "[IO.File]::WriteAllText('" + str(native_membership).replace("'", "''")
            + "', $hasGitMarker.ToString()); return $hasGitMarker",
        ), encoding="utf-8")
        with (scripts / "managed-session.ps1").open("a", encoding="utf-8") as output:
            output.write(
                "\n$script:originalPreparedPath = ${function:Get-PreparedWorktreesPath}\n"
                "function Get-PreparedWorktreesPath {\n    param([object]$Path)\n"
                "    if ($Path -is [string] -and $Path.StartsWith('z:\\inventory-')) {\n"
                + "        [IO.File]::WriteAllText('" + str(inventory_probe).replace("'", "''") + "', 'called')\n"
                "        throw 'Unexpected inventory filesystem probe'\n    }\n"
                "    return & $script:originalPreparedPath $Path\n}\n",
            )
    if case == "old-admission":
        admission = scripts / "managed-session.ps1"
        admission.write_text(
            admission.read_text(encoding="utf-8").replace(
                "function Test-ManagedWorktreesSession {", "function Test-OriginalManagedWorktreesSession {",
            )
            + "\nfunction Test-ManagedWorktreesSession {\n"
            "    param([Parameter(Mandatory = $true)][string]$Raw, [ref]$Prepared)\n"
            "    return Test-OriginalManagedWorktreesSession -Raw $Raw -Prepared $Prepared\n"
            "}\n",
            encoding="utf-8",
        )
    with (scripts / "managed-session.ps1").open("a", encoding="utf-8") as output:
        output.write(
            "\nfunction Get-ManagedWorktreesPython { "
            + "[IO.File]::WriteAllText('" + str(membership).replace("'", "''") + "', 'called'); "
            + "return '" + sys.executable.replace("'", "''") + "' }\n"
        )
    env["COPILOT_PLUGIN_ROOT"] = str(plugin)
    entry = json.loads((SCRIPTS.parent / "hooks.json").read_text(encoding="utf-8"))["hooks"]["preToolUse"][0]
    raw = json.dumps(event, ensure_ascii=False, indent=2).encode("utf-8")
    started = time.monotonic()
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", entry["powershell"]],
        input=raw, capture_output=True, timeout=10,
        cwd=workspace, env=env, **no_window_kwargs(),
    )
    native_seconds = time.monotonic() - started
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert not membership.exists()
    if case == "ordinary-unrelated-inventory":
        assert native_membership.read_text() == "True"
    assert not inventory_probe.exists()
    policy_required = not (case.startswith("ordinary") or case in {
        "create", "path-alias", "json-args", "real-policy", "batch",
        "batch-mixed", "batch-json-args", "batch-limit", "batch-working-directory", "batch-real-policy",
    })
    assert observed.exists() is policy_required, result.stdout
    if policy_required:
        assert observed.read_bytes() == raw
        assert json.loads(result.stdout)["permissionDecision"] == "deny"
    else:
        assert result.stdout == b""
    if case in {"real-policy", "batch-real-policy"}:
        contract = scripts / "hook-read-policy.json"
        value = json.loads(contract.read_text(encoding="utf-8"))
        value.pop("nativeWriteDependencies")
        contract.write_text(json.dumps(value), encoding="utf-8")
        started = time.monotonic()
        fallback = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", entry["powershell"]],
            input=raw, capture_output=True, timeout=10,
            cwd=workspace, env=env, **no_window_kwargs(),
        )
        fallback_seconds = time.monotonic() - started
        assert fallback.returncode == 0 and not fallback.stderr, fallback.stdout + fallback.stderr
        assert json.loads(fallback.stdout) == {}
        protected = {**event, "toolArgs": {"path": "efforts/private.md"}}
        if case == "batch-real-policy":
            protected["toolCalls"] = [
                *event["toolCalls"][:-1],
                {"name": "edit", "args": {"path": "efforts/private.md"}},
            ]
        denied = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", entry["powershell"]],
            input=json.dumps(protected).encode("utf-8"), capture_output=True, timeout=10,
            cwd=workspace, env=env, **no_window_kwargs(),
        )
        assert denied.returncode == 0 and not denied.stderr, denied.stdout + denied.stderr
        assert json.loads(denied.stdout)["permissionDecision"] == "deny"
        assert not membership.exists()
        print(json.dumps({
            "shell": shell, "same_fixture": True, "native_entry_seconds": native_seconds,
            "real_policy_entry_seconds": fallback_seconds,
        }))


@pytest.mark.skipif(os.name != "nt", reason="Native Windows prepared cell membership")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", [
    "current", "receipt", "payload", "source-witness", "scope", "legacy-association",
    "receipt-aba", "payload-aba", "payload-selection", "missing-files",
    "original-fallback", "root-versions", "root-snapshots", "root-state", "root-run",
    "root-logs", "root-cache", "root-launchers", "root-alias", "missing-roots",
    "directory-source", "directory-ancestor", "directory-fallback", "missing-source-kind",
    "payload-junction", "receipt-payload-junction", "receipt-plugin-junction",
    "receipt-namespace-junction", "missing-declared-paths",
    "receipt-payload-fallback", "receipt-plugin-fallback", "receipt-namespace-fallback",
])
def test_prepared_cell_membership_reuses_only_complete_current_owner_observation(
    tmp_path, monkeypatch, shell, case,
):
    from types import SimpleNamespace
    import test_registry_paths as fixtures

    executable = shutil.which(shell)
    if not executable:
        if os.environ.get("AGENT_WORKTREES_REQUIRE_NATIVE_MEMBERSHIP") == "1":
            pytest.fail(f"Required native membership shell {shell} is unavailable")
        pytest.skip(f"{shell} is unavailable")
    native_run = subprocess.run
    monkeypatch.setattr(fixtures, "subprocess", SimpleNamespace(
        run=lambda *args, **kwargs: native_run(
            *args, **(kwargs | no_window_kwargs() | {"timeout": 10}),
        ),
    ))
    anchor, legacy = fixture_project(tmp_path)
    workspace = fixture_linked_worktree(tmp_path, anchor)[0]
    payload_root = fixtures._payload(tmp_path, "selected")
    shutil.copyfile(SCRIPTS / "managed-session.ps1", payload_root / "scripts" / "managed-session.ps1")
    namespace_source = tmp_path / "source-parent" / "marketplace"
    source = None
    if case.startswith("directory-"):
        namespace_source.mkdir(parents=True)
        source = {"source": "directory", "path": str(namespace_source)}
    context, registry = fixtures._stamp(
        tmp_path / "durable", payload_root, marketplace="selected", repository="example/selected",
        source=source,
    )
    if case == "root-alias":
        inner = registry / "inner"
        inner.mkdir()
        alias = registry / "alias"
        native_run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(alias), str(inner)],
            check=True, capture_output=True, timeout=10, **no_window_kwargs(),
        )
        receipt = json.loads(context.read_text())
        receipt["roots"]["versions"] = "alias/versions"
        context.write_text(json.dumps(receipt))
    receipt_alias = tmp_path / "receipt-alias"
    if case.startswith(("receipt-payload-", "receipt-plugin-", "receipt-namespace-")):
        target = (
            payload_root if case.startswith("receipt-payload-") else
            registry if case.startswith("receipt-plugin-") else registry.parent.parent
        )
        native_run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(receipt_alias), str(target)],
            check=True, capture_output=True, timeout=10, **no_window_kwargs(),
        )
        receipt = json.loads(context.read_text())
        if case.startswith("receipt-payload-"):
            receipt["payload"]["root"] = str(receipt_alias)
        elif case.startswith("receipt-plugin-"):
            receipt["pluginRoot"] = str(receipt_alias)
        else:
            receipt["namespaceReceipt"] = str(receipt_alias / "namespace.json")
        context.write_text(json.dumps(receipt))
    for name in ("projects.yaml", "repos.yaml"):
        (registry / name).write_text((legacy / name).read_text())
    selected_payload = payload_root
    if case == "payload-junction":
        selected_payload = tmp_path / "payload-alias"
        native_run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(selected_payload), str(payload_root)],
            check=True, capture_output=True, timeout=10, **no_window_kwargs(),
        )
    fixtures._select(monkeypatch, context, selected_payload)
    session = tmp_path / ".copilot" / "session-state" / "prepared-cell"
    session.mkdir(parents=True)
    event = {"sessionId": "prepared-cell", "cwd": str(workspace)}
    module = load("managed_session")
    resolution_errors = []
    original_resolve = module.resolve_registry_context

    def resolve_with_diagnostics(*args, **kwargs):
        try:
            return original_resolve(*args, **kwargs)
        except Exception as error:
            resolution_errors.append(repr(error))
            raise

    monkeypatch.setattr(module, "resolve_registry_context", resolve_with_diagnostics)
    observation = module.inspect(event, home=tmp_path)
    assert observation["admitted"], (resolution_errors, observation)
    marker = session / "agent-worktrees" / "managed-session.json"
    value = json.loads(marker.read_text())
    if case == "receipt":
        context.write_text(context.read_text() + "\n")
    elif case == "payload":
        manifest = payload_root / "plugin.json"
        manifest.write_text(manifest.read_text() + "\n")
    elif case == "source-witness":
        namespace = json.loads(context.read_text())["namespaceReceipt"]
        value["sources"].pop(namespace)
        value["inputs"].pop(namespace)
        marker.write_text(json.dumps(value))
    elif case == "scope":
        value["scope"]["registry"] = str(legacy)
        marker.write_text(json.dumps(value))
    elif case == "legacy-association":
        value.pop("installation", None)
        marker.write_text(json.dumps(value))
    elif case in {"receipt-aba", "payload-aba"}:
        path = context if case == "receipt-aba" else payload_root / "plugin.json"
        before = path.stat()
        path.write_bytes(path.read_bytes().replace(b"{", b"[", 1))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert path.stat().st_size == before.st_size
        assert path.stat().st_mtime_ns == before.st_mtime_ns
        assert not load("managed_session").admitted(event, home=tmp_path)
    elif case == "missing-files":
        value["installation"].pop("files")
        marker.write_text(json.dumps(value))
    elif case == "missing-roots":
        value["installation"].pop("roots")
        marker.write_text(json.dumps(value))
    elif case == "missing-source-kind":
        value["installation"].pop("sourceKind")
        marker.write_text(json.dumps(value))
    elif case == "missing-declared-paths":
        value["installation"].pop("declaredPaths")
        marker.write_text(json.dumps(value))
    elif case == "payload-junction":
        other = fixtures._payload(tmp_path, "other")
        selected_payload.rmdir()
        native_run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(selected_payload), str(other)],
            check=True, capture_output=True, timeout=10, **no_window_kwargs(),
        )
        assert not module.admitted(event, home=tmp_path)
    elif case in {"receipt-payload-junction", "receipt-plugin-junction", "receipt-namespace-junction"}:
        assert "installation" not in value
        other = fixtures._payload(tmp_path, "other") if case == "receipt-payload-junction" else (
            tmp_path / "other"
        )
        other.mkdir(exist_ok=True)
        receipt_alias.rmdir()
        native_run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(receipt_alias), str(other)],
            check=True, capture_output=True, timeout=10, **no_window_kwargs(),
        )
        assert not module.admitted(event, home=tmp_path)
    elif case in {
        "original-fallback", "directory-fallback", "receipt-payload-fallback",
        "receipt-plugin-fallback", "receipt-namespace-fallback",
    }:
        if case.startswith("receipt-"):
            assert "installation" not in value
        value.pop("installation", None)
        marker.write_text(json.dumps(value))
        for name in ("managed_session.py", "execution_platform.py", "managed_project_identity.py",
                     "remote_identity.py", "guarded_anchor_map.py", "hook_policy_process.py"):
            shutil.copyfile(SCRIPTS / name, payload_root / "scripts" / name)
    elif case.startswith("directory-"):
        path = namespace_source if case == "directory-source" else namespace_source.parent
        shutil.rmtree(path)
        external = tmp_path / "external"
        (external / "marketplace").mkdir(parents=True)
        native_run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(path), str(external)],
            check=True, capture_output=True, timeout=10, **no_window_kwargs(),
        )
        assert not module.admitted(event, home=tmp_path)
    elif case.startswith("root-"):
        path = registry / (case.removeprefix("root-") if case != "root-alias" else "alias")
        if case == "root-alias":
            path.rmdir()
        elif path.exists():
            shutil.rmtree(path)
        external = tmp_path / "external"
        external.mkdir()
        native_run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(path), str(external)],
            check=True, capture_output=True, timeout=10, **no_window_kwargs(),
        )
        assert not module.admitted(event, home=tmp_path)
    env = environment(tmp_path, workspace)
    env.update(
        COPILOT_EXTENSIONS_CONTEXT=str(context), COPILOT_PLUGIN_ROOT=str(payload_root),
        AGENT_WORKTREES_PAYLOAD_ROOT=str(selected_payload),
        PREPARED_MEMBERSHIP_SCRIPT=str(payload_root / "scripts" / "managed-session.ps1"),
    )
    if case == "payload-selection":
        env["COPILOT_PLUGIN_ROOT"] = str(legacy)
    command = ". $env:PREPARED_MEMBERSHIP_SCRIPT; "
    fallback = case in {
        "original-fallback", "directory-fallback", "receipt-payload-fallback",
        "receipt-plugin-fallback", "receipt-namespace-fallback",
    }
    if fallback:
        env["AGENT_WORKTREES_RESIDENT_PRE_TOOL_PYTHON"] = sys.executable
    else:
        command += "function Get-ManagedWorktreesPython { throw 'interpreter lookup is forbidden' }; "
    command += (
        "[Console]::Out.Write((Test-ManagedWorktreesSession -Raw ([Console]::In.ReadToEnd())).ToString())"
    )
    result = native_run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", command],
        input=json.dumps(event), text=True, capture_output=True, timeout=10,
        cwd=workspace, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert result.stdout == ("True" if case == "current" or fallback else "False")
    if case == "original-fallback":
        assert len(json.loads(marker.read_text())["installation"]["files"]) == 3
    assert not (legacy / "cache").exists()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows prepared membership")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("damage", [
    "withdraw", "nested", "git-link", "session", "registry", "context",
    "missing-witness", "malformed", "oversize", "wrong-type", "workspace-unicode",
    "session-array", "cwd-array", "working-directory-array",
])
def test_prepared_native_membership_rejects_changed_or_unavailable_inputs(
    tmp_path, monkeypatch, shell, damage,
):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, registry = fixture_project(tmp_path)
    workspace, admin = fixture_linked_worktree(tmp_path, anchor)
    nested = workspace / "src"
    nested.mkdir()
    session = tmp_path / ".copilot" / "session-state" / "prepared"
    session.mkdir(parents=True)
    payload = {"sessionId": "prepared", "cwd": str(anchor if damage == "workspace-unicode" else nested)}
    assert module.admitted(payload, home=tmp_path)
    marker = session / "agent-worktrees" / "managed-session.json"
    env = environment(tmp_path, nested)
    if damage == "withdraw":
        (registry / "projects.yaml").write_text("projects: {}\n")
    elif damage == "nested":
        fixture_checkout(nested)
    elif damage == "git-link":
        (admin / "gitdir").write_text(str(tmp_path / "foreign" / ".git") + "\n")
    elif damage == "session":
        payload["sessionId"] = "another"
    elif damage == "registry":
        env["AGENT_HOME"] = str(tmp_path / "another")
    elif damage == "context":
        env["COPILOT_EXTENSIONS_CONTEXT"] = str(tmp_path / "install.json")
    elif damage == "missing-witness":
        value = json.loads(marker.read_text())
        del value["inputs"][os.path.normcase(str(workspace / ".git"))]
        marker.write_text(json.dumps(value))
    elif damage == "malformed":
        marker.write_text("{")
    elif damage == "oversize":
        marker.write_bytes(b" " * (1024 * 1024 + 1))
    elif damage == "wrong-type":
        value = json.loads(marker.read_text())
        value["scope"]["session"] = ["prepared"]
        marker.write_text(json.dumps(value))
    elif damage == "workspace-unicode":
        other = fixture_checkout(tmp_path / "anchor-e\u0301")
        payload["cwd"] = str(other)
        assert not module.admitted(payload, home=tmp_path)
    elif damage == "session-array":
        payload["sessionId"] = ["prepared"]
        assert not module.admitted(payload, home=tmp_path)
    elif damage == "cwd-array":
        payload["cwd"] = [str(nested)]
        assert not module.admitted(payload, home=tmp_path)
    elif damage == "working-directory-array":
        payload["workingDirectory"] = [payload.pop("cwd")]
        assert not module.admitted(payload, home=tmp_path)
    env["PREPARED_MEMBERSHIP_SCRIPT"] = str(SCRIPTS / "managed-session.ps1")
    command = (
        ". $env:PREPARED_MEMBERSHIP_SCRIPT; "
        "$raw = [Console]::In.ReadToEnd(); "
        "[Console]::Out.Write((Test-PreparedManagedWorktreesSession -Raw $raw).ToString())"
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", command],
        input=json.dumps(payload), text=True, capture_output=True, timeout=10,
        cwd=nested, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "False", result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["preToolUse", "postToolUse", "sessionStart", "sessionEnd"])
@pytest.mark.parametrize("repo_class", ["worktree", "reference", None])
def test_real_python_entry_is_inert_for_registry_only_repo(kind, repo_class, tmp_path):
    anchor, registry = fixture_project(tmp_path)
    (registry / "projects.yaml").write_text("projects: {}\n")
    repos = registry / "repos.yaml"
    if repo_class is None:
        repos.unlink()
        (registry / "projects.yaml").unlink()
    else:
        repos.write_text(repos.read_text().replace("class: worktree", "class: " + repo_class))
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "hook_client.py"), kind],
        input=json.dumps({"sessionId": "unmanaged", "cwd": str(anchor), "toolName": "edit",
                          "toolArgs": {"path": str(anchor / "source.py")}}),
        text=True, capture_output=True, timeout=10, cwd=anchor,
        env=environment(tmp_path, anchor), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in {"", "{}"}
    assert not result.stderr
    assert not (tmp_path / ".copilot").exists()
    assert not (registry / "cache").exists()


def test_real_managed_pretool_preserves_anchor_write_protection(tmp_path):
    anchor, _ = fixture_project(tmp_path)
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "hook_client.py"), "preToolUse"],
        input=json.dumps({"sessionId": "managed", "cwd": str(anchor), "toolName": "edit",
                          "toolArgs": {"path": str(anchor / "source.py")}}),
        text=True, capture_output=True, timeout=10, cwd=anchor,
        env=environment(tmp_path, anchor), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["permissionDecision"] == "deny"
    assert "anchor-write-guard" in result.stdout


@pytest.mark.parametrize("entry", ["python", "powershell"])
@pytest.mark.timeout(120)
def test_native_batch_entry_and_live_scoped_grants(tmp_path, record_testsuite_property, entry):
    anchor, registry = fixture_project(tmp_path)
    caller, _ = fixture_linked_worktree(tmp_path, anchor)
    reader = load("guarded_anchor_map")
    targets = [fixture_checkout(tmp_path / name) for name in ("first", "second")]
    rows = [{
        "name": target.name, "delegate": "agent-bridge", "path": reader.canonical(target),
        "locus": None, "identity": reader.repository_identity(target),
    } for target in targets]
    for index in range(98):
        path = reader.canonical(tmp_path / f"inventory-{index}")
        rows.append({
            "name": f"inventory-{index}", "delegate": "agent-bridge", "path": path,
            "locus": None,
            "identity": {"root": path, "git_dir": path + os.sep + ".git",
                         "common_dir": path + os.sep + ".git"},
        })
    path = reader.map_path(caller, registry)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema": "agent-worktrees.guarded-anchors", "version": 1,
        "source_root": reader.canonical(caller),
        "source_identity": reader.repository_identity(caller),
        "registry_root": reader.canonical(registry), "inventory_authority": "complete",
        "generation": "native-entry-fixture", "roots": rows,
    }))
    env = environment(tmp_path, caller)
    for key in ("CROSS_REPO_GUARD", "CROSS_REPO_GUARD_MODE"):
        env.pop(key, None)
    command = [sys.executable, "-B", str(SCRIPTS / "hook_client.py"), "preToolUse"]
    if entry == "powershell":
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if not powershell:
            pytest.skip("PowerShell is unavailable")
        payload = tmp_path / "payload"
        shutil.copytree(SCRIPTS, payload / "scripts")
        (payload / "scripts" / "resolve-runtime.ps1").write_text(
            "param([string]$TraceMode)\n$AwPy = '" + sys.executable.replace("'", "''") + "'\n",
            encoding="utf-8-sig",
        )
        env["COPILOT_PLUGIN_ROOT"] = str(payload)
        hooks = json.loads((SCRIPTS.parent / "hooks.json").read_text())
        command = [powershell, "-NoProfile", "-NonInteractive", "-Command",
                   hooks["hooks"]["preToolUse"][0]["powershell"]]
    session = tmp_path / ".copilot" / "session-state" / "native-entry"
    session.mkdir(parents=True)
    event = {"sessionId": "native-entry", "cwd": str(caller), "toolCalls": [
        {"id": "read", "name": "view", "args": {"path": str(targets[0] / "source.py")}},
        {"id": "write", "name": "apply_patch", "args": (
            f"*** Begin Patch\n*** Update File: {targets[0] / 'source.py'}\n*** End Patch\n"
        )},
    ]}
    timings = []

    def invoke(value):
        started = time.monotonic()
        result = subprocess.run(
            command, input=json.dumps(value), text=True, capture_output=True,
            timeout=15, cwd=caller, env=env, **no_window_kwargs(),
        )
        timings.append(round((time.monotonic() - started) * 1000, 2))
        print(f"{entry} {timings[-1]} ms: {result.stdout} {result.stderr}")
        assert result.returncode == 0, result.stderr
        decision = json.loads(result.stdout or "{}")
        assert "could not evaluate" not in decision.get("additionalContext", ""), result.stderr
        return decision

    assert invoke({**event, "cwd": str(tmp_path)}).get("permissionDecision") is None
    denied = invoke(event)
    assert denied["permissionDecision"] == "deny"
    assert "allow-edits first" in denied["permissionDecisionReason"]
    assert (session / "agent-worktrees" / "managed-session.json").is_file()
    grants = registry / "allow-edits.json"
    grants.write_text(json.dumps({
        "grants": {"first": {"expires_at_ms": (time.time() + 600) * 1000}},
    }))
    assert invoke(event).get("permissionDecision") is None
    mixed = {**event, "toolCalls": [*event["toolCalls"], {
        "id": "other", "name": "apply_patch", "args": (
            f"*** Begin Patch\n*** Delete File: {targets[1] / 'source.py'}\n*** End Patch\n"
        ),
    }]}
    assert "allow-edits second" in invoke(mixed)["permissionDecisionReason"]
    grants.write_text(json.dumps({
        "grants": {"first": {"expires_at_ms": (time.time() - 10) * 1000}},
    }))
    assert invoke(event)["permissionDecision"] == "deny"
    grants.unlink()
    assert invoke(event)["permissionDecision"] == "deny"
    assert invoke({**event, "toolCalls": event["toolCalls"][:1]}).get("permissionDecision") is None
    record_testsuite_property(f"native_batch_entry_ms:{entry}", json.dumps(timings))
    record_testsuite_property(f"native_batch_inventory:{entry}", len(rows))


def test_invalid_query_does_not_reuse_ambient_admission(tmp_path):
    anchor, registry = fixture_project(tmp_path)
    env = environment(tmp_path, anchor)
    env["COPILOT_AGENT_SESSION_ID"] = "managed"
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "managed_session.py")],
        input="{", text=True, capture_output=True, timeout=5, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["admitted"] is False
    assert not (registry / "cache").exists()


@pytest.mark.parametrize("kind", ["preToolUse", "postToolUse", "sessionStart", "sessionEnd"])
def test_real_powershell_hook_does_not_activate_unmanaged_repo(kind, tmp_path):
    powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("PowerShell is unavailable")
    anchor, registry = fixture_project(tmp_path)
    (registry / "projects.yaml").write_text("projects: {}\n")
    hooks = json.loads((SCRIPTS.parent / "hooks.json").read_text())
    command = hooks["hooks"][kind][0]["powershell"]
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        input=json.dumps({"sessionId": "unmanaged", "cwd": str(anchor), "toolName": "edit",
                          "toolArgs": {"path": str(anchor / "source.py")}}),
        text=True, capture_output=True, timeout=15, cwd=anchor,
        env=environment(tmp_path, anchor), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in {"", "{}"}, result.stdout
    assert not result.stderr
    assert not (tmp_path / ".copilot").exists()


@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe", "bash"])
@pytest.mark.parametrize("case", ["unmanaged", "managed", "missing-helper"])
def test_session_start_gates_older_client_before_fallback(shell, case, tmp_path, native_bash):
    executable = native_bash if shell == "bash" else shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    anchor, registry = fixture_project(tmp_path)
    if case == "unmanaged":
        (registry / "projects.yaml").write_text("projects: {}\n")
    payload = tmp_path / "payload"
    shutil.copytree(SCRIPTS, payload / "scripts")
    (payload / "scripts" / "hook_client.py").unlink()
    if case == "missing-helper":
        (payload / "scripts" / "managed_session.py").unlink()
    legacy = registry / "bin"
    legacy.mkdir()
    marker = tmp_path / "older-client-ran"
    (legacy / "hook_client.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\nprint('{}')\n"
    )
    env = environment(tmp_path, anchor)
    env.update(COPILOT_PLUGIN_ROOT=str(payload), ADMISSION_TEST_PYTHON=sys.executable)
    hooks = json.loads((SCRIPTS.parent / "hooks.json").read_text())
    command = hooks["hooks"]["sessionStart"][0]["bash" if shell == "bash" else "powershell"]
    if shell == "bash":
        prefix = (
            'export HOME="$(cygpath -au "$HOME")"; '
            'export COPILOT_PLUGIN_ROOT="$(cygpath -au "$COPILOT_PLUGIN_ROOT")"; '
            'ADMISSION_TEST_PYTHON="$(cygpath -au "$ADMISSION_TEST_PYTHON")"; '
        ) if os.name == "nt" else ""
        command = prefix + 'python3() { "$ADMISSION_TEST_PYTHON" "$@"; }; ' + command
        args = [executable, "-c", command]
    else:
        args = [executable, "-NoProfile", "-NonInteractive", "-Command", command]
    result = subprocess.run(
        args, input=json.dumps({"sessionId": "older-client", "cwd": str(anchor)}),
        text=True, capture_output=True, timeout=15, cwd=anchor, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "{}", result.stdout
    assert not result.stderr
    assert marker.exists() is (case == "managed")


@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
def test_session_start_forwards_unicode_membership_query(shell, tmp_path):
    powershell = shutil.which(shell)
    if not powershell:
        pytest.skip("PowerShell is unavailable")
    anchor, _ = fixture_project(tmp_path)
    payload = tmp_path / "payload"
    shutil.copytree(SCRIPTS, payload / "scripts")
    (payload / "scripts" / "hook_client.py").write_text(
        "import json,sys\nfrom managed_session import admitted\n"
        "assert admitted(json.load(sys.stdin))\nprint('{}')\n"
    )
    env = environment(tmp_path, anchor)
    env["COPILOT_PLUGIN_ROOT"] = str(payload)
    hooks = json.loads((SCRIPTS.parent / "hooks.json").read_text())
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command",
         hooks["hooks"]["sessionStart"][0]["powershell"]],
        input=json.dumps({"sessionId": "managed", "cwd": str(anchor)}, ensure_ascii=False),
        text=True, capture_output=True, encoding="utf-8", timeout=10,
        cwd=anchor, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert json.loads(result.stdout) == {}


@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("opaque", [
    {"Name": 1, "name": 2},
    {"": {"Name": 1, "name": 2}},
    {"__type": None, "Name": 1, "name": 2, "": 3},
    {"__type": "opaque", "Name": 1, "name": 2, "": 3},
])
def test_real_admission_accepts_opaque_json_keys(tmp_path, shell, opaque):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    anchor, registry = fixture_project(tmp_path)
    if os.name == "nt":
        (registry / "repos.yaml").write_text(json.dumps({
            "repos": {"example": {"windows": str(anchor).upper()}},
        }))
    event = {
        "sessionId": "json-keys", "cwd": str(anchor), "CWD": str(tmp_path),
        "toolArgs": opaque, "toolResult": opaque, "": "ignored", "Keys": "opaque",
    }
    helper = str(SCRIPTS / "managed-session.ps1").replace("'", "''")
    env = environment(tmp_path, anchor)
    env["AGENT_WORKTREES_RESIDENT_PRE_TOOL_PYTHON"] = sys.executable
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command",
         f". '{helper}'; Test-ManagedWorktreesSession ([Console]::In.ReadToEnd())"],
        input=json.dumps(event), capture_output=True, text=True, timeout=10,
        env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "True", result.stdout + result.stderr
    assert not result.stderr


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(os.name != "nt", reason="Native Windows admission serialization")
@pytest.mark.parametrize("shell,old_engine", [
    ("pwsh", False), ("pwsh", True), ("powershell.exe", False),
])
def test_admission_query_serialization_cold_fallback(tmp_path, shell, old_engine, capsys):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    samples = []
    for index, variant in enumerate(("cmdlet", "native", "cmdlet")):
        home = tmp_path / str(index)
        home.mkdir()
        anchor, registry = fixture_project(home)
        session = home / ".copilot" / "session-state" / "query"
        session.mkdir(parents=True)
        scripts = home / "payload" / "scripts"
        shutil.copytree(SCRIPTS, scripts)
        managed = scripts / "managed-session.ps1"
        managed.write_bytes(managed.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        source = managed.read_text(encoding="utf-8").replace("\r\n", "\n")
        original = "$frame = ConvertTo-Json -InputObject $query -Compress"
        candidate = (
            "$frame = if ($PSVersionTable.PSVersion.Major -ge 6) {\n"
            "            ConvertTo-PreparedWorktreesJson -InputObject $query -Depth 2\n"
            "        } else { ConvertTo-Json -InputObject $query -Compress }"
        )
        assert source.count(candidate) == 1
        if variant == "cmdlet":
            source = source.replace(candidate, original)
        for token in ("        $frame =", "        $python = Get-ManagedWorktreesPython"):
            assert source.count(token) == 1
        source = source.replace(
            "        $frame =",
            "        $script:serializationClock = [Diagnostics.Stopwatch]::StartNew()\n        $frame =",
        )
        source = source.replace(
            "        $python = Get-ManagedWorktreesPython",
            "        $script:serializationClock.Stop()\n"
            "        $script:queryFrame = $frame\n        $python = Get-ManagedWorktreesPython",
        )
        if old_engine:
            source = source.replace(
                "'Microsoft.PowerShell.Commands.JsonObject+ConvertToJsonContext'",
                "'UnavailablePreparedJsonContext'",
            )
        (scripts / "managed-session.ps1").write_text(source, encoding="utf-8")
        query = {"sessionId": "query", "cwd": str(anchor), "workingDirectory": str(home)}
        event = {
            **query, "CWD": str(home),
            "toolName": "edit", "toolArgs": {"text": "quote'\"\\\n\t\u00e9\U0001f680"},
            "__type": "opaque", "Keys": [], "": {"discard": True},
        }
        driver = home / "admission-comparison.ps1"
        driver.write_text(
            "$ErrorActionPreference='Stop'\n"
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false,$true)\n"
            ". $env:PREPARED_MEMBERSHIP_SCRIPT\n"
            "$script:jsonCalls=0; $script:pythonCalls=0\n"
            + r"""
function ConvertTo-Json {
    param([object]$InputObject, [switch]$Compress, [int]$Depth=2)
    $script:jsonCalls++
    & ($ExecutionContext.InvokeCommand.GetCommand('ConvertTo-Json',
        [Management.Automation.CommandTypes]::Cmdlet)) @PSBoundParameters
}
$script:originalPython=(Get-Command Get-ManagedWorktreesPython).ScriptBlock
function Get-ManagedWorktreesPython {
    $script:pythonCalls++
    & $script:originalPython
}
$raw=[Console]::In.ReadToEnd()
$clock=[Diagnostics.Stopwatch]::StartNew()
if (-not (Test-ManagedWorktreesSession $raw)) { throw 'Cold admission failed' }
$clock.Stop()
[Console]::Error.WriteLine(('admission_seconds='+$clock.Elapsed.TotalSeconds))
[Console]::Error.WriteLine(('serialization_seconds='+$script:serializationClock.Elapsed.TotalSeconds))
if ($script:pythonCalls -ne 1) { throw 'Cold admission did not use the real helper' }
if ($script:jsonCalls -ne [int]$env:EXPECTED_JSON_CALLS) { throw 'Unexpected JSON cmdlet count' }
if ($script:queryFrame.Contains("`r") -or $script:queryFrame.Contains("`n")) { throw 'Query framing is not compact' }
[Console]::Out.WriteLine($script:queryFrame)
$script:queryFrame=$null
if (-not (Test-ManagedWorktreesSession $raw)) { throw 'Warm admission failed' }
if ($script:pythonCalls -ne 1) { throw 'Warm admission rediscovered membership' }
if ($null -ne $script:queryFrame) { throw 'Warm admission serialized another query' }
""",
            encoding="utf-8",
        )
        env = environment(home, anchor)
        env.update(
            COPILOT_PLUGIN_ROOT=str(scripts.parent),
            PREPARED_MEMBERSHIP_SCRIPT=str(scripts / "managed-session.ps1"),
            AGENT_WORKTREES_RESIDENT_PRE_TOOL_PYTHON=sys.executable,
            EXPECTED_JSON_CALLS=str(int(variant == "cmdlet" or old_engine or shell == "powershell.exe")),
        )
        assert not (registry / "cache").exists()
        started = time.perf_counter()
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-File", str(driver)],
            input=json.dumps(event), capture_output=True, text=True, encoding="utf-8",
            timeout=10, cwd=anchor, env=env, **no_window_kwargs(),
        )
        elapsed = time.perf_counter() - started
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout) == query
        assert (registry / "cache" / "managed-sessions").is_dir()
        assert result.stderr.startswith("admission_seconds="), result.stderr
        timings = dict(line.split("=") for line in result.stderr.splitlines())
        assert set(timings) == {"admission_seconds", "serialization_seconds"}
        samples.append({
            "variant": variant, "process_seconds": elapsed,
            **{key: float(value) for key, value in timings.items()},
        })
    with capsys.disabled():
        print(json.dumps({"admission_query_serialization": {
            "shell": shell, "old_engine": old_engine, "samples": samples,
        }}))


@pytest.mark.skipif(os.name != "nt", reason="Windows runtime-only interpreter discovery")
@pytest.mark.parametrize("consumer", ["powershell.exe", "pwsh", "node"])
@pytest.mark.parametrize("cell", [False, True])
def test_admission_resolves_healthy_selected_runtime(
    tmp_path, monkeypatch, record_testsuite_property, consumer, cell,
):
    import venv
    from types import SimpleNamespace
    import test_registry_paths as fixtures

    executable = shutil.which(consumer)
    if not executable:
        pytest.skip(f"{consumer} is unavailable")
    anchor, legacy = fixture_project(tmp_path)
    payload = tmp_path / "payload"
    shutil.copytree(SCRIPTS, payload / "scripts")
    (payload / "plugin.json").write_text('{"name":"agent-worktrees","version":"0.0.1-dev1"}')
    env = environment(tmp_path, anchor)
    env["COPILOT_PLUGIN_ROOT"] = str(payload)
    runtime = legacy
    if cell:
        native_run = subprocess.run
        monkeypatch.setattr(fixtures, "subprocess", SimpleNamespace(
            run=lambda *args, **kwargs: native_run(*args, **(kwargs | no_window_kwargs())),
        ))
        context, runtime = fixtures._stamp(
            tmp_path / "durable", payload, marketplace="runtime-only", repository="example/runtime-only",
        )
        for name in ("projects.yaml", "repos.yaml"):
            shutil.copyfile(legacy / name, runtime / name)
        env["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
        env["AGENT_WORKTREES_PAYLOAD_ROOT"] = str(payload)
        (legacy / "projects.yaml").write_text("projects: {}\n")
    slot = runtime / "versions" / "0.0.1"
    venv.EnvBuilder(with_pip=False).create(slot / ".venv")
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "0.0.1", "completed_at": "2026-01-01T00:00:00Z", "pid": 1,
    }))
    (runtime / "current-version").write_text("0.0.1\n")
    env["PATH"] = str(Path(env["SYSTEMROOT"]) / "System32")
    assert shutil.which("python", path=env["PATH"]) is None
    env["FIXTURE_EXPECTED_PYTHON"] = str(slot / ".venv" / "Scripts" / "python.exe")
    if consumer == "node":
        driver = tmp_path / "runtime-only.mjs"
        driver.write_text("""
import assert from "node:assert/strict";
import childProcess from "node:child_process";
import {syncBuiltinESMExports} from "node:module";
import {pathToFileURL} from "node:url";
const [module, scriptsRoot, workingDirectory] = process.argv.slice(2);
let calls = 0;
const failures = [];
const original = childProcess.execFile;
childProcess.execFile = (...args) => {
  calls++;
  const callback = args.pop();
  return original(...args, (error, stdout, stderr) => {
    if (error) failures.push({code: error.code, killed: error.killed, signal: error.signal});
    callback(error, stdout, stderr);
  });
};
syncBuiltinESMExports();
const {createManagedSessionAdmission} = await import(pathToFileURL(module));
const check = createManagedSessionAdmission({scriptsRoot});
for (const sessionId of ["runtime-only", "runtime-second"]) {
  const input = {sessionId, workingDirectory};
  assert.deepEqual(await Promise.all([check(input), check(input)]), [true, true],
    JSON.stringify({sessionId, failures}));
  const coldCalls = calls;
  assert.equal(await check(input), true);
  assert.equal(calls, coldCalls, "warm membership must not spawn another discovery helper");
}
assert.equal(calls, 4,
  "concurrent checks share runtime discovery and the admission subprocess");
""")
        command = [
            executable, str(driver),
            str(SCRIPTS.parent / "extensions" / "agent-worktrees" / "managed-session.mjs"),
            str(payload / "scripts"), str(anchor),
        ]
        frame = ""
    else:
        helper = str(payload / "scripts" / "managed-session.ps1").replace("'", "''")
        command = [
            executable, "-NoProfile", "-NonInteractive", "-Command",
            f". '{helper}'; "
            "if ((Get-ManagedWorktreesPython) -ne $env:FIXTURE_EXPECTED_PYTHON) "
            "{ throw 'selected runtime was not resolved' }; "
            "Test-ManagedWorktreesSession ([Console]::In.ReadToEnd())",
        ]
        frame = json.dumps({"sessionId": "runtime-only", "cwd": str(anchor)})
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=anchor, env=env, **no_window_kwargs(),
    )
    visible, foreground, observed = set(), set(), set()
    try:
        process.stdin.write(frame)
        process.stdin.close()
        process.stdin = None
        deadline = time.monotonic() + 15
        from test_status_monitor_windows import (
            _descendants, _foreground_state, _is_terminal_window,
            _process_snapshot, _window_snapshot,
        )

        while process.poll() is None:
            processes = _process_snapshot()
            descendants = _descendants(process.pid, processes)
            observed.update(pid for pid in descendants if pid != process.pid and
                            "python" in processes.get(pid, ("", 0))[0].lower())
            visible.update(hwnd for hwnd, window in _window_snapshot(processes).items()
                           if window[0] in descendants)
            active = _foreground_state(processes)
            if active[1] in descendants and _is_terminal_window(active):
                foreground.add(active[0])
            assert time.monotonic() < deadline, "runtime-only admission exceeded its fixture deadline"
            time.sleep(0.01)
        stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    assert process.returncode == 0, stdout + stderr
    if consumer != "node":
        assert stdout.strip() == "True", stdout + stderr
    assert observed and not visible and not foreground
    record_testsuite_property(f"runtime_only_visible_windows:{consumer}:{cell}", len(visible))
    record_testsuite_property(f"runtime_only_foreground_transitions:{consumer}:{cell}", len(foreground))
    assert not (runtime / "logs").exists(), "passive resolver must not write boot traces"
    assert not (tmp_path / ".copilot").exists()
    if cell:
        assert not (legacy / "cache").exists(), "selected cell must not admit through legacy ownership"


@pytest.mark.skipif(os.name != "nt", reason="Git Bash consumer on Windows")
def test_posix_session_end_uses_runtime_without_path_python(tmp_path, native_bash):
    import venv

    anchor, runtime = fixture_project(tmp_path)
    payload = tmp_path / "payload"
    shutil.copytree(SCRIPTS, payload / "scripts")
    (payload / "scripts" / "hook_client.py").write_text(
        "import json,sys\nprint(json.dumps({'event':sys.argv[1],'payload':json.load(sys.stdin)}))\n"
    )
    slot = runtime / "versions" / "0.0.1"
    venv.EnvBuilder(with_pip=False).create(slot / ".venv")
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "0.0.1", "completed_at": "2026-01-01T00:00:00Z", "pid": 1,
    }))
    (runtime / "current-version").write_text("0.0.1\n")
    env = environment(tmp_path, anchor)
    converted = subprocess.run(
        [native_bash, "-c", 'cygpath -au "$1"', "fixture", str(tmp_path)],
        capture_output=True, text=True, timeout=5, env=env, **no_window_kwargs(),
    )
    assert converted.returncode == 0, converted.stderr
    env.update(HOME=converted.stdout.strip(), PATH="/usr/bin:/bin")
    event = {"sessionId": "runtime-only", "cwd": str(anchor), "reason": "user_exit"}
    result = subprocess.run(
        [native_bash, "-c",
         'if command -v python3 || command -v python; then exit 90; fi; bash "$1"',
         "fixture", str(payload / "scripts" / "deregister-session.sh")],
        input=json.dumps(event), capture_output=True, text=True, timeout=15,
        env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"event": "sessionEnd", "payload": event}
    assert not result.stderr
    assert not (runtime / "logs").exists()


@pytest.mark.skipif(os.name == "nt", reason="Native POSIX extension process")
def test_posix_extension_resolves_runtime_without_path_python(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    anchor, runtime = fixture_project(tmp_path)
    slot = runtime / "versions" / "0.0.1"
    python = slot / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "0.0.1", "completed_at": "2026-01-01T00:00:00Z", "pid": 1,
    }))
    (runtime / "current-version").write_text("0.0.1\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("dirname", "basename", "awk", "tr", "cat", "sort", "find", "date", "head", "tail"):
        executable = shutil.which(name)
        assert executable, name
        (tools / name).symlink_to(executable)
    driver = tmp_path / "runtime-only.mjs"
    driver.write_text("""
import assert from "node:assert/strict";
import {pathToFileURL} from "node:url";
const [module, scriptsRoot, workingDirectory] = process.argv.slice(2);
const {createManagedSessionAdmission} = await import(pathToFileURL(module));
const check = createManagedSessionAdmission({scriptsRoot});
assert.equal(await check({sessionId: "runtime-only", workingDirectory}), true);
""")
    env = environment(tmp_path, anchor)
    env["PATH"] = str(tools)
    result = subprocess.run(
        [node, str(driver), str(SCRIPTS.parent / "extensions" / "agent-worktrees" / "managed-session.mjs"),
         str(SCRIPTS), str(anchor)],
        capture_output=True, text=True, timeout=15, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (runtime / "logs").exists()


@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
def test_real_powershell_managed_pretool_preserves_protection(tmp_path, record_testsuite_property, shell):
    powershell = shutil.which(shell)
    if not powershell:
        pytest.skip("PowerShell is unavailable")
    anchor, _ = fixture_project(tmp_path)
    hooks = json.loads((SCRIPTS.parent / "hooks.json").read_text())
    env = environment(tmp_path, anchor)
    env["AGENT_WORKTREES_RESIDENT_PRE_TOOL_PYTHON"] = sys.executable
    payload = tmp_path / "payload"
    shutil.copytree(SCRIPTS, payload / "scripts")
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        "param([string]$TraceMode)\n$AwPy = '" + sys.executable.replace("'", "''") + "'\n",
        encoding="utf-8-sig",
    )
    env["COPILOT_PLUGIN_ROOT"] = str(payload)
    event = json.dumps({"sessionId": "managed", "cwd": str(anchor), "toolName": "edit",
                        "toolArgs": {"path": str(anchor / "source.py")}})
    visible, foreground, observed, timings = set(), set(), set(), []
    for _ in range(2):
        started = time.monotonic()
        process = subprocess.Popen(
            [powershell, "-NoProfile", "-NonInteractive", "-Command",
             hooks["hooks"]["preToolUse"][0]["powershell"]],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=anchor, env=env, **no_window_kwargs(),
        )
        try:
            process.stdin.write(event)
            process.stdin.close()
            process.stdin = None
            while process.poll() is None:
                if os.name == "nt":
                    from test_status_monitor_windows import (
                        _descendants, _foreground_state, _is_terminal_window,
                        _process_snapshot, _window_snapshot,
                    )

                    processes = _process_snapshot()
                    descendants = _descendants(process.pid, processes)
                    observed.update(pid for pid in descendants if pid != process.pid and
                                    "python" in processes.get(pid, ("", 0))[0].lower())
                    visible.update(hwnd for hwnd, window in _window_snapshot(processes).items()
                                   if window[0] in descendants)
                    active = _foreground_state(processes)
                    if active[1] in descendants and _is_terminal_window(active):
                        foreground.add(active[0])
                assert time.monotonic() - started < 15, "native hook exceeded its fixture deadline"
                time.sleep(0.01)
            stdout, stderr = process.communicate(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
        timings.append(round((time.monotonic() - started) * 1000, 2))
        assert process.returncode == 0, stderr
        assert json.loads(stdout).get("permissionDecision") == "deny", stdout + stderr
    if os.name == "nt":
        assert len(observed) >= 2, "both windowless cycles must observe actual Python descendants"
        assert not visible and not foreground
    record_testsuite_property(f"native_hook_cold_warm_ms:{shell}", json.dumps(timings))
    record_testsuite_property(f"visible_windows:{shell}", len(visible))
    record_testsuite_property(f"foreground_transitions:{shell}", len(foreground))


def test_extension_is_inert_until_adoption_and_stops_after_revocation(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    anchor, registry = fixture_project(tmp_path)
    (registry / "projects.yaml").write_text("projects: {}\n")
    payload = tmp_path / "payload"
    extensions = payload / "extensions" / "agent-worktrees"
    shutil.copytree(SCRIPTS.parent / "extensions" / "agent-worktrees", extensions)
    shutil.copytree(SCRIPTS, payload / "scripts")
    (payload / "plugin.json").write_text('{"name":"agent-worktrees"}')
    sdk = payload / "node_modules" / "@github" / "copilot-sdk"
    sdk.mkdir(parents=True)
    (sdk / "package.json").write_text(json.dumps({
        "type": "module", "exports": {".": "./index.mjs", "./extension": "./extension.mjs"},
    }))
    (sdk / "index.mjs").write_text("export const approveAll = () => {};\n")
    (sdk / "extension.mjs").write_text(
        "export const joinSession = async options => { globalThis.residentHooks = options.hooks; "
        "return { sessionId: 'fixture-session', "
        "on: (name, callback) => { globalThis.handlers[name] = callback; }, "
        "send: () => { throw new Error('unexpected nudge'); } }; };\n"
    )
    state = tmp_path / ".copilot" / "session-state" / "fixture-session"
    state.mkdir(parents=True)
    driver = tmp_path / "driver.mjs"
    driver.write_text("""
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
globalThis.handlers = {};
const [extension, projects, sidecar] = process.argv.slice(2);
await import(pathToFileURL(extension));
const {createResidentPreToolHook} = await import(new URL("./resident-pre-tool.mjs", pathToFileURL(extension)));
let policies = 0;
const hook = createResidentPreToolHook({
  scriptsRoot: fileURLToPath(new URL("../../scripts/", pathToFileURL(extension))),
  invokePolicy: async () => { policies++; return {permissionDecision: "deny"}; },
});
const nested = process.env.COPILOT_PROJECT_DIR + "/src";
mkdirSync(nested);
const event = {sessionId: "fixture-session", workingDirectory: nested,
  toolName: "edit", toolArgs: {path: process.env.COPILOT_PROJECT_DIR + "/source.py"}};
async function pulse(text) {
  await handlers["assistant.intent"]({data: {intent: text}});
  await handlers["session.idle"]({});
}
await pulse("unmanaged");
assert.equal(existsSync(sidecar), false);
assert.deepEqual(await hook(event), {});
assert.deepEqual(await residentHooks.onPreToolUse(event), {});
assert.equal(policies, 0);
writeFileSync(projects, "projects:\\n  example: {}\\n");
const head = process.env.COPILOT_PROJECT_DIR + "/.git/HEAD";
const headText = readFileSync(head);
unlinkSync(head);
await pulse("incomplete");
assert.equal(existsSync(sidecar), false);
writeFileSync(head, headText);
await pulse("managed");
assert.equal(JSON.parse(readFileSync(sidecar)).intent, "managed");
assert.equal((await hook(event)).permissionDecision, "deny");
assert.equal((await residentHooks.onPreToolUse(event)).permissionDecision, "deny");
assert.equal(policies, 1);
for (const part of ["objects", "refs"]) mkdirSync(nested + "/.git/" + part, {recursive: true});
writeFileSync(nested + "/.git/HEAD", "ref: refs/heads/main\\n");
assert.deepEqual(await hook(event), {}, "a new nested repository invalidates membership");
assert.equal(policies, 1);
writeFileSync(projects, "projects: {}\\n");
await pulse("revoked");
assert.equal(JSON.parse(readFileSync(sidecar)).intent, "managed");
assert.deepEqual(await hook(event), {});
assert.deepEqual(await residentHooks.onPreToolUse(event), {});
assert.equal(policies, 1);
""")
    env = environment(tmp_path, anchor)
    env["COPILOT_PLUGIN_ROOT"] = str(payload)
    env["AGENT_WORKTREES_RESIDENT_PRE_TOOL_PYTHON"] = sys.executable
    env["AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION"] = "resident"
    result = subprocess.run(
        [node, str(driver), str(extensions / "extension.mjs"),
         str(registry / "projects.yaml"), str(state / "substatus.json")],
        text=True, capture_output=True, timeout=15, cwd=anchor,
        env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows venv redirector lifetime")
def test_admission_timeout_retires_the_actual_python_worker(tmp_path):
    from test_hook_policy_lifetime import _pin_descendant

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    scripts = tmp_path / "scripts"
    shutil.copytree(SCRIPTS, scripts)
    marker = tmp_path / "worker.json"
    helper = scripts / "managed_session.py"
    source = helper.read_text()
    pause = (
        "    from hook_process_identity import process_start_time\n"
        "    import time\n"
        f"    marker = Path({str(marker)!r})\n"
        "    prepared = marker.with_suffix('.prepared')\n"
        "    prepared.write_text(json.dumps({'pid': os.getpid(), "
        "'birth': int(process_start_time(os.getpid()))}))\n"
        "    prepared.replace(marker)\n"
        "    time.sleep(60)\n"
    )
    assert source.count("        result = inspect(payload)\n") == 1
    helper.write_text(source.replace(
        "        result = inspect(payload)\n",
        "".join("    " + line + "\n" for line in pause.splitlines()) + "        result = inspect(payload)\n",
    ))
    driver = tmp_path / "timeout.mjs"
    driver.write_text("""
import {pathToFileURL} from "node:url";
const [module, scriptsRoot, python, workingDirectory] = process.argv.slice(2);
const {createManagedSessionAdmission} = await import(pathToFileURL(module));
const check = createManagedSessionAdmission({scriptsRoot, python});
console.log(JSON.stringify(await check({sessionId: "timeout-fixture", workingDirectory})));
""")
    process = subprocess.Popen(
        [node, str(driver), str(SCRIPTS.parent / "extensions" / "agent-worktrees" / "managed-session.mjs"),
         str(scripts), sys.executable, str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=environment(tmp_path, tmp_path), **no_window_kwargs(),
    )
    close = None
    try:
        deadline = time.monotonic() + 10
        while not marker.is_file():
            assert process.poll() is None, process.communicate(timeout=5)
            assert time.monotonic() < deadline, "admission worker did not publish its native identity"
            time.sleep(0.01)
        worker = json.loads(marker.read_text())
        retired, close = _pin_descendant(worker["pid"], f"windows:{worker['birth']}")
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert json.loads(stdout) is False
        assert retired(), "actual admission interpreter survived its caller-owned timeout"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        if close:
            close()


@pytest.mark.skipif(os.name != "nt", reason="Windows application execution aliases")
def test_extension_admission_never_launches_windowsapps_aliases(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    aliases = tmp_path / "WindowsApps"
    aliases.mkdir()
    (aliases / "python.exe").write_text("fixture alias must never execute")
    driver = tmp_path / "aliases.mjs"
    driver.write_text("""
import assert from "node:assert/strict";
import childProcess from "node:child_process";
import {syncBuiltinESMExports} from "node:module";
import {dirname, delimiter, join} from "node:path";
import {pathToFileURL} from "node:url";
const [module, scriptsRoot, aliases, python, workingDirectory] = process.argv.slice(2);
const original = childProcess.execFile;
const calls = [];
childProcess.execFile = (...args) => { calls.push(args[0]); return original(...args); };
syncBuiltinESMExports();
const {createManagedSessionAdmission} = await import(pathToFileURL(module));
const environment = {...process.env, PATH: aliases};
delete environment.Path;
const input = {sessionId: "alias-fixture", workingDirectory};
assert.equal(await createManagedSessionAdmission({scriptsRoot, environment})(input), false);
assert.equal(calls.length, 1, "read-only runtime discovery may run without PATH Python");
assert.match(calls[0], /powershell.exe$/i);
calls.length = 0;
environment.PATH += delimiter + dirname(python);
assert.equal(await createManagedSessionAdmission({
  scriptsRoot, environment, python: join(aliases, "python.exe"),
})(input), false);
assert.deepEqual(calls, []);
assert.equal(await createManagedSessionAdmission({scriptsRoot, environment})(input), false);
assert.deepEqual(calls, [python]);
""")
    result = subprocess.run(
        [node, str(driver), str(SCRIPTS.parent / "extensions" / "agent-worktrees" / "managed-session.mjs"),
         str(SCRIPTS), str(aliases), sys.executable, str(tmp_path)],
        capture_output=True, text=True, timeout=10,
        env=environment(tmp_path, tmp_path), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
