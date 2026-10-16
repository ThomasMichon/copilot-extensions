"""Exact-record cases collected by the existing install-contract suite."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from runtime_profile import ProfileError, SCHEMA, select_profile, validate_profile


def _wheel(name="engine", version="1.0", dependencies=(), extras=(), selected=()):
    return {
        "name": name, "version": version,
        "filename": f"{name}-{version}-py3-none-any.whl",
        "sha256": "sha256:" + "1" * 64, "requires_python": ">=3.11",
        "provides_extras": list(extras), "selected_extras": list(selected),
        "requires_dist": list(dependencies),
    }


def _record():
    return {
        "schema": SCHEMA, "version": 1,
        "release": {"identity": "release-1", "sha256": "sha256:" + "2" * 64},
        "mode": "source", "artifact": None, "component": "index-engine",
        "backend": "cpu", "approval_source": "adopter-config",
        "acquisition_policy": "governed-source-policy",
        "target": {
            "device": "cpu",
            "environment": {
                "implementation_name": "cpython", "implementation_version": "3.11.9",
                "os_name": "nt", "platform_machine": "AMD64",
                "platform_release": "11", "platform_system": "Windows",
                "platform_version": "10.0.1", "python_full_version": "3.11.9",
                "platform_python_implementation": "CPython", "python_version": "3.11",
                "sys_platform": "win32",
            },
            "tags": ["cp311-cp311-win_amd64", "py3-none-any"],
        },
        "roots": ["engine[metrics]==1.0"],
        "wheels": [
            _wheel(dependencies=(
                'counter>=1; extra == "metrics"',
                'posix-only>=1; sys_platform == "linux"',
            ), extras=("metrics",), selected=("metrics",)),
            _wheel("counter"),
        ],
    }


def _select(records, **overrides):
    reference = records[0]
    values = {
        "release": reference["release"], "target": reference["target"],
        "component": reference["component"], "roots": reference["roots"],
        "mode": reference["mode"],
        "approvals": {
            validate_profile(record).lock_id: record["approval_source"] for record in records
        },
    }
    return select_profile(records, **(values | overrides))


class TestExactRuntimeProfile:
    def test_canonical_identity_freezes_input_and_mapping_order(self):
        record = _record()
        profile = validate_profile(record)
        assert profile == validate_profile(dict(reversed(list(record.items()))))
        record["wheels"][0]["version"] = "2"
        copy = profile.record()
        copy["backend"] = "cuda"
        assert profile.record()["backend"] == "cpu"
        assert profile.record()["wheels"][0]["version"] == "1.0"
        assert profile.lock_id.startswith("sha256:")

    @pytest.mark.parametrize("mutate", [
        lambda r: r.update(version=True),
        lambda r: r.update(version=2),
        lambda r: r.update(schema="other"),
        lambda r: r.update(approved=True),
        lambda r: r["release"].update(sha256="bad"),
        lambda r: r.update(approval_source="https://untrusted.example/token"),
        lambda r: r.update(acquisition_policy="C:\\private\\config"),
        lambda r: r.update(backend="cuda-12"),
        lambda r: r.update(artifact={"artifact_id": "sha256:" + "0" * 64}),
        lambda r: r["wheels"].pop(),
        lambda r: r["wheels"].append(deepcopy(r["wheels"][0])),
        lambda r: r["wheels"].append(_wheel("unused")),
        lambda r: r["wheels"][0].update(filename="engine-2.0-py3-none-any.whl"),
        lambda r: r["wheels"][0].update(filename="../engine-1.0-py3-none-any.whl"),
        lambda r: r["wheels"][0].update(filename="engine-1.0-cp312-cp312-win_amd64.whl"),
        lambda r: r["wheels"][0].update(sha256="bad"),
        lambda r: r["wheels"][0].update(requires_python=">=3.12"),
        lambda r: r["wheels"][0].update(requires_dist=["counter>=2"]),
        lambda r: r["wheels"][0].update(requires_dist=["counter @ https://example.invalid/a.whl"]),
        lambda r: r["wheels"][0].update(requires_dist=['counter; "unsafe" in dependency_groups']),
        lambda r: r["wheels"][0].update(provides_extras=[]),
        lambda r: r["wheels"][0].update(selected_extras=[]),
        lambda r: r["wheels"][0].update(provides_extras=["metrics", "Metrics"]),
        lambda r: r["target"]["environment"].pop("python_version"),
        lambda r: r["target"]["environment"].update(python_version="3.12"),
        lambda r: r["target"].update(tags=[]),
        lambda r: r.update(roots=[]),
    ])
    def test_malformed_or_incomplete_record_refuses(self, mutate):
        record = _record()
        mutate(record)
        with pytest.raises(ProfileError):
            validate_profile(record)

    def test_transitive_extras_cycles_and_markers(self):
        record = _record()
        record["wheels"][1] = _wheel(
            "counter", dependencies=('leaf; extra == "detail"', "engine==1.0"),
            extras=("detail",), selected=("detail",),
        )
        record["wheels"][0]["requires_dist"].append("counter[detail]==1.0")
        record["wheels"].append(_wheel("leaf"))
        validate_profile(record)
        record["wheels"].pop()
        with pytest.raises(ProfileError, match="incomplete applicable closure: leaf"):
            validate_profile(record)

    def test_absent_optional_requires_python_has_no_invented_constraint(self):
        record = _record()
        record["wheels"][0]["requires_python"] = ""
        profile = validate_profile(record)
        assert profile.record()["wheels"][0]["requires_python"] == ""
        assert _select([record]).profile == profile

    @pytest.mark.parametrize("value", [None, 3.11, " ", " >=3.11", ">=3.11 "])
    def test_requires_python_still_refuses_non_string_or_untrimmed_metadata(self, value):
        record = _record()
        record["wheels"][0]["requires_python"] = value
        with pytest.raises(ProfileError, match="Requires-Python must be a trimmed string"):
            validate_profile(record)

    def test_normalized_duplicate_distribution_refuses(self):
        record = _record()
        record["wheels"].extend([_wheel("some_pkg"), _wheel("some.pkg")])
        with pytest.raises(ProfileError, match="duplicate normalized distribution"):
            validate_profile(record)

    def test_approval_binds_every_field_and_is_not_a_record_flag(self):
        record = _record()
        approved = {validate_profile(record).lock_id: record["approval_source"]}
        selected = _select([record], approvals=approved)
        assert selected.approval_source == "adopter-config"
        record["wheels"][0]["sha256"] = "sha256:" + "3" * 64
        with pytest.raises(ProfileError, match="whole-record approval"):
            _select([record], approvals=approved)
        with pytest.raises(ProfileError, match="whole-record approval"):
            _select([_record()], approvals={selected.profile.lock_id: "foreign-source"})

    @pytest.mark.parametrize("change", [
        {"release": {"identity": "other", "sha256": "sha256:" + "2" * 64}},
        {"release": {"identity": "release-1", "sha256": "sha256:" + "4" * 64}},
        {"component": "index-client"},
        {"roots": ["engine==1.0"]},
        {"backend": "cuda"},
        {"mode": "artifact"},
    ])
    def test_requested_release_and_capability_never_silently_change(self, change):
        with pytest.raises(ProfileError, match="unavailable"):
            _select([_record()], **change)

    @pytest.mark.parametrize("section,key,value", [
        ("environment", "python_full_version", "3.11.10"),
        ("environment", "platform_machine", "ARM64"),
        ("environment", "implementation_name", "pypy"),
        (None, "device", "cuda-12"),
        (None, "tags", ["cp312-cp312-win_amd64"]),
    ])
    def test_actual_target_mismatch_refuses(self, section, key, value):
        record = _record()
        target = deepcopy(record["target"])
        (target[section] if section else target)[key] = value
        with pytest.raises(ProfileError, match="unavailable"):
            _select([record], target=target)

    def test_retention_and_explicit_unprofiled_migration(self):
        cpu = _record()
        gpu = deepcopy(cpu)
        gpu["backend"] = "cuda-12"
        gpu["target"]["device"] = "cuda-12"
        assert _select([cpu, gpu]).profile.record()["backend"] == "cpu"
        assert _select([cpu, gpu], existing_installation=True,
                       target=gpu["target"],
                       retained_backend="cuda-12").profile.record()["backend"] == "cuda-12"
        with pytest.raises(ProfileError, match="explicit migration"):
            _select([cpu], existing_installation=True)
        assert _select([cpu], existing_installation=True, backend="cpu")
        with pytest.raises(ProfileError, match="unavailable"):
            _select([cpu], retained_backend="cuda-12")

    def test_ambiguity_requires_explicit_identity_or_preference(self):
        one = _record()
        two = deepcopy(one)
        two["acquisition_policy"] = "second-approved-policy"
        identity = validate_profile(two).lock_id
        with pytest.raises(ProfileError, match="ambiguous"):
            _select([one, two])
        for records in ([one, two], [two, one]):
            assert _select(records, preference=(identity,)).profile.lock_id == identity
        with pytest.raises(ProfileError, match="unavailable"):
            _select([one], preference=(identity,))

    def test_source_and_artifact_locks_remain_distinct(self):
        source = _record()
        artifact = deepcopy(source)
        artifact.update(mode="artifact", artifact={
            "artifact_id": "sha256:" + "5" * 64,
            "manifest_sha256": "sha256:" + "6" * 64,
        })
        assert validate_profile(source).lock_id != validate_profile(artifact).lock_id
        assert _select([source, artifact], mode="source").profile.record()["artifact"] is None
        with pytest.raises(ProfileError, match="unavailable"):
            _select([source], mode="artifact")

    def test_approval_changes_when_release_mode_or_target_changes(self):
        for field in ("release", "artifact", "target"):
            record = _record()
            original = validate_profile(record).lock_id
            if field == "release":
                record[field]["identity"] = "release-2"
            elif field == "artifact":
                record.update(mode="artifact", artifact={
                    "artifact_id": "sha256:" + "5" * 64,
                    "manifest_sha256": "sha256:" + "6" * 64,
                })
            else:
                record[field]["device"] = "different-device"
            assert validate_profile(record).lock_id != original
            with pytest.raises(ProfileError, match="whole-record approval"):
                _select([record], approvals={original: record["approval_source"]})

    def test_real_fresh_process_uses_inert_api_without_installing(self, tmp_path):
        payload = tmp_path / "profile.json"
        payload.write_text(json.dumps(_record()), encoding="utf-8")
        tools = Path(__file__).resolve().parent
        script = (
            "import json,sys; from runtime_profile import validate_profile,select_profile;"
            "r=json.load(open(sys.argv[1]));p=validate_profile(r);"
            "s=select_profile([r],approvals={p.lock_id:r['approval_source']},"
            "release=r['release'],target=r['target'],component=r['component'],"
            "roots=r['roots'],mode=r['mode']);print(s.profile.lock_id)"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c",
             f"import sys;sys.path.insert(0,{str(tools)!r});" + script, str(payload)],
            cwd=tmp_path, capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == validate_profile(_record()).lock_id
        assert sorted(p.name for p in tmp_path.iterdir()) == ["profile.json"]
