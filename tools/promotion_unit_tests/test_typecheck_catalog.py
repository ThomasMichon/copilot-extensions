"""Pure correspondence controls for the fixed in-process catalog family."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tools import typecheck
from tools import typecheck_catalog_views as catalog
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
