from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

import construction_qualification as qualification


def target_policy():
    return {
        "schema": "copilot-extensions.construction-targets",
        "version": 1,
        "python_minors": ["3.13"],
        "targets": [{
            "id": "windows-arm64", "os": "windows", "architecture": "arm64", "hardware": "generic",
        }],
    }


def assess(expected, observed):
    return qualification.assess_qualification(expected, observed, target_policy=target_policy())


def plan():
    return {
        "schema": "copilot-extensions.construction-plan",
        "version": 2,
        "source_commit": "a" * 40,
        "candidate_sha256": "b" * 64,
        "policy_sha256": "c" * 64,
        "native_policy_sha256": qualification.target_policy_digest(target_policy()),
        "rows": [{
            "id": "example/base",
            "project": "plugins/example/pyproject.toml",
            "profile": "base",
            "extras": [],
            "native": {
                "os": "windows", "implementation": "cpython", "python_version": "3.13.15",
                "architecture": "arm64", "abi": "cp313", "hardware": "generic",
            },
            "construction_sha256": "d" * 64,
            "installed_launch_sha256": "e" * 64,
        }],
    }


def report(expected):
    return {
        "schema": "copilot-extensions.construction-results",
        "version": 2,
        "plan_sha256": qualification.plan_digest(expected),
        "results": [{
            "row_id": row["id"],
            "native": copy.deepcopy(row["native"]),
            "status": "passed",
            "detail": "",
            "duration_seconds": 1.5,
            "checks": {name: {"status": "passed", "evidence_sha256": "f" * 64} for name in (
                "construction", "installed_origin", "installed_launch", "feed_policy", "cold_environment",
            )},
        } for row in expected["rows"]],
    }


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.plan = plan()
        self.report = report(self.plan)

    def assess(self):
        return assess(self.plan, self.report)

    def test_exact_success_has_complete_counts_and_no_refusals(self):
        self.assertEqual(self.assess(), {
            "schema": "copilot-extensions.construction-assessment", "version": 2,
            "plan_sha256": qualification.plan_digest(self.plan), "qualified": True,
            "expected_rows": 1, "reported_rows": 1, "reasons": [],
        })

    def test_plan_identity_binds_every_source_candidate_policy_and_row_input(self):
        changes = [
            ("source_commit", "0" * 40), ("candidate_sha256", "0" * 64), ("policy_sha256", "0" * 64),
            ("native_policy_sha256", "0" * 64),
        ]
        for key, value in changes:
            with self.subTest(key=key):
                expected = copy.deepcopy(self.plan)
                expected[key] = value
                result = assess(expected, self.report)
                self.assertFalse(result["qualified"])
                self.assertIn("plan-binding-mismatch", result["reasons"])
        for key, value in (
            ("id", "example/other"), ("project", "services/example/pyproject.toml"),
            ("profile", "server"), ("extras", ["server"]), ("construction_sha256", "0" * 64),
            ("installed_launch_sha256", "0" * 64),
        ):
            with self.subTest(key=key):
                expected = copy.deepcopy(self.plan)
                expected["rows"][0][key] = value
                self.assertFalse(assess(expected, self.report)["qualified"])

    def test_every_native_dimension_is_bound_and_observed_independently(self):
        for key in self.plan["rows"][0]["native"]:
            with self.subTest(key=key):
                observed = copy.deepcopy(self.report)
                observed["results"][0]["native"][key] = "3.12.1" if key == "python_version" else "other"
                self.assertIn("example/base:native-binding-mismatch",
                              assess(self.plan, observed)["reasons"])
                expected = copy.deepcopy(self.plan)
                expected["rows"][0]["native"] = observed["results"][0]["native"]
                self.assertIn("plan-binding-mismatch",
                              assess(expected, self.report)["reasons"])

    def test_missing_unknown_duplicate_and_empty_results_cannot_pass(self):
        self.report["results"] = []
        self.assertEqual(self.assess()["reasons"], ["example/base:missing-row"])
        self.report = report(self.plan)
        self.report["results"][0]["row_id"] = "unknown"
        self.assertEqual(self.assess()["reasons"], ["example/base:missing-row", "unknown:unexpected-row"])
        self.report = report(self.plan)
        self.report["results"].append(copy.deepcopy(self.report["results"][0]))
        with self.assertRaisesRegex(qualification.QualificationError, "duplicate result"):
            self.assess()

    def test_no_unsuccessful_row_or_check_is_a_successful_skip(self):
        for status in ("failed", "skipped", "timed-out", "unavailable"):
            with self.subTest(status=status):
                self.report = report(self.plan)
                self.report["results"][0].update(status=status, detail="explicit failure")
                self.assertFalse(self.assess()["qualified"])
            for name in self.report["results"][0]["checks"]:
                with self.subTest(status=status, check=name):
                    self.report = report(self.plan)
                    self.report["results"][0]["checks"][name]["status"] = status
                    self.assertFalse(self.assess()["qualified"])

    def test_pass_requires_evidence_for_every_check_and_actual_native_identity(self):
        for name in self.report["results"][0]["checks"]:
            with self.subTest(check=name):
                observed = report(self.plan)
                observed["results"][0]["checks"][name]["evidence_sha256"] = None
                self.assertFalse(assess(self.plan, observed)["qualified"])
        self.report["results"][0]["native"] = None
        self.assertFalse(self.assess()["qualified"])

    def test_bad_durations_are_not_measurements(self):
        for value in (True, -1, float("nan"), float("inf"), "1", None):
            with self.subTest(value=value):
                self.report["results"][0]["duration_seconds"] = value
                with self.assertRaisesRegex(qualification.QualificationError, "duration"):
                    self.assess()

    def test_schemas_unknown_fields_and_boolean_versions_are_refused(self):
        for target in ("plan", "report", "row", "result", "native", "check"):
            for mutation in ("extra", "missing"):
                with self.subTest(target=target, mutation=mutation):
                    expected, observed = plan(), report(plan())
                    records = {
                        "plan": expected, "report": observed, "row": expected["rows"][0],
                        "result": observed["results"][0], "native": expected["rows"][0]["native"],
                        "check": observed["results"][0]["checks"]["construction"],
                    }
                    if mutation == "extra":
                        records[target]["unexpected"] = True
                    else:
                        records[target].pop(next(iter(records[target])))
                    with self.assertRaises(qualification.QualificationError):
                        assess(expected, observed)
        for target in ("plan", "report"):
            for key, value in (("version", True), ("version", 1), ("schema", "other")):
                with self.subTest(target=target, key=key, value=value):
                    expected, observed = plan(), report(plan())
                    (expected if target == "plan" else observed)[key] = value
                    with self.assertRaises(qualification.QualificationError):
                        assess(expected, observed)

    def test_empty_duplicate_and_aliased_plan_rows_are_refused(self):
        self.plan["rows"] = []
        with self.assertRaisesRegex(qualification.QualificationError, "nonempty"):
            self.assess()
        self.plan = plan()
        self.plan["rows"].append(copy.deepcopy(self.plan["rows"][0]))
        with self.assertRaisesRegex(qualification.QualificationError, "duplicate plan"):
            self.assess()
        self.plan["rows"][1]["id"] = "alias"
        with self.assertRaisesRegex(qualification.QualificationError, "duplicate environment"):
            self.assess()

    def test_paths_profiles_extras_and_partial_native_descriptions_are_refused(self):
        for project in ("../pyproject.toml", "/pyproject.toml", "C:/pyproject.toml",
                        "plugins\\example\\pyproject.toml", "./pyproject.toml", "x//pyproject.toml", "x.py"):
            with self.subTest(project=project):
                self.plan = plan()
                self.plan["rows"][0]["project"] = project
                with self.assertRaises(qualification.QualificationError):
                    self.assess()
        for extras in (None, "server", ["Server"], ["some_extra"], ["a", "a"], ["z", "a"], [None]):
            with self.subTest(extras=extras):
                self.plan = plan()
                self.plan["rows"][0]["extras"] = extras
                with self.assertRaises(qualification.QualificationError):
                    self.assess()
        for key in self.plan["rows"][0]["native"]:
            with self.subTest(native=key):
                self.plan = plan()
                self.plan["rows"][0]["native"][key] = ""
                with self.assertRaises(qualification.QualificationError):
                    self.assess()
        self.plan = plan()
        self.plan["rows"][0]["native"]["python_version"] = ">=3.10"
        with self.assertRaises(qualification.QualificationError):
            self.assess()

    def test_malformed_digests_statuses_and_silent_errors_are_refused(self):
        for value in (None, "abc", "A" * 64, 0):
            with self.subTest(value=value):
                self.report["plan_sha256"] = value
                with self.assertRaises(qualification.QualificationError):
                    self.assess()
        for value in ("unknown", True, []):
            with self.subTest(status=value):
                self.report = report(self.plan)
                self.report["results"][0]["status"] = value
                with self.assertRaises(qualification.QualificationError):
                    self.assess()
        self.report = report(self.plan)
        self.report["results"][0]["status"] = "failed"
        with self.assertRaisesRegex(qualification.QualificationError, "diagnostic"):
            self.assess()

    def test_every_stage_must_have_its_exact_evidence_shape(self):
        for name in self.report["results"][0]["checks"]:
            for value in (
                None, {"status": "passed"}, {"status": True, "evidence_sha256": None},
                {"status": "passed", "evidence_sha256": "not-a-digest"},
            ):
                with self.subTest(check=name, value=value):
                    observed = report(self.plan)
                    observed["results"][0]["checks"][name] = value
                    with self.assertRaises(qualification.QualificationError):
                        assess(self.plan, observed)
            with self.subTest(missing_check=name):
                observed = report(self.plan)
                del observed["results"][0]["checks"][name]
                with self.assertRaises(qualification.QualificationError):
                    assess(self.plan, observed)

    def test_independent_profiles_require_independent_coverage_and_stable_ordering(self):
        server = copy.deepcopy(self.plan["rows"][0])
        server.update(id="example/server", profile="server", extras=["server"])
        self.plan["rows"].append(server)
        self.report = report(self.plan)
        digest = qualification.plan_digest(self.plan)
        self.plan["rows"].reverse()
        self.report["results"].reverse()
        self.assertEqual(qualification.plan_digest(self.plan), digest)
        self.assertTrue(self.assess()["qualified"])
        self.report["results"].pop()
        self.assertFalse(self.assess()["qualified"])


class NativeMatrixTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads(Path(__file__).with_name("construction-targets.json").read_text("utf-8"))
        self.plan = plan()
        self.plan["native_policy_sha256"] = qualification.target_policy_digest(self.policy)
        self.plan["rows"] = []
        for target in self.policy["targets"]:
            for minor in self.policy["python_minors"]:
                row = copy.deepcopy(plan()["rows"][0])
                row["id"] = f"example/base/{target['id']}/{minor}"
                row["native"] = {
                    "os": target["os"], "implementation": "cpython", "python_version": minor + ".0",
                    "architecture": target["architecture"], "hardware": target["hardware"],
                    "abi": "cp" + minor.replace(".", ""),
                }
                self.plan["rows"].append(row)

    def assess(self):
        return qualification.assess_qualification(
            self.plan, report(self.plan), target_policy=self.policy,
        )

    def test_canonical_policy_requires_seven_native_targets_and_five_released_minors(self):
        self.assertEqual(self.policy["python_minors"], ["3.11", "3.12", "3.13", "3.14", "3.15"])
        self.assertEqual({target["id"] for target in self.policy["targets"]}, {
            "windows-x64", "windows-arm64", "linux-x64", "linux-arm64",
            "macos-x64", "macos-arm64", "raspberry-pi-5",
        })
        result = self.assess()
        self.assertTrue(result["qualified"])
        self.assertEqual(result["expected_rows"], 35)

    def test_exact_reports_for_a_reduced_plan_cannot_hide_missing_targets(self):
        for target in self.policy["targets"]:
            with self.subTest(target=target["id"]):
                retained = self.plan["rows"]
                self.plan["rows"] = [row for row in retained if f"/{target['id']}/" not in row["id"]]
                result = self.assess()
                self.assertFalse(result["qualified"])
                self.assertEqual(len(result["reasons"]), 5)
                self.assertTrue(all(f":missing-target:{target['id']}:" in reason for reason in result["reasons"]))
                self.plan["rows"] = retained
        self.plan["rows"].pop()
        self.assertFalse(self.assess()["qualified"])

    def test_a_profile_cannot_borrow_another_profiles_native_rows(self):
        extra = copy.deepcopy(self.plan["rows"][0])
        extra.update(id="example/server", profile="server", extras=["server"])
        self.plan["rows"].append(extra)
        result = self.assess()
        self.assertFalse(result["qualified"])
        self.assertEqual(len(result["reasons"]), 34)
        self.assertTrue(all(":server:server:missing-target:" in reason for reason in result["reasons"]))

    def test_generic_linux_arm64_cannot_supply_a_pi5_observation(self):
        observed = report(self.plan)
        pi5 = next(row for row in observed["results"] if "/raspberry-pi-5/" in row["row_id"])
        pi5["native"]["hardware"] = "generic"
        result = qualification.assess_qualification(self.plan, observed, target_policy=self.policy)
        self.assertFalse(result["qualified"])
        self.assertIn(pi5["row_id"] + ":native-binding-mismatch", result["reasons"])

    def test_two_patch_versions_do_not_supply_two_independent_native_targets(self):
        duplicate = copy.deepcopy(self.plan["rows"][0])
        duplicate["id"] += "/different-patch"
        duplicate["native"]["python_version"] = "3.11.1"
        self.plan["rows"].append(duplicate)
        self.assertIn(duplicate["id"] + ":duplicate-native-target", self.assess()["reasons"])

    def test_another_implementation_or_free_threaded_abi_does_not_supply_standard_cpython(self):
        for key, value in (("implementation", "pypy"), ("abi", "cp311t"), ("abi", "cp311d")):
            with self.subTest(key=key, value=value):
                original = self.plan["rows"][0]["native"][key]
                self.plan["rows"][0]["native"][key] = value
                self.assertIn(self.plan["rows"][0]["id"] + ":nonstandard-python-abi",
                              self.assess()["reasons"])
                self.plan["rows"][0]["native"][key] = original

    def test_extra_targets_and_old_python_versions_are_not_silent_policy_changes(self):
        for key, value in (("python_version", "3.10.0"), ("os", "other"), ("architecture", "x86")):
            with self.subTest(key=key, value=value):
                original = self.plan["rows"][0]["native"][key]
                self.plan["rows"][0]["native"][key] = value
                self.assertIn(self.plan["rows"][0]["id"] + ":unexpected-native-target",
                              self.assess()["reasons"])
                self.plan["rows"][0]["native"][key] = original

    def test_native_policy_is_independently_bound_and_target_order_is_nonsemantic(self):
        digest = qualification.target_policy_digest(self.policy)
        self.policy["targets"].reverse()
        self.assertEqual(qualification.target_policy_digest(self.policy), digest)
        self.assertTrue(self.assess()["qualified"])
        self.policy["targets"].pop()
        self.assertIn("native-policy-binding-mismatch", self.assess()["reasons"])

    def test_bad_target_policy_is_an_explicit_error(self):
        for minors in ([], None, "3.11", ["3.10"], ["3.11", "3.13"], ["3.12", "3.11"],
                       ["3.11", "3.11"], ["3.11.1"], [True]):
            with self.subTest(minors=minors):
                policy = target_policy()
                policy["python_minors"] = minors
                with self.assertRaises(qualification.QualificationError):
                    qualification.target_policy_digest(policy)
        for targets in ([], None, [target_policy()["targets"][0]] * 2):
            with self.subTest(targets=targets):
                policy = target_policy()
                policy["targets"] = targets
                with self.assertRaises(qualification.QualificationError):
                    qualification.target_policy_digest(policy)
        for key, value in (("os", "other"), ("architecture", "aarch64"), ("hardware", "unknown")):
            with self.subTest(key=key, value=value):
                policy = target_policy()
                policy["targets"][0][key] = value
                with self.assertRaises(qualification.QualificationError):
                    qualification.target_policy_digest(policy)
        policy = target_policy()
        policy["targets"][0]["hardware"] = "raspberry-pi-5"
        with self.assertRaises(qualification.QualificationError):
            qualification.target_policy_digest(policy)


if __name__ == "__main__":
    unittest.main()
