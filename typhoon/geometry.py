"""Geospatial and time-series helpers used by typhoon matching."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Callable, Iterable, Sequence

from .config import ZHEJIANG_COAST_POINTS


EARTH_RADIUS_KM = 6371.0088


def normalize_longitude(lon: float) -> float:
    """Normalize longitude to [-180, 180)."""
    return ((float(lon) + 180.0) % 360.0) - 180.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 coordinates."""
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    dphi = phi2 - phi1
    dlambda = math.radians(normalize_longitude(lon2) - normalize_longitude(lon1))
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing in degrees clockwise from north."""
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    dlambda = math.radians(normalize_longitude(lon2) - normalize_longitude(lon1))
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def circular_difference_deg(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return abs((float(a) - float(b) + 180.0) % 360.0 - 180.0)


def distance_to_zhejiang_km(lat: float, lon: float) -> float:
    return min(haversine_km(lat, lon, coast_lat, coast_lon) for coast_lat, coast_lon in ZHEJIANG_COAST_POINTS)


def closest_zhejiang_index(points: Sequence[dict]) -> int:
    if not points:
        raise ValueError("track is empty")
    return min(range(len(points)), key=lambda i: distance_to_zhejiang_km(points[i]["lat"], points[i]["lon"]))


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def compute_motion(points: Sequence[dict]) -> tuple[float | None, float | None]:
    """Return mean translation speed (kt) and circular mean direction."""
    speeds = []
    bearings = []
    for first, second in zip(points, points[1:]):
        distance = haversine_km(first["lat"], first["lon"], second["lat"], second["lon"])
        t1 = _parse_time(first.get("time", ""))
        t2 = _parse_time(second.get("time", ""))
        hours = None
        if t1 is not None and t2 is not None:
            try:
                hours = abs((t2 - t1).total_seconds()) / 3600.0
            except TypeError:
                # A provider may mix offset-aware and naive timestamps.
                hours = None
        if hours and hours > 0:
            speeds.append(distance / hours / 1.852)
        bearings.append(bearing_deg(first["lat"], first["lon"], second["lat"], second["lon"]))

    mean_speed = sum(speeds) / len(speeds) if speeds else None
    if not bearings:
        return mean_speed, None
    x = sum(math.cos(math.radians(value)) for value in bearings)
    y = sum(math.sin(math.radians(value)) for value in bearings)
    mean_direction = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    return mean_speed, mean_direction


def dtw_average_distance(
    first: Sequence,
    second: Sequence,
    cost: Callable[[object, object], float],
) -> float | None:
    """Dynamic time warping distance averaged over the selected warping path."""
    if not first or not second:
        return None
    rows = len(first) + 1
    cols = len(second) + 1
    infinity = float("inf")
    distances = [[infinity] * cols for _ in range(rows)]
    steps = [[0] * cols for _ in range(rows)]
    distances[0][0] = 0.0

    for i in range(1, rows):
        for j in range(1, cols):
            predecessors = [
                (distances[i - 1][j], steps[i - 1][j]),
                (distances[i][j - 1], steps[i][j - 1]),
                (distances[i - 1][j - 1], steps[i - 1][j - 1]),
            ]
            best_distance, best_steps = min(predecessors, key=lambda item: item[0])
            distances[i][j] = best_distance + float(cost(first[i - 1], second[j - 1]))
            steps[i][j] = best_steps + 1

    return distances[-1][-1] / max(1, steps[-1][-1])


def track_dtw_km(first: Sequence[dict], second: Sequence[dict]) -> float | None:
    return dtw_average_distance(
        first,
        second,
        lambda a, b: haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]),
    )


def select_approach_segment(points: Sequence[dict], target_length: int) -> list[dict]:
    """Select a window around the point closest to Zhejiang."""
    if not points:
        return []
    if len(points) <= max(6, target_length * 2):
        return list(points)
    center = closest_zhejiang_index(points)
    radius = max(3, target_length)
    start = max(0, center - radius)
    end = min(len(points), center + radius + 1)
    return list(points[start:end])


def finite_numbers(values: Iterable[float | None]) -> list[float]:
    result = []
    for value in values:
        if value is None:
            continue
        number = float(value)
        if math.isfinite(number):
            result.append(number)
    return result
