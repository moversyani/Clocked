"""
Tests for the detection engine. These run against local fixtures and need no
API key, no network and no credentials — the whole engine is testable offline.
"""

import json
import unittest
from pathlib import Path

from clocked.detect import Severity, Verdict, analyse
from clocked.normalise import normalise

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def load(name: str):
    with open(FIXTURES / name) as handle:
        return json.load(handle)


def report_for(name: str):
    readings, skipped = normalise(load(name))
    return analyse(readings, skipped)


def codes(report) -> set[str]:
    return {finding.code for finding in report.findings}


class TestCleanHistory(unittest.TestCase):
    def setUp(self):
        self.report = report_for("clean_history.json")

    def test_verdict_is_clear(self):
        self.assertEqual(self.report.verdict, Verdict.CLEAR)

    def test_all_readings_used(self):
        self.assertEqual(len(self.report.readings), 5)
        self.assertEqual(self.report.skipped, [])

    def test_no_serious_findings(self):
        severities = {finding.severity for finding in self.report.findings}
        self.assertNotIn(Severity.CRITICAL, severities)
        self.assertNotIn(Severity.WARNING, severities)


class TestRollbackHistory(unittest.TestCase):
    def setUp(self):
        self.report = report_for("rollback_history.json")

    def test_rollback_is_detected(self):
        self.assertIn("ODOMETER_ROLLBACK", codes(self.report))

    def test_verdict_is_tampering(self):
        self.assertEqual(self.report.verdict, Verdict.EVIDENCE_OF_TAMPERING)

    def test_rollback_measured_against_running_peak(self):
        """
        The car was wound back in 2021 and then driven normally for two more
        years. Every later reading is still below the 2020 peak, so comparing
        only against the previous reading would find nothing after 2021.
        """
        rollbacks = [f for f in self.report.findings if f.code == "ODOMETER_ROLLBACK"]
        self.assertEqual(len(rollbacks), 3)
        self.assertEqual(rollbacks[0].from_reading.miles, 104_510)
        self.assertEqual(rollbacks[0].to_reading.miles, 58_300)


class TestMessyHistory(unittest.TestCase):
    def setUp(self):
        self.report = report_for("messy_history.json")

    def test_kilometre_reading_is_converted(self):
        converted = [r for r in self.report.readings if r.was_converted]
        self.assertEqual(len(converted), 1)
        self.assertEqual(converted[0].miles, 28_707)

    def test_conversion_prevents_a_false_rollback(self):
        """
        Raw, the 2020 reading of 46,200 km looks higher than the 2022 reading
        of 39,880 mi. Converted, it is lower and the history is consistent.
        """
        self.assertNotIn("ODOMETER_ROLLBACK", codes(self.report))

    def test_missing_reading_is_skipped_not_guessed(self):
        self.assertEqual(len(self.report.skipped), 1)
        self.assertIn("no odometer reading", self.report.skipped[0].reason)

    def test_comma_formatted_value_is_parsed(self):
        self.assertIn(39_880, [r.miles for r in self.report.readings])

    def test_static_year_is_flagged(self):
        self.assertIn("STATIC_MILEAGE", codes(self.report))
        self.assertEqual(self.report.verdict, Verdict.REVIEW)


class TestEdgeCases(unittest.TestCase):
    def test_empty_history(self):
        readings, skipped = normalise({"motTests": []})
        report = analyse(readings, skipped)
        self.assertEqual(report.verdict, Verdict.INSUFFICIENT_DATA)

    def test_single_reading_cannot_be_judged(self):
        readings, skipped = normalise(
            {
                "motTests": [
                    {
                        "completedDate": "2023-01-01",
                        "odometerValue": "10000",
                        "odometerUnit": "mi",
                        "odometerResultType": "READ",
                    }
                ]
            }
        )
        report = analyse(readings, skipped)
        self.assertEqual(report.verdict, Verdict.INSUFFICIENT_DATA)


if __name__ == "__main__":
    unittest.main(verbosity=2)
