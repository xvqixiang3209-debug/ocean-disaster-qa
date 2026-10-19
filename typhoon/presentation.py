"""User-facing wording for Zhejiang track relationships."""

from __future__ import annotations


_ZHEJIANG_PLACE_MARKERS = (
    "浙江", "杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州",
    "舟山", "台州", "丽水", "温岭", "玉环", "苍南", "象山", "乐清", "瑞安",
    "平阳", "三门", "椒江", "临海",
)


def zhejiang_landfall_location(analogue: dict) -> str | None:
    """Return a Zhejiang landfall location when the report explicitly records one."""
    report = analogue.get("historical_report") or {}
    location = str(report.get("landfall_location") or "").strip()
    if location and any(marker in location for marker in _ZHEJIANG_PLACE_MARKERS):
        return location
    return None


def zhejiang_relation_text(analogue: dict, *, digits: int = 1) -> str:
    """Describe landfall separately from the sampled-track distance metric.

    ``closest_distance_zhejiang`` is a geometric value calculated from sampled
    track points to the system's Zhejiang reference boundary.  It is useful for
    ranking but is not a landfall determination and therefore must not contradict
    an explicit Zhejiang landfall record.
    """
    value = analogue.get("closest_distance_zhejiang")
    try:
        distance = f"{float(value):.{max(0, int(digits))}f} km"
    except (TypeError, ValueError):
        distance = "无数据"
    location = zhejiang_landfall_location(analogue)
    if location:
        return (
            f"已在{location}登陆；轨迹采样点至浙江参考边界的最小计算距离为{distance}"
        )
    return f"距浙江参考边界最近为{distance}"
