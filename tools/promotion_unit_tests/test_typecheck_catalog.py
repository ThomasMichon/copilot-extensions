"""Pure correspondence controls for the fixed in-process catalog family."""
from __future__ import annotations

import ast
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from tools import typecheck
from tools import typecheck_catalog_views as catalog
from tools import typecheck_bootstrap_views as bootstrap
from tools.typecheck_source_views import SourceViewError, _shape


def _fixture(root: Path) -> tuple[str, ...]:
    directory = typecheck.ROOT / catalog.PREFIX
    files = [
        *(typecheck.ROOT / p for p in catalog.MEMBERS),
        *sorted((directory / "templates").glob("*.tmpl")),
        typecheck.ROOT / catalog.DECLARATION,
    ]
    for source in files:
        target = root / source.relative_to(typecheck.ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    return catalog.MEMBERS


def _refresh_reviewed_shapes(root: Path) -> None:
    tree = ast.parse((root / catalog.PRODUCER).read_bytes())
    data = {
        "kind": "in-process-catalog-v1", "module": _shape(tree),
        "functions": {n.name: _shape(n) for n in tree.body
                      if isinstance(n, ast.FunctionDef)
                      and n.name in {"_load_command", "load_manifest", "render", "expected_files"}},
    }
    (root / catalog.DECLARATION).write_text(json.dumps(data), encoding="utf-8")


def test_catalog_witnesses_preserve_real_type_branches_and_mapping(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    owner = typecheck._SourceViews(tmp_path, tmp_path / "stage")
    emitted = owner.add_catalog(paths)
    assert {v.output.name for v in emitted} == {
        "catalog_single_powershell.py", "catalog_single_cmd.py",
        "catalog_multi_powershell.py", "catalog_multi_cmd.py",
    }
    for view in emitted:
        tree = ast.parse(view.content, feature_version=(3, 10))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
        last = function.body[-1]
        assert isinstance(last, ast.Return)
        diagnostic = f"{view.output}:{last.lineno}: error: incompatible return [return-value]\n"
        assert owner.diagnostics(diagnostic).startswith(catalog.TEMPLATE + ":60:")
        assert any(line.path == catalog.PRODUCER for line in view.lines)
        assert all(b"__harness_typing_witness__" not in v.content for v in emitted)
    owner.cleanup()


def test_catalog_template_return_error_remains_a_real_mapped_body(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    template = tmp_path / catalog.TEMPLATE
    original = template.read_text(encoding="utf-8")
    template.write_text(original.replace("    return context +", "    return 0 #", 1), encoding="utf-8")
    emitted = catalog.catalog_views(tmp_path, paths, tmp_path / "stage")
    for view in emitted:
        tree = ast.parse(view.content)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
        last = function.body[-1]
        assert isinstance(last, ast.Return) and isinstance(last.value, ast.Constant)
        assert last.value.value == 0
        assert view.diagnostics(f"{view.output}:{last.lineno}: error: return [return-value]\n", tmp_path).startswith(
            catalog.TEMPLATE + ":60:"
        )


@pytest.mark.parametrize("change", ["unknown-digit", "duplicate", "missing", "embedded-flight", "metadata-code"])
def test_catalog_refuses_changed_slot_membership(tmp_path: Path, change: str) -> None:
    paths = _fixture(tmp_path)
    path = tmp_path / catalog.TEMPLATE
    text = path.read_text(encoding="utf-8")
    if change == "unknown-digit":
        text += "\n# @@UNREVIEWED_256@@\n"
    elif change == "duplicate":
        text += "\n# @@CATALOG_FILE_SHA256@@\n"
    elif change == "missing":
        text = text.replace("@@CATALOG_MULTI_PYTHON@@", "False")
    elif change == "embedded-flight":
        text = text.replace("@@CATALOG_FLIGHT_IN_PROCESS@@", "    @@CATALOG_FLIGHT_IN_PROCESS@@")
    else:
        text = text.replace("# payload-command-catalog-sha256: ", "digest = ")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SourceViewError):
        catalog.catalog_views(tmp_path, paths, tmp_path / "stage")


@pytest.mark.parametrize("change", ["conversion", "format-spec", "extra-slot", "validator", "shim-enum", "data-conversion"])
def test_catalog_closed_semantics_refuse_even_refreshed_shapes(tmp_path: Path, change: str) -> None:
    paths = _fixture(tmp_path)
    path = tmp_path / catalog.PRODUCER
    text = path.read_text(encoding="utf-8")
    if change == "conversion":
        text = text.replace("{data['runtimeRoot']}'", "{data['runtimeRoot']!r}'", 1)
    elif change == "format-spec":
        text = text.replace("{data['runtimeRoot']}'", "{data['runtimeRoot']:s}'", 1)
    elif change == "extra-slot":
        text = text.replace("{data['runtimeRoot']}'", "{data['runtimeRoot']}{data['plugin']}'", 1)
    elif change == "validator":
        text = text.replace(r"^\.[a-z0-9-]+$", r"^.+$", 1)
    elif change == "shim-enum":
        text = text.replace('{"powershell", "cmd"}', '{"powershell", "cmd", "unknown"}', 1)
    else:
        text = text.replace('repr(data["plugin"])', 'str(data["plugin"])', 1)
    assert text != path.read_text(encoding="utf-8")
    path.write_text(text, encoding="utf-8")
    _refresh_reviewed_shapes(tmp_path)
    with pytest.raises(SourceViewError):
        catalog.catalog_inputs(tmp_path, paths)


@pytest.mark.parametrize("change", ["extra-member", "source", "declaration", "output"])
def test_catalog_custody_refuses_changed_members_and_bytes(tmp_path: Path, change: str) -> None:
    paths = _fixture(tmp_path)
    owner = typecheck._SourceViews(tmp_path, tmp_path / "stage")
    views = owner.add_catalog(paths)
    if change == "extra-member":
        (tmp_path / catalog.PREFIX / "unreviewed.py").write_text("pass\n", encoding="utf-8")
    else:
        path = (tmp_path / catalog.PRODUCER if change == "source" else
                tmp_path / catalog.DECLARATION if change == "declaration" else views[-1].output)
        path.write_bytes(path.read_bytes() + b"\n# changed\n")
    with pytest.raises((SourceViewError, typecheck.TypecheckError)):
        owner.verify()


def test_catalog_frozen_inputs_do_not_follow_host_source(tmp_path: Path) -> None:
    _fixture(tmp_path / "host")
    paths = _fixture(tmp_path / "candidate")
    views = catalog.catalog_views(tmp_path / "candidate", paths, tmp_path / "stage")
    host = tmp_path / "host" / catalog.TEMPLATE
    host.write_bytes(host.read_bytes() + b"\n# unrelated host change\n")
    for view in views:
        view.verify(tmp_path / "candidate")
    candidate = tmp_path / "candidate" / catalog.PRODUCER
    candidate.write_bytes(candidate.read_bytes() + b"\n# candidate change\n")
    with pytest.raises(SourceViewError, match="source changed"):
        views[0].verify(tmp_path / "candidate")


def _bootstrap_source_fixture(root: Path) -> tuple[str, ...]:
    paths = _fixture(root)
    target = root / bootstrap.MANIFEST
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((typecheck.ROOT / bootstrap.MANIFEST).read_bytes())
    return paths


def _render_bootstrap_consumer(root: Path) -> str:
    manifest = root / bootstrap.MANIFEST
    record = json.loads(manifest.read_bytes())
    source_consumer = (typecheck.ROOT / bootstrap.MANIFEST).parent
    for platform in ("posix", "windows"):
        relative = Path(record["payloadDispatcher"][platform])
        assert not relative.is_absolute() and not relative.drive and ".." not in relative.parts
        target = manifest.parent / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source_consumer / relative).read_bytes())
    primitive = manifest.parent / "scripts/installation-context/installation_context.py"
    primitive.parent.mkdir(parents=True, exist_ok=True)
    primitive.write_bytes((typecheck.ROOT / "libs/installation-context/installation_context.py").read_bytes())
    spec = importlib.util.spec_from_file_location(
        "_bootstrap_fixture_producer", typecheck.ROOT / catalog.PRODUCER,
    )
    assert spec and spec.loader
    generator = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(generator)
    finally:
        sys.dont_write_bytecode = previous
    data = generator.load_manifest(manifest, canonical_root=typecheck.ROOT)
    rendered: object = generator.render((root / bootstrap.TEMPLATE).read_text(encoding="utf-8"), data)
    if not isinstance(rendered, str):
        raise TypeError("Canonical bootstrap renderer must return text")
    return rendered


def _bootstrap_fixture(root: Path) -> tuple[str, ...]:
    paths = _bootstrap_source_fixture(root)
    consumer = root / bootstrap.CONSUMER
    consumer.parent.mkdir(parents=True, exist_ok=True)
    consumer.write_text(_render_bootstrap_consumer(root), encoding="utf-8", newline="\n")
    (root / bootstrap.HELPER).write_bytes((root / catalog.MEMBERS[0]).read_bytes())
    return (*paths, bootstrap.HELPER)


def test_bootstrap_view_preserves_real_program_transport_and_floor(tmp_path: Path) -> None:
    paths = _bootstrap_fixture(tmp_path)
    manifest = tmp_path / bootstrap.MANIFEST
    dispatchers = json.loads(manifest.read_bytes())["payloadDispatcher"]
    for relative in (dispatchers["posix"], dispatchers["windows"]):
        assert (manifest.parent / relative).read_bytes() == (
            (typecheck.ROOT / bootstrap.MANIFEST).parent / relative
        ).read_bytes()
    assert (manifest.parent / "scripts/installation-context/installation_context.py").read_bytes() == (
        typecheck.ROOT / "libs/installation-context/installation_context.py"
    ).read_bytes()
    view = bootstrap.materialized_bootstrap_view(tmp_path, paths, tmp_path / "stage/bootstrap.py")
    consumer = (tmp_path / bootstrap.CONSUMER).read_text()
    quoted = next(line.split(" = ", 1)[1] for line in consumer.splitlines()
                  if line.startswith("$bindingBootstrap = "))
    assert view.content == (bootstrap._literal(quoted) + "\n").encode()
    assert consumer.count("-c $bindingBootstrap ") == 2
    diagnostic = view.diagnostics(f"{view.output}:1: error: assignment [assignment]\n", tmp_path)
    assert diagnostic.startswith(bootstrap.CONSUMER + ":43:")
    assert "bootstrap materialized consumer" in diagnostic
    assert f"producer {catalog.PRODUCER}:{view.producer_range[0]}-{view.producer_range[1]}" in diagnostic
    assert "full body requires Python 3.11" in diagnostic
    bootstrap.require_bootstrap_version("strict", ("3.10", "3.11"))
    bootstrap.require_bootstrap_version("pending", ("3.10",))
    with pytest.raises(SourceViewError, match="persistent Python 3.11"):
        bootstrap.require_bootstrap_version("strict", ("3.10",))


def test_bootstrap_wrong_assignment_is_actual_mapped_source(tmp_path: Path) -> None:
    paths = _bootstrap_source_fixture(tmp_path)
    path = tmp_path / catalog.PRODUCER
    original = path.read_text()
    changed = original.replace(
        "print(sys.executable,flush=True); ",
        "probe: int = ''wrong''; print(sys.executable,flush=True); ", 1,
    )
    assert changed != original
    path.write_text(changed, encoding="utf-8")
    _refresh_reviewed_shapes(tmp_path)
    view = bootstrap.bootstrap_view(tmp_path, paths, tmp_path / "stage/bootstrap.py")
    assignment = next(n for n in ast.parse(view.content).body if isinstance(n, ast.AnnAssign))
    assert isinstance(assignment.value, ast.Constant) and assignment.value.value == "wrong"
    assert view.diagnostics(
        f"{view.output}:{assignment.lineno}: error: wrong assignment [assignment]\n", tmp_path,
    ).startswith(catalog.PRODUCER + ":")
    assert not view.materialized


@pytest.mark.parametrize("stale", [False, True])
def test_bootstrap_canonical_source_does_not_read_dev_mirrors(tmp_path: Path, stale: bool) -> None:
    paths = _bootstrap_source_fixture(tmp_path)
    expected = _render_bootstrap_consumer(tmp_path)
    quoted = next(line.split(" = ", 1)[1] for line in expected.splitlines()
                  if line.startswith("$bindingBootstrap = "))
    if stale:
        for name in (bootstrap.CONSUMER, bootstrap.HELPER):
            target = tmp_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"unusable Dev mirror\n")
    view = bootstrap.bootstrap_view(tmp_path, paths, tmp_path / "stage/bootstrap.py")
    assert view.content == (bootstrap._literal(quoted) + "\n").encode()
    assert not {bootstrap.CONSUMER, bootstrap.HELPER} & {name for name, _raw in view.inputs}
    assert view.lines[0].path == catalog.PRODUCER and not view.materialized
    assert "canonical source" in view.diagnostics(f"{view.output}:1: error: assignment\n", tmp_path)
    view.verify(tmp_path)


def test_bootstrap_canonical_source_refuses_changed_launch(tmp_path: Path) -> None:
    paths = _bootstrap_source_fixture(tmp_path)
    path = tmp_path / catalog.PRODUCER
    text = path.read_text()
    text = text.replace("Source -3 -I -B -E -X utf8 -c ", "Source -I -B -E -X utf8 -c ", 1)
    path.write_text(text, encoding="utf-8")
    _refresh_reviewed_shapes(tmp_path)
    with pytest.raises(SourceViewError, match="argv/helper"):
        bootstrap.bootstrap_view(tmp_path, paths, tmp_path / "stage/bootstrap.py")


def test_bootstrap_strict_floor_refuses_before_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    project = next(p for p in typecheck.load_projects() if p.name == "lib-payload-invocation")
    assert project.python_version == "3.10" and project.additional_python_versions == ("3.11",)

    def forbidden(*_args: object) -> None:
        raise AssertionError("Missing full-body floor must refuse before SDK lookup")

    monkeypatch.setattr(typecheck, "environment_path", forbidden)
    with pytest.raises(SourceViewError, match="persistent Python 3.11"):
        typecheck.check_project(
            typecheck.ROOT, replace(project, level="strict", additional_python_versions=()),
            catalog.MEMBERS, all_paths=catalog.MEMBERS, strict=True,
        )


@pytest.mark.parametrize("change", [
    "conversion", "format", "unknown-slot", "quote", "launch", "helper", "member", "template-slot",
])
def test_bootstrap_refuses_unreviewed_composition(tmp_path: Path, change: str) -> None:
    paths = _bootstrap_fixture(tmp_path)
    if change in {"conversion", "format", "unknown-slot"}:
        path = tmp_path / catalog.PRODUCER
        prefix, body = path.read_text().split('"CATALOG_BINDING_POWERSHELL": (', 1)
        replacement = {
            "conversion": "{data['plugin']!r}", "format": "{data['plugin']:s}",
            "unknown-slot": "{data['command']}",
        }[change]
        body = body.replace("{data['plugin']}", replacement, 1)
        path.write_text(prefix + '"CATALOG_BINDING_POWERSHELL": (' + body, encoding="utf-8")
        _refresh_reviewed_shapes(tmp_path)
    elif change in {"quote", "launch"}:
        path = tmp_path / bootstrap.CONSUMER
        old, new = (
            ("run_name=''__main__''", "run_name='__main__'")
            if change == "quote" else ("-I -B -E -X utf8 -c $bindingBootstrap", "-B -c $bindingBootstrap")
        )
        path.write_text(path.read_text().replace(old, new, 1), encoding="utf-8")
    elif change == "helper":
        path = tmp_path / bootstrap.HELPER
        path.write_bytes(path.read_bytes() + b"\n# changed helper\n")
    elif change == "member":
        (tmp_path / catalog.PREFIX / "unreviewed.py").write_text("pass\n", encoding="utf-8")
    else:
        path = tmp_path / bootstrap.TEMPLATE
        path.write_bytes(path.read_bytes() + b"\n@@CATALOG_BINDING_POWERSHELL@@\n")
    with pytest.raises(SourceViewError):
        bootstrap.materialized_bootstrap_view(tmp_path, paths, tmp_path / "stage/bootstrap.py")


def test_bootstrap_custody_is_candidate_local(tmp_path: Path) -> None:
    paths = _bootstrap_fixture(tmp_path / "candidate")
    _bootstrap_fixture(tmp_path / "host")
    owner = typecheck._SourceViews(tmp_path / "candidate", tmp_path / "stage")
    owner.add_catalog(paths)
    owner.add(bootstrap.materialized_bootstrap_view, paths, bootstrap.MATERIALIZED_INPUTS, "bootstrap.py")
    (tmp_path / "host" / bootstrap.CONSUMER).write_text("foreign host\n", encoding="utf-8")
    owner.verify()
    path = tmp_path / "candidate" / bootstrap.CONSUMER
    path.write_bytes(path.read_bytes() + b"\n# drift\n")
    with pytest.raises((SourceViewError, typecheck.TypecheckError)):
        owner.verify()


def _selector_fixture(root: Path) -> tuple[str, ...]:
    paths = _fixture(root)
    for relative in (bootstrap.SELECTOR_HELPER, bootstrap.SELECTOR_PACKAGE):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((typecheck.ROOT / relative).read_bytes())
    return (*paths, bootstrap.SELECTOR_HELPER, bootstrap.SELECTOR_PACKAGE)


@pytest.mark.parametrize("index", [0, 1])
def test_shim_selector_preserves_actual_argument_bytes_and_mapping(tmp_path: Path, index: int) -> None:
    paths = _selector_fixture(tmp_path)
    factory = (bootstrap.powershell_selector_view, bootstrap.posix_selector_view)[index]
    owner = typecheck._SourceViews(tmp_path, tmp_path / "stage")
    view = owner.add(factory, paths, bootstrap.SELECTOR_INPUTS, "selector.py")
    template = (tmp_path / bootstrap.SELECTOR_TEMPLATES[index]).read_text(encoding="utf-8")
    body = (
        template.split("    $selector = @'\n", 1)[1].split("\n'@", 1)[0]
        if index == 0 else
        template.split(' -c \'', 1)[1].split('\' "$_selector_root"', 1)[0]
    )
    assert view.content == body.encode("utf-8")
    if index == 1:
        assert view.content.startswith(b"\n") and view.content.endswith(b"\n")
    number = next(n for n, line in enumerate(body.splitlines(), 1) if "sys.pycache_prefix" in line)
    physical = next(n for n, line in enumerate(template.splitlines(), 1) if "sys.pycache_prefix" in line)
    assert owner.diagnostics(f"{view.output}:{number}: error: assignment [assignment]\n") == (
        f"{bootstrap.SELECTOR_TEMPLATES[index]}:{physical}: error: assignment [assignment]\n"
    )
    owner.cleanup()


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("change", [
    "launch", "quote", "helper-call", "token", "duplicate", "directive", "embedded-terminator",
])
def test_shim_selector_rejects_changed_transport_or_helper(
    tmp_path: Path, index: int, change: str,
) -> None:
    paths = _selector_fixture(tmp_path)
    path = tmp_path / bootstrap.SELECTOR_TEMPLATES[index]
    original = path.read_text(encoding="utf-8")
    if change == "launch":
        changed = original.replace("-I -B -X", "-B -X", 1)
    elif change == "quote":
        changed = original.replace("$selector = @'", '$selector = @"', 1) if index == 0 else (
            original.replace(' -c \'', ' -c "', 1)
        )
    elif change == "helper-call":
        changed = original.replace("runtime=sys.executable", "runtime=sys.prefix", 1)
    elif change == "token":
        changed = original.replace("try:\n", "# @@PLUGIN@@\ntry:\n", 1)
    elif change == "duplicate":
        changed = original + "\n" + original
    elif change == "embedded-terminator":
        changed = original.replace("try:\n", "probe = \"\"\"\n'@\n\"\"\"\ntry:\n", 1)
    else:
        changed = original.replace("try:\n", "# type: ignore\ntry:\n", 1)
    assert changed != original
    path.write_text(changed, encoding="utf-8")
    factory = (bootstrap.powershell_selector_view, bootstrap.posix_selector_view)[index]
    with pytest.raises(SourceViewError):
        factory(tmp_path, paths, tmp_path / "stage/selector.py")


@pytest.mark.parametrize("change", ["helper", "output", "member"])
def test_shim_selector_custody_does_not_follow_host_drift(tmp_path: Path, change: str) -> None:
    paths = _selector_fixture(tmp_path / "candidate")
    _selector_fixture(tmp_path / "host")
    owner = typecheck._SourceViews(tmp_path / "candidate", tmp_path / "stage")
    view = owner.add(
        bootstrap.powershell_selector_view, paths, bootstrap.SELECTOR_INPUTS, "selector.py",
    )
    host = tmp_path / "host" / bootstrap.SELECTOR_HELPER
    host.write_bytes(host.read_bytes() + b"\n# unrelated host\n")
    owner.verify()
    if change == "member":
        changed = tmp_path / "candidate" / catalog.PREFIX / "unreviewed.py"
        changed.write_text("pass\n", encoding="utf-8")
    else:
        changed = (
            tmp_path / "candidate" / bootstrap.SELECTOR_HELPER if change == "helper"
            else view.output
        )
        changed.write_bytes(changed.read_bytes() + b"\n# candidate drift\n")
    with pytest.raises((SourceViewError, typecheck.TypecheckError)):
        owner.verify()
