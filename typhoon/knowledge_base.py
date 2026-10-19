"""SQLite-backed historical typhoon knowledge base built from IBTrACS."""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import (
    DEFAULT_DATABASE,
    DEFAULT_IBTRACS_ZIP,
    ZHEJIANG_INFLUENCE_BBOX,
    ZHEJIANG_MAX_DISTANCE_KM,
)
from .geometry import compute_motion, distance_to_zhejiang_km, normalize_longitude


IBTRACS_MEMBER_SUFFIX = "ibtracs.ALL.list.v04r01.csv"


def _number(value, *, minimum=None, maximum=None):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"9999", "-999", "-9999"}:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if minimum is not None and number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


def _integer(value):
    number = _number(value)
    return int(number) if number is not None else None


def _first_number(row: dict, fields: Iterable[str], **kwargs):
    for field in fields:
        number = _number(row.get(field), **kwargs)
        if number is not None:
            return number, field.split("_", 1)[0]
    return None, ""


def _normalize_name(value: str | None) -> str:
    name = str(value or "").strip()
    return name if name else "UNNAMED"


def _inside_influence_bbox(lat: float, lon: float) -> bool:
    bbox = ZHEJIANG_INFLUENCE_BBOX
    return bbox["lat_min"] <= lat <= bbox["lat_max"] and bbox["lon_min"] <= lon <= bbox["lon_max"]


def _selected_intensity(row: dict) -> tuple[float | None, float | None, str]:
    # The application serves Zhejiang, so CMA is preferred when available.
    wind, wind_source = _first_number(
        row,
        ("CMA_WIND", "WMO_WIND", "TOKYO_WIND", "USA_WIND"),
        minimum=0,
        maximum=250,
    )
    pressure, pressure_source = _first_number(
        row,
        ("CMA_PRES", "WMO_PRES", "TOKYO_PRES", "USA_PRES"),
        minimum=800,
        maximum=1100,
    )
    sources = [source for source in (wind_source, pressure_source) if source]
    source = "/".join(dict.fromkeys(sources))
    return wind, pressure, source


SCHEMA = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE storms (
    sid TEXT PRIMARY KEY,
    season INTEGER,
    number INTEGER,
    name TEXT NOT NULL,
    basin TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    max_wind REAL,
    min_pressure REAL,
    closest_distance_zhejiang REAL,
    closest_time_zhejiang TEXT,
    closest_lat REAL,
    closest_lon REAL,
    wind_at_closest REAL,
    pressure_at_closest REAL,
    mean_speed REAL,
    mean_direction REAL,
    affects_zhejiang INTEGER NOT NULL,
    track_point_count INTEGER NOT NULL
);

CREATE TABLE track_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sid TEXT NOT NULL,
    time TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    wind REAL,
    pressure REAL,
    nature TEXT,
    storm_speed REAL,
    storm_direction REAL,
    dist_to_land REAL,
    landfall_distance REAL,
    agency TEXT,
    FOREIGN KEY (sid) REFERENCES storms(sid)
);

CREATE INDEX idx_storms_zhejiang ON storms(affects_zhejiang, max_wind, min_pressure);
CREATE INDEX idx_storms_season ON storms(season);
CREATE INDEX idx_points_sid_time ON track_points(sid, time);
"""


def _point_from_row(row: dict) -> dict | None:
    lat = _number(row.get("LAT"), minimum=-90, maximum=90)
    lon = _number(row.get("LON"), minimum=-360, maximum=360)
    iso_time = str(row.get("ISO_TIME") or "").strip()
    if lat is None or lon is None or not iso_time:
        return None
    wind, pressure, agency = _selected_intensity(row)
    return {
        "time": iso_time.replace(" ", "T") + ("Z" if "+" not in iso_time and not iso_time.endswith("Z") else ""),
        "lat": lat,
        "lon": normalize_longitude(lon),
        "wind": wind,
        "pressure": pressure,
        "nature": str(row.get("NATURE") or "").strip(),
        "storm_speed": _number(row.get("STORM_SPEED"), minimum=0, maximum=200),
        "storm_direction": _number(row.get("STORM_DIR"), minimum=0, maximum=360),
        "dist_to_land": _number(row.get("DIST2LAND"), minimum=0),
        "landfall_distance": _number(row.get("LANDFALL"), minimum=0),
        "agency": agency or str(row.get("WMO_AGENCY") or "").strip(),
    }


def _summary(sid: str, meta: dict, points: list[dict]) -> dict:
    points.sort(key=lambda point: point["time"])
    closest = min(points, key=lambda point: distance_to_zhejiang_km(point["lat"], point["lon"]))
    closest_distance = distance_to_zhejiang_km(closest["lat"], closest["lon"])
    winds = [point["wind"] for point in points if point["wind"] is not None]
    pressures = [point["pressure"] for point in points if point["pressure"] is not None]
    # Use coordinate/time-derived motion so current and historical tracks are
    # compared with the same definition.  Reported agency values remain in the
    # point table for audit and display.
    mean_speed, mean_direction = compute_motion(points)
    affects = closest_distance <= ZHEJIANG_MAX_DISTANCE_KM or any(
        _inside_influence_bbox(point["lat"], point["lon"]) for point in points
    )
    return {
        "sid": sid,
        "season": _integer(meta.get("SEASON")),
        "number": _integer(meta.get("NUMBER")),
        "name": _normalize_name(meta.get("NAME")),
        "basin": "WP",
        "start_time": points[0]["time"],
        "end_time": points[-1]["time"],
        "max_wind": max(winds) if winds else None,
        "min_pressure": min(pressures) if pressures else None,
        "closest_distance_zhejiang": closest_distance,
        "closest_time_zhejiang": closest["time"],
        "closest_lat": closest["lat"],
        "closest_lon": closest["lon"],
        "wind_at_closest": closest["wind"],
        "pressure_at_closest": closest["pressure"],
        "mean_speed": mean_speed,
        "mean_direction": mean_direction,
        "affects_zhejiang": 1 if affects else 0,
        "track_point_count": len(points),
    }


def _insert_storm(connection: sqlite3.Connection, sid: str, meta: dict, points: list[dict]) -> None:
    if not points:
        return
    summary = _summary(sid, meta, points)
    connection.execute(
        """
        INSERT INTO storms (
            sid, season, number, name, basin, start_time, end_time,
            max_wind, min_pressure, closest_distance_zhejiang,
            closest_time_zhejiang, closest_lat, closest_lon,
            wind_at_closest, pressure_at_closest, mean_speed,
            mean_direction, affects_zhejiang, track_point_count
        ) VALUES (
            :sid, :season, :number, :name, :basin, :start_time, :end_time,
            :max_wind, :min_pressure, :closest_distance_zhejiang,
            :closest_time_zhejiang, :closest_lat, :closest_lon,
            :wind_at_closest, :pressure_at_closest, :mean_speed,
            :mean_direction, :affects_zhejiang, :track_point_count
        )
        """,
        summary,
    )
    connection.executemany(
        """
        INSERT INTO track_points (
            sid, time, lat, lon, wind, pressure, nature, storm_speed,
            storm_direction, dist_to_land, landfall_distance, agency
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                sid,
                point["time"],
                point["lat"],
                point["lon"],
                point["wind"],
                point["pressure"],
                point["nature"],
                point["storm_speed"],
                point["storm_direction"],
                point["dist_to_land"],
                point["landfall_distance"],
                point["agency"],
            )
            for point in points
        ],
    )


def _find_member(archive: zipfile.ZipFile) -> str:
    matches = [name for name in archive.namelist() if name.endswith(IBTRACS_MEMBER_SUFFIX)]
    if not matches:
        raise FileNotFoundError(f"Archive does not contain {IBTRACS_MEMBER_SUFFIX}")
    return matches[0]


def build_database(
    source_zip: str | Path = DEFAULT_IBTRACS_ZIP,
    output_path: str | Path = DEFAULT_DATABASE,
    *,
    force: bool = False,
    progress=None,
) -> dict:
    """Build an atomic SQLite database from the full IBTrACS CSV."""
    source_zip = Path(source_zip).resolve()
    output_path = Path(output_path).resolve()
    if not source_zip.exists():
        raise FileNotFoundError(f"IBTrACS archive not found: {source_zip}")
    if output_path.exists() and not force:
        raise FileExistsError(f"Database already exists: {output_path}; pass force=True to rebuild")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temp_file = tempfile.NamedTemporaryFile(
        prefix="typhoon_history_",
        suffix=".sqlite3",
        dir=output_path.parent,
        delete=False,
    )
    temp_path = Path(temp_file.name)
    temp_file.close()
    storm_count = 0
    point_count = 0
    try:
        connection = sqlite3.connect(temp_path)
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        with zipfile.ZipFile(source_zip) as archive:
            member = _find_member(archive)
            with archive.open(member) as raw_stream:
                text_stream = io.TextIOWrapper(raw_stream, encoding="utf-8-sig", newline="")
                reader = csv.DictReader(text_stream)
                current_sid = None
                current_meta = None
                current_points = []
                for row in reader:
                    # IBTrACS includes a units row immediately after the header.
                    season = str(row.get("SEASON") or "").strip()
                    if not season.isdigit():
                        continue
                    if str(row.get("BASIN") or "").strip() != "WP":
                        continue
                    if str(row.get("TRACK_TYPE") or "main").strip().lower() not in {"", "main"}:
                        continue
                    sid = str(row.get("SID") or "").strip()
                    if not sid:
                        continue
                    if current_sid is not None and sid != current_sid:
                        _insert_storm(connection, current_sid, current_meta, current_points)
                        storm_count += 1
                        point_count += len(current_points)
                        if storm_count % 250 == 0:
                            connection.commit()
                            if progress:
                                progress(storm_count, point_count)
                        current_points = []
                    if sid != current_sid:
                        current_sid = sid
                        current_meta = row
                    point = _point_from_row(row)
                    if point is not None:
                        current_points.append(point)

                if current_sid is not None and current_points:
                    _insert_storm(connection, current_sid, current_meta, current_points)
                    storm_count += 1
                    point_count += len(current_points)

        metadata = {
            "source": str(source_zip),
            "source_member": member,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "storm_count": storm_count,
            "point_count": point_count,
            "basin": "WP",
            "schema_version": 1,
        }
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()],
        )
        connection.commit()
        connection.execute("ANALYZE")
        connection.execute("VACUUM")
        connection.close()
        if output_path.exists():
            output_path.unlink()
        os.replace(temp_path, output_path)
        return {**metadata, "database": str(output_path)}
    except Exception:
        try:
            connection.close()
        except Exception:
            pass
        if temp_path.exists():
            temp_path.unlink()
        raise


class TyphoonKnowledgeBase:
    def __init__(self, database_path: str | Path = DEFAULT_DATABASE):
        self.database_path = Path(database_path).resolve()

    @property
    def available(self) -> bool:
        return self.database_path.exists()

    def _connect(self) -> sqlite3.Connection:
        if not self.available:
            raise FileNotFoundError(
                f"Typhoon knowledge base not found: {self.database_path}. "
                "Run: python -m typhoon.build_knowledge_base"
            )
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def status(self) -> dict:
        if not self.available:
            return {"available": False, "database": str(self.database_path)}
        with closing(self._connect()) as connection:
            metadata = {
                row["key"]: json.loads(row["value"])
                for row in connection.execute("SELECT key, value FROM metadata")
            }
            zhejiang_count = connection.execute(
                "SELECT COUNT(*) FROM storms WHERE affects_zhejiang = 1"
            ).fetchone()[0]
        return {
            "available": True,
            "database": str(self.database_path),
            "zhejiang_candidate_count": zhejiang_count,
            **metadata,
        }

    def candidate_summaries(
        self,
        exclude_sid: str | None = None,
        *,
        require_intensity: bool = True,
        require_complete_intensity: bool = False,
        require_named: bool = False,
    ) -> list[dict]:
        query = """
            SELECT * FROM storms
            WHERE affects_zhejiang = 1
              AND track_point_count >= 4
        """
        params = []
        if require_complete_intensity:
            query += " AND max_wind IS NOT NULL AND min_pressure IS NOT NULL"
        elif require_intensity:
            query += " AND (max_wind IS NOT NULL OR min_pressure IS NOT NULL)"
        if require_named:
            query += " AND name IS NOT NULL AND TRIM(name) != '' AND UPPER(name) != 'UNNAMED'"
        if exclude_sid:
            query += " AND sid <> ?"
            params.append(exclude_sid)
        query += " ORDER BY season DESC, sid"
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute(query, params)]

    def get_storm(self, sid: str, include_track: bool = True) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM storms WHERE sid = ?", (sid,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            if include_track:
                result["track"] = [
                    dict(point)
                    for point in connection.execute(
                        """
                        SELECT time, lat, lon, wind, pressure, nature,
                               storm_speed, storm_direction, dist_to_land,
                               landfall_distance, agency
                        FROM track_points WHERE sid = ? ORDER BY time
                        """,
                        (sid,),
                    )
                ]
            return result

    def get_tracks(self, sids: Iterable[str]) -> dict[str, list[dict]]:
        sid_list = list(dict.fromkeys(sids))
        if not sid_list:
            return {}
        result = {sid: [] for sid in sid_list}
        with closing(self._connect()) as connection:
            # SQLite builds commonly limit one statement to 999 bound values.
            # Path-only mode can evaluate more than one thousand candidates.
            for offset in range(0, len(sid_list), 900):
                batch = sid_list[offset:offset + 900]
                placeholders = ",".join("?" for _ in batch)
                query = f"""
                    SELECT sid, time, lat, lon, wind, pressure, nature,
                           storm_speed, storm_direction, dist_to_land,
                           landfall_distance, agency
                    FROM track_points
                    WHERE sid IN ({placeholders})
                    ORDER BY sid, time
                """
                for row in connection.execute(query, batch):
                    point = dict(row)
                    sid = point.pop("sid")
                    result.setdefault(sid, []).append(point)
        return result
