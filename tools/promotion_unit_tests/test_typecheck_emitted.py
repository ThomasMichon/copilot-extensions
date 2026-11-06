"""Actor-free emitted-source admission using synthetic, nonexecuted producers."""
from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

import typecheck as checker
from tools import typecheck_source_views as views


def emitted_source(root: Path) -> tuple[tuple[str, ...], dict]:
    relative = views.HARNESS_PREFIX + "producer.py"
    source = b"def emit():\n    subprocess.run([sys.executable, '-c', \"value: int = 1\"])\n"
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(source)
    shell = views.HARNESS_PREFIX + "fixture.sh"
    shell_body = b"value: int = 2\n"
    shell_source = b"python <<'PY'\n" + shell_body + b"PY\n"
    (root / shell).write_bytes(shell_source)
    tree = ast.parse(source)
    container = next(n for n in ast.walk(tree) if isinstance(n, ast.List))
    expression = container.elts[-1]
    declaration = {
        "version": 1,
        "members": [{"path": relative, "sha256": [hashlib.sha256(source).hexdigest()],
                     "structure": views._shape(tree)}],
        "assets": [{"path": shell, "sha256": [hashlib.sha256(shell_source).hexdigest()],
                    "structure": [hashlib.sha256(shell_source).hexdigest()]}],
        "programs": [
            {"path": relative, "function": "emit", "selector": "python-c", "index": 0,
             "expression": views._shape(expression), "origins": {}, "transport": "unicode-python-c",
             "role": "synthetic-constant", "classification": "static-parent-constant-relation-only",
             "sites": [[container.lineno, container.col_offset, container.end_lineno, container.end_col_offset]]},
            {"path": shell, "function": "", "selector": "quoted-heredoc", "index": 0,
             "expression": hashlib.sha256(shell_body).hexdigest(), "origins": {},
             "transport": "quoted-shell-utf8", "role": "synthetic-shell",
             "classification": "static-shell-body-unobserved", "sites": []},
        ],
        "data_writes": [],
    }
    (root / views.HARNESS_DECLARATION).write_text(json.dumps(declaration), encoding="utf-8")
    return (relative,), declaration


@pytest.mark.parametrize("change", ["unchanged", "missing-site", "hidden-producer", "duplicate-epoch"])
def test_emitted_census_checks_current_synthetic_producer(
    tmp_path: Path, change: str,
) -> None:
    paths, declaration = emitted_source(tmp_path)
    if change == "missing-site":
        declaration["programs"][0]["sites"] = []
    elif change == "duplicate-epoch":
        declaration["programs"].append(dict(declaration["programs"][0]))
    elif change == "hidden-producer":
        path = tmp_path / paths[0]
        content = path.read_bytes() + b"\ndef hidden():\n    subprocess.run([sys.executable, '-c', 'pass'])\n"
        path.write_bytes(content)
        declaration["members"][0].update(
            sha256=[hashlib.sha256(content).hexdigest()], structure=views._shape(ast.parse(content)),
        )
    (tmp_path / views.HARNESS_DECLARATION).write_text(json.dumps(declaration), encoding="utf-8")
    if change != "unchanged":
        with pytest.raises(views.SourceViewError):
            views.harness_emitted_inputs(tmp_path, paths)
        return
    inputs, programs = views.harness_emitted_inputs(tmp_path, paths)
    assert len(inputs) == 3 and len(programs) == 2
    assert [body for _, body, _, _ in programs] == [b"value: int = 1", b"value: int = 2\n"]
    assert programs[0][0] == paths[0] and programs[0][2:] == (2, 2)


def test_shared_binding_verification_is_per_pass_and_never_skips_an_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _ = emitted_source(tmp_path)
    owner = checker._SourceViews(tmp_path, tmp_path / "stage")
    emitted = owner.add_harness(paths)
    calls = []
    verify = views.HarnessProgramView.verify

    def observe(view, root):
        calls.append(view.output)
        verify(view, root)

    monkeypatch.setattr(views.HarnessProgramView, "verify", observe)
    owner.verify()
    owner.verify()
    assert len(calls) == 2
    last = emitted[-1].output
    content = last.read_bytes()
    last.write_bytes(content + b"# changed\n")
    with pytest.raises(views.SourceViewError, match="Checker output changed"):
        owner.verify()
    last.write_bytes(content)
    owner.views[-1] = replace(emitted[-1], members=())
    with pytest.raises(views.SourceViewError, match="membership changed"):
        owner.verify()
    owner.views[-1] = emitted[-1]
    owner.cleanup()
    assert not list(owner.stage.iterdir())


@pytest.mark.parametrize("change", ["source", "member", "asset", "replaced-output"])
def test_emitted_views_refuse_current_binding_drift_without_foreign_cleanup(
    tmp_path: Path, change: str,
) -> None:
    paths, _ = emitted_source(tmp_path)
    owner = checker._SourceViews(tmp_path, tmp_path / "stage")
    emitted = owner.add_harness(paths)
    if change == "source":
        path = tmp_path / paths[0]
        path.write_bytes(path.read_bytes() + b"\n")
    elif change == "member":
        (tmp_path / views.HARNESS_PREFIX / "extra.py").write_bytes(b"pass\n")
    elif change == "asset":
        (tmp_path / views.HARNESS_PREFIX / "extra.sh").write_bytes(b"exit 0\n")
    else:
        output = emitted[-1].output
        output.rename(output.with_suffix(".saved"))
        output.write_bytes(b"foreign: int = 1\n")
    with pytest.raises((checker.TypecheckError, views.SourceViewError)):
        owner.verify()
    if change == "replaced-output":
        with pytest.raises(checker.TypecheckError, match="Replaced checker output retained"):
            owner.cleanup()
        assert emitted[-1].output.read_bytes() == b"foreign: int = 1\n"


@pytest.mark.parametrize("change", ["later-copy", "winning-copy", "winning-stub"])
def test_emitted_provider_uses_ordered_canonical_winner(tmp_path: Path, change: str) -> None:
    canonical = tmp_path / "libs/agent-procutil/src"
    package = canonical / "agent_procutil"
    package.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b"raise RuntimeError('never imported')\n")
    later = tmp_path / "later"
    later.mkdir()
    (later / "agent_procutil.py").write_bytes(b"raise RuntimeError('never imported')\n")
    if change == "later-copy":
        assert checker._harness_procutil_provider(tmp_path, [str(canonical), str(later)]) == package
    elif change == "winning-copy":
        with pytest.raises(checker.TypecheckError, match="winning candidate-local"):
            checker._harness_procutil_provider(tmp_path, [str(later), str(canonical)])
    else:
        (package / "__init__.pyi").write_bytes(b"")
        with pytest.raises(checker.TypecheckError, match="winning agent_procutil stub"):
            checker._harness_procutil_provider(tmp_path, [str(canonical), str(later)])


@pytest.mark.parametrize("change", ["nested-sink", "nested-literal", "function", "parameter", "import", "module-store"])
def test_reviewed_member_digests_cannot_hide_lexical_bindings(tmp_path: Path, change: str) -> None:
    paths, declaration = emitted_source(tmp_path)
    path = tmp_path / paths[0]
    source = path.read_bytes()
    if change == "nested-sink":
        source = (
            b"def emit():\n    script = 'value: int = 1'\n"
            b"    def launch(script: str):\n        subprocess.run([sys.executable, '-c', script])\n"
            b"    launch(\"value: int = 'wrong'\")\n"
        )
    elif change == "nested-literal":
        source = b"def emit():\n    def launch():\n        subprocess.run([sys.executable, '-c', \"value: int = 1\"])\n"
    elif change == "function":
        source += b"\ndef str(value):\n    return 42\n"
    elif change == "parameter":
        source = source.replace(b"def emit():", b"def emit(repr):")
    elif change == "import":
        source += b"\nfrom builtins import int as str\n"
    else:
        source += b"\nrepr = lambda value: 42\n"
    path.write_bytes(source)
    tree = ast.parse(source)
    declaration["members"][0].update(
        sha256=[hashlib.sha256(source).hexdigest()], structure=views._shape(tree),
    )
    if change == "nested-sink":
        expression = next(n.value for n in ast.walk(tree) if isinstance(n, ast.Assign))
        container = next(n for n in ast.walk(tree) if isinstance(n, ast.List))
        declaration["programs"][0].update(
            selector="assignment:script", expression=views._shape(expression),
            sites=[[container.lineno, container.col_offset, container.end_lineno, container.end_col_offset]],
        )
    elif change == "nested-literal":
        container = next(n for n in ast.walk(tree) if isinstance(n, ast.List))
        declaration["programs"][0]["sites"] = [
            [container.lineno, container.col_offset, container.end_lineno, container.end_col_offset]
        ]
    (tmp_path / views.HARNESS_DECLARATION).write_text(json.dumps(declaration), encoding="utf-8")
    if change == "nested-literal":
        assert views.harness_emitted_inputs(tmp_path, paths)[1][0][1] == b"value: int = 1"
        return
    with pytest.raises(views.SourceViewError, match="nested|conversion binding"):
        views.harness_emitted_inputs(tmp_path, paths)
