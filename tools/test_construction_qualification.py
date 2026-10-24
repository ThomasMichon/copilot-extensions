from __future__ import annotations

import copy
import unittest

import construction_qualification as qualification


def plan():
    return {
        "schema": "copilot-extensions.construction-plan",
        "version": 1,
        "source_commit": "a" * 40,
        "candidate_sha256": "b" * 64,
        "policy_sha256": "c" * 64,
        "rows": [{
            "id": "example/base",
            "project": "plugins/example/pyproject.toml",
            "profile": "base",
            "extras": [],
            "native": {
                "os": "windows", "implementation": "cpython", "python_version": "3.13.15",
                "architecture": "arm64", "abi": "cp313",
            },
            "construction_sha256": "d" * 64,
            "installed_launch_sha256": "e" * 64,
        }],
    }


def report(expected):
    return {
        "schema": "copilot-extensions.construction-results",
        "version": 1,
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
        return qualification.assess_qualification(self.plan, self.report)

    def test_exact_success_has_complete_counts_and_no_refusals(self):
        self.assertEqual(self.assess(), {
            "schema": "copilot-extensions.construction-assessment", "version": 1,
            "plan_sha256": qualification.plan_digest(self.plan), "qualified": True,
            "expected_rows": 1, "reported_rows": 1, "reasons": [],
        })

    def test_plan_identity_binds_every_source_candidate_policy_and_row_input(self):
        changes = [
            ("source_commit", "0" * 40), ("candidate_sha256", "0" * 64), ("policy_sha256", "0" * 64),
        ]
        for key, value in changes:
            with self.subTest(key=key):
                expected = copy.deepcopy(self.plan)
                expected[key] = value
                result = qualification.assess_qualification(expected, self.report)
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
                self.assertFalse(qualification.assess_qualification(expected, self.report)["qualified"])

    def test_every_native_dimension_is_bound_and_observed_independently(self):
        for key in self.plan["rows"][0]["native"]:
            with self.subTest(key=key):
                observed = copy.deepcopy(self.report)
                observed["results"][0]["native"][key] = "3.12.1" if key == "python_version" else "other"
                self.assertIn("example/base:native-binding-mismatch",
                              qualification.assess_qualification(self.plan, observed)["reasons"])
                expected = copy.deepcopy(self.plan)
                expected["rows"][0]["native"] = observed["results"][0]["native"]
                self.assertIn("plan-binding-mismatch",
                              qualification.assess_qualification(expected, self.report)["reasons"])

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
                self.assertFalse(qualification.assess_qualification(self.plan, observed)["qualified"])
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
                        qualification.assess_qualification(expected, observed)
        for target in ("plan", "report"):
            for key, value in (("version", True), ("version", 2), ("schema", "other")):
                with self.subTest(target=target, key=key, value=value):
                    expected, observed = plan(), report(plan())
                    (expected if target == "plan" else observed)[key] = value
                    with self.assertRaises(qualification.QualificationError):
                        qualification.assess_qualification(expected, observed)

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
                        qualification.assess_qualification(self.plan, observed)
            with self.subTest(missing_check=name):
                observed = report(self.plan)
                del observed["results"][0]["checks"][name]
                with self.assertRaises(qualification.QualificationError):
                    qualification.assess_qualification(self.plan, observed)

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


if __name__ == "__main__":
    unittest.main()
