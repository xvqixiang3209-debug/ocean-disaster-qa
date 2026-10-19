"""
网格点定义海区
"""
import json
import os
import math

WAVE_LEVELS = [
    (0.5, 1.2, "轻浪"),
    (1.2, 2.5, "中浪"),
    (2.5, 4.0, "大浪"),
    (4.0, 6.0, "巨浪"),
    (6.0, 9.0, "狂浪"),
]

# 低不统计
WAVE_HEIGHT_MIN_FILTER = 0.5

def get_wave_level(height):
    for low, high, name in WAVE_LEVELS:
        if low <= height < high:
            return name
    return "狂浪" if height >= 6.0 else "轻浪"

def get_wave_level_range(h_min, h_max):
    level_min = get_wave_level(h_min)
    level_max = get_wave_level(h_max)
    if level_min == level_max:
        return level_min
    return f"{level_min}到{level_max}"

# 加载海区定义
REGIONS_FILE = os.path.join(os.path.dirname(__file__), "data", "ocean_regions.json")

def _load_regions():
    with open(REGIONS_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    regions = {}
    for name, info in data['regions'].items():
        regions[name] = {
            'name': name,
            'color': info['color'],
            'grid_points': [tuple(p) for p in info['grid_points']],
            'point_count': info['point_count'],
        }
    return regions

SEA_REGIONS = _load_regions()

# 海区显示顺序
REGION_DISPLAY_ORDER = [
    "渤海", "黄海北部", "黄海中部", "黄海南部",
    "东海北部", "东海南部", "钓鱼岛附近海域",
    "台湾海峡", "台湾以东洋面", "巴士海峡",
    "南海北部", "南海中部", "南海南部", "北部湾",
    "日本海", "日本以东洋面", "日本以南洋面",
    "菲律宾以东至关岛附近洋面",
]

# 第一天需要播报
DAY1_REGIONS_ORDER = REGION_DISPLAY_ORDER[:]

# 第二天、第三天
DAY23_REGIONS = [
    "东海", "东海南部", "钓鱼岛附近海域",
    "台湾海峡", "台湾以东洋面",
    "日本以东洋面", "日本以南洋面",
    "菲律宾以东至关岛附近洋面",
]

# 合并
MERGE_RULES = {
    "黄海": ["黄海北部", "黄海中部", "黄海南部"],
    "东海": ["东海北部", "东海南部"],
    "南海": ["南海北部", "南海中部", "南海南部"],
}

# 地理相邻关系
ADJACENT_GROUPS = [
    ["渤海", "黄海北部"],
    ["黄海北部", "黄海中部"],
    ["黄海中部", "黄海南部"],
    ["黄海南部", "东海北部"],
    ["东海北部", "东海南部"],
    ["东海南部", "钓鱼岛附近海域"],
    ["东海南部", "台湾海峡"],
    ["台湾海峡", "巴士海峡"],
    ["巴士海峡", "南海北部"],
    ["南海北部", "南海中部"],
    ["南海中部", "南海南部"],
    ["南海北部", "北部湾"],
    ["巴士海峡", "台湾以东洋面"],
    ["台湾海峡", "台湾以东洋面"],
    ["日本海", "日本以东洋面"],
    ["日本以东洋面", "日本以南洋面"],
    ["日本以南洋面", "菲律宾以东至关岛附近洋面"],
    ["日本海", "日本以南洋面"],
]

# 大浪区
TRACKING_CONFIG = {
    "big_wave_threshold": 2.5,      # 阈值
    "min_area_ratio": 0.1,         # 面积至少占所在海区网格点数的10%才算有效
}

# 数据配置
DATA_CONFIG = {
    "base_url": "https://oss.oceanguide.org.cn/Global/ww3/{ymdhm}-sig_height-wave-30m.json",
    "data_dir": "./data",
    "output_dir": "./output",
    "hours": [0, 6, 12, 18],  # UTC hours
}
