"""
Turn raw DVSA MOT History JSON into a clean, sorted list of odometer readings.

The API does not promise clean data. A single vehicle's history can contain
readings in miles and in kilometres, tests with no reading at all, and tests
recorded out of order. Every one of those is handled here, before any
detection logic runs, so the detector only ever sees trustworthy input.

Each test's advisory and failure text is kept alongside its reading rather
than discarded, since clocked.wear turns that free text into a wear signal
that a mileage figure alone cannot provide.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime

KM_TO_MILES = 0.621371

# Two tests within this many days of each other are almost certainly a
# failed test followed by its retest, not two independent inspections.
# Provisional until validated against a larger sample of real retest
# intervals.
RETEST_WINDOW_DAYS = 30

# A retest's mileage should barely have moved from the failed test that
# preceded it. This margin tells a genuine retest apart from two unrelated
# tests that happen to land close together. Provisional until validated
# against a larger sample.
RETEST_MARGIN_MILES = 100

# A sanity bound on a user-entered current reading, not a fraud threshold.
# It exists only to reject obvious typos and keyboard slips before the value
# ever reaches the detector. Provisional until validated against a larger
# sample.
CURRENT_READING_MAX_MILES = 1_000_000


@dataclass(frozen=True)
class Reading:
    """One odometer reading, normalised to miles."""

    test_date: date
    miles: int
    original_value: int
    original_unit: str
    test_result: str
    superseded_reading: "Reading | None" = None
    is_user_reported: bool = False
    advisory_texts: tuple[str, ...] = ()

    @property
    def was_converted(self) -> bool:
        return self.original_unit == "km"


class InvalidCurrentReading(ValueError):
    """Raised when a user-supplied current reading cannot be used."""


@dataclass(frozen=True)
class SkippedTest:
    """A test we could not use, and why."""

    test_date: date | None
    reason: str


def _parse_date(raw: str) -> date | None:
    """DVSA has used more than one date format over the years. Try each."""
    if not raw:
        return None

    formats = (
        "%Y-%m-%d",
        "%Y.%m.%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
    )

    for fmt in formats:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue

    return None


def _to_miles(value: int, unit: str) -> int | None:
    """Normalise a reading to miles. Unknown units are rejected, not guessed."""
    unit = (unit or "").strip().lower()

    if unit in ("mi", "miles"):
        return value
    if unit in ("km", "kilometres", "kilometers"):
        return round(value * KM_TO_MILES)

    return None


def _collapse_retests(readings: list[Reading]) -> list[Reading]:
    """
    An MOT failure followed by a retest days later is one real world test,
    not two. Where a failed reading is followed within RETEST_WINDOW_DAYS by
    another reading within RETEST_MARGIN_MILES of it, keep the later reading
    and record the earlier one it superseded, so the count of readings
    reflects real inspections rather than paperwork.
    """
    if not readings:
        return readings

    collapsed = [readings[0]]

    for current in readings[1:]:
        previous = collapsed[-1]
        days = (current.test_date - previous.test_date).days
        margin = abs(current.miles - previous.miles)

        if (
            previous.test_result.upper() == "FAILED"
            and days <= RETEST_WINDOW_DAYS
            and margin <= RETEST_MARGIN_MILES
        ):
            collapsed[-1] = replace(current, superseded_reading=previous)
        else:
            collapsed.append(current)

    return collapsed


def add_current_reading(
    readings: list[Reading], miles: int, reported_date: date
) -> list[Reading]:
    """
    Fold a user-reported dashboard reading into the reading history.

    This closes the gap between the last MOT and today, the one window
    where tampering leaves no trace in the official record. It is the only
    reading in the history that DVSA has not verified, so it is flagged as
    user-reported rather than silently blended in with the rest, and it must
    sit after every existing reading: it exists specifically to cover the
    period since the last known test, and an earlier date would mean either
    the date or the history is wrong.
    """
    if miles < 0 or miles > CURRENT_READING_MAX_MILES:
        raise InvalidCurrentReading(f"{miles} miles is not a plausible odometer reading.")

    if readings and reported_date <= readings[-1].test_date:
        raise InvalidCurrentReading(
            "The current reading's date must be after the most recent MOT test."
        )

    current = Reading(
        test_date=reported_date,
        miles=miles,
        original_value=miles,
        original_unit="mi",
        test_result="USER_REPORTED",
        is_user_reported=True,
    )

    return [*readings, current]


def normalise(payload: dict) -> tuple[list[Reading], list[SkippedTest]]:
    """
    Extract usable readings from a DVSA vehicle payload.

    Returns the readings sorted oldest first, plus everything that had to be
    skipped. The skipped list is not thrown away, because sparse or unreadable
    histories change how much confidence the final verdict deserves.
    """
    readings: list[Reading] = []
    skipped: list[SkippedTest] = []

    for test in payload.get("motTests") or []:
        test_date = _parse_date(test.get("completedDate", ""))

        if test_date is None:
            skipped.append(SkippedTest(None, "unparseable test date"))
            continue

        raw_value = test.get("odometerValue")
        raw_unit = test.get("odometerUnit")

        if test.get("odometerResultType") == "NO_ODOMETER_READING":
            skipped.append(SkippedTest(test_date, "no odometer reading recorded"))
            continue

        if raw_value in (None, ""):
            skipped.append(SkippedTest(test_date, "missing odometer value"))
            continue

        try:
            value = int(str(raw_value).replace(",", "").strip())
        except ValueError:
            skipped.append(SkippedTest(test_date, "non-numeric odometer value"))
            continue

        miles = _to_miles(value, raw_unit)

        if miles is None:
            skipped.append(SkippedTest(test_date, f"unrecognised unit {raw_unit!r}"))
            continue

        raw_comments = test.get("rfrAndComments") or []
        advisory_texts = tuple(
            comment["text"].strip()
            for comment in raw_comments
            if isinstance(comment, dict) and comment.get("text")
        )

        readings.append(
            Reading(
                test_date=test_date,
                miles=miles,
                original_value=value,
                original_unit=(raw_unit or "").strip().lower(),
                test_result=test.get("testResult", "UNKNOWN"),
                advisory_texts=advisory_texts,
            )
        )

    readings.sort(key=lambda r: r.test_date)
    readings = _collapse_retests(readings)

    return readings, skipped
