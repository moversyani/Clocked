"""
Command line entry point.

Runs against a local fixture file or, once credentials exist, a live
registration. The fixture path exists so the project is demonstrable without
network access, which helps when the API is rate limiting, and useful in an
interview on someone else's wifi.

    python -m clocked.cli --fixture fixtures/rollback_history.json
    python -m clocked.cli --reg AB12CDE
"""

from __future__ import annotations

import argparse
import json
import sys

from .detect import Severity, analyse
from .normalise import normalise

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
            print(f"  {reading.test_date:%d %b %Y}   {reading.miles:>9,} mi{note}")
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
    render(analyse(readings, skipped), label)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
