"""
Command line entry point.

Runs against a local fixture file or, once credentials exist, a live
registration. The fixture path exists so the project is demonstrable without
network access, which helps when the API is rate limiting, and useful in an
interview on someone else's wifi.

    python -m clocked.cli --fixture fixtures/rollback_history.json
    python -m clocked.cli --reg AB12CDE
    python -m clocked.cli --reg AB12CDE --current-mileage 42150
    python -m clocked.cli --reg AB12CDE --current-mileage 42150 --current-date 2024-03-01
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime

from .detect import Severity, analyse
from .normalise import InvalidCurrentReading, add_current_reading, normalise

ICONS = {
    Severity.INFO: "·",
    Severity.WARNING: "!",
    Severity.CRITICAL: "X",
}


def render(report, label: str) -> None:
    print(f"\n{label}")
    print("=" * len(label))
    print(f"Verdict: {report.verdict.value.replace('_', ' ').upper()}\n")

    if report.readings:
        print("Mileage history")
        for reading in report.readings:
            note = f"  (converted from {reading.original_value:,} km)" if reading.was_converted else ""
            tag = "  (user-reported)" if reading.is_user_reported else ""
            print(f"  {reading.test_date:%d %b %Y}   {reading.miles:>9,} mi{note}{tag}")
        print()

    if report.findings:
        print("Findings")
        for finding in report.findings:
            print(f"  [{ICONS[finding.severity]}] {finding.code}: {finding.summary}")
        print()

    if report.skipped:
        print("Skipped tests")
        for skipped in report.skipped:
            when = f"{skipped.test_date:%d %b %Y}" if skipped.test_date else "unknown date"
            print(f"  {when}: {skipped.reason}")
        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a UK vehicle's mileage integrity.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", help="Path to a local MOT history JSON file")
    source.add_argument("--reg", help="UK registration number (requires DVSA credentials)")
    parser.add_argument(
        "--current-mileage",
        type=int,
        help="Optional current odometer reading, read off the dashboard today",
    )
    parser.add_argument(
        "--current-date",
        help="Date the current reading was taken, YYYY-MM-DD (defaults to today)",
    )

    args = parser.parse_args(argv)

    if args.fixture:
        with open(args.fixture) as handle:
            payload = json.load(handle)
        label = f"{payload.get('registration', 'Unknown')} ({args.fixture})"
    else:
        from .config import MissingCredentials
        from .mot_client import MotApiError, MotClient

        try:
            payload = MotClient().get_vehicle(args.reg)
        except (MotApiError, MissingCredentials) as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1

        label = f"{args.reg.upper()} (live DVSA lookup)"

    readings, skipped = normalise(payload)

    if args.current_mileage is not None:
        if args.current_date:
            try:
                current_date = datetime.strptime(args.current_date, "%Y-%m-%d").date()
            except ValueError:
                print(f"Error: --current-date must be YYYY-MM-DD, got {args.current_date!r}", file=sys.stderr)
                return 1
        else:
            current_date = date.today()

        try:
            readings = add_current_reading(readings, args.current_mileage, current_date)
        except InvalidCurrentReading as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1

    render(analyse(readings, skipped), label)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
