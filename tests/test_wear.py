"""
Tests for the wear categorisation module. Pure functions over plain data,
no fixtures, no network or credentials required.
"""

import unittest
from datetime import date

from clocked.normalise import Reading
from clocked.wear import (
    WEAR_CATEGORY_KEYWORDS,
    accumulated_wear,
    build_wear_history,
    build_wear_profile,
    categorise,
)


def reading(miles: int, advisory_texts: tuple[str, ...] = (), test_date=date(2022, 1, 1)) -> Reading:
    return Reading(
        test_date=test_date,
        miles=miles,
        original_value=miles,
        original_unit="mi",
        test_result="PASSED",
        advisory_texts=advisory_texts,
    )


class TestCategorise(unittest.TestCase):
    def test_matches_a_known_keyword(self):
        self.assertIn("corrosion", categorise("Nearside sill corroded"))

    def test_matching_is_case_insensitive(self):
        self.assertIn("brakes", categorise("BRAKE PAD WORN"))

    def test_unmatched_text_returns_no_category(self):
        self.assertEqual(categorise("Registration plate lamp not working"), set())

    def test_one_defect_can_match_more_than_one_category(self):
        categories = categorise("Suspension spring corroded")
        self.assertIn("suspension", categories)
        self.assertIn("corrosion", categories)

    def test_every_category_has_at_least_one_keyword(self):
        for category, keywords in WEAR_CATEGORY_KEYWORDS.items():
            self.assertTrue(keywords, f"{category} has no keywords")


class TestWearProfile(unittest.TestCase):
    def test_profile_counts_each_matching_category(self):
        profile = build_wear_profile(
            reading(10_000, ("Front tyre tread depth below limit", "Brake pad worn"))
        )
        self.assertEqual(profile.category_counts["tyre_wear"], 1)
        self.assertEqual(profile.category_counts["brakes"], 1)
        self.assertEqual(profile.category_counts["corrosion"], 0)
        self.assertEqual(profile.total, 2)

    def test_reading_with_no_advisories_has_an_empty_profile(self):
        profile = build_wear_profile(reading(10_000))
        self.assertEqual(profile.total, 0)

    def test_profile_keeps_the_reading_date_and_mileage(self):
        profile = build_wear_profile(reading(15_000, test_date=date(2021, 6, 1)))
        self.assertEqual(profile.miles, 15_000)
        self.assertEqual(profile.test_date, date(2021, 6, 1))


class TestWearHistory(unittest.TestCase):
    def test_history_has_one_profile_per_reading(self):
        readings = [reading(10_000), reading(20_000, ("Corroded sill",))]
        history = build_wear_history(readings)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[1].category_counts["corrosion"], 1)

    def test_accumulated_wear_sums_across_the_whole_history(self):
        readings = [
            reading(10_000, ("Brake pad worn",)),
            reading(20_000, ("Brake disc worn", "Corroded sill")),
        ]
        totals = accumulated_wear(build_wear_history(readings))
        self.assertEqual(totals["brakes"], 2)
        self.assertEqual(totals["corrosion"], 1)
        self.assertEqual(totals["steering"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
