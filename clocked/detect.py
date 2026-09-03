"""
The detection engine.

Given a vehicle's normalised odometer readings, decide whether the mileage
history is internally consistent. Everything here is a claim about the
vehicle's own history — no external averages, no assumptions about what a
"normal" car does. That matters, because a van doing 40,000 miles a year is
not suspicious and a model that treats it as suspicious is useless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .normalise import Reading, SkippedTest

# A rollback of a handful of miles is almost always a clerical slip at the
# test station, not fraud. Below this, we note it but do not accuse.
CLERICAL_TOLERANCE_MILES = 100

# Annualised mileage above this is physically possible but rare enough that a
# human should look at it. Long-haul vans legitimately exceed it.
IMPLAUSIBLE_ANNUAL_MILES = 60_000

# A year on the road adding almost nothing can mean a disconnected odometer.
STATIC_MIN_DAYS = 300
STATIC_MAX_MILES = 100

# Below this many readings there is not enough history to conclude anything.
SPARSE_HISTORY_THRESHOLD = 3


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Verdict(str, Enum):
    CLEAR = "clear"
    REVIEW = "review"
    EVIDENCE_OF_TAMPERING = "evidence_of_tampering"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass
class Finding:
    code: str
    severity: Severity
    summary: str
    from_reading: Reading | None = None
    to_reading: Reading | None = None


@dataclass
class Report:
    verdict: Verdict
    findings: list[Finding] = field(default_factory=list)
    readings: list[Reading] = field(default_factory=list)
    skipped: list[SkippedTest] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return self.verdict == Verdict.CLEAR


def _days_between(a: Reading, b: Reading) -> int:
    return (b.test_date - a.test_date).days


def _find_rollbacks(readings: list[Reading]) -> list[Finding]:
    """
    A rollback is any reading lower than the highest reading that came before
    it. Comparing against the running maximum rather than the immediately
    previous reading matters: if someone winds a car back and then drives it
    normally for two years, consecutive-pair checking sees only steady
    increases and misses the fraud entirely.
    """
    findings: list[Finding] = []
    high_water = readings[0]

    for reading in readings[1:]:
        if reading.miles >= high_water.miles:
            high_water = reading
            continue

        drop = high_water.miles - reading.miles

        if drop <= CLERICAL_TOLERANCE_MILES:
            findings.append(
                Finding(
                    code="MINOR_DISCREPANCY",
                    severity=Severity.INFO,
                    summary=(
                        f"Reading is {drop:,} miles below the earlier peak of "
                        f"{high_water.miles:,}. Small enough to be a recording error."
                    ),
                    from_reading=high_water,
                    to_reading=reading,
                )
            )
            continue

        findings.append(
            Finding(
                code="ODOMETER_ROLLBACK",
                severity=Severity.CRITICAL,
                summary=(
                    f"Mileage fell by {drop:,} miles — from {high_water.miles:,} on "
                    f"{high_water.test_date:%d %b %Y} to {reading.miles:,} on "
                    f"{reading.test_date:%d %b %Y}. An odometer cannot decrease."
                ),
                from_reading=high_water,
                to_reading=reading,
            )
        )

    return findings


def _find_implausible_jumps(readings: list[Reading]) -> list[Finding]:
    findings: list[Finding] = []

    for previous, current in zip(readings, readings[1:]):
        days = _days_between(previous, current)
        gained = current.miles - previous.miles

        if days <= 0 or gained <= 0:
            continue

        annualised = gained / days * 365

        if annualised <= IMPLAUSIBLE_ANNUAL_MILES:
            continue

        findings.append(
            Finding(
                code="IMPLAUSIBLE_JUMP",
                severity=Severity.WARNING,
                summary=(
                    f"{gained:,} miles added in {days} days — a rate of "
                    f"{annualised:,.0f} miles a year. Possible, but worth "
                    f"questioning, or a sign an earlier reading was understated."
                ),
                from_reading=previous,
                to_reading=current,
            )
        )

    return findings


def _find_static_periods(readings: list[Reading]) -> list[Finding]:
    findings: list[Finding] = []

    for previous, current in zip(readings, readings[1:]):
        days = _days_between(previous, current)
        gained = current.miles - previous.miles

        if days < STATIC_MIN_DAYS or gained > STATIC_MAX_MILES or gained < 0:
            continue

        findings.append(
            Finding(
                code="STATIC_MILEAGE",
                severity=Severity.WARNING,
                summary=(
                    f"Only {gained:,} miles recorded across {days} days. Could be "
                    f"genuine storage, or a disconnected odometer."
                ),
                from_reading=previous,
                to_reading=current,
            )
        )

    return findings


def _find_unit_flips(readings: list[Reading]) -> list[Finding]:
    """
    Mixed units are a known source of false rollbacks. If a history is mostly
    miles and one test was logged in kilometres, the raw numbers jump around
    even though the vehicle is fine. Surface it so the user knows a conversion
    was applied rather than silently trusting it.
    """
    units = {r.original_unit for r in readings}

    if len(units) < 2:
        return []

    converted = [r for r in readings if r.was_converted]

    return [
        Finding(
            code="MIXED_UNITS",
            severity=Severity.INFO,
            summary=(
                f"This history mixes miles and kilometres. "
                f"{len(converted)} reading(s) were converted to miles before "
                f"comparison."
            ),
        )
    ]


def _decide(findings: list[Finding], readings: list[Reading]) -> Verdict:
    if len(readings) < 2:
        return Verdict.INSUFFICIENT_DATA

    if any(f.severity == Severity.CRITICAL for f in findings):
        return Verdict.EVIDENCE_OF_TAMPERING

    if any(f.severity == Severity.WARNING for f in findings):
        return Verdict.REVIEW

    if len(readings) < SPARSE_HISTORY_THRESHOLD:
        return Verdict.INSUFFICIENT_DATA

    return Verdict.CLEAR


def analyse(readings: list[Reading], skipped: list[SkippedTest] | None = None) -> Report:
    """Run every check and return a single report."""
    skipped = skipped or []

    if not readings:
        return Report(
            verdict=Verdict.INSUFFICIENT_DATA,
            findings=[
                Finding(
                    code="NO_READINGS",
                    severity=Severity.INFO,
                    summary="No usable odometer readings found in this vehicle's history.",
                )
            ],
            readings=[],
            skipped=skipped,
        )

    findings: list[Finding] = []
    findings += _find_rollbacks(readings)
    findings += _find_implausible_jumps(readings)
    findings += _find_static_periods(readings)
    findings += _find_unit_flips(readings)

    if skipped:
        findings.append(
            Finding(
                code="INCOMPLETE_HISTORY",
                severity=Severity.INFO,
                summary=(
                    f"{len(skipped)} test(s) could not be used. Gaps reduce how "
                    f"much this result can be relied on."
                ),
            )
        )

    return Report(
        verdict=_decide(findings, readings),
        findings=findings,
        readings=readings,
        skipped=skipped,
    )
