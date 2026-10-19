"""Validated post-event typhoon report facts stored in the unified knowledge base."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .config import DEFAULT_DATABASE


WARNING_PRIORITY = {"蓝色": 1, "黄色": 2, "橙色": 3, "红色": 4}


def _station_key(value: str | None) -> str:
    """Normalize harmless station-name variants without changing identity."""
    station = "".join(str(value or "").split()).strip()
    return station[:-1] if station.endswith("站") and len(station) > 1 else station


def _station_surge_details(connection: sqlite3.Connection, case_id: str) -> list[dict]:
    """Return QC-approved station warning levels and event maxima for one case.

    A missing warning level stays missing: it must never be interpreted as
    either "未超警" or a lower warning level.
    """
    has_metrics = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='typhoon_report_station_metrics'"
    ).fetchone()
    if not has_metrics:
        return []
    rows = connection.execute(
        """
        SELECT station_name, metric_type, value_si, unit_si,
               observed_time_text, warning_level, source_excerpt
        FROM typhoon_report_station_metrics
        WHERE case_id = ?
          AND qc_status != 'fail'
          AND metric_type IN ('storm_surge', 'highest_water_level')
        ORDER BY metric_id
        """,
        (case_id,),
    ).fetchall()
    grouped: dict[str, dict] = {}
    for row in rows:
        station = _station_key(row["station_name"])
        if not station:
            continue
        detail = grouped.setdefault(station, {
            "station": station,
            "warning_level": None,
            "max_surge_m": None,
            "max_surge_time": None,
            "max_water_level_m": None,
            "max_water_level_time": None,
            "source_excerpts": [],
        })
        warning = str(row["warning_level"] or "").strip() or None
        if warning and WARNING_PRIORITY.get(warning, 0) > WARNING_PRIORITY.get(detail["warning_level"], 0):
            detail["warning_level"] = warning
        value = row["value_si"]
        if value is None or str(row["unit_si"] or "").strip().lower() != "m":
            continue
        value = float(value)
        if row["metric_type"] == "storm_surge":
            if detail["max_surge_m"] is None or value > detail["max_surge_m"]:
                detail["max_surge_m"] = value
                detail["max_surge_time"] = row["observed_time_text"]
        elif row["metric_type"] == "highest_water_level":
            if detail["max_water_level_m"] is None or value > detail["max_water_level_m"]:
                detail["max_water_level_m"] = value
                detail["max_water_level_time"] = row["observed_time_text"]
        excerpt = str(row["source_excerpt"] or "").strip()
        if excerpt and excerpt not in detail["source_excerpts"] and len(detail["source_excerpts"]) < 3:
            detail["source_excerpts"].append(excerpt[:160])
    return sorted(
        grouped.values(),
        key=lambda item: (
            -WARNING_PRIORITY.get(item["warning_level"], 0),
            -(item["max_surge_m"] if item["max_surge_m"] is not None else -999.0),
            item["station"],
        ),
    )


class TyphoonReportRepository:
    """Read QC-approved report facts without exposing the raw source document."""

    def __init__(self, database_path: str | Path = DEFAULT_DATABASE):
        self.database_path = Path(database_path).resolve()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _has_tables(self, connection: sqlite3.Connection) -> bool:
        required = {"typhoon_reports", "typhoon_report_cases"}
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'typhoon_report_%'"
            )
        }
        return required.issubset(existing)

    @property
    def available(self) -> bool:
        if not self.database_path.exists():
            return False
        with closing(self._connect()) as connection:
            return self._has_tables(connection)

    def status(self) -> dict:
        if not self.database_path.exists():
            return {"available": False, "database": str(self.database_path)}
        with closing(self._connect()) as connection:
            if not self._has_tables(connection):
                return {"available": False, "database": str(self.database_path)}
            report_count = connection.execute("SELECT COUNT(*) FROM typhoon_reports").fetchone()[0]
            case_count = connection.execute("SELECT COUNT(*) FROM typhoon_report_cases").fetchone()[0]
            linked_count = connection.execute(
                "SELECT COUNT(*) FROM typhoon_report_cases WHERE sid IS NOT NULL"
            ).fetchone()[0]
            pending_count = connection.execute(
                "SELECT COUNT(*) FROM typhoon_report_cases WHERE sid IS NULL"
            ).fetchone()[0]
            metric_count = connection.execute(
                "SELECT COUNT(*) FROM typhoon_report_station_metrics"
            ).fetchone()[0]
            unit_inferred_count = connection.execute(
                "SELECT COUNT(*) FROM typhoon_report_station_metrics WHERE unit_inferred = 1"
            ).fetchone()[0]
            qc_issue_count = connection.execute(
                "SELECT COUNT(*) FROM typhoon_report_qc_issues"
            ).fetchone()[0]
            year_range = connection.execute(
                "SELECT MIN(season_resolved), MAX(season_resolved) FROM typhoon_report_cases"
            ).fetchone()
            qc = dict(connection.execute(
                "SELECT qc_status, COUNT(*) FROM typhoon_reports GROUP BY qc_status"
            ).fetchall())
        return {
            "available": bool(report_count),
            "database": str(self.database_path),
            "report_count": report_count,
            "case_count": case_count,
            "linked_case_count": linked_count,
            "pending_track_count": pending_count,
            "metric_count": metric_count,
            "unit_inferred_count": unit_inferred_count,
            "qc_issue_count": qc_issue_count,
            "season_start": year_range[0],
            "season_end": year_range[1],
            "qc_counts": qc,
        }

    def lookup(self, storm: dict) -> dict | None:
        if not self.available:
            return None
        sid = str(storm.get("sid") or "").strip()
        name = str(storm.get("name") or "").strip().upper()
        season = storm.get("season")
        clauses = []
        params: list[object] = []
        if sid:
            clauses.append("c.sid = ?")
            params.append(sid)
        if name and season is not None:
            clauses.append("(UPPER(c.storm_name_en) = ? AND c.season_resolved = ?)")
            params.extend((name, int(season)))
        if not clauses:
            return None
        confidence_order = "CASE c.link_confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END"
        query = f"""
            SELECT c.*, r.title AS report_title, r.source_file, r.source_sha256,
                   r.imported_at, r.qc_status AS report_qc_status
            FROM typhoon_report_cases c
            JOIN typhoon_reports r ON r.report_id = c.report_id
            WHERE ({' OR '.join(clauses)})
              AND c.qc_status != 'fail' AND r.qc_status != 'fail'
            ORDER BY {confidence_order} DESC, r.imported_at DESC
            LIMIT 1
        """
        with closing(self._connect()) as connection:
            row = connection.execute(query, params).fetchone()
            station_details = _station_surge_details(connection, row["case_id"]) if row else []
        if row is None:
            return None
        record = dict(row)
        return {
            "available": True,
            "case_id": record["case_id"],
            "report_id": record["report_id"],
            "storm_code": record["storm_code"],
            "storm_name_cn": record["storm_name_cn"],
            "storm_name_en": record["storm_name_en"],
            "season": record["season_resolved"],
            "event_start": record["event_start"],
            "event_end": record["event_end"],
            "landfall_time": record["landfall_time_text"],
            "landfall_location": record["landfall_location"],
            "max_wind_ms": record["max_wind_ms"],
            "min_pressure_hpa": record["min_pressure_hpa"],
            "max_surge_m": record["max_surge_m"],
            "max_surge_station": record["max_surge_station"],
            "max_surge_time": record["max_surge_time_text"],
            "max_water_level_m": record["max_water_level_m"],
            "max_water_level_station": record["max_water_level_station"],
            "max_water_level_time": record["max_water_level_time_text"],
            "max_significant_wave_m": record["max_significant_wave_m"],
            "max_significant_wave_station": record["max_significant_wave_station"],
            "max_wave_height_m": record["max_wave_height_m"],
            "max_wave_height_station": record["max_wave_height_station"],
            "storm_surge_warning_level": record["storm_surge_warning_level"],
            "storm_surge_station_details": station_details,
            "storm_surge_warning_station_count": sum(
                1 for item in station_details if item.get("warning_level")
            ),
            "wave_warning_level": record["wave_warning_level"],
            "direct_economic_loss_cny": record["direct_economic_loss_cny"],
            "deaths": record["deaths"],
            "affected_population": record["affected_population"],
            "historical_impact_level": record["historical_impact_level"],
            "impact_level_basis": record["impact_level_basis"],
            "disaster_summary": record["disaster_summary"],
            "forecast_summary": record["forecast_summary"],
            "response_summary": record["response_summary"],
            "link_method": record["link_method"],
            "link_confidence": record["link_confidence"],
            "time_match_status": record["time_match_status"],
            "qc_status": record["report_qc_status"],
            "source_file": record["source_file"],
            "source_sha256": record["source_sha256"],
            "source_type": "浙江台风影响总结报告",
        }
