"""
Geometry for the mileage timeline chart.

Turns a list of readings into plain numbers an SVG template can drop
straight into <polyline>/<circle> coordinates. No template math, no JS,
no charting library — just arithmetic the template can't do on its own.
"""

from __future__ import annotations

from clocked.normalise import Reading

WIDTH = 640
HEIGHT = 200
PAD_LEFT = 46
PAD_RIGHT = 16
PAD_TOP = 16
PAD_BOTTOM = 34
Y_TICKS = 4


def build_timeline_chart(readings: list[Reading], flagged_dates: dict) -> dict | None:
    """Returns chart geometry, or None when there are too few points to plot a line."""
    if len(readings) < 2:
        return None

    plot_w = WIDTH - PAD_LEFT - PAD_RIGHT
    plot_h = HEIGHT - PAD_TOP - PAD_BOTTOM
    plot_bottom = PAD_TOP + plot_h

    dates = [r.test_date for r in readings]
    date_min, date_max = min(dates), max(dates)
    date_span = (date_max - date_min).days or 1

    miles = [r.miles for r in readings]
    miles_min, miles_max = min(miles), max(miles)
    miles_span = miles_max - miles_min

    if miles_span == 0:
        # A flat line has nothing to scale against — give it some headroom.
        miles_span = max(miles_max * 0.1, 1000)
        miles_min -= miles_span / 2
        miles_max += miles_span / 2
    else:
        breathing_room = miles_span * 0.12
        miles_min -= breathing_room
        miles_max += breathing_room
        miles_span = miles_max - miles_min

    def x_for(test_date) -> float:
        return PAD_LEFT + (test_date - date_min).days / date_span * plot_w

    def y_for(value: float) -> float:
        return PAD_TOP + (1 - (value - miles_min) / miles_span) * plot_h

    points = [
        {
            "cx": round(x_for(r.test_date), 1),
            "cy": round(y_for(r.miles), 1),
            "flag": flagged_dates.get(r.test_date),
            "tooltip": f"{r.test_date:%d %b %Y} — {r.miles:,} mi",
            "date_label": f"{r.test_date:%b %y}",
        }
        for r in readings
    ]

    path = " L ".join(f"{p['cx']},{p['cy']}" for p in points)
    area = f"M {points[0]['cx']},{plot_bottom} L {path} L {points[-1]['cx']},{plot_bottom} Z"

    y_ticks = []
    for i in range(Y_TICKS + 1):
        value = miles_min + miles_span * i / Y_TICKS
        y_ticks.append({"y": round(y_for(value), 1), "label": f"{int(round(value / 100) * 100):,}"})

    # Skip every other date label once there isn't room for all of them.
    label_stride = 1 if len(points) <= 8 else 2

    return {
        "width": WIDTH,
        "height": HEIGHT,
        "plot_left": PAD_LEFT,
        "plot_bottom": plot_bottom,
        "points": points,
        "path": f"M {path}",
        "area": area,
        "y_ticks": y_ticks,
        "label_stride": label_stride,
    }
