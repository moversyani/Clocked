"""
Tests for the normalisation layer. These run against local fixtures and
synthetic payloads, no network or credentials required.
"""

import json
import unittest
from datetime import date
from pathlib import Path

from clocked.normalise import InvalidCurrentReading, add_current_reading, normalise

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def load(name: str):
    with open(FIXTURES / name) as handle:
        return json.load(handle)


def test_payload(tests: list[dict]) -> dict:
    return {"registration": "TEST123", "make": "TEST", "model": "TEST", "motTests": tests}


class TestRetestCollapsing(unittest.TestCase):
    def setUp(self):
        self.readings, self.skipped = normalise(load("retest_history.json"))

    def test_fail_and_retest_collapse_to_one_reading(self):
        """
        Five tests were recorded, but one of those is a failure followed by
        its retest six days later. That is one real inspection, so four
        readings should come out, not five.
        """
        self.assertEqual(len(self.readings), 4)

    def test_surviving_reading_is_the_retest(self):
        collapsed = [r for r in self.readings if r.miles == 40_015]
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0].test_result, "PASSED")

    def test_collapse_is_recorded_on_the_surviving_reading(self):
        collapsed = next(r for r in self.readings if r.miles == 40_015)
        self.assertIsNotNone(collapsed.superseded_reading)
        self.assertEqual(collapsed.superseded_reading.miles, 40_000)
        self.assertEqual(collapsed.superseded_reading.test_result, "FAILED")

    def test_unaffected_readings_carry_no_superseded_reading(self):
        untouched = [r for r in self.readings if r.miles != 40_015]
        self.assertTrue(all(r.superseded_reading is None for r in untouched))


class TestRetestBoundaries(unittest.TestCase):
    def test_failure_and_close_retest_collapse(self):
        readings, _ = normalise(
            test_payload(
                [
                    {
                        "completedDate": "2022-06-01",
                        "testResult": "FAILED",
                        "odometerValue": "10000",
                        "odometerUnit": "mi",
                        "odometerResultType": "READ",
                    },
                    {
                        "completedDate": "2022-06-10",
                        "testResult": "PASSED",
                        "odometerValue": "10010",
                        "odometerUnit": "mi",
                        "odometerResultType": "READ",
                    },
                ]
            )
        )
        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0].miles, 10_010)

    def test_pass_followed_by_close_reading_does_not_collapse(self):
        """
        Collapsing only applies after a failure. Two independent passes that
        happen to land close together, most likely two different vehicles'
        annual due dates lining up, are not a retest and stay separate.
        """
        readings, _ = normalise(
            test_payload(
                [
                    {
                        "completedDate": "2022-06-01",
                        "testResult": "PASSED",
                        "odometerValue": "10000",
                        "odometerUnit": "mi",
                        "odometerResultType": "READ",
                    },
                    {
                        "completedDate": "2022-06-10",
                        "testResult": "PASSED",
                        "odometerValue": "10010",
                        "odometerUnit": "mi",
                        "odometerResultType": "READ",
                    },
                ]
            )
        )
        self.assertEqual(len(readings), 2)

    def test_failure_far_outside_window_does_not_collapse(self):
        readings, _ = normalise(
            test_payload(
                [
                    {
                        "completedDate": "2022-06-01",
                        "testResult": "FAILED",
                        "odometerValue": "10000",
                        "odometerUnit": "mi",
                        "odometerResultType": "READ",
                    },
                    {
                        "completedDate": "2022-09-01",
                        "testResult": "PASSED",
                        "odometerValue": "10050",
                        "odometerUnit": "mi",
                        "odometerResultType": "READ",
                    },
                ]
            )
        )
        self.assertEqual(len(readings), 2)

    def test_failure_with_large_margin_does_not_collapse(self):
        readings, _ = normalise(
            test_payload(
                [
                    {
                        "completedDate": "2022-06-01",
                        "testResult": "FAILED",
                        "odometerValue": "10000",
                        "odometerUnit": "mi",
                        "odometerResultType": "READ",
                    },
                    {
                        "completedDate": "2022-06-10",
                        "testResult": "PASSED",
                        "odometerValue": "12000",
                        "odometerUnit": "mi",
                        "odometerResultType": "READ",
                    },
                ]
            )
        )
        self.assertEqual(len(readings), 2)


class TestCurrentReading(unittest.TestCase):
    def setUp(self):
        self.readings, _ = normalise(
            test_payload(
                [
                    {
                        "completedDate": "2022-01-10",
                        "testResult": "PASSED",
                        "odometerValue": "20000",
                        "odometerUnit": "mi",
                        "odometerResultType": "READ",
                    }
                ]
            )
        )

    def test_current_reading_is_appended_and_flagged(self):
        updated = add_current_reading(self.readings, 21_500, date(2023, 1, 5))
        self.assertEqual(len(updated), 2)
        self.assertTrue(updated[-1].is_user_reported)
        self.assertEqual(updated[-1].miles, 21_500)
        self.assertEqual(updated[-1].test_date, date(2023, 1, 5))

    def test_existing_readings_are_not_flagged(self):
        updated = add_current_reading(self.readings, 21_500, date(2023, 1, 5))
        self.assertFalse(updated[0].is_user_reported)

    def test_negative_mileage_is_rejected(self):
        with self.assertRaises(InvalidCurrentReading):
            add_current_reading(self.readings, -1, date(2023, 1, 5))

    def test_implausibly_large_mileage_is_rejected(self):
        with self.assertRaises(InvalidCurrentReading):
            add_current_reading(self.readings, 5_000_000, date(2023, 1, 5))

    def test_date_before_last_mot_is_rejected(self):
        with self.assertRaises(InvalidCurrentReading):
            add_current_reading(self.readings, 21_500, date(2021, 1, 1))

    def test_date_equal_to_last_mot_is_rejected(self):
        with self.assertRaises(InvalidCurrentReading):
            add_current_reading(self.readings, 21_500, date(2022, 1, 10))

    def test_current_reading_alone_is_accepted(self):
        """A brand new vehicle with no MOT history yet has nothing to be after."""
        updated = add_current_reading([], 500, date(2023, 1, 5))
        self.assertEqual(len(updated), 1)
        self.assertTrue(updated[0].is_user_reported)


if __name__ == "__main__":
    unittest.main(verbosity=2)
