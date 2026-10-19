"""Import legacy annual storm-surge tables stored in Word 97-2003 DOC files.

The source collection uses one DOC per year and repeats a compact event code,
date and station coordinate at the start of every station table.  The parser is
deliberately independent of Microsoft Word: it recovers printable streams from
the OLE file, groups station tables by event code and resolves the most likely
IBTrACS SID from event time plus track-to-station distance.
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import statistics
import unicodedata
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path


_HEADER_RE = re.compile(
    r"S\.N\.\s*(?P<serial>\d+)\s+(?P<code>\d{4})\s+"
    r"(?P<month>\d{1,2})\s+(?P<day>\d{1,2})\s+"
    r"(?:(?P<station>[A-Za-z][A-Za-z0-9_-]{1,20})\b)?",
    re.I,
)
_LON_RE = re.compile(r"\bLon\s+(?P<value>\d{4,5})\b", re.I)
_LAT_RE = re.compile(r"\bLat\s+(?P<value>\d{4})\b", re.I)


class LegacyStormSurgeDocError(ValueError):
    pass


def _year_from_filename(filename: str) -> int | None:
    stem = Path(filename).stem.strip()
    if re.fullmatch(r"(?:19|20)\d{2}", stem):
        year = int(stem)
        return year if 1950 <= year <= 2016 else None
    if re.fullmatch(r"\d{2}", stem):
        value = int(stem)
        return 1900 + value if value >= 50 else 2000 + value
    return None


def _is_text_char(char: str) -> bool:
    if char in "\r\n\t":
        return True
    if not char or char == "\x00":
        return False
    category = unicodedata.category(char)
    return not category.startswith("C") and category not in {"Cn", "Co"}


def recover_doc_text(data: bytes) -> str:
    """Recover plain text from an OLE DOC without Office or third-party tools."""
    if not data.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        raise LegacyStormSurgeDocError("文件不是有效的 Word 97-2003 DOC/OLE 文档")
    runs: list[tuple[int, str]] = []
    for match in re.finditer(rb"[\x09\x0a\x0d\x20-\x7e]{4,}", data):
        text = match.group(0).decode("latin-1", errors="ignore")
        if re.search(r"[A-Za-z0-9]", text):
            runs.append((match.start(), text))
    # Word may store text as UTF-16LE. Scan both alignments and retain long
    # printable sequences. Byte offsets let us restore the original order.
    for alignment in (0, 1):
        decoded = data[alignment:].decode("utf-16le", errors="ignore")
        start: int | None = None
        buffer: list[str] = []
        for index, char in enumerate(decoded):
            if _is_text_char(char):
                if start is None:
                    start = index
                buffer.append(char)
            else:
                if start is not None and len(buffer) >= 4:
                    text = "".join(buffer)
                    if re.search(r"[A-Za-z0-9\u3400-\u9fff]", text):
                        runs.append((alignment + start * 2, text))
                start, buffer = None, []
        if start is not None and len(buffer) >= 4:
            runs.append((alignment + start * 2, "".join(buffer)))
    runs.sort(key=lambda item: item[0])
    cleaned: list[str] = []
    previous = ""
    for _, value in runs:
        value = value.replace("\x0b", "\n").strip()
        if not value or value == previous:
            continue
        cleaned.append(value)
        previous = value
    return "\n".join(cleaned)


def _coordinate(value: str, *, longitude: bool) -> float | None:
    digits = re.sub(r"\D", "", value)
    degree_digits = 3 if longitude else 2
    if len(digits) != degree_digits + 2:
        return None
    degrees, minutes = int(digits[:degree_digits]), int(digits[degree_digits:])
    limit = 180 if longitude else 90
    if degrees > limit or minutes >= 60:
        return None
    return degrees + minutes / 60.0


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    value = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(value)))


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        result = datetime.fromisoformat(text)
        return result.replace(tzinfo=None)
    except ValueError:
        return None


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def _associate_event(
    database_path: Path, season: int, headers: list[dict]
) -> dict:
    event_dates = sorted({item["date"] for item in headers if item.get("date")})
    stations = [
        (float(item["lat"]), float(item["lon"]), item["date"])
        for item in headers
        if item.get("lat") is not None and item.get("lon") is not None and item.get("date")
    ]
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        storms = connection.execute(
            "SELECT sid, season, name, start_time, end_time FROM storms WHERE season = ?",
            (season,),
        ).fetchall()
        tracks: dict[str, list[tuple[datetime, float, float]]] = defaultdict(list)
        for row in connection.execute(
            """SELECT p.sid, p.time, p.lat, p.lon FROM track_points p
               JOIN storms s ON s.sid=p.sid WHERE s.season=? ORDER BY p.sid, p.time""",
            (season,),
        ):
            point_time = _parse_time(row[1])
            if point_time is not None:
                tracks[str(row[0])].append((point_time, float(row[2]), float(row[3])))

    candidates: list[dict] = []
    for storm in storms:
        sid = str(storm["sid"])
        start, end = _parse_time(storm["start_time"]), _parse_time(storm["end_time"])
        if start is None or end is None or not event_dates:
            continue
        covered = [date for date in event_dates if start.date() <= date.date() <= end.date()]
        coverage = len(covered) / len(event_dates)
        time_gaps = [
            0.0 if start.date() <= date.date() <= end.date()
            else min(abs((date - start).total_seconds()), abs((date - end).total_seconds())) / 3600
            for date in event_dates
        ]
        distances: list[float] = []
        for lat, lon, date in stations:
            near = [p for p in tracks.get(sid, []) if abs(p[0] - date) <= timedelta(hours=96)]
            if near:
                distances.append(min(_haversine(lat, lon, p[1], p[2]) for p in near))
        q_distance = _percentile(distances, 0.25)
        median_distance = statistics.median(distances) if distances else 0.0
        no_geometry_penalty = 180.0 if stations and not distances else 0.0
        score = (
            (1.0 - coverage) * 800.0
            + statistics.median(time_gaps) * 4.0
            + q_distance * 0.35
            + median_distance * 0.05
            + no_geometry_penalty
        )
        candidates.append({
            "sid": sid,
            "name": str(storm["name"] or ""),
            "score": round(score, 3),
            "date_overlap": round(coverage, 4),
            "station_distance_q25_km": round(q_distance, 3) if distances else None,
            "station_distance_median_km": round(median_distance, 3) if distances else None,
        })
    candidates.sort(key=lambda item: (item["score"], item["sid"]))
    if not candidates:
        return {"candidate_sid": None, "candidate_name": None, "confidence": "unlinked", "top_candidates": []}
    best = candidates[0]
    margin = (candidates[1]["score"] - best["score"]) if len(candidates) > 1 else 9999.0
    has_geometry = bool(stations) and best["station_distance_q25_km"] is not None
    if best["date_overlap"] >= 0.8 and margin >= 50 and has_geometry:
        confidence = "high"
    elif best["date_overlap"] >= 0.5 and margin >= 15:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "candidate_sid": best["sid"],
        "candidate_name": best["name"],
        "confidence": confidence,
        "score_margin": round(margin, 3),
        "top_candidates": candidates[:3],
    }


def split_annual_doc(
    filename: str, data: bytes, history_database_path: str | Path
) -> list[dict]:
    """Return one auditable UTF-8 text document per legacy storm code."""
    year = _year_from_filename(filename)
    if year is None:
        raise LegacyStormSurgeDocError(
            "历史风暴潮 DOC 文件名必须是两位年份（如 50.doc）或四位年份（如 2000.doc）"
        )
    database_path = Path(history_database_path)
    if not database_path.exists():
        raise LegacyStormSurgeDocError("台风历史数据库不存在，无法自动关联 SID")
    recovered = recover_doc_text(data)
    matches = list(_HEADER_RE.finditer(recovered))
    expected_prefix = f"{year % 100:02d}"
    valid = [match for match in matches if match.group("code").startswith(expected_prefix)]
    if not valid:
        raise LegacyStormSurgeDocError(f"未在 {Path(filename).name} 中识别到 {year} 年事件表")

    groups: dict[str, list[dict]] = defaultdict(list)
    seen_blocks: set[str] = set()
    for index, match in enumerate(valid):
        end = valid[index + 1].start() if index + 1 < len(valid) else len(recovered)
        block = recovered[match.start():end].strip()
        digest = hashlib.sha256(re.sub(r"\s+", " ", block).encode("utf-8")).hexdigest()
        if digest in seen_blocks:
            continue
        seen_blocks.add(digest)
        # In old compound DOCs the station heading may be split across an
        # ANSI run and a later Unicode run.  Search the complete station block
        # so coordinates remain recoverable even when those streams are far
        # apart in the physical OLE file.
        excerpt = recovered[match.end():end]
        lon_match, lat_match = _LON_RE.search(excerpt), _LAT_RE.search(excerpt)
        try:
            event_date = datetime(year, int(match.group("month")), int(match.group("day")))
        except ValueError:
            event_date = None
        groups[match.group("code")].append({
            "serial": match.group("serial"),
            "station": (match.group("station") or f"SN-{match.group('serial')}").upper(),
            "date": event_date,
            "lon": _coordinate(lon_match.group("value"), longitude=True) if lon_match else None,
            "lat": _coordinate(lat_match.group("value"), longitude=False) if lat_match else None,
            "block": block,
        })

    parent_sha256 = hashlib.sha256(data).hexdigest()
    sections: list[dict] = []
    seen_codes: set[str] = set()
    for code in sorted(groups):
        headers = groups[code]
        association = _associate_event(database_path, year, headers)
        dates = sorted({item["date"].date().isoformat() for item in headers if item.get("date")})
        stations = sorted({item["station"] for item in headers})
        confidence = association["confidence"]
        prefix = [
            f"历史风暴潮逐站逐时记录：{year} 年事件代码 {code}",
            f"事件代码：{code}",
            f"资料年份：{year}",
            f"事件日期：{'、'.join(dates) if dates else '未识别'}",
            f"站点：{'、'.join(stations)}",
            f"站点表数量：{len(headers)}",
            f"SID 自动关联置信度：{confidence}",
        ]
        if confidence == "high":
            prefix.extend([
                f"关联台风：{association['candidate_name']}",
                f"IBTrACS SID：{association['candidate_sid']}",
            ])
        else:
            prefix.append("候选台风需人工复核，正文检索结果不将候选身份视为确定事实。")
        text = "\n".join(prefix) + "\n\n原始逐站逐时表：\n" + "\n\n".join(
            item["block"] for item in headers
        )
        metadata = {
            "structured_data_type": "legacy_storm_surge_year_doc",
            "legacy_doc_parent": Path(filename).name,
            "legacy_doc_parent_sha256": parent_sha256,
            "storm_code": code,
            "season": year,
            "event_start": dates[0] if dates else None,
            "event_end": dates[-1] if dates else None,
            "station_count": len(stations),
            "station_table_count": len(headers),
            "station_codes": stations,
            "storm_link_method": "legacy_event_time_track_geometry",
            "candidate_sid": association.get("candidate_sid"),
            "candidate_name": association.get("candidate_name"),
            "candidate_link_confidence": confidence,
            "candidate_score_margin": association.get("score_margin"),
            "candidate_top3": association.get("top_candidates", []),
        }
        if confidence == "high":
            metadata["sid"] = association["candidate_sid"]
            metadata["storm_name"] = association["candidate_name"]
            metadata["storm_link_confidence"] = 1.0
        sections.append({
            "filename": f"历史风暴潮-{year}-{code}.txt",
            "data": text.encode("utf-8"),
            "metadata": metadata,
        })
        seen_codes.add(code)
    if len(seen_codes) != len(groups):
        raise LegacyStormSurgeDocError("年度DOC事件拆分不完整，已停止导入")
    return sections
