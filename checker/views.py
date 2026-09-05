"""
Views for the registration lookup.

This is a thin wrapper: everything that decides what a mileage history means
lives in clocked.normalise and clocked.detect. This module's only job is to
take a registration from a form, run it through that existing pipeline, and
hand the result to a template.
"""

from __future__ import annotations

from datetime import date, datetime

from django.shortcuts import render

from clocked.config import MissingCredentials
from clocked.detect import analyse
from clocked.mot_client import MotApiError, MotClient, VehicleNotFound
from clocked.normalise import InvalidCurrentReading, add_current_reading, normalise

from .charting import build_timeline_chart


def index(request):
    return render(request, "checker/index.html")


def results(request):
    registration = (request.GET.get("reg") or "").strip().upper()
    current_mileage_raw = (request.GET.get("current_mileage") or "").strip()
    current_date_raw = (request.GET.get("current_date") or "").strip()

    form_state = {
        "reg": registration,
        "current_mileage": current_mileage_raw,
        "current_date": current_date_raw,
    }

    if not registration:
        return render(
            request, "checker/index.html", {"error": "Enter a registration number.", **form_state}
        )

    try:
        payload = MotClient().get_vehicle(registration)
    except VehicleNotFound:
        return render(
            request,
            "checker/index.html",
            {"error": f"No MOT history found for {registration}.", **form_state},
        )
    except (MotApiError, MissingCredentials) as error:
        return render(
            request,
            "checker/index.html",
            {"error": str(error), **form_state},
        )

    readings, skipped = normalise(payload)

    if current_mileage_raw:
        try:
            current_mileage = int(current_mileage_raw)
        except ValueError:
            return render(
                request,
                "checker/index.html",
                {"error": "Current mileage must be a whole number.", **form_state},
            )

        if current_date_raw:
            try:
                current_date = datetime.strptime(current_date_raw, "%Y-%m-%d").date()
            except ValueError:
                return render(
                    request,
                    "checker/index.html",
                    {"error": "Current reading date must be in YYYY-MM-DD format.", **form_state},
                )
        else:
            current_date = date.today()

        try:
            readings = add_current_reading(readings, current_mileage, current_date)
        except InvalidCurrentReading as error:
            return render(
                request,
                "checker/index.html",
                {"error": str(error), **form_state},
            )

    report = analyse(readings, skipped)

    # Flag the reading a CRITICAL or WARNING finding points to, so the
    # timeline can highlight exactly where a rollback or implausible jump
    # lands rather than making the reader cross-reference the findings list.
    flagged_dates: dict = {}
    for finding in report.findings:
        if finding.to_reading is not None and finding.severity.value != "info":
            flagged_dates[finding.to_reading.test_date] = finding.severity.value

    timeline = [
        {"reading": reading, "flag": flagged_dates.get(reading.test_date)}
        for reading in report.readings
    ]

    return render(
        request,
        "checker/results.html",
        {
            "registration": registration,
            "make": payload.get("make", "?"),
            "model": payload.get("model", "?"),
            "report": report,
            "verdict_label": report.verdict.value.replace("_", " ").upper(),
            "timeline": timeline,
            "chart": build_timeline_chart(report.readings, flagged_dates),
        },
    )
