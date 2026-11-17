"""Pure native-selection joins; process capture belongs to the native portfolio."""
import json
import time

import pytest

from runtime_profile import ProfileError
from runtime_source_capture import capture
from runtime_source_lock import _retain_source_lock, _wheel_pins


def lock(*, filename="demo-1.0-py3-none-any.whl", digest="a" * 64):
    return (
        'lock-version="1.0"\ncreated-by="uv"\nrequires-python=">=3.10"\n'
        '[[packages]]\nname="demo"\nversion="1.0"\n'
        'wheels=[{url="https://example.invalid/' + filename
        + '?example=not-persisted",hashes={sha256="' + digest + '"}}]\n'
    ).encode()


def diagnostic(filename="demo-1.0-py3-none-any.whl", version="1.0"):
    return f"DEBUG Selecting: demo=={version} [compatible] ({filename})\n".encode()


def parse(content, trace):
    return _wheel_pins(
        content, {"environment": {"python_full_version": "3.11.0"}, "tags": ["py3-none-any"]},
        time.monotonic() + 5, diagnostic=trace,
    )


@pytest.mark.parametrize("alternatives", [False, True])
def test_witness_uses_native_choice_and_excludes_urls_and_unselected_versions(alternatives):
    content = lock()
    if alternatives:
        content = content.replace(
            b'wheels=[', b'wheels=[{url="https://example.invalid/demo-1.0-1-py3-none-any.whl",'
            b'hashes={sha256="' + b"b" * 64 + b'"}},',
        )
    result = parse(content, diagnostic(version="0.9") + diagnostic(version="1.0.0"))
    assert result == [{"name": "demo", "version": "1.0", "filename": "demo-1.0-py3-none-any.whl",
                       "sha256": "sha256:" + "a" * 64}]
    assert "not-persisted" not in json.dumps(result)


@pytest.mark.parametrize("fault", [
    "missing", "version", "invalid-version", "ambiguous", "hash", "filename", "traversal", "directory", "duplicate",
])
def test_missing_ambiguous_or_nonwheel_witness_refuses(fault):
    content, trace = lock(), diagnostic()
    if fault == "missing":
        trace = b""
    elif fault == "version":
        trace = diagnostic(version="0.9")
    elif fault == "invalid-version":
        trace = diagnostic(version="not-persisted")
    elif fault == "ambiguous":
        trace += diagnostic("demo-1.0-cp311-cp311-win_amd64.whl")
    elif fault == "hash":
        content = lock(digest="x" * 64)
    elif fault == "filename":
        content = lock(filename="other-1.0-py3-none-any.whl")
    elif fault == "traversal":
        trace = diagnostic("../demo-1.0-py3-none-any.whl")
    elif fault == "directory":
        content = b'lock-version="1.0"\n[[packages]]\nname="demo"\nversion="1.0"\ndirectory={path="."}\n'
    elif fault == "duplicate":
        content = lock().replace(
            b'wheels=[', b'wheels=[{url="https://example.invalid/demo-1.0-py3-none-any.whl",'
            b'hashes={sha256="' + b"a" * 64 + b'"}},',
        )
    with pytest.raises(ProfileError) as observed:
        parse(content, trace)
    if fault == "invalid-version":
        assert observed.value.__cause__ is None
        assert "not-persisted" not in str(observed.value)


@pytest.mark.parametrize("wheel_witness,budget", [(True, 0), (True, -1), (True, True), (False, 1)])
def test_diagnostic_budget_refuses_before_source_configuration(wheel_witness, budget):
    with pytest.raises(ProfileError, match="diagnostic byte budget"):
        with _retain_source_lock(
            None, max_lock_bytes=1, deadline=time.monotonic() + 5,
            wheel_witness=wheel_witness, max_diagnostic_bytes=budget,
        ):
            pytest.fail("invalid capture policy reached source configuration")


@pytest.mark.parametrize("stdout,stderr", [(0, 1), (True, 1), (1, None)])
def test_capture_budget_refuses_before_worker_inputs(stdout, stderr):
    with pytest.raises(ProfileError, match="stream byte budgets"):
        capture(None, None, max_stdout=stdout, max_stderr=stderr, deadline=time.monotonic() + 5)
