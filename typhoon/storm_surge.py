"""Optional historical storm-surge linkage and qualitative assessment."""

from __future__ import annotations

import csv
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path
from statistics import quantiles

from .config import DEFAULT_STORM_SURGE_DATA
from .units import format_wind_from_knots


def _first(data: dict, keys: tuple[str, ...]):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _number(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value):
    number = _number(value)
    return int(number) if number is not None else None


def _normalize_warning(value) -> str | None:
    text = str(value or "").strip()
    for level in ("红色", "橙色", "黄色", "蓝色"):
        if level in text:
            return level
    return text or None


def _normalize_severity(value) -> str | None:
    text = str(value or "").strip()
    mappings = {
        "严重": "严重", "重": "严重", "高": "严重", "severe": "严重", "high": "严重",
        "一般": "一般", "中": "一般", "moderate": "一般", "medium": "一般",
        "轻微": "轻微", "轻": "轻微", "低": "轻微", "mild": "轻微", "low": "轻微",
    }
    lowered = text.lower()
    for token, normalized in mappings.items():
        if token in lowered:
            return normalized
    return None


def _normalize_record(raw: dict) -> dict:
    return {
        "sid": str(_first(raw, ("sid", "storm_id", "typhoon_id")) or "").strip(),
        "season": _integer(_first(raw, ("season", "year"))),
        "name": str(_first(raw, ("name", "storm_name", "typhoon_name")) or "").strip(),
        "station": str(_first(raw, ("station", "station_name", "tide_station")) or "").strip() or None,
        "province": str(raw.get("province") or "").strip() or None,
        "max_surge_m": _number(_first(raw, ("max_surge_m", "surge_m", "storm_surge_m", "max_storm_surge_m"))),
        "max_water_level_m": _number(_first(raw, ("max_water_level_m", "water_level_m", "max_tide_level_m"))),
        "alert_exceedance_m": _number(_first(raw, ("alert_exceedance_m", "exceedance_m", "over_warning_level_m"))),
        "warning_level": _normalize_warning(_first(raw, ("warning_level", "alert_level", "warning"))),
        "severity": _normalize_severity(_first(raw, ("severity", "impact_level", "level"))),
        "event_time": str(_first(raw, ("event_time", "time", "datetime")) or "").strip() or None,
        "source": str(raw.get("source") or "").strip() or None,
        "note": str(raw.get("note") or "").strip() or None,
    }


def _maximum(records: list[dict], key: str):
    values = [record[key] for record in records if record.get(key) is not None]
    return max(values) if values else None


def _peak_metric(records: list[dict], key: str) -> tuple[float | None, list[str]]:
    usable = [record for record in records if record.get(key) is not None]
    if not usable:
        return None, []
    maximum = max(float(record[key]) for record in usable)
    stations = sorted({
        str(record["station"]).strip()
        for record in usable
        if record.get("station")
        and math.isclose(float(record[key]), maximum, rel_tol=0.0, abs_tol=1e-9)
    })
    return maximum, stations


def _format_metric(value: float | None, unit: str) -> str:
    return "无数据" if value is None else f"{value:.2f} {unit}"


def _warning_severity(warning: str | None) -> str | None:
    return {"蓝色": "轻微", "黄色": "一般", "橙色": "严重", "红色": "严重"}.get(warning)


def _relative_severity(value: float, population: list[float]) -> tuple[str, str]:
    usable = sorted(number for number in population if number is not None)
    if len(usable) >= 3:
        low, high = quantiles(usable, n=3, method="inclusive")
        if value <= low:
            return "轻微", f"最大增水位于历史样本低三分位（≤{low:.2f} m）"
        if value >= high:
            return "严重", f"最大增水位于历史样本高三分位（≥{high:.2f} m）"
        return "一般", "最大增水位于历史样本中间三分位"
    if value >= 1.5:
        return "严重", "按有限样本下的辅助增水阈值判断"
    if value >= 0.5:
        return "一般", "按有限样本下的辅助增水阈值判断"
    return "轻微", "按有限样本下的辅助增水阈值判断"


class StormSurgeRepository:
    """Read historical storm-surge records from the unified SQLite KB or CSV/JSON."""

    def __init__(self, data_path: str | Path | None = None):
        self.data_path = Path(data_path or DEFAULT_STORM_SURGE_DATA)
        self._records: list[dict] | None = None

    def _load(self) -> list[dict]:
        if not self.data_path.exists():
            return []
        if self.data_path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
            with closing(sqlite3.connect(self.data_path)) as connection:
                connection.row_factory = sqlite3.Row
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='storm_surge_events'"
                ).fetchone()
                if not exists:
                    return []
                event_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(storm_surge_events)")
                }
                has_time_validation = "time_match_status" in event_columns
                time_filter = (
                    "WHERE e.sid IS NOT NULL "
                    "AND e.time_match_status IN ('overlap', 'nearby')"
                    if has_time_validation
                    else "WHERE e.sid IS NOT NULL"
                )
                time_note = (
                    " || '，时间匹配=' || e.time_match_status"
                    " || '，时间偏移=' || printf('%.2f', e.time_offset_hours) || '小时'"
                    if has_time_validation
                    else ""
                )
                rows = [
                    dict(row)
                    for row in connection.execute(
                        f"""
                        SELECT e.sid, e.season, e.storm_name AS name,
                               s.station_name AS station, '浙江' AS province,
                               s.max_surge_m, s.max_water_level_m,
                               NULL AS alert_exceedance_m, NULL AS warning_level,
                               e.severity, COALESCE(e.peak_surge_time, e.event_start) AS event_time,
                               e.source_archive || '#' || e.source_file AS source,
                               e.link_method || '，置信度=' || e.link_confidence
                               {time_note} AS note
                        FROM storm_surge_events e
                        LEFT JOIN storm_surge_stations s ON s.event_id = e.event_id
                        {time_filter}
                        ORDER BY e.season, e.storm_number, s.station_code
                        """
                    )
                ]
        elif self.data_path.suffix.lower() == ".json":
            raw = json.loads(self.data_path.read_text(encoding="utf-8-sig"))
            rows = raw.get("records", raw) if isinstance(raw, dict) else raw
            if not isinstance(rows, list):
                raise ValueError("风暴潮 JSON 必须是数组，或包含 records 数组")
        else:
            with self.data_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        return [_normalize_record(row) for row in rows if isinstance(row, dict)]

    @property
    def records(self) -> list[dict]:
        if self._records is None:
            self._records = self._load()
        return self._records

    def reload(self) -> None:
        self._records = None

    def status(self) -> dict:
        records = self.records
        linked = {
            record["sid"] or f"{record.get('season')}:{record.get('name', '').upper()}"
            for record in records
            if record.get("sid") or record.get("name")
        }
        event_count = len(linked)
        observation_count = None
        time_match_counts = None
        if self.data_path.suffix.lower() in {".sqlite", ".sqlite3", ".db"} and self.data_path.exists():
            with closing(sqlite3.connect(self.data_path)) as connection:
                has_events = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='storm_surge_events'"
                ).fetchone()
                if has_events:
                    event_count = connection.execute("SELECT COUNT(*) FROM storm_surge_events").fetchone()[0]
                    observation_count = connection.execute(
                        "SELECT COUNT(*) FROM storm_surge_observations"
                    ).fetchone()[0]
                    columns = {
                        row[1] for row in connection.execute("PRAGMA table_info(storm_surge_events)")
                    }
                    if "time_match_status" in columns:
                        time_match_counts = dict(
                            connection.execute(
                                "SELECT time_match_status, COUNT(*) "
                                "FROM storm_surge_events GROUP BY time_match_status"
                            ).fetchall()
                        )
        return {
            "available": bool(records),
            "data_path": str(self.data_path),
            "record_count": len(records),
            "event_count": event_count,
            "linked_storm_count": len(linked),
            "observation_count": observation_count,
            "time_match_counts": time_match_counts,
            "fallback": "typhoon-condition proxy" if not records else None,
        }

    def _lookup(self, storm: dict) -> list[dict]:
        sid = str(storm.get("sid") or "").strip()
        if sid:
            matched = [record for record in self.records if record.get("sid") == sid]
            if matched:
                return matched
        name = str(storm.get("name") or "").strip().upper()
        season = storm.get("season")
        if not name:
            return []
        return [
            record for record in self.records
            if str(record.get("name") or "").strip().upper() == name
            and (season is None or record.get("season") is None or int(record["season"]) == int(season))
        ]

    def _population(self) -> list[float]:
        return [record["max_surge_m"] for record in self.records if record.get("max_surge_m") is not None]

    def assess(self, storm: dict, track: list[dict] | None = None, provided: dict | list[dict] | None = None) -> dict:
        if provided is not None:
            rows = provided if isinstance(provided, list) else [provided]
            records = [_normalize_record(row) for row in rows if isinstance(row, dict)]
            if records:
                return self._aggregate(records, "provided")
        records = self._lookup(storm)
        if records:
            has_measurement = any(
                record.get("severity")
                or record.get("warning_level")
                or record.get("max_surge_m") is not None
                or record.get("max_water_level_m") is not None
                for record in records
            )
            if has_measurement:
                return self._aggregate(records, "historical_record")
            estimate = self._proxy(storm, track or [])
            estimate["summary"] = (
                "已关联历史风暴潮事件文件，但缺少可用实测增水；" + estimate["summary"]
            )
            return estimate
        return self._proxy(storm, track or [])

    def _aggregate(self, records: list[dict], origin: str) -> dict:
        explicit = [record["severity"] for record in records if record.get("severity")]
        warnings = [record["warning_level"] for record in records if record.get("warning_level")]
        priority = {"红色": 4, "橙色": 3, "黄色": 2, "蓝色": 1}
        warning = max(warnings, key=lambda item: priority.get(item, 0)) if warnings else None
        max_surge, max_surge_stations = _peak_metric(records, "max_surge_m")
        max_water, max_water_stations = _peak_metric(records, "max_water_level_m")
        max_surge_station = "、".join(max_surge_stations) or None
        max_water_station = "、".join(max_water_stations) or None
        exceedance = _maximum(records, "alert_exceedance_m")
        if explicit:
            severity = max(explicit, key=lambda item: {"轻微": 1, "一般": 2, "严重": 3}.get(item, 0))
            basis = "采用关联记录中的历史影响等级"
        elif warning:
            severity = _warning_severity(warning) or "一般"
            basis = f"按关联记录中的{warning}风暴潮警报归并为三级影响"
        elif max_surge is not None:
            severity, basis = _relative_severity(max_surge, self._population() + [max_surge])
        else:
            severity, basis = "一般", "关联记录关键指标不完整，暂作一般影响判断"
        details = []
        station_peaks = {}
        for record in records:
            station = record.get("station")
            if not station:
                continue
            peak = record.get("max_surge_m")
            if station not in station_peaks or (
                peak is not None and (station_peaks[station] is None or peak > station_peaks[station])
            ):
                station_peaks[station] = peak
        stations = sorted(
            station_peaks,
            key=lambda station: (
                station_peaks[station] is None,
                -(station_peaks[station] or 0.0),
                station,
            ),
        )
        if stations:
            station_text = "、".join(stations[:4])
            if len(stations) > 4:
                station_text += f"等{len(stations)}个站"
            details.append(f"关联验潮站{station_text}")
        if max_surge is not None:
            station_text = f"（站点：{max_surge_station}）" if max_surge_station else "（站点未知）"
            details.append(f"最大风暴增水约{_format_metric(max_surge, 'm')}{station_text}")
        if max_water is not None:
            station_text = f"站点：{max_water_station}，" if max_water_station else "站点未知，"
            details.append(
                f"最高潮位约{_format_metric(max_water, 'm')}"
                f"（{station_text}沿用原验潮站高程基准）"
            )
        if warning:
            details.append(f"最高记录为{warning}警报")
        summary = "，".join(details) if details else "已关联风暴潮记录，但关键数值不完整"
        return {
            "severity": severity,
            "is_estimate": False,
            "confidence": "high" if warning or explicit else "medium",
            "origin": origin,
            "basis": basis,
            "summary": f"{summary}，历史影响综合判断为{severity}",
            "warning_level": warning,
            "max_surge_m": max_surge,
            "max_surge_station": max_surge_station,
            "max_surge_stations": max_surge_stations,
            "max_water_level_m": max_water,
            "max_water_level_station": max_water_station,
            "max_water_level_stations": max_water_stations,
            "alert_exceedance_m": exceedance,
            "stations": stations,
            "sources": sorted({record["source"] for record in records if record.get("source")}),
            "record_count": len(records),
        }

    def _proxy(self, storm: dict, track: list[dict]) -> dict:
        wind = _number(storm.get("wind_at_closest"))
        if wind is None:
            wind = _number(storm.get("max_wind"))
        pressure = _number(storm.get("pressure_at_closest"))
        if pressure is None:
            pressure = _number(storm.get("min_pressure"))
        distance = _number(storm.get("closest_distance_zhejiang"))

        score = 0
        if wind is not None:
            score += 3 if wind >= 80 else 2 if wind >= 55 else 1 if wind >= 34 else 0
        if pressure is not None:
            score += 2 if pressure <= 950 else 1 if pressure <= 980 else 0
        if distance is not None:
            score += 3 if distance <= 100 else 2 if distance <= 250 else 1 if distance <= 500 else 0
        severity = "严重" if score >= 5 else "一般" if score >= 3 else "轻微"
        parts = []
        if distance is not None:
            parts.append(f"轨迹采样点至浙江参考边界的最小计算距离约{distance:.0f} km")
        if wind is not None:
            parts.append(f"近浙江或峰值风速约{format_wind_from_knots(wind)}")
        if pressure is not None:
            parts.append(f"中心气压约{pressure:.0f} hPa")
        conditions = "、".join(parts) if parts else "可用台风条件有限"
        return {
            "severity": severity,
            "is_estimate": True,
            "confidence": "medium" if len(parts) >= 2 else "low",
            "origin": "proxy",
            "basis": "台风强度、中心气压及轨迹采样点至浙江参考边界距离的代理判断",
            "summary": f"暂无关联实测风暴潮数据；依据{conditions}，风暴潮影响潜势粗略判断为{severity}",
            "warning_level": None,
            "max_surge_m": None,
            "max_surge_station": None,
            "max_surge_stations": [],
            "max_water_level_m": None,
            "max_water_level_station": None,
            "max_water_level_stations": [],
            "alert_exceedance_m": None,
            "stations": [],
            "sources": [],
            "record_count": 0,
        }
