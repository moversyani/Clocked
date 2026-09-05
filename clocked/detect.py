"""
The detection engine.

Given a vehicle's normalised odometer readings, decide whether the mileage
history is internally consistent. Everything here is a claim about the
vehicle's own history. No external averages, no assumptions about what a
"normal" car does. That matters, because a van doing 40,000 miles a year is
not suspicious and a model that treats it as suspicious is useless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from .normalise import Reading, SkippedTest
from .wear import accumulated_wear, build_wear_history

# A rollback of a handful of miles is almost always a clerical slip at the
# test station, not fraud. Below this, we note it but do not accuse.
# Provisional until validated against a larger sample.
CLERICAL_TOLERANCE_MILES = 100

# Annualised mileage above this is physically possible but rare enough that a
# human should look at it. Long-haul vans legitimately exceed it. Provisional
# until validated against a larger sample.
IMPLAUSIBLE_ANNUAL_MILES = 60_000

# A year on the road adding almost nothing can mean a disconnected odometer.
# Provisional until validated against a larger sample.
STATIC_MIN_DAYS = 300
STATIC_MAX_MILES = 100

# Below this many readings there is not enough history to conclude anything.
# Provisional until validated against a larger sample.
SPARSE_HISTORY_THRESHOLD = 3

# Minimum number of clean, pre-rollback readings needed to establish a
# baseline annual mileage rate. Below this there is nothing solid to project
# from. Provisional until validated against a larger sample.
SHORTFALL_MIN_BASELINE_READINGS = 2

# A predicted versus actual gap smaller than this is noise in the
# projection, not evidence of anything. Provisional until validated against
# a larger sample.
SHORTFALL_MIN_MILES = 3_000

# MOTs are annual. An interval much longer than this means the record has
# nothing to say about that period. The margin allows for ordinary early or
# late testing without flagging routine variation. Provisional until
# validated against a larger sample.
TESTING_GAP_MIN_DAYS = 450

# The United Kingdom's COVID-19 MOT exemption. Tests due in this window were
# automatically extended by six months, so a gap overlapping it is likely
# explained by the exemption rather than anything unusual. Provisional
# until confirmed against the exact DVSA exemption dates.
COVID_EXEMPTION_START = date(2020, 3, 30)
COVID_EXEMPTION_END = date(2020, 8, 1)

# An annualised rate below this, over a period shorter than STATIC_MIN_DAYS,
# is too slow to be an ordinary pattern of use. Unlike STATIC_MILEAGE, which
# needs a year or more of near-zero movement, this catches the same kind of
# implausible slowdown over a shorter window, such as the gap between the
# last MOT and a newly reported current reading. Provisional until validated
# against a larger sample.
IMPLAUSIBLE_LOW_ANNUAL_MILES = 500

# Every finding derived even partly from a user-reported reading carries
# this, so nobody mistakes an unverified figure for something DVSA checked.
USER_READING_CAVEAT = (
    "This relies on a reading entered by the user rather than recorded at "
    "an MOT test, so treat the figure as unverified."
)

# Minimum accumulated wear-indicator count, summed across every category and
# every test, before a history counts as showing heavy wear at all. Wear is
# weak evidence on its own, so this is set high enough that only a genuinely
# defect-heavy history reaches it. Provisional until validated against a
# larger sample.
WEAR_MISMATCH_MIN_INDICATORS = 4

# Claimed mileage at or below this still counts as "low" for the wear
# comparison. Heavy wear at genuinely high mileage is expected and is not
# flagged; this bound is what keeps the two apart. Provisional until
# validated against a larger sample.
WEAR_MISMATCH_MAX_MILES = 40_000


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
    affected_tests: int | None = None
    duration_days: int | None = None


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

    Consecutive readings sitting below the same peak are one event, not one
    finding per reading. A single act of tampering would otherwise produce
    several near identical criticals, one for every test until the mileage
    recovers. A drop persisting across several tests is stronger evidence
    than a single dip, because a typo does not persist: that persistence is
    recorded as affected_tests and duration_days on the finding.
    """
    findings: list[Finding] = []
    high_water = readings[0]
    group: list[Reading] | None = None

    def close_group() -> None:
        nonlocal group

        if group is None:
            return

        peak = high_water
        lowest = min(group, key=lambda r: r.miles)
        duration = (group[-1].test_date - peak.test_date).days

        summary = (
            f"Mileage fell from a peak of {peak.miles:,} on "
            f"{peak.test_date:%d %b %Y} to a low of {lowest.miles:,} on "
            f"{lowest.test_date:%d %b %Y}. The reading stayed below that "
            f"peak across {len(group)} test(s), a depression lasting "
            f"{duration} days. An odometer cannot decrease."
        )

        if any(r.is_user_reported for r in group):
            summary += " " + USER_READING_CAVEAT

        findings.append(
            Finding(
                code="ODOMETER_ROLLBACK",
                severity=Severity.CRITICAL,
                summary=summary,
                from_reading=peak,
                to_reading=lowest,
                affected_tests=len(group),
                duration_days=duration,
            )
        )
        group = None

    for reading in readings[1:]:
        if reading.miles >= high_water.miles:
            close_group()
            high_water = reading
            continue

        drop = high_water.miles - reading.miles

        if drop <= CLERICAL_TOLERANCE_MILES:
            close_group()
            summary = (
                f"Reading is {drop:,} miles below the earlier peak of "
                f"{high_water.miles:,}. Small enough to be a recording error."
            )

            if reading.is_user_reported:
                summary += " " + USER_READING_CAVEAT

            findings.append(
                Finding(
                    code="MINOR_DISCREPANCY",
                    severity=Severity.INFO,
                    summary=summary,
                    from_reading=high_water,
                    to_reading=reading,
                )
            )
            continue

        if group is None:
            group = [reading]
        else:
            group.append(reading)

    close_group()

    return findings


def _find_mileage_shortfall(
    readings: list[Reading], rollback_findings: list[Finding]
) -> list[Finding]:
    """
    A rollback's effect can outlast the event itself. Even after the
    odometer recovers and climbs normally again, it can take years to catch
    up to where the vehicle's own pre-rollback trend said it should be, if
    it ever does. This establishes that trend from the readings before the
    first rollback and compares the most recent reading against it.

    Only MOT-verified readings take part, on both sides of the comparison.
    A user-reported reading is not independently confirmed, so it must not
    redefine the baseline, and treating it as the figure this check measures
    against would let one unverified number both create and erase the
    evidence.
    """
    rollbacks = [f for f in rollback_findings if f.code == "ODOMETER_ROLLBACK"]

    if not rollbacks:
        return []

    mot_readings = [r for r in readings if not r.is_user_reported]

    if not mot_readings:
        return []

    peak = rollbacks[0].from_reading
    peak_index = next(i for i, r in enumerate(mot_readings) if r is peak)

    baseline = mot_readings[: peak_index + 1]

    if len(baseline) < SHORTFALL_MIN_BASELINE_READINGS:
        return []

    span_days = _days_between(baseline[0], baseline[-1])

    if span_days <= 0:
        return []

    annual_rate = (baseline[-1].miles - baseline[0].miles) / span_days * 365

    if annual_rate <= 0:
        return []

    latest = mot_readings[-1]

    if latest.test_date <= peak.test_date:
        return []

    elapsed_days = _days_between(peak, latest)
    predicted = peak.miles + annual_rate * elapsed_days / 365
    shortfall = predicted - latest.miles

    if shortfall <= SHORTFALL_MIN_MILES:
        return []

    return [
        Finding(
            code="MILEAGE_SHORTFALL",
            severity=Severity.WARNING,
            summary=(
                f"Based on this vehicle's mileage rate before the earlier rollback, "
                f"the reading on {latest.test_date:%d %b %Y} is {shortfall:,.0f} miles "
                f"short of what that rate would predict. The gap has not been made up "
                f"since the odometer was wound back."
            ),
            from_reading=peak,
            to_reading=latest,
        )
    ]


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

        summary = (
            f"{gained:,} miles added in {days} days, a rate of "
            f"{annualised:,.0f} miles a year. Possible, but worth "
            f"questioning, or a sign an earlier reading was understated."
        )

        if current.is_user_reported:
            summary += " " + USER_READING_CAVEAT

        findings.append(
            Finding(
                code="IMPLAUSIBLE_JUMP",
                severity=Severity.WARNING,
                summary=summary,
                from_reading=previous,
                to_reading=current,
            )
        )

    return findings


def _find_implausible_slowdowns(readings: list[Reading]) -> list[Finding]:
    """
    An annualised rate this low is only meaningful over a shorter window
    than STATIC_MILEAGE requires: a year or more of near-zero movement is
    already caught there. This fills the gap for a slowdown implausible
    enough to question even over a few months, which matters most for the
    period since the last MOT, where a newly reported current reading is
    the only thing standing between an ordinary quiet spell and a rollback
    with no trace in the official record.
    """
    findings: list[Finding] = []

    for previous, current in zip(readings, readings[1:]):
        days = _days_between(previous, current)
        gained = current.miles - previous.miles

        if days <= 0 or gained < 0 or days >= STATIC_MIN_DAYS:
            continue

        annualised = gained / days * 365

        if annualised >= IMPLAUSIBLE_LOW_ANNUAL_MILES:
            continue

        summary = (
            f"Only {gained:,} miles added in {days} days, a rate of "
            f"{annualised:,.0f} miles a year. Possible for a vehicle kept off "
            f"the road for a while, but low enough that the more recent "
            f"reading is worth questioning."
        )

        if current.is_user_reported:
            summary += " " + USER_READING_CAVEAT

        findings.append(
            Finding(
                code="IMPLAUSIBLE_SLOWDOWN",
                severity=Severity.WARNING,
                summary=summary,
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

        summary = (
            f"Only {gained:,} miles recorded across {days} days. Could be "
            f"genuine storage, or a disconnected odometer."
        )

        if current.is_user_reported:
            summary += " " + USER_READING_CAVEAT

        findings.append(
            Finding(
                code="STATIC_MILEAGE",
                severity=Severity.WARNING,
                summary=summary,
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


def _find_wear_mismatches(readings: list[Reading]) -> list[Finding]:
    """
    Heavy wear against low claimed mileage is a soft signal, not proof. Wear
    varies enormously with age, salt exposure, driving style and
    maintenance, so a defect-heavy history on its own never rises above a
    warning, and the wording says plainly that this is weak evidence.

    The claimed mileage compared against is whichever reading is most
    recent, MOT-verified or user-reported, because the concern this check
    exists for is exactly a newly claimed low figure sitting against a
    vehicle's own recorded wear history. When that reading is user-reported,
    the finding says so.
    """
    totals = accumulated_wear(build_wear_history(readings))
    total_indicators = sum(totals.values())

    if total_indicators < WEAR_MISMATCH_MIN_INDICATORS:
        return []

    claimed = readings[-1]

    if claimed.miles > WEAR_MISMATCH_MAX_MILES:
        return []

    active_categories = sorted(category for category, count in totals.items() if count)
    category_text = ", ".join(category.replace("_", " ") for category in active_categories)

    summary = (
        f"This vehicle's MOT history carries {total_indicators} wear-related "
        f"advisory or failure note(s) ({category_text}) against a claimed "
        f"{claimed.miles:,} miles. That is a lot of wear for the mileage shown. "
        f"Wear varies enormously with age, salt exposure, driving style and "
        f"maintenance, so this is weak evidence on its own, not proof of anything."
    )

    if claimed.is_user_reported:
        summary += " " + USER_READING_CAVEAT

    return [
        Finding(
            code="WEAR_MILEAGE_MISMATCH",
            severity=Severity.WARNING,
            summary=summary,
            to_reading=claimed,
        )
    ]


def _find_testing_gaps(
    readings: list[Reading], skipped: list[SkippedTest]
) -> list[Finding]:
    """
    MOTs are annual, so an interval much longer than a year means the record
    has nothing to say about that period, and mileage altered during a gap
    leaves no trace. A skipped test with a known date counts as a test
    having happened even though its odometer reading could not be used, so
    it is not treated as a gap.

    This only ever measures the space between two known tests. The three
    year exemption for new vehicles and the point past which an old or
    off-road vehicle simply stops being tested both fall outside that space,
    so neither is ever measured as a gap in the first place.

    A gap is reported as unexplained, not suspicious, because this check
    cannot tell a legitimate SORN period, a classic or low-use vehicle kept
    off the road, or an ordinary late test apart from anything else. The one
    cause it can identify is the COVID-19 MOT exemption, so a gap
    overlapping that window is called out as likely explained by it.

    A user-reported current reading is excluded. It is not an MOT test, so
    the space it happens to sit in is not "no test on record", and reporting
    it as a gap would fire on almost every use of that feature regardless of
    how recent the last real MOT was.
    """
    mot_readings = [r for r in readings if not r.is_user_reported]
    known_dates = sorted({s.test_date for s in skipped if s.test_date is not None})

    findings: list[Finding] = []

    for previous, current in zip(mot_readings, mot_readings[1:]):
        days = _days_between(previous, current)

        if days < TESTING_GAP_MIN_DAYS:
            continue

        if any(previous.test_date < known < current.test_date for known in known_dates):
            continue

        overlaps_covid = (
            previous.test_date <= COVID_EXEMPTION_END
            and current.test_date >= COVID_EXEMPTION_START
        )

        if overlaps_covid:
            summary = (
                f"No MOT test on record between {previous.test_date:%d %b %Y} and "
                f"{current.test_date:%d %b %Y}, a gap of {days} days. This overlaps "
                f"the COVID-19 MOT exemption period, so it is likely explained by "
                f"that rather than anything unusual."
            )
        else:
            summary = (
                f"No MOT test on record between {previous.test_date:%d %b %Y} and "
                f"{current.test_date:%d %b %Y}, a gap of {days} days. This could be "
                f"a SORN period, a classic or low-use vehicle kept off the road, an "
                f"ordinary late test, or something else the record does not say. "
                f"The gap is unexplained, not necessarily suspicious."
            )

        findings.append(
            Finding(
                code="TESTING_GAP",
                severity=Severity.INFO,
                summary=summary,
                from_reading=previous,
                to_reading=current,
                duration_days=days,
            )
        )

    return findings


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

    rollback_findings = _find_rollbacks(readings)

    findings: list[Finding] = []
    findings += rollback_findings
    findings += _find_mileage_shortfall(readings, rollback_findings)
    findings += _find_implausible_jumps(readings)
    findings += _find_implausible_slowdowns(readings)
    findings += _find_static_periods(readings)
    findings += _find_unit_flips(readings)
    findings += _find_wear_mismatches(readings)
    findings += _find_testing_gaps(readings, skipped)

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
