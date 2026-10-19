"""
任务处理
"""
import json
import os
from datetime import datetime, timedelta
from config import (
    load_region_mapping, load_region_info,
    OCEAN_FORECAST_OUTPUT,
)
from ocean_api import get_location_data


def process_query(locations, user_message, region_mapping):
    """
    处理查询任务
    返回: (system_prompt_key, formatted_user_message)
    """
    data_parts = []
    for loc_name in locations:
        loc_info = region_mapping.get(loc_name)
        if not loc_info:
            continue
        county_id = loc_info["countyID"]
        lon = loc_info["lon"]
        lat = loc_info["lat"]
        loc_data = get_location_data(county_id, lon, lat)
        data_parts.append(f"{loc_name}:{loc_data}")

    if not data_parts:
        return "other", user_message

    combined_data = "\n".join(data_parts)
    formatted = f"{combined_data}\n 问题：{user_message}"

    if len(locations) == 1:
        return "query_single", formatted
    else:
        return "query_multi", formatted


def process_judgment_advice(locations, user_message, region_mapping, region_info):
    """
    处理判断建议任务
    返回: (system_prompt_key, formatted_user_message)
    """
    data_parts = []
    info_parts = []

    for loc_name in locations:
        loc_info = region_mapping.get(loc_name)
        if not loc_info:
            continue

        # 获取地点资料
        loc_desc = region_info.get(loc_name, "")
        if loc_desc:
            info_parts.append(f"地点资料：{loc_desc}")

        # 获取实时数据
        county_id = loc_info["countyID"]
        lon = loc_info["lon"]
        lat = loc_info["lat"]
        loc_data = get_location_data(county_id, lon, lat)
        data_parts.append(f"{loc_name}:{loc_data}")

    if not data_parts:
        return "other", user_message

    combined_info = "\n".join(info_parts) if info_parts else ""
    combined_data = "\n".join(data_parts)
    formatted = f"{combined_info}\n{combined_data}\n 问题：{user_message}"

    return "judgment_advice", formatted


def process_sea_area_forecast(date_str):
    """
    处理海区预报任务
    返回: (system_prompt_key, formatted_user_message) 或 (None, error_msg)
    """
    # 解析日期
    target_date = _resolve_date(date_str)
    if not target_date:
        return None, f"无法解析日期：{date_str}"

    mm_dd = target_date.strftime("%m-%d")
    info_path = os.path.join(OCEAN_FORECAST_OUTPUT, f"{mm_dd}-info.json")

    if not os.path.exists(info_path):
        return None, f"未找到 {target_date.strftime('%Y-%m-%d')} 的海浪预报数据"

    with open(info_path, "r", encoding="utf-8") as f:
        info_data = json.load(f)
    # 格式化用户消息
    formatted = f"请根据以下海浪数据，生成{target_date.strftime('%Y-%m-%d')}的海浪预报文本。\n\n{json.dumps(info_data, ensure_ascii=False, indent=2)}"

    return "sea_area_forecast", formatted


def process_typhoon_analogue(application, current_provider, model_name=None):
    """Run the configured typhoon application using the current-data adapter."""
    current = current_provider.get_current_typhoon()
    result = application.analyze(current, top_k=10, model_name=model_name)
    return result, result["report"]


def _resolve_date(date_str):
    """
    解析日期字符串为datetime对象YYYY-MM-DD, today, yesterday, 今天, 昨天
    """
    today = datetime.now()

    if date_str in ("today", "今天"):
        return today
    elif date_str in ("yesterday", "昨天"):
        return today - timedelta(days=1)

    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        pass

    return None
