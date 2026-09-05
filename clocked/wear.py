"""
Turns MOT advisory and failure text into a wear signal.

Defect wording is free text, not a fixed vocabulary, so this matches on
keyword patterns per category rather than an exact set of phrases. The
categorisation rules are a plain data structure precisely so a new keyword
or a whole new category can be added without touching any matching logic.

This module only categorises and counts. Deciding whether a wear profile
is unusual enough to report is clocked.detect's job, not this one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .normalise import Reading

# Each category maps to the keywords whose presence in a defect's text marks
# it as belonging to that wear signal. Matching is a case-insensitive
# substring search, deliberately simple because wording is inconsistent
# between testers and test centres. One defect's text can match more than
# one category; that is not double counting, it is one defect touching more
# than one system. Provisional: a starting set of keywords, to be extended
# as real defect text is reviewed.
WEAR_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "corrosion": ("corroded", "corrosion", "rust", "rusty", "perished"),
    "tyre_wear": ("tyre", "tire", "tread"),
    "suspension": ("suspension", "shock absorber", "spring", "strut", "bush"),
    "brakes": ("brake", "braking", "disc worn", "pad"),
    "steering": ("steering", "track rod", "power steering", "ball joint"),
}


@dataclass(frozen=True)
class WearProfile:
    """How much wear signal one test's defect text carries, by category."""

    test_date: date
    miles: int
    category_counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.category_counts.values())


def categorise(text: str) -> set[str]:
    """Every wear category whose keywords appear in this defect text."""
    lowered = text.lower()
    return {
        category
        for category, keywords in WEAR_CATEGORY_KEYWORDS.items()
        if any(keyword in lowered for keyword in keywords)
    }


def build_wear_profile(reading: Reading) -> WearProfile:
    """The wear profile for a single test's reading."""
    counts = {category: 0 for category in WEAR_CATEGORY_KEYWORDS}

    for text in reading.advisory_texts:
        for category in categorise(text):
            counts[category] += 1

    return WearProfile(test_date=reading.test_date, miles=reading.miles, category_counts=counts)


def build_wear_history(readings: list[Reading]) -> list[WearProfile]:
    """A wear profile for the vehicle at each test, oldest first."""
    return [build_wear_profile(reading) for reading in readings]


def accumulated_wear(profiles: list[WearProfile]) -> dict[str, int]:
    """Total wear-indicator count per category across every profile supplied."""
    totals = {category: 0 for category in WEAR_CATEGORY_KEYWORDS}

    for profile in profiles:
        for category, count in profile.category_counts.items():
            totals[category] += count

    return totals
