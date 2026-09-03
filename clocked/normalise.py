"""
Turn raw DVSA MOT History JSON into a clean, sorted list of odometer readings.

The API does not promise clean data. A single vehicle's history can contain
readings in miles and in kilometres, tests with no reading at all, and tests
recorded out of order. Every one of those is handled here, before any
detection logic runs, so the detector only ever sees trustworthy input.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

KM_TO_MILES = 0.621371


@dataclass(frozen=True)
class Reading:
    """One odometer reading, normalised to miles."""

    test_date: date
    miles: int
    original_value: int
    original_unit: str
    test_result: str

    @property
    def was_converted(self) -> bool:
        return self.original_unit == "km"


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


def normalise(payload: dict) -> tuple[list[Reading], list[SkippedTest]]:
    """
    Extract usable readings from a DVSA vehicle payload.

    Returns the readings sorted oldest first, plus everything that had to be
    skipped. The skipped list is not thrown away — sparse or unreadable
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

        readings.append(
            Reading(
                test_date=test_date,
                miles=miles,
                original_value=value,
                original_unit=(raw_unit or "").strip().lower(),
                test_result=test.get("testResult", "UNKNOWN"),
            )
        )

    readings.sort(key=lambda r: r.test_date)

    return readings, skipped
