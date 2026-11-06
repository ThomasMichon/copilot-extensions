"""Temporary refusal-only census diagnostic in a copied fixture."""
import json
import pytest

from test_native_uninstall_footprint import (
    _host, _native_arguments, _preserved, _run, native_install as _native_install,
)

native_install = _native_install


def test_bash_controller_diagnostic(native_install):
    home, payload, root, owned, durable = native_install
    helper = payload / "scripts/installer_engine_uninstall.py"
    source = helper.read_text(encoding="utf-8")
    marker = '    for row in rows:\n        if (row["pid"] == os.getpid()'
    assert source.count(marker) == 1
    diagnostic = '''
    def safe_args(row):
        result = []
        for value in _arguments(row):
            normalized = re.sub(r"^/(?:cygdrive/)?([A-Za-z])/", r"\\1:/", value)
            path = Path(normalized)
            if path.is_absolute() and path.is_relative_to(Path.home()):
                result.append("<home>/" + path.relative_to(Path.home()).as_posix())
            elif value in {"-I", "-S", "-B", "-X", "utf8", "--", "--teardown",
                           "--run-lifecycle", "uninstall", "--install-dir"}:
                result.append(value)
            elif path.is_absolute() and len(value) < 260:
                result.append("<external-path>")
            else:
                result.append("<value>")
        return result
    facts = [{
        "pid": row["pid"], "ppid": row["ppid"], "image": Path(row["image"]).name,
        "admitted": row["pid"] in controllers | redirectors | gates,
        "inspector": row["pid"] == os.getpid(),
        "inspection_host": row.get("inspectionHost", False),
        "inspector_tail": _arguments(row)[-len(sys.argv):] == sys.argv,
        "parent_match": _parent(row["pid"], row["image"], row["command"],
                                payload, root, plugin, row["pid"], arguments=row.get("argv")),
        "argv": safe_args(row),
    } for row in rows if prefix in row["command"].replace("\\\\", "/").casefold()
       or prefix in " ".join(_argv(row["command"], row["image"])).replace("\\\\", "/").casefold()]
    (Path.home() / "controller-diagnostic.json").write_text(json.dumps(facts), encoding="utf-8")
    raise UninstallRefused("Diagnostic only; removal forbidden")
'''
    helper.write_text(source.replace(marker, diagnostic + marker), encoding="utf-8")
    executable = _host("bash")
    result = _run(_native_arguments("bash", executable, payload, root), home)
    _preserved(durable)
    assert all(path.exists() for path in owned)
    report = home / "controller-diagnostic.json"
    assert report.exists(), result.stdout + result.stderr
    pytest.fail(json.dumps(json.loads(report.read_text()), indent=2), pytrace=False)
