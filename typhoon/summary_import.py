"""Import Zhejiang typhoon post-event reports into the unified SQLite knowledge base.

The source documents are narrative Word/WPS reports.  A Windows extraction step
captures their text and tables without executing macros; this module then links
reports to IBTrACS storms, normalizes units, records QC evidence and performs an
atomic database update.  Existing IBTrACS and hourly storm-surge tables are not
modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import DEFAULT_DATABASE


CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUMMARY_ARCHIVE = PROJECT_ROOT.parent / "影响严重的台风总结.zip"
EXTRACTION_SCRIPT = PROJECT_ROOT / "tools" / "extract_typhoon_summary_documents.ps1"


CHINESE_TO_ENGLISH = {
    "威玛逊": "RAMMASUN",
    "森拉克": "SINLAKU",
    "云娜": "RANANIM",
    "海棠": "HAITANG",
    "麦莎": "MATSA",
    "桑美": "SAOMAI",
    "韦帕": "WIPHA",
    "蔷薇": "JANGMI",
    "罗莎": "KROSA",
    "莫拉克": "MORAKOT",
    "圆规": "KOMPASU",
    "梅花": "MUIFA",
    "南玛都": "NANMADOL",
    "苏拉": "SAOLA",
    "海葵": "HAIKUI",
    "天秤": "TEMBIN",
    "苏力": "SOULIK",
    "潭美": "TRAMI",
    "菲特": "FITOW",
    "凤凰": "FUNG-WONG",
    "灿鸿": "CHAN-HOM",
    "苏迪罗": "SOUDELOR",
    "杜鹃": "DUJUAN",
    "马勒卡": "MALAKAS",
    "纳沙": "NESAT",
    "泰利": "TALIM",
    "卡努": "KHANUN",
    "玛莉亚": "MARIA",
    "安比": "AMPIL",
    "云雀": "JONGDARI",
    "摩羯": "YAGI",
    "温比亚": "RUMBIA",
    "利奇马": "LEKIMA",
    "米娜": "MITAG",
    "黑格比": "HAGUPIT",
    "美莎克": "MAYSAK",
    "烟花": "IN-FA",
    "灿都": "CHANTHU",
    "轩岚诺": "HINNAMNOR",
    "杜苏芮": "DOKSURI",
    "小犬": "KOINU",
    "格美": "GAEMI",
    "贝碧嘉": "BEBINCA",
    "普拉桑": "PULASAN",
    "山陀儿": "KRATHON",
    "康妮": "KONG-REY",
    "丹娜丝": "DANAS",
    "范斯高": "FRANCISCO",
    "竹节草": "CO-MAY",
    "巴威": "BAVI",
    "白海豚": "DOLPHIN",
    "沙德尔": "SAUDEL",
}

# Explicit source identities are auditable overrides for reports whose file name
# is incomplete or erroneous.  In particular, "0816 罗莎" is a 2007 KROSA
# report even though its legacy filename starts with 08.
IDENTITY_OVERRIDES = {
    "0205": {"name_cn": "威玛逊", "name_en": "RAMMASUN"},
    "0216": {"name_cn": "森拉克", "name_en": "SINLAKU"},
    "0414": {"name_cn": "云娜", "name_en": "RANANIM"},
    "0713": {"name_cn": "韦帕", "name_en": "WIPHA"},
    "0816": {"season": 2007, "storm_number": 16, "name_cn": "罗莎", "name_en": "KROSA"},
    "1323": {"name_cn": "菲特", "name_en": "FITOW"},
    "1909": {"name_cn": "利奇马", "name_en": "LEKIMA"},
    "1918": {"name_cn": "米娜", "name_en": "MITAG"},
    "2004": {"name_cn": "黑格比", "name_en": "HAGUPIT"},
}


REPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS typhoon_reports (
    report_id TEXT PRIMARY KEY,
    source_archive TEXT,
    source_file TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    file_type TEXT NOT NULL,
    title TEXT,
    page_count INTEGER,
    table_count INTEGER,
    figure_count INTEGER,
    extraction_method TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    qc_status TEXT NOT NULL,
    qc_issue_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS typhoon_report_cases (
    case_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    storm_code TEXT NOT NULL,
    season_reported INTEGER,
    season_resolved INTEGER,
    storm_number INTEGER,
    storm_name_cn TEXT,
    storm_name_en TEXT,
    sid TEXT,
    link_method TEXT NOT NULL,
    link_confidence TEXT NOT NULL,
    time_match_status TEXT NOT NULL,
    time_offset_hours REAL,
    event_start TEXT,
    event_end TEXT,
    landfall_time_text TEXT,
    landfall_location TEXT,
    max_wind_ms REAL,
    max_wind_kt REAL,
    min_pressure_hpa REAL,
    max_surge_m REAL,
    max_surge_station TEXT,
    max_surge_time_text TEXT,
    max_water_level_m REAL,
    max_water_level_station TEXT,
    max_water_level_time_text TEXT,
    max_significant_wave_m REAL,
    max_significant_wave_station TEXT,
    max_wave_height_m REAL,
    max_wave_height_station TEXT,
    storm_surge_warning_level TEXT,
    wave_warning_level TEXT,
    direct_economic_loss_cny REAL,
    deaths INTEGER,
    affected_population INTEGER,
    forecast_accuracy_surge_pct REAL,
    forecast_accuracy_wave_pct REAL,
    historical_impact_level TEXT,
    impact_level_basis TEXT,
    disaster_summary TEXT,
    forecast_summary TEXT,
    response_summary TEXT,
    qc_status TEXT NOT NULL,
    FOREIGN KEY (report_id) REFERENCES typhoon_reports(report_id),
    FOREIGN KEY (sid) REFERENCES storms(sid),
    UNIQUE (report_id, storm_code)
);

CREATE TABLE IF NOT EXISTS typhoon_report_station_metrics (
    metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    station_name TEXT,
    metric_type TEXT NOT NULL,
    value_si REAL NOT NULL,
    unit_si TEXT NOT NULL,
    source_value REAL NOT NULL,
    source_unit TEXT,
    unit_inferred INTEGER NOT NULL DEFAULT 0,
    normalization_rule TEXT NOT NULL,
    observed_time_text TEXT,
    warning_level TEXT,
    source_table_index INTEGER,
    source_row INTEGER,
    source_excerpt TEXT,
    qc_status TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES typhoon_report_cases(case_id)
);

CREATE TABLE IF NOT EXISTS typhoon_report_sections (
    section_id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id TEXT NOT NULL,
    section_order INTEGER NOT NULL,
    section_type TEXT NOT NULL,
    heading TEXT,
    section_text TEXT NOT NULL,
    FOREIGN KEY (report_id) REFERENCES typhoon_reports(report_id)
);

CREATE TABLE IF NOT EXISTS typhoon_report_qc_issues (
    issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id TEXT NOT NULL,
    case_id TEXT,
    severity TEXT NOT NULL,
    issue_code TEXT NOT NULL,
    field_name TEXT,
    message TEXT NOT NULL,
    source_excerpt TEXT,
    FOREIGN KEY (report_id) REFERENCES typhoon_reports(report_id),
    FOREIGN KEY (case_id) REFERENCES typhoon_report_cases(case_id)
);

CREATE INDEX IF NOT EXISTS idx_typhoon_report_cases_sid
    ON typhoon_report_cases(sid);
CREATE INDEX IF NOT EXISTS idx_typhoon_report_cases_code
    ON typhoon_report_cases(season_resolved, storm_number);
CREATE INDEX IF NOT EXISTS idx_typhoon_report_metrics_case_type
    ON typhoon_report_station_metrics(case_id, metric_type);
CREATE INDEX IF NOT EXISTS idx_typhoon_report_sections_report
    ON typhoon_report_sections(report_id, section_order);
"""


WARNING_PRIORITY = {"蓝色": 1, "黄色": 2, "橙色": 3, "红色": 4}
SECTION_TYPES = (
    ("storm_surge", ("风暴潮", "潮位")),
    ("wave", ("海浪", "波高")),
    ("meteorology", ("气象", "风速", "气压")),
    ("disaster", ("灾害", "灾情", "损失", "伤亡")),
    ("forecast_verification", ("预报结果检验", "警报检验", "预警准确率", "预报误差")),
    ("emergency_response", ("防御", "应急", "值班", "会商")),
    ("lessons", ("存在问题", "经验总结", "工作亮点", "下步思路", "不足")),
    ("overview", ("台风概况", "基本情况", "实况分析")),
)


def _clean_text(value) -> str:
    text = str(value or "").replace("\u0007", "\t").replace("\u000b", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\u0000-\u0006\u0008\u000e-\u001f]", "", text)
    text = re.sub(r"[ \u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _first_number(value) -> float | None:
    match = re.search(r"(?<!\d)(-?\d+(?:\.\d+)?)", str(value or "").replace(",", ""))
    if not match:
        return None
    try:
        number = float(match.group(1))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _normalize_name(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _warning(text: str | None) -> str | None:
    value = str(text or "")
    for item in ("红色", "橙色", "黄色", "蓝色"):
        if item in value:
            return item
    return None


def _highest_warning(values) -> str | None:
    usable = [item for item in values if item in WARNING_PRIORITY]
    return max(usable, key=WARNING_PRIORITY.get) if usable else None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    localized = value if value.tzinfo else value.replace(tzinfo=CHINA_STANDARD_TIME)
    return localized.astimezone(CHINA_STANDARD_TIME).isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(CHINA_STANDARD_TIME)


def _parse_event_window(text: str) -> tuple[datetime | None, datetime | None, list[dict]]:
    issues: list[dict] = []
    head = text[:2500]
    pattern = re.compile(
        r"(?P<y1>20\d{2})年(?P<m1>\d{1,2})月(?P<d1>\d{1,2})日?\s*"
        r"(?:至|到|～|~|—|－|-)\s*"
        r"(?:(?P<y2>20\d{2})年)?(?:(?P<m2>\d{1,2})月)?(?P<d2>\d{1,2})日"
    )
    for match in pattern.finditer(head):
        values = match.groupdict()
        y1, m1, d1 = int(values["y1"]), int(values["m1"]), int(values["d1"])
        y2 = int(values["y2"] or y1)
        m2 = int(values["m2"] or m1)
        d2 = int(values["d2"])
        try:
            start = datetime(y1, m1, d1, tzinfo=CHINA_STANDARD_TIME)
            end = datetime(y2, m2, d2, 23, 59, 59, tzinfo=CHINA_STANDARD_TIME)
        except ValueError:
            continue
        if end < start:
            issues.append({
                "severity": "warning",
                "issue_code": "EVENT_DATE_REVERSED",
                "field_name": "event_window",
                "message": "报告中的结束日期早于开始日期，未采用该时间窗。",
                "source_excerpt": match.group(0),
            })
            continue
        if (end - start).days > 45:
            continue
        return start, end, issues
    return None, None, issues


def _storm_codes(filename: str, text: str) -> list[str]:
    stem = Path(filename).stem
    result: list[str] = []

    def add(code: str) -> None:
        if code not in result:
            result.append(code)

    leading = re.match(r"^(\d{2})(\d{2})", stem)
    if leading:
        add("".join(leading.groups()))
    for match in re.finditer(r"(?<!\d)(\d{2})(\d{2})号", stem):
        add("".join(match.groups()))
    for match in re.finditer(r"(20\d{2})年第\s*(\d{1,2})号", text[:1800]):
        add(f"{int(match.group(1)) % 100:02d}{int(match.group(2)):02d}")
    return result


def _candidate_chinese_names(filename: str, text: str) -> list[str]:
    names: list[str] = []

    def add(value: str) -> None:
        cleaned = re.sub(r"\s+", "", value).strip("台风号（）()“”\"《》")
        if cleaned in CHINESE_TO_ENGLISH and cleaned not in names:
            names.append(cleaned)

    for source in (Path(filename).stem, text[:2200]):
        for match in re.finditer(r"[“\"]([^”\"]{1,12})[”\"]", source):
            add(match.group(1))
        for known in CHINESE_TO_ENGLISH:
            if known in source:
                add(known)
    return names


def _identity_cases(record: dict) -> list[dict]:
    filename = str(record.get("source_file") or "")
    text = _clean_text(record.get("text"))
    codes = _storm_codes(filename, text)
    names = _candidate_chinese_names(filename, text)
    cases = []
    for index, code in enumerate(codes):
        year = 2000 + int(code[:2])
        number = int(code[2:])
        name_cn = names[index] if index < len(names) else (names[0] if len(codes) == 1 and names else None)
        name_en = CHINESE_TO_ENGLISH.get(name_cn or "")
        override = IDENTITY_OVERRIDES.get(code, {})
        cases.append({
            "storm_code": code,
            "season_reported": year,
            "season_resolved": int(override.get("season", year)),
            "storm_number": int(override.get("storm_number", number)),
            "storm_name_cn": override.get("name_cn", name_cn),
            "storm_name_en": override.get("name_en", name_en),
            "identity_overridden": bool(override.get("season") or override.get("storm_number")),
        })
    return cases


def _load_storms(connection: sqlite3.Connection) -> list[dict]:
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute(
        """
        SELECT sid, season, name, start_time, end_time,
               closest_distance_zhejiang, affects_zhejiang
        FROM storms
        """
    )]


def _time_gap_hours(start: datetime, end: datetime, storm: dict) -> float | None:
    storm_start = _parse_iso(storm.get("start_time"))
    storm_end = _parse_iso(storm.get("end_time"))
    if storm_start is None or storm_end is None:
        return None
    if storm_start <= end and storm_end >= start:
        return 0.0
    if storm_start > end:
        return (storm_start - end).total_seconds() / 3600.0
    return (start - storm_end).total_seconds() / 3600.0


def _link_case(case: dict, storms: list[dict], event_start, event_end) -> dict:
    expected_name = _normalize_name(case.get("storm_name_en"))
    reported_year = int(case["season_reported"])
    resolved_year = int(case["season_resolved"])
    if not expected_name:
        return {
            "sid": None,
            "season_resolved": resolved_year,
            "storm_name_en": case.get("storm_name_en"),
            "link_method": "未匹配（缺少可核验的中英文台风名称）",
            "link_confidence": "none",
            "time_match_status": "unavailable",
            "time_offset_hours": None,
        }

    named = [storm for storm in storms if _normalize_name(storm.get("name")) == expected_name]
    same_year = [storm for storm in named if int(storm.get("season") or 0) == resolved_year]
    nearby_year = [storm for storm in named if abs(int(storm.get("season") or 0) - reported_year) <= 1]
    candidates = same_year or nearby_year
    if not candidates:
        return {
            "sid": None,
            "season_resolved": resolved_year,
            "storm_name_en": case.get("storm_name_en"),
            "link_method": "未匹配（当前路径库无同年同名台风）",
            "link_confidence": "none",
            "time_match_status": "unavailable",
            "time_offset_hours": None,
        }
    candidates.sort(key=lambda storm: (
        abs(int(storm.get("season") or 0) - resolved_year),
        0 if storm.get("affects_zhejiang") else 1,
        float(storm.get("closest_distance_zhejiang") or 99999),
    ))
    storm = candidates[0]
    status = "unavailable"
    gap = None
    if event_start and event_end:
        gap = _time_gap_hours(event_start, event_end, storm)
        status = "overlap" if gap == 0 else "nearby" if gap is not None and gap <= 72 else "conflict"
        if status == "conflict":
            return {
                "sid": None,
                "season_resolved": int(storm.get("season") or resolved_year),
                "storm_name_en": storm.get("name"),
                "link_method": "同名候选与报告时间冲突，拒绝关联",
                "link_confidence": "none",
                "time_match_status": status,
                "time_offset_hours": round(gap, 2) if gap is not None else None,
            }
    corrected_year = int(storm.get("season") or resolved_year) != reported_year
    if status == "overlap" and not corrected_year:
        confidence = "high"
    elif status in {"overlap", "nearby"} or not corrected_year:
        confidence = "medium"
    else:
        confidence = "low"
    method = "报告编号+中英文名称"
    if status != "unavailable":
        method += "+事件时间"
    if corrected_year:
        method += "（修正旧文件年份）"
    return {
        "sid": storm["sid"],
        "season_resolved": int(storm.get("season") or resolved_year),
        "storm_name_en": storm.get("name"),
        "link_method": method,
        "link_confidence": confidence,
        "time_match_status": status,
        "time_offset_hours": round(gap, 2) if gap is not None else None,
    }


def _table_rows(table: dict) -> list[dict[int, str]]:
    rows: dict[int, dict[int, str]] = defaultdict(dict)
    for cell in table.get("cells") or []:
        try:
            row = int(cell.get("row"))
            column = int(cell.get("column"))
        except (TypeError, ValueError):
            continue
        rows[row][column] = _clean_text(cell.get("text"))
    return [rows[index] for index in sorted(rows)]


def _column(rows: list[dict[int, str]], include: tuple[str, ...], exclude: tuple[str, ...] = ()):
    for row_index, row in enumerate(rows[:5]):
        for column, value in row.items():
            compact = value.replace("\n", "").replace(" ", "")
            if any(token in compact for token in include) and not any(token in compact for token in exclude):
                return row_index, column, compact
    return None


def _source_unit(header: str, cell: str, default: str) -> str:
    text = f"{header} {cell}".lower()
    if "m/s" in text or "米/秒" in text:
        return "m/s"
    if "hpa" in text or "百帕" in text:
        return "hPa"
    if "cm" in text or "厘米" in text:
        return "cm"
    if re.search(r"(?:^|[^a-z])m(?:$|[^a-z/])", text) or "米" in text:
        return "m"
    return default


def _unit_is_explicit(header: str, cell: str) -> bool:
    text = f"{header} {cell}".lower()
    return bool(
        "m/s" in text
        or "米/秒" in text
        or "hpa" in text
        or "百帕" in text
        or "cm" in text
        or "厘米" in text
        or re.search(r"(?:^|[^a-z])m(?:$|[^a-z/])", text)
        or "米" in text
    )


def _normalized_metric_value(metric_type: str, value: float, source_unit: str) -> tuple[float, str]:
    if metric_type in {
        "storm_surge", "highest_water_level", "astronomical_tide",
        "forecast_water_level", "threshold_blue", "threshold_yellow",
        "threshold_orange", "threshold_red", "significant_wave_height", "max_wave_height",
    }:
        return (value / 100.0, "m") if source_unit == "cm" else (value, "m")
    if metric_type in {"max_mean_wind", "max_wind"}:
        return value, "m/s"
    if metric_type == "min_pressure":
        return value, "hPa"
    return value, source_unit


def _metric_plausible(metric_type: str, value: float) -> bool:
    if metric_type in {"storm_surge"}:
        return -3 <= value <= 10
    if metric_type in {
        "highest_water_level", "astronomical_tide", "forecast_water_level",
        "threshold_blue", "threshold_yellow", "threshold_orange", "threshold_red",
    }:
        return -5 <= value <= 20
    if metric_type in {"significant_wave_height", "max_wave_height"}:
        return 0 <= value <= 30
    if metric_type in {"max_mean_wind", "max_wind"}:
        return 0 <= value <= 100
    if metric_type == "min_pressure":
        return 850 <= value <= 1100
    return True


TABLE_METRICS = (
    ("max_mean_wind", ("最大平均风速",), (), "m/s"),
    ("max_wind", ("最大风速",), ("最大平均风速",), "m/s"),
    ("min_pressure", ("最低气压",), (), "hPa"),
    ("significant_wave_height", ("有效波高",), (), "m"),
    ("max_wave_height", ("最大波高",), (), "m"),
    # 历史总结中的潮位/增水表在未标单位时沿用海洋站业务表习惯，数值按厘米解释。
    # 表头明确写有 m 或 cm 时，_source_unit 会优先采用显式单位。
    ("storm_surge", ("过程最大增水", "整点最大增水", "最大增水", "高潮位上增水"), (), "cm"),
    ("highest_water_level", ("实测最高潮位", "实况高潮位", "最高潮位", "高潮位"), ("预报", "天文", "警戒"), "cm"),
    ("astronomical_tide", ("天文高潮位",), (), "cm"),
    ("forecast_water_level", ("预报高潮位",), (), "cm"),
    ("threshold_blue", ("蓝色警戒潮位", "蓝色警戒水位"), (), "cm"),
    ("threshold_yellow", ("黄色警戒潮位", "黄色警戒水位"), (), "cm"),
    ("threshold_orange", ("橙色警戒潮位", "橙色警戒水位"), (), "cm"),
    ("threshold_red", ("红色警戒潮位", "红色警戒水位"), (), "cm"),
)


KNOWN_STATIONS = {
    "温州", "瑞安", "鳌江", "石砰", "洞头", "龙湾", "沙港头", "坎门", "石塘",
    "海门", "健跳", "石浦", "乌沙山", "北仑", "镇海", "六横", "舟山", "定海",
    "岱山", "嵊山", "乍浦", "澉浦", "小衢山", "长白", "金塘", "五龙", "大陈",
    "南麂", "朱家尖", "东极", "渔山", "三门", "头门", "徐家浦", "西门岛",
}


def _looks_like_station(value: str | None, *, permissive: bool = False) -> bool:
    compact = re.sub(r"\s+", "", _clean_text(value))
    if not compact or len(compact) > 24 or re.search(r"\d", compact):
        return False
    if any(token in compact for token in (
        "站名", "站点", "岸段", "行政区", "代表海域", "预报海域", "时间", "日期",
        "潮位", "增水", "波高", "风速", "气压", "警戒", "实测", "实况", "预报",
        "东岸", "西岸", "南岸", "北岸", "海岸", "沿岸", "本岛",
    )):
        return False
    if compact in KNOWN_STATIONS or compact.endswith(("站", "浮标", "平台")):
        return True
    return permissive and bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z·（）()、-]{1,16}", compact))


def _time_columns(rows: list[dict[int, str]]) -> list[tuple[int, int, str]]:
    found = []
    for row_index, row in enumerate(rows[:5]):
        for column, value in row.items():
            compact = value.replace("\n", "").replace(" ", "")
            if "时间" in compact or "时刻" in compact:
                found.append((row_index, column, compact))
    return found


def _metric_time_column(metric_type: str, metric_column: int, time_refs: list[tuple[int, int, str]]) -> int | None:
    if not time_refs:
        return None
    if metric_type == "highest_water_level":
        preferred = [ref for ref in time_refs if any(token in ref[2] for token in ("高潮", "潮位", "实测", "实况"))]
    elif metric_type == "storm_surge":
        preferred = [ref for ref in time_refs if "增水" in ref[2] or "出现" in ref[2]]
    elif metric_type in {"significant_wave_height", "max_wave_height"}:
        preferred = [ref for ref in time_refs if "波" in ref[2] or "出现" in ref[2]]
    elif metric_type in {"max_mean_wind", "max_wind"}:
        preferred = [ref for ref in time_refs if "风" in ref[2] or "出现" in ref[2]]
    else:
        preferred = []
    candidates = preferred or time_refs
    # 同一指标右侧紧邻的时间列最常见；没有时再取距离最近者。
    ordered = sorted(candidates, key=lambda ref: (ref[1] < metric_column, abs(ref[1] - metric_column)))
    return ordered[0][1]


def _parse_table_metrics(tables: list[dict]) -> tuple[list[dict], list[dict]]:
    metrics: list[dict] = []
    issues: list[dict] = []
    for table in tables:
        rows = _table_rows(table)
        if not rows:
            continue
        station_ref = _column(rows, ("站名", "站点", "预报站点"))
        # 没有明确站点列的表通常是路径、预报产品或统计表，不能生成站点实测指标。
        if not station_ref:
            continue
        metric_refs = []
        for metric_type, includes, excludes, default_unit in TABLE_METRICS:
            found = _column(rows, includes, excludes)
            if found:
                metric_refs.append((metric_type, found, default_unit, found[0] == station_ref[0]))
        first_data_index = len(rows)
        for row_index in range(station_ref[0] + 1, len(rows)):
            row = rows[row_index]
            if any(
                _looks_like_station(row.get(column))
                for column in (
                    station_ref[1], station_ref[1] + 1, station_ref[1] + 2,
                    station_ref[1] + 3, station_ref[1] - 1,
                )
            ):
                first_data_index = row_index
                break
        header_rows = rows[:first_data_index]
        table_header_text = " ".join(value for row in header_rows for value in row.values())
        color_headers: dict[str, tuple[int, int, str]] = {}
        if "警戒潮位" in table_header_text or "警戒水位" in table_header_text:
            for row_index, row in enumerate(header_rows):
                for column, value in row.items():
                    compact = value.replace("\n", "").replace(" ", "")
                    for color, metric_type in (
                        ("蓝色", "threshold_blue"),
                        ("黄色", "threshold_yellow"),
                        ("橙色", "threshold_orange"),
                        ("红色", "threshold_red"),
                    ):
                        if compact == color:
                            color_headers[metric_type] = (row_index, column, f"{color}警戒潮位(cm)")
            present_types = {item[0] for item in metric_refs}
            for metric_type, found in color_headers.items():
                if metric_type not in present_types:
                    # 二级表头单元格已与数据列对齐，不再套用主表头的列偏移。
                    metric_refs.append((metric_type, found, "cm", False))
        if not metric_refs:
            continue
        header_end = max(ref[1][0] for ref in metric_refs)
        header_end = max(header_end, station_ref[0])
        station_column = station_ref[1]
        warning_ref = _column(
            rows,
            ("实测最高潮位的警戒级别", "达到级别", "警戒级别", "预警级别"),
            ("预报最高潮位",),
        )
        warning_column = warning_ref[1] if warning_ref else None
        warning_uses_shift = True
        if color_headers:
            warning_column = max(ref[1] for ref in color_headers.values()) + 1
            warning_uses_shift = False
        time_refs = _time_columns(rows)
        first_header_text = " ".join(rows[station_ref[0]].values())
        default_station_shift = 1 if "行政区" in first_header_text else 0
        last_station = None
        last_shift = default_station_shift
        for source_row, row in enumerate(rows, start=1):
            if source_row <= header_end + 1:
                continue
            station = None
            actual_station_column = None
            direct_station = _clean_text(row.get(station_column))
            preferred_station_column = station_column + default_station_shift
            preferred_station = _clean_text(row.get(preferred_station_column))
            if _looks_like_station(preferred_station, permissive=True):
                station = preferred_station
                actual_station_column = preferred_station_column
            # 先找明确带“站/浮标”或位于站名词典中的值，避免把合并表里的“某某岸段”误作站名。
            if not station:
                for candidate_column in (station_column, station_column + 1, station_column + 2, station_column + 3, station_column - 1):
                    candidate = _clean_text(row.get(candidate_column))
                    if _looks_like_station(candidate):
                        station = candidate
                        actual_station_column = candidate_column
                        break
            if not station and _looks_like_station(direct_station, permissive=True):
                station = direct_station
                actual_station_column = station_column
            if station:
                station = re.sub(r"\s+", "", station)
                last_station = station
                last_shift = int(actual_station_column - station_column)
            else:
                station = last_station
            if not station:
                continue
            shift = last_shift
            actual_warning_column = warning_column + shift if warning_uses_shift and warning_column else warning_column
            warning_level = _warning(row.get(actual_warning_column)) if actual_warning_column else None
            for metric_type, (_, metric_column, header), default_unit, metric_uses_shift in metric_refs:
                actual_metric_column = metric_column + shift if metric_uses_shift else metric_column
                cell_text = _clean_text(row.get(actual_metric_column))
                value = _first_number(cell_text)
                if value is None:
                    continue
                source_unit = _source_unit(header, cell_text, default_unit)
                normalized, unit_si = _normalized_metric_value(metric_type, value, source_unit)
                unit_inferred = not _unit_is_explicit(header, cell_text)
                normalization_rule = (
                    "cm_to_m_divide_100"
                    if source_unit == "cm" and unit_si == "m"
                    else "identity"
                )
                qc_status = "pass" if _metric_plausible(metric_type, normalized) else "fail"
                observed_time = None
                embedded_time = re.search(r"(20\d{2}-\d{1,2}-\d{1,2}[^）)]*)", cell_text)
                if embedded_time:
                    observed_time = embedded_time.group(1).strip()
                else:
                    logical_time_column = _metric_time_column(metric_type, metric_column, time_refs)
                    if logical_time_column is not None:
                        observed_time = _clean_text(row.get(logical_time_column + shift)) or None
                if qc_status == "fail":
                    issues.append({
                        "severity": "error",
                        "issue_code": "METRIC_OUT_OF_RANGE",
                        "field_name": metric_type,
                        "message": f"{station or '未知站点'}的{metric_type}归一化值{normalized} {unit_si}超出物理范围。",
                        "source_excerpt": cell_text,
                    })
                metrics.append({
                    "station_name": station,
                    "metric_type": metric_type,
                    "value_si": round(normalized, 6),
                    "unit_si": unit_si,
                    "source_value": value,
                    "source_unit": source_unit,
                    "unit_inferred": unit_inferred,
                    "normalization_rule": normalization_rule,
                    "observed_time_text": observed_time,
                    "warning_level": warning_level,
                    "source_table_index": int(table.get("index") or 0),
                    "source_row": source_row,
                    "source_excerpt": cell_text[:500],
                    "qc_status": qc_status,
                })
    return metrics, issues


def _narrative_metric(
    *,
    station: str | None,
    metric_type: str,
    value: float,
    source_unit: str,
    excerpt: str,
) -> dict:
    normalized, unit_si = _normalized_metric_value(metric_type, value, source_unit)
    return {
        "station_name": station,
        "metric_type": metric_type,
        "value_si": round(normalized, 6),
        "unit_si": unit_si,
        "source_value": value,
        "source_unit": source_unit,
        "unit_inferred": False,
        "normalization_rule": "cm_to_m_divide_100" if source_unit == "cm" and unit_si == "m" else "identity",
        "observed_time_text": None,
        "warning_level": None,
        "source_table_index": None,
        "source_row": None,
        "source_excerpt": excerpt[:500],
        "qc_status": "pass",
    }


def _clean_narrative_station(value: str | None) -> str | None:
    station = re.sub(r"\s+", "", str(value or "")).strip("：:，,（）()")
    if not station:
        return None
    for token in ("台风过程最大增水", "过程最大增水", "最大增水", "增水最大"):
        if token in station:
            station = station.rsplit(token, 1)[-1]
    if re.search(r"\d", station) or "见图" in station:
        return None
    for known in sorted(KNOWN_STATIONS, key=len, reverse=True):
        if station.endswith(known):
            station = known
            break
    return station or None


def _narrative_surge_metrics(text: str) -> list[dict]:
    metrics = []
    for paragraph in re.split(r"[。；;\n]", text):
        if not any(token in paragraph for token in ("最大增水", "增水最大", "风暴增水", "高潮位上增水")):
            continue
        for match in re.finditer(
            r"(?P<station>[\u4e00-\u9fff]{1,10})站\s*(?:最大增水)?(?:为|达|约)?\s*"
            r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>厘米|cm|米|m)",
            paragraph,
            re.IGNORECASE,
        ):
            value = float(match.group("value"))
            unit = "cm" if match.group("unit").lower() in {"cm", "厘米"} else "m"
            normalized = value / 100.0 if unit == "cm" else value
            if not _metric_plausible("storm_surge", normalized):
                continue
            station = _clean_narrative_station(match.group("station"))
            metrics.append(_narrative_metric(
                station=f"{station}站" if station and not station.endswith("站") else station,
                metric_type="storm_surge", value=value, source_unit=unit, excerpt=paragraph,
            ))
        match = re.search(
            r"增水最大(?:达|为)?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>厘米|cm|米|m)"
            r"(?:（(?P<station>[^）]{1,20})）)?",
            paragraph,
            re.IGNORECASE,
        )
        if match:
            value = float(match.group("value"))
            unit = "cm" if match.group("unit").lower() in {"cm", "厘米"} else "m"
            normalized = value / 100.0 if unit == "cm" else value
            if _metric_plausible("storm_surge", normalized):
                metrics.append(_narrative_metric(
                    station=_clean_narrative_station(match.group("station")),
                    metric_type="storm_surge", value=value, source_unit=unit, excerpt=paragraph,
                ))
        range_match = re.search(
            r"(?P<low>\d+(?:\.\d+)?)\s*(?P<unit1>厘米|cm|米|m)\s*(?:至|到|～|~|—|－|-)\s*"
            r"(?P<high>\d+(?:\.\d+)?)\s*(?P<unit2>厘米|cm|米|m)[^。；]{0,15}(?:风暴)?增水",
            paragraph,
            re.IGNORECASE,
        )
        if range_match:
            value = max(float(range_match.group("low")), float(range_match.group("high")))
            unit = "cm" if range_match.group("unit2").lower() in {"cm", "厘米"} else "m"
            metrics.append(_narrative_metric(
                station=None, metric_type="storm_surge", value=value, source_unit=unit, excerpt=paragraph,
            ))
    return metrics


def _narrative_wave_metrics(text: str) -> list[dict]:
    metrics = []
    for paragraph in re.split(r"[。；;\n]", text):
        if "波高" not in paragraph or not any(token in paragraph for token in ("实测", "过程最大", "达到")):
            continue
        range_match = re.search(
            r"有效波高\s*(?P<low>\d+(?:\.\d+)?)\s*(?:米|m)\s*(?:至|到|～|~|—|－|-)\s*"
            r"(?P<high>\d+(?:\.\d+)?)\s*(?:米|m)",
            paragraph,
            re.IGNORECASE,
        )
        if range_match:
            metrics.append(_narrative_metric(
                station=None,
                metric_type="significant_wave_height",
                value=max(float(range_match.group("low")), float(range_match.group("high"))),
                source_unit="m",
                excerpt=paragraph,
            ))
        max_match = re.search(
            r"(?:最大|最高)(?:有效)?波高(?:为|达|约)?\s*(?P<value>\d+(?:\.\d+)?)\s*(?:米|m)"
            r"(?:[（(](?P<station>[^）)]{1,30})[）)])?",
            paragraph,
            re.IGNORECASE,
        )
        if max_match:
            metrics.append(_narrative_metric(
                station=_clean_narrative_station(max_match.group("station")),
                metric_type="significant_wave_height",
                value=float(max_match.group("value")),
                source_unit="m",
                excerpt=paragraph,
            ))
    return metrics


def _narrative_water_metrics(text: str) -> list[dict]:
    metrics = []
    for paragraph in re.split(r"[。；;\n]", text):
        if "最高潮位" not in paragraph or not any(token in paragraph for token in ("实测", "出现", "达到", "达")):
            continue
        for match in re.finditer(
            r"(?:实测)?最高潮位(?:为|达|约)?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>厘米|cm|米|m)"
            r"(?:[（(](?P<station>[^）)]{1,30})[）)])?",
            paragraph,
            re.IGNORECASE,
        ):
            unit = "cm" if match.group("unit").lower() in {"cm", "厘米"} else "m"
            value = float(match.group("value"))
            normalized, _ = _normalized_metric_value("highest_water_level", value, unit)
            if _metric_plausible("highest_water_level", normalized):
                metrics.append(_narrative_metric(
                    station=_clean_narrative_station(match.group("station")),
                    metric_type="highest_water_level",
                    value=value,
                    source_unit=unit,
                    excerpt=paragraph,
                ))
    return metrics


def _actual_warning(text: str, hazard: str) -> str | None:
    matches = []
    for sentence in re.split(r"[。；;\n]", text):
        if hazard not in sentence or not any(token in sentence for token in ("达到", "实况", "实测", "最高")):
            continue
        if "预计" in sentence or ("预报" in sentence and "实况" not in sentence and "实测" not in sentence):
            continue
        level = _warning(sentence)
        if level:
            matches.append(level)
    return _highest_warning(matches)


def _peak(metrics: list[dict], metric_type: str) -> tuple[float | None, str | None, str | None]:
    usable = [item for item in metrics if item["metric_type"] == metric_type and item["qc_status"] == "pass"]
    if not usable:
        return None, None, None
    maximum = max(item["value_si"] for item in usable)
    peaks = [item for item in usable if math.isclose(item["value_si"], maximum, abs_tol=1e-9)]
    stations = sorted({str(item["station_name"]).strip() for item in peaks if item.get("station_name")})
    times = sorted({str(item["observed_time_text"]).strip() for item in peaks if item.get("observed_time_text")})
    return maximum, "、".join(stations) or None, "、".join(times) or None


def _body_number(text: str, patterns: tuple[str, ...], minimum: float, maximum: float) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = float(match.group("value"))
            if minimum <= value <= maximum:
                return value
    return None


def _direct_loss(text: str) -> float | None:
    values = []
    for match in re.finditer(
        r"直接经济损失(?:达|为|约)?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>亿元|万元|元)",
        text,
    ):
        value = float(match.group("value"))
        multiplier = {"亿元": 100_000_000, "万元": 10_000, "元": 1}[match.group("unit")]
        values.append(value * multiplier)
    return max(values) if values else None


def _integer_fact(text: str, patterns: tuple[str, ...]) -> int | None:
    values = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = float(match.group("value"))
            unit = match.groupdict().get("unit")
            if unit == "万":
                value *= 10000
            values.append(int(round(value)))
    return max(values) if values else None


def _accuracy(text: str, hazard: str) -> float | None:
    patterns = (
        rf"{hazard}[^。；\n]{{0,30}}准确率(?:为|达到)?\s*(?P<value>\d+(?:\.\d+)?)%",
        rf"准确率(?:为|达到)?\s*(?P<value>\d+(?:\.\d+)?)%[^。；\n]{{0,20}}{hazard}",
    )
    return _body_number(text, patterns, 0, 100)


def _classify_section(heading: str) -> str:
    compact = heading.replace(" ", "")
    for section_type, tokens in SECTION_TYPES:
        if any(token in compact for token in tokens):
            return section_type
    return "other"


def _sections(text: str) -> list[dict]:
    lines = [line.strip() for line in text.splitlines()]
    heading_pattern = re.compile(r"^(?:[一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[）)]|\d+[、.])")
    sections = []
    heading = "文档开头"
    buffer: list[str] = []
    for line in lines:
        if not line:
            continue
        looks_like_heading = bool(heading_pattern.match(line)) and len(line) <= 80
        if looks_like_heading and buffer:
            section_text = "\n".join(buffer).strip()
            if section_text:
                sections.append({"heading": heading, "section_type": _classify_section(heading), "text": section_text})
            heading = line
            buffer = []
        else:
            if looks_like_heading:
                heading = line
            buffer.append(line)
    if buffer:
        section_text = "\n".join(buffer).strip()
        if section_text:
            sections.append({"heading": heading, "section_type": _classify_section(heading), "text": section_text})
    if not sections and text:
        sections.append({"heading": "全文", "section_type": "other", "text": text})
    return sections


def _section_excerpt(sections: list[dict], section_type: str, maximum: int = 1200) -> str | None:
    text = "\n".join(item["text"] for item in sections if item["section_type"] == section_type).strip()
    return text[:maximum] if text else None


def _landfall(text: str) -> tuple[str | None, str | None]:
    for sentence in re.split(r"[。；;\n]", text[:3500]):
        if "登陆" not in sentence:
            continue
        time_match = re.search(r"\d{1,2}月\d{1,2}日[^，,]{0,12}(?:时|点)(?:\d{1,2}分)?", sentence)
        location_match = re.search(r"在([^，,。；;]{2,30}?)(?:沿海)?登陆", sentence)
        return (
            time_match.group(0) if time_match else None,
            location_match.group(1).strip() if location_match else None,
        )
    return None, None


def _impact_level(surge_warning: str | None) -> tuple[str | None, str | None]:
    if surge_warning in {"红色", "橙色"}:
        return "严重", f"报告实况达到{surge_warning}风暴潮警戒/预警级别"
    if surge_warning == "黄色":
        return "一般", "报告实况达到黄色风暴潮警戒/预警级别"
    if surge_warning == "蓝色":
        return "轻微", "报告实况达到蓝色风暴潮警戒/预警级别"
    return None, None


def _qc_status(issues: list[dict]) -> str:
    severities = {item["severity"] for item in issues}
    if "error" in severities:
        return "fail"
    if "warning" in severities:
        return "review"
    return "pass"


def _prepare_reports(records: list[dict], storms: list[dict], source_archive: Path) -> list[dict]:
    prepared = []
    max_storm_year = max((int(storm.get("season") or 0) for storm in storms), default=0)
    imported_at = datetime.now(timezone.utc).isoformat()
    for record in records:
        text = _clean_text(record.get("text"))
        source_file = str(record.get("source_file") or "").strip()
        source_sha = str(record.get("source_sha256") or "").strip().lower()
        if not source_sha:
            source_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        report_id = "TR-" + source_sha[:24]
        issues: list[dict] = []
        event_start, event_end, time_issues = _parse_event_window(text)
        issues.extend(time_issues)
        metrics, metric_issues = _parse_table_metrics(record.get("tables") or [])
        issues.extend(metric_issues)
        if not any(item["metric_type"] == "storm_surge" for item in metrics):
            metrics.extend(_narrative_surge_metrics(text))
        if not any(item["metric_type"] == "highest_water_level" for item in metrics):
            metrics.extend(_narrative_water_metrics(text))
        if not any(item["metric_type"] == "significant_wave_height" for item in metrics):
            metrics.extend(_narrative_wave_metrics(text))
        cases = _identity_cases(record)
        if not cases:
            issues.append({
                "severity": "error",
                "issue_code": "STORM_CODE_MISSING",
                "field_name": "storm_code",
                "message": "无法从文件名或正文识别台风年编号。",
                "source_excerpt": source_file,
            })
        sections = _sections(text)
        landfall_time, landfall_location = _landfall(text)
        max_wind_ms = _body_number(
            text,
            (
                r"近中心(?:的)?最大(?:风速|风力)[^\d]{0,12}(?P<value>\d+(?:\.\d+)?)\s*(?:米/秒|m/s)",
                r"最大风速[^\d]{0,20}(?P<value>\d+(?:\.\d+)?)\s*(?:米/秒|m/s)",
            ),
            0,
            100,
        )
        min_pressure = _body_number(
            text,
            (
                r"(?:中心最低气压|中心气压)(?:为|达|只有)?[^\d]{0,8}(?P<value>\d{3,4}(?:\.\d+)?)\s*(?:百帕|hpa)",
            ),
            850,
            1100,
        )
        surge_warning = _actual_warning(text, "风暴潮")
        wave_warning = _actual_warning(text, "海浪")
        metric_warnings = [item.get("warning_level") for item in metrics if item["metric_type"] in {"storm_surge", "highest_water_level"}]
        surge_warning = _highest_warning([surge_warning, *metric_warnings])
        impact_level, impact_basis = _impact_level(surge_warning)
        direct_loss = _direct_loss(text)
        deaths = _integer_fact(text, (r"(?:因灾)?死亡\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>万)?人",))
        affected = _integer_fact(text, (r"(?P<value>\d+(?:\.\d+)?)(?P<unit>万)?人受灾", r"受灾人口(?:为|达)?\s*(?P<value>\d+(?:\.\d+)?)(?P<unit>万)?人"))
        peaks = {
            "surge": _peak(metrics, "storm_surge"),
            "water": _peak(metrics, "highest_water_level"),
            "significant_wave": _peak(metrics, "significant_wave_height"),
            "max_wave": _peak(metrics, "max_wave_height"),
        }
        prepared_cases = []
        for identity in cases:
            link = _link_case(identity, storms, event_start, event_end)
            case = {**identity, **link}
            case_id = "TC-" + hashlib.sha256(f"{report_id}:{identity['storm_code']}".encode()).hexdigest()[:24]
            case["case_id"] = case_id
            case_issues: list[dict] = []
            if not case.get("storm_name_cn"):
                case_issues.append({
                    "severity": "warning",
                    "issue_code": "CHINESE_NAME_MISSING",
                    "field_name": "storm_name_cn",
                    "message": "未识别出台风中文名。",
                    "source_excerpt": source_file,
                })
            if not case.get("sid"):
                code = "TRACK_DATA_NOT_AVAILABLE" if case["season_resolved"] > max_storm_year else "STORM_LINK_FAILED"
                message = (
                    f"路径库当前只到{max_storm_year}年，该报告保留为待补路径。"
                    if code == "TRACK_DATA_NOT_AVAILABLE"
                    else "报告未通过同年同名及时间校验，未关联SID。"
                )
                case_issues.append({
                    "severity": "warning",
                    "issue_code": code,
                    "field_name": "sid",
                    "message": message,
                    "source_excerpt": case.get("link_method"),
                })
            if identity.get("identity_overridden"):
                case_issues.append({
                    "severity": "warning",
                    "issue_code": "LEGACY_FILENAME_IDENTITY_CORRECTED",
                    "field_name": "season_resolved",
                    "message": "旧文件编号年份与正文中英文名称不一致，已按正文名称和IBTrACS记录修正。",
                    "source_excerpt": source_file,
                })
            if case.get("time_match_status") == "conflict":
                case_issues.append({
                    "severity": "error",
                    "issue_code": "STORM_TIME_CONFLICT",
                    "field_name": "event_window",
                    "message": "同名台风生命期与报告事件窗口冲突，SID关联已拒绝。",
                    "source_excerpt": case.get("link_method"),
                })
            case.update({
                "event_start": event_start,
                "event_end": event_end,
                "landfall_time_text": landfall_time,
                "landfall_location": landfall_location,
                "max_wind_ms": max_wind_ms,
                "max_wind_kt": round(max_wind_ms * 1.943844, 3) if max_wind_ms is not None else None,
                "min_pressure_hpa": min_pressure,
                "max_surge_m": peaks["surge"][0],
                "max_surge_station": peaks["surge"][1],
                "max_surge_time_text": peaks["surge"][2],
                "max_water_level_m": peaks["water"][0],
                "max_water_level_station": peaks["water"][1],
                "max_water_level_time_text": peaks["water"][2],
                "max_significant_wave_m": peaks["significant_wave"][0],
                "max_significant_wave_station": peaks["significant_wave"][1],
                "max_wave_height_m": peaks["max_wave"][0],
                "max_wave_height_station": peaks["max_wave"][1],
                "storm_surge_warning_level": surge_warning,
                "wave_warning_level": wave_warning,
                "direct_economic_loss_cny": direct_loss,
                "deaths": deaths,
                "affected_population": affected,
                "forecast_accuracy_surge_pct": _accuracy(text, "风暴潮"),
                "forecast_accuracy_wave_pct": _accuracy(text, "海浪"),
                "historical_impact_level": impact_level,
                "impact_level_basis": impact_basis,
                "disaster_summary": _section_excerpt(sections, "disaster"),
                "forecast_summary": _section_excerpt(sections, "forecast_verification"),
                "response_summary": _section_excerpt(sections, "emergency_response"),
                "metrics": [dict(item) for item in metrics],
                "issues": case_issues,
                "qc_status": _qc_status(case_issues + metric_issues),
            })
            prepared_cases.append(case)
        all_issues = issues + [issue for case in prepared_cases for issue in case["issues"]]
        prepared.append({
            "report_id": report_id,
            "source_archive": str(source_archive),
            "source_file": source_file,
            "source_sha256": source_sha,
            "file_type": str(record.get("file_type") or Path(source_file).suffix.lstrip(".")).lower(),
            "title": str(record.get("title") or "").strip() or None,
            "page_count": int(record.get("page_count") or 0),
            "table_count": int(record.get("table_count") or 0),
            "figure_count": int(record.get("figure_count") or 0),
            "extraction_method": str(record.get("extraction_method") or "unknown"),
            "raw_text": text,
            "imported_at": imported_at,
            "qc_status": _qc_status(all_issues),
            "issues": all_issues,
            "sections": sections,
            "cases": prepared_cases,
        })
    return prepared


def _read_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"抽取JSONL第{line_number}行无效") from exc
            if not isinstance(item, dict):
                raise ValueError(f"抽取JSONL第{line_number}行不是对象")
            records.append(item)
    return records


def _extract(source: Path, output_jsonl: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("DOC/WPS抽取需要Windows及Microsoft Word；可在Windows预处理后传入--extracted-jsonl")
    if not EXTRACTION_SCRIPT.exists():
        raise FileNotFoundError(f"文档抽取脚本不存在: {EXTRACTION_SCRIPT}")
    command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(EXTRACTION_SCRIPT), "-Source", str(source), "-OutputJsonl", str(output_jsonl),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "未知错误").strip()
        raise RuntimeError(f"Word/WPS文档抽取失败: {detail}")


def _delete_existing_source(
    connection: sqlite3.Connection,
    source_archive: str,
    incoming_report_ids: list[str] | None = None,
) -> None:
    incoming_report_ids = list(dict.fromkeys(incoming_report_ids or []))
    conditions = ["source_archive = ?"]
    params: list[object] = [source_archive]
    if incoming_report_ids:
        conditions.append("report_id IN ({})".format(",".join("?" for _ in incoming_report_ids)))
        params.extend(incoming_report_ids)
    report_ids = [row[0] for row in connection.execute(
        f"SELECT report_id FROM typhoon_reports WHERE {' OR '.join(conditions)}", params
    )]
    if not report_ids:
        return
    placeholders = ",".join("?" for _ in report_ids)
    case_ids = [row[0] for row in connection.execute(
        f"SELECT case_id FROM typhoon_report_cases WHERE report_id IN ({placeholders})", report_ids
    )]
    if case_ids:
        case_placeholders = ",".join("?" for _ in case_ids)
        connection.execute(f"DELETE FROM typhoon_report_station_metrics WHERE case_id IN ({case_placeholders})", case_ids)
        connection.execute(f"DELETE FROM typhoon_report_qc_issues WHERE case_id IN ({case_placeholders})", case_ids)
    connection.execute(f"DELETE FROM typhoon_report_qc_issues WHERE report_id IN ({placeholders})", report_ids)
    connection.execute(f"DELETE FROM typhoon_report_sections WHERE report_id IN ({placeholders})", report_ids)
    connection.execute(f"DELETE FROM typhoon_report_cases WHERE report_id IN ({placeholders})", report_ids)
    connection.execute(f"DELETE FROM typhoon_reports WHERE report_id IN ({placeholders})", report_ids)


def _write_database(database_path: Path, reports: list[dict], source_archive: Path) -> None:
    temp_handle = tempfile.NamedTemporaryFile(
        prefix="typhoon_with_reports_", suffix=".sqlite3", dir=database_path.parent, delete=False
    )
    temp_path = Path(temp_handle.name)
    temp_handle.close()
    shutil.copy2(database_path, temp_path)
    connection = None
    try:
        connection = sqlite3.connect(temp_path)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(REPORT_SCHEMA)
        metric_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(typhoon_report_station_metrics)")
        }
        if "unit_inferred" not in metric_columns:
            connection.execute(
                "ALTER TABLE typhoon_report_station_metrics ADD COLUMN unit_inferred INTEGER NOT NULL DEFAULT 0"
            )
        if "normalization_rule" not in metric_columns:
            connection.execute(
                "ALTER TABLE typhoon_report_station_metrics ADD COLUMN normalization_rule TEXT NOT NULL DEFAULT 'identity'"
            )
        _delete_existing_source(
            connection,
            str(source_archive),
            [report["report_id"] for report in reports],
        )
        for report in reports:
            connection.execute(
                """
                INSERT INTO typhoon_reports (
                    report_id, source_archive, source_file, source_sha256, file_type,
                    title, page_count, table_count, figure_count, extraction_method,
                    raw_text, imported_at, qc_status, qc_issue_count
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    report["report_id"], report["source_archive"], report["source_file"],
                    report["source_sha256"], report["file_type"], report["title"],
                    report["page_count"], report["table_count"], report["figure_count"],
                    report["extraction_method"], report["raw_text"], report["imported_at"],
                    report["qc_status"], len(report["issues"]),
                ),
            )
            for order, section in enumerate(report["sections"], start=1):
                connection.execute(
                    "INSERT INTO typhoon_report_sections(report_id, section_order, section_type, heading, section_text) VALUES (?,?,?,?,?)",
                    (report["report_id"], order, section["section_type"], section["heading"], section["text"]),
                )
            for case in report["cases"]:
                connection.execute(
                    """
                    INSERT INTO typhoon_report_cases (
                        case_id, report_id, storm_code, season_reported, season_resolved,
                        storm_number, storm_name_cn, storm_name_en, sid, link_method,
                        link_confidence, time_match_status, time_offset_hours, event_start,
                        event_end, landfall_time_text, landfall_location, max_wind_ms,
                        max_wind_kt, min_pressure_hpa, max_surge_m, max_surge_station,
                        max_surge_time_text, max_water_level_m, max_water_level_station,
                        max_water_level_time_text, max_significant_wave_m,
                        max_significant_wave_station, max_wave_height_m,
                        max_wave_height_station, storm_surge_warning_level,
                        wave_warning_level, direct_economic_loss_cny, deaths,
                        affected_population, forecast_accuracy_surge_pct,
                        forecast_accuracy_wave_pct, historical_impact_level,
                        impact_level_basis, disaster_summary, forecast_summary,
                        response_summary, qc_status
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        case["case_id"], report["report_id"], case["storm_code"],
                        case["season_reported"], case["season_resolved"], case["storm_number"],
                        case.get("storm_name_cn"), case.get("storm_name_en"), case.get("sid"),
                        case["link_method"], case["link_confidence"], case["time_match_status"],
                        case.get("time_offset_hours"), _iso(case.get("event_start")),
                        _iso(case.get("event_end")), case.get("landfall_time_text"),
                        case.get("landfall_location"), case.get("max_wind_ms"),
                        case.get("max_wind_kt"), case.get("min_pressure_hpa"),
                        case.get("max_surge_m"), case.get("max_surge_station"),
                        case.get("max_surge_time_text"), case.get("max_water_level_m"),
                        case.get("max_water_level_station"), case.get("max_water_level_time_text"),
                        case.get("max_significant_wave_m"), case.get("max_significant_wave_station"),
                        case.get("max_wave_height_m"), case.get("max_wave_height_station"),
                        case.get("storm_surge_warning_level"), case.get("wave_warning_level"),
                        case.get("direct_economic_loss_cny"), case.get("deaths"),
                        case.get("affected_population"), case.get("forecast_accuracy_surge_pct"),
                        case.get("forecast_accuracy_wave_pct"), case.get("historical_impact_level"),
                        case.get("impact_level_basis"), case.get("disaster_summary"),
                        case.get("forecast_summary"), case.get("response_summary"),
                        case["qc_status"],
                    ),
                )
                for metric in case["metrics"]:
                    connection.execute(
                        """
                        INSERT INTO typhoon_report_station_metrics (
                            case_id, station_name, metric_type, value_si, unit_si,
                            source_value, source_unit, unit_inferred, normalization_rule,
                            observed_time_text, warning_level,
                            source_table_index, source_row, source_excerpt, qc_status
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            case["case_id"], metric.get("station_name"), metric["metric_type"],
                            metric["value_si"], metric["unit_si"], metric["source_value"],
                            metric.get("source_unit"), int(bool(metric.get("unit_inferred"))),
                            metric.get("normalization_rule") or "identity", metric.get("observed_time_text"),
                            metric.get("warning_level"), metric.get("source_table_index"),
                            metric.get("source_row"), metric.get("source_excerpt"), metric["qc_status"],
                        ),
                    )
                for issue in case["issues"]:
                    connection.execute(
                        "INSERT INTO typhoon_report_qc_issues(report_id, case_id, severity, issue_code, field_name, message, source_excerpt) VALUES (?,?,?,?,?,?,?)",
                        (report["report_id"], case["case_id"], issue["severity"], issue["issue_code"], issue.get("field_name"), issue["message"], issue.get("source_excerpt")),
                    )
            report_only_issues = [issue for issue in report["issues"] if issue not in [nested for case in report["cases"] for nested in case["issues"]]]
            for issue in report_only_issues:
                connection.execute(
                    "INSERT INTO typhoon_report_qc_issues(report_id, case_id, severity, issue_code, field_name, message, source_excerpt) VALUES (?,?,?,?,?,?,?)",
                    (report["report_id"], None, issue["severity"], issue["issue_code"], issue.get("field_name"), issue["message"], issue.get("source_excerpt")),
                )
        cases = [case for report in reports for case in report["cases"]]
        metrics = [metric for case in cases for metric in case["metrics"]]
        metadata = {
            "typhoon_report_source": str(source_archive),
            "typhoon_report_imported_at": datetime.now(timezone.utc).isoformat(),
            "typhoon_report_report_count": len(reports),
            "typhoon_report_case_count": len(cases),
            "typhoon_report_linked_case_count": sum(bool(case.get("sid")) for case in cases),
            "typhoon_report_pending_track_count": sum(not case.get("sid") for case in cases),
            "typhoon_report_metric_count": len(metrics),
            "typhoon_report_unit_inferred_count": sum(bool(metric.get("unit_inferred")) for metric in metrics),
            "typhoon_report_qc_issue_count": sum(len(report["issues"]) for report in reports),
            "typhoon_report_qc_pass_count": sum(report["qc_status"] == "pass" for report in reports),
            "typhoon_report_qc_review_count": sum(report["qc_status"] == "review" for report in reports),
            "typhoon_report_qc_fail_count": sum(report["qc_status"] == "fail" for report in reports),
            "typhoon_report_schema_version": 2,
        }
        connection.executemany(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()],
        )
        connection.commit()
        connection.execute("ANALYZE")
        connection.close()
        connection = None
        try:
            os.replace(temp_path, database_path)
        except PermissionError:
            if os.name != "nt":
                raise
            # Windows 不允许替换被其他只读进程打开的 SQLite 文件。此时保留已生成的
            # 完整临时库，使用 SQLite 在线备份 API 在单一事务内覆盖目标库。
            with closing(sqlite3.connect(temp_path)) as source_connection, closing(sqlite3.connect(database_path)) as target_connection:
                source_connection.backup(target_connection)
            temp_path.unlink()
    except Exception:
        if connection is not None:
            connection.close()
        if temp_path.exists():
            temp_path.unlink()
        raise


def import_typhoon_summaries(
    source: str | Path = DEFAULT_SUMMARY_ARCHIVE,
    database_path: str | Path = DEFAULT_DATABASE,
    *,
    extracted_jsonl: str | Path | None = None,
    dry_run: bool = False,
    backup: bool = True,
) -> dict:
    source = Path(source).resolve()
    database_path = Path(database_path).resolve()
    if not source.exists():
        raise FileNotFoundError(f"台风总结数据不存在: {source}")
    if not database_path.exists():
        raise FileNotFoundError(f"台风知识库不存在: {database_path}")

    temporary_directory = None
    if extracted_jsonl:
        jsonl_path = Path(extracted_jsonl).resolve()
    else:
        temporary_directory = tempfile.TemporaryDirectory(prefix="typhoon_report_import_")
        jsonl_path = Path(temporary_directory.name) / "documents.jsonl"
        _extract(source, jsonl_path)
    try:
        records = _read_jsonl(jsonl_path)
        with sqlite3.connect(database_path) as connection:
            storms = _load_storms(connection)
        reports = _prepare_reports(records, storms, source)
        cases = [case for report in reports for case in report["cases"]]
        metrics = [metric for case in cases for metric in case["metrics"]]
        backup_path = None
        if not dry_run:
            if backup:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = database_path.with_name(
                    f"{database_path.stem}.before_typhoon_reports_{timestamp}{database_path.suffix}"
                )
                shutil.copy2(database_path, backup_path)
            _write_database(database_path, reports, source)
        return {
            "source": str(source),
            "database": str(database_path),
            "extracted_document_count": len(records),
            "report_count": len(reports),
            "case_count": len(cases),
            "linked_case_count": sum(bool(case.get("sid")) for case in cases),
            "high_confidence_links": sum(case.get("link_confidence") == "high" for case in cases),
            "medium_confidence_links": sum(case.get("link_confidence") == "medium" for case in cases),
            "low_confidence_links": sum(case.get("link_confidence") == "low" for case in cases),
            "pending_track_count": sum(not case.get("sid") for case in cases),
            "time_conflict_count": sum(case.get("time_match_status") == "conflict" for case in cases),
            "metric_count": len(metrics),
            "normalized_metric_counts": dict(sorted(
                (metric_type, sum(metric["metric_type"] == metric_type for metric in metrics))
                for metric_type in {metric["metric_type"] for metric in metrics}
            )),
            "qc": {
                "pass": sum(report["qc_status"] == "pass" for report in reports),
                "review": sum(report["qc_status"] == "review" for report in reports),
                "fail": sum(report["qc_status"] == "fail" for report in reports),
            },
            "unlinked_cases": [
                {"storm_code": case["storm_code"], "name_cn": case.get("storm_name_cn"), "reason": case["link_method"]}
                for case in cases if not case.get("sid")
            ],
            "backup": str(backup_path) if backup_path else None,
            "dry_run": dry_run,
        }
    finally:
        if temporary_directory is not None:
            temporary_directory.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description="解析、校验并导入浙江台风总结Word/WPS报告")
    parser.add_argument("--source", default=str(DEFAULT_SUMMARY_ARCHIVE), help="总结ZIP或已解压目录")
    parser.add_argument("--database", default=str(DEFAULT_DATABASE), help="SQLite知识库路径")
    parser.add_argument("--extracted-jsonl", help="已通过PowerShell抽取的JSONL；提供后不再启动Word")
    parser.add_argument("--dry-run", action="store_true", help="完成解析、关联和QC，但不写数据库")
    parser.add_argument("--no-backup", action="store_true", help="写库前不创建备份（不推荐）")
    args = parser.parse_args()
    result = import_typhoon_summaries(
        args.source,
        args.database,
        extracted_jsonl=args.extracted_jsonl,
        dry_run=args.dry_run,
        backup=not args.no_backup,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
