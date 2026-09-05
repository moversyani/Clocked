"""
Tests for the detection engine. These run against local fixtures and need no
API key, no network and no credentials. The whole engine is testable offline.
"""

import json
import unittest
from datetime import date
from pathlib import Path

from clocked.detect import Severity, Verdict, analyse
from clocked.normalise import add_current_reading, normalise

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def load(name: str):
    with open(FIXTURES / name) as handle:
        return json.load(handle)


def report_for(name: str):
    readings, skipped = normalise(load(name))
    return analyse(readings, skipped)


def codes(report) -> set[str]:
    return {finding.code for finding in report.findings}


def mot_test(completed_date: str, miles: str, result: str = "PASSED") -> dict:
    return {
        "completedDate": completed_date,
        "testResult": result,
        "odometerValue": miles,
        "odometerUnit": "mi",
        "odometerResultType": "READ",
    }


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
        self.assertEqual(len(rollbacks), 1)
        self.assertEqual(rollbacks[0].from_reading.miles, 104_510)
        self.assertEqual(rollbacks[0].to_reading.miles, 58_300)

    def test_rollback_is_grouped_into_one_event(self):
        """
        Three tests in a row (2021, 2022, 2023) sit below the 2020 peak. That
        is one act of tampering, not three, so it must be one finding that
        records how many tests it spans and for how long.
        """
        rollbacks = [f for f in self.report.findings if f.code == "ODOMETER_ROLLBACK"]
        self.assertEqual(len(rollbacks), 1)
        finding = rollbacks[0]
        self.assertEqual(finding.affected_tests, 3)
        self.assertEqual(
            finding.duration_days,
            (date(2023, 4, 15) - date(2020, 3, 14)).days,
        )


class TestDoubleRollbackHistory(unittest.TestCase):
    def setUp(self):
        self.report = report_for("double_rollback_history.json")

    def test_recovery_ends_a_group_and_a_new_drop_starts_another(self):
        """
        The car recovers above its first peak (65,000 in 2021) before being
        wound back a second time. That recovery must close the first event
        rather than let the second drop merge into it.
        """
        rollbacks = [f for f in self.report.findings if f.code == "ODOMETER_ROLLBACK"]
        self.assertEqual(len(rollbacks), 2)

        self.assertEqual(rollbacks[0].from_reading.miles, 60_000)
        self.assertEqual(rollbacks[0].to_reading.miles, 40_000)
        self.assertEqual(rollbacks[0].affected_tests, 1)

        self.assertEqual(rollbacks[1].from_reading.miles, 65_000)
        self.assertEqual(rollbacks[1].to_reading.miles, 45_000)
        self.assertEqual(rollbacks[1].affected_tests, 1)


class TestMileageShortfall(unittest.TestCase):
    def setUp(self):
        self.report = report_for("shortfall_history.json")

    def test_shortfall_is_reported(self):
        """
        The car did 15,000 miles a year before being wound back in 2017. By
        2019 it has only reached 21,000, far short of what that rate would
        put it at, even though every reading since the rollback has climbed
        normally on its own.
        """
        self.assertIn("MILEAGE_SHORTFALL", codes(self.report))

    def test_shortfall_measured_from_the_pre_rollback_peak(self):
        finding = next(
            f for f in self.report.findings if f.code == "MILEAGE_SHORTFALL"
        )
        self.assertEqual(finding.from_reading.miles, 25_000)
        self.assertEqual(finding.to_reading.miles, 21_000)

    def test_shortfall_is_a_warning_not_a_verdict_changer_on_its_own(self):
        finding = next(
            f for f in self.report.findings if f.code == "MILEAGE_SHORTFALL"
        )
        self.assertEqual(finding.severity, Severity.WARNING)


class TestMileageShortfallSkipsThinBaseline(unittest.TestCase):
    def test_shortfall_is_skipped_without_enough_clean_history(self):
        """
        The rollback here happens on the very first test, so there is only
        one clean reading before it, not enough to establish a baseline
        rate. The check must skip rather than guess.
        """
        report = report_for("shortfall_insufficient_baseline.json")
        self.assertIn("ODOMETER_ROLLBACK", codes(report))
        self.assertNotIn("MILEAGE_SHORTFALL", codes(report))


class TestWearMismatch(unittest.TestCase):
    def test_heavy_wear_against_low_mileage_is_flagged(self):
        report = report_for("heavy_wear_history.json")
        self.assertIn("WEAR_MILEAGE_MISMATCH", codes(report))

    def test_finding_is_never_more_than_a_warning(self):
        report = report_for("heavy_wear_history.json")
        finding = next(f for f in report.findings if f.code == "WEAR_MILEAGE_MISMATCH")
        self.assertEqual(finding.severity, Severity.WARNING)

    def test_wording_is_hedged_not_accusatory(self):
        report = report_for("heavy_wear_history.json")
        finding = next(f for f in report.findings if f.code == "WEAR_MILEAGE_MISMATCH")
        self.assertIn("weak evidence", finding.summary)
        self.assertIn("varies enormously", finding.summary)

    def test_same_wear_at_high_mileage_is_not_flagged(self):
        """
        The exact same accumulated wear indicators, but against 90,000
        claimed miles instead of 11,000. Heavy wear at genuinely high
        mileage is expected, not mismatched, so this must not fire.
        """
        report = report_for("heavy_wear_high_mileage.json")
        self.assertNotIn("WEAR_MILEAGE_MISMATCH", codes(report))

    def test_light_wear_is_not_flagged(self):
        report = report_for("clean_history.json")
        self.assertNotIn("WEAR_MILEAGE_MISMATCH", codes(report))

    def test_claimed_mileage_can_be_a_current_reading(self):
        readings, skipped = normalise(load("heavy_wear_history.json"))
        readings = add_current_reading(readings, 12_000, date(2024, 1, 8))
        report = analyse(readings, skipped)

        self.assertIn("WEAR_MILEAGE_MISMATCH", codes(report))
        finding = next(f for f in report.findings if f.code == "WEAR_MILEAGE_MISMATCH")
        self.assertTrue(finding.to_reading.is_user_reported)
        self.assertIn("unverified", finding.summary)


class TestCurrentReading(unittest.TestCase):
    def readings_from(self, tests: list[dict]) -> list:
        payload = {"registration": "CR20ABC", "make": "TEST", "model": "TEST", "motTests": tests}
        readings, _ = normalise(payload)
        return readings

    def test_current_reading_below_peak_is_a_rollback(self):
        readings = self.readings_from(
            [
                mot_test("2020-01-10", "20000"),
                mot_test("2021-01-10", "40000"),
            ]
        )
        readings = add_current_reading(readings, 25_000, date(2022, 1, 10))
        report = analyse(readings)

        self.assertIn("ODOMETER_ROLLBACK", codes(report))
        finding = next(f for f in report.findings if f.code == "ODOMETER_ROLLBACK")
        self.assertEqual(finding.to_reading.miles, 25_000)
        self.assertTrue(finding.to_reading.is_user_reported)
        self.assertIn("unverified", finding.summary)
        self.assertEqual(report.verdict, Verdict.EVIDENCE_OF_TAMPERING)

    def test_current_reading_with_implausible_jump_is_flagged(self):
        readings = self.readings_from([mot_test("2023-01-10", "20000")])
        readings = add_current_reading(readings, 120_000, date(2023, 4, 10))
        report = analyse(readings)

        self.assertIn("IMPLAUSIBLE_JUMP", codes(report))
        finding = next(f for f in report.findings if f.code == "IMPLAUSIBLE_JUMP")
        self.assertTrue(finding.to_reading.is_user_reported)
        self.assertIn("unverified", finding.summary)

    def test_current_reading_with_implausibly_low_rate_is_flagged(self):
        """
        Four months after a test at 40,000 miles, the dashboard shows
        40,020. That is not a rollback and not a year of storage, but the
        rate is too slow to be ordinary use, and the only new figure in that
        pair is the unverified one.
        """
        readings = self.readings_from([mot_test("2023-01-10", "40000")])
        readings = add_current_reading(readings, 40_020, date(2023, 5, 10))
        report = analyse(readings)

        self.assertIn("IMPLAUSIBLE_SLOWDOWN", codes(report))
        finding = next(f for f in report.findings if f.code == "IMPLAUSIBLE_SLOWDOWN")
        self.assertEqual(finding.severity, Severity.WARNING)
        self.assertTrue(finding.to_reading.is_user_reported)
        self.assertIn("unverified", finding.summary)

    def test_ordinary_current_reading_raises_no_findings(self):
        readings = self.readings_from(
            [
                mot_test("2021-01-10", "20000"),
                mot_test("2022-01-10", "31000"),
            ]
        )
        readings = add_current_reading(readings, 36_500, date(2022, 7, 10))
        report = analyse(readings)

        self.assertEqual(report.findings, [])
        self.assertEqual(report.verdict, Verdict.CLEAR)

    def test_current_reading_does_not_change_the_shortfall_baseline(self):
        """
        The shortfall projection is drawn from the vehicle's own pre-rollback
        MOT history. Appending an unverified current reading afterwards must
        not become the figure that projection is measured against.
        """
        without_current = report_for("shortfall_history.json")
        shortfall_without = next(
            f for f in without_current.findings if f.code == "MILEAGE_SHORTFALL"
        )

        readings, skipped = normalise(load("shortfall_history.json"))
        readings = add_current_reading(readings, 21_050, date(2019, 6, 1))
        with_current = analyse(readings, skipped)
        shortfall_with = next(
            f for f in with_current.findings if f.code == "MILEAGE_SHORTFALL"
        )

        self.assertEqual(shortfall_without.to_reading.miles, shortfall_with.to_reading.miles)
        self.assertFalse(shortfall_with.to_reading.is_user_reported)

    def test_current_reading_does_not_trigger_a_testing_gap(self):
        """
        A car last tested well over a year ago and checked today via the
        dashboard has a real gap in its MOT record, but the pair ending in
        the unverified current reading is not itself an untested period in
        the same sense, so it must not also raise a TESTING_GAP.
        """
        readings = self.readings_from([mot_test("2015-01-10", "15000")])
        readings = add_current_reading(readings, 22_000, date(2020, 1, 10))
        report = analyse(readings)

        self.assertNotIn("TESTING_GAP", codes(report))


class TestTestingGaps(unittest.TestCase):
    def test_no_gap_on_a_regular_annual_history(self):
        report = report_for("clean_history.json")
        self.assertNotIn("TESTING_GAP", codes(report))

    def test_covid_era_gap_is_noted_as_likely_explained(self):
        report = report_for("covid_gap_history.json")
        self.assertIn("TESTING_GAP", codes(report))
        finding = next(f for f in report.findings if f.code == "TESTING_GAP")
        self.assertEqual(finding.severity, Severity.INFO)
        self.assertIn("COVID", finding.summary)

    def test_long_gap_outside_covid_is_unexplained_not_suspicious(self):
        report = report_for("long_gap_history.json")
        self.assertIn("TESTING_GAP", codes(report))
        finding = next(f for f in report.findings if f.code == "TESTING_GAP")
        self.assertEqual(finding.severity, Severity.INFO)
        self.assertNotIn("COVID", finding.summary)
        self.assertIn("unexplained", finding.summary)
        self.assertIn("not necessarily suspicious", finding.summary)

    def test_a_test_with_no_usable_reading_still_counts_as_a_test(self):
        """
        messy_history has a real test in 2021 that just has no usable
        odometer value. The gap between the surviving readings either side
        of it spans more than a year, but a test did happen there, so this
        must not be reported as the record going dark.
        """
        report = report_for("messy_history.json")
        self.assertNotIn("TESTING_GAP", codes(report))


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
