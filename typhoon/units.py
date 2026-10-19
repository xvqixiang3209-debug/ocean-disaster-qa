"""User-facing unit conversions; storage and similarity calculations stay unchanged."""

from __future__ import annotations


KNOT_TO_MPS = 0.514444

# Upper bounds of the extended Beaufort scale in metres per second.
_BEAUFORT_UPPER_BOUNDS = (
    (0.2, 0),
    (1.5, 1),
    (3.3, 2),
    (5.4, 3),
    (7.9, 4),
    (10.7, 5),
    (13.8, 6),
    (17.1, 7),
    (20.7, 8),
    (24.4, 9),
    (28.4, 10),
    (32.6, 11),
    (36.9, 12),
    (41.4, 13),
    (46.1, 14),
    (50.9, 15),
    (56.0, 16),
)


def knots_to_mps(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value) * KNOT_TO_MPS
    except (TypeError, ValueError):
        return None


def wind_force_level_ms(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        wind_ms = max(0.0, float(value))
    except (TypeError, ValueError):
        return None
    for upper, level in _BEAUFORT_UPPER_BOUNDS:
        if wind_ms <= upper:
            return level
    return 17


def wind_force_level_from_knots(value) -> int | None:
    return wind_force_level_ms(knots_to_mps(value))


def format_wind_from_knots(value, *, decimals: int = 1) -> str:
    wind_ms = knots_to_mps(value)
    if wind_ms is None:
        return "无数据"
    level = wind_force_level_ms(wind_ms)
    level_text = f"（{level}级）" if level is not None else ""
    return f"{wind_ms:.{decimals}f} m/s{level_text}"
