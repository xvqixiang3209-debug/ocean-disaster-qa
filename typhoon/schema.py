"""Input validation and normalization for current-typhoon payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .geometry import normalize_longitude


class TyphoonInputError(ValueError):
    """Raised when current-typhoon data cannot support analogue matching."""


def _first(mapping: dict, names: tuple[str, ...]) -> Any:
    for name in names:
        value = mapping.get(name)
        if value not in (None, ""):
            return value
    return None


def _number(value: Any, field: str, *, minimum: float | None = None, maximum: float | None = None) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TyphoonInputError(f"{field} 必须是数字") from exc
    if minimum is not None and result < minimum:
        raise TyphoonInputError(f"{field} 不能小于 {minimum}")
    if maximum is not None and result > maximum:
        raise TyphoonInputError(f"{field} 不能大于 {maximum}")
    return result


def _time(value: Any, index: int) -> str:
    if value in (None, ""):
        raise TyphoonInputError(f"第 {index + 1} 个路径点缺少 time")
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError as exc:
            raise TyphoonInputError(f"第 {index + 1} 个路径点的时间格式无效: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_current_typhoon(payload: dict) -> dict:
    """Normalize accepted JSON variants into the internal schema."""
    if not isinstance(payload, dict):
        raise TyphoonInputError("请求体必须是 JSON 对象")
    raw = payload.get("current_typhoon", payload)
    if not isinstance(raw, dict):
        raise TyphoonInputError("current_typhoon 必须是 JSON 对象")

    raw_track = _first(raw, ("track", "forecast_track", "points", "path"))
    if not isinstance(raw_track, list):
        raise TyphoonInputError("缺少 track 或 forecast_track 数组")
    if len(raw_track) < 2:
        raise TyphoonInputError("路径相似度至少需要两个路径点")

    points = []
    for index, item in enumerate(raw_track):
        if not isinstance(item, dict):
            raise TyphoonInputError(f"第 {index + 1} 个路径点必须是对象")
        lat = _number(_first(item, ("lat", "latitude")), f"track[{index}].lat", minimum=-90, maximum=90)
        lon = _number(_first(item, ("lon", "longitude")), f"track[{index}].lon", minimum=-360, maximum=360)
        if lat is None or lon is None:
            raise TyphoonInputError(f"第 {index + 1} 个路径点缺少经纬度")
        wind = _number(
            _first(item, ("wind_kt", "wind", "max_wind", "wmo_wind", "cma_wind")),
            f"track[{index}].wind_kt",
            minimum=0,
            maximum=250,
        )
        pressure = _number(
            _first(item, ("pressure_hpa", "pressure", "pres", "central_pressure", "wmo_pres", "cma_pres")),
            f"track[{index}].pressure_hpa",
            minimum=800,
            maximum=1100,
        )
        points.append(
            {
                "time": _time(_first(item, ("time", "iso_time", "datetime", "valid_time")), index),
                "lat": round(lat, 4),
                "lon": round(normalize_longitude(lon), 4),
                "wind": wind,
                "pressure": pressure,
                "nature": item.get("nature") or item.get("status") or "",
            }
        )

    points.sort(key=lambda point: point["time"])
    deduplicated = []
    seen_times = set()
    for point in points:
        if point["time"] in seen_times:
            continue
        seen_times.add(point["time"])
        deduplicated.append(point)
    if len(deduplicated) < 2:
        raise TyphoonInputError("去除重复时次后至少需要两个路径点")
    if not any(point["wind"] is not None or point["pressure"] is not None for point in deduplicated):
        raise TyphoonInputError("至少需要提供一个风速或中心气压值")

    storm_surge = _first(raw, ("storm_surge", "surge", "storm_tide"))
    if storm_surge is not None and not isinstance(storm_surge, (dict, list)):
        raise TyphoonInputError("storm_surge 必须是对象或对象数组")

    return {
        "sid": str(raw.get("sid") or raw.get("id") or "CURRENT").strip(),
        "name": str(raw.get("name") or "当前台风").strip(),
        "source": str(raw.get("source") or raw.get("agency") or "UNKNOWN").strip(),
        "issued_at": raw.get("issued_at") or raw.get("update_time"),
        "track": deduplicated,
        "storm_surge": storm_surge,
    }
