"""
海洋API调用
"""
import requests
from config import FOREOCEAN_TOKEN, FOREOCEAN_BASE


def query_county_data(county_id):
    """
    查询近岸海域数据
    返回: dict 或 None
    """
    url = f"{FOREOCEAN_BASE}/forecast/countyData"
    headers = {"Token": FOREOCEAN_TOKEN}
    params = {"countyID": county_id}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        data = resp.json()
        if data.get("success") and data.get("code") == "1000":
            return data["data"]
        return None
    except Exception as e:
        print(f"[API Error] countyData {county_id}: {e}")
        return None


def query_realtime_weather(lon, lat):
    """
    查询海洋天气实况
    返回: dict 或 None
    """
    url = f"{FOREOCEAN_BASE}/weather/realtime"
    headers = {"Token": FOREOCEAN_TOKEN}
    params = {"location": f"{lon},{lat}", "version": "1.0"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        data = resp.json()
        if data.get("success") and data.get("code") == "1000":
            return data["data"]
        return None
    except Exception as e:
        print(f"[API Error] realtime weather {lon},{lat}: {e}")
        return None


def query_depth(lon, lat):
    """
    查询全球水深数据
    返回: float 或 None
    """
    url = f"{FOREOCEAN_BASE}/geography/depth"
    headers = {"Token": FOREOCEAN_TOKEN}
    params = {"location": f"{lon},{lat}"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        data = resp.json()
        if data.get("success") and data.get("code") == "1000":
            return float(data["data"])
        return None
    except Exception as e:
        print(f"[API Error] depth {lon},{lat}: {e}")
        return None


def get_location_data(county_id, lon, lat):
    """
    获取单个地点的完整数据组合
    返回格式: "地点名:浪高 xx 米\n天气 天气类型\n温度 xx 摄氏度\n海温 xx 摄氏度\n风速 xx 米每秒\n水深 xx 米"
    """
    # 并行调用三个 API
    county_data = query_county_data(county_id)
    weather_data = query_realtime_weather(lon, lat)
    depth = query_depth(lon, lat)

    # 提取数据
    wave_height = "N/A"
    weather_desc = "N/A"
    temperature = "N/A"
    sst = "N/A"
    wind_speed = "N/A"
    depth_str = "N/A"

    if county_data and county_data.get("forecast"):
        forecast = county_data["forecast"][0] if county_data["forecast"] else {}
        wave_height = forecast.get("waveHeight", "N/A")
        sst = forecast.get("sst", "N/A")

    if weather_data:
        weather_desc = weather_data.get("weatherDescription", "N/A")
        temperature = weather_data.get("temperature", "N/A")
        wind_speed = weather_data.get("windSpeed", "N/A")

    if depth is not None:
        depth_str = f"{depth:.1f}"

    # 格式化数值
    def fmt(v):
        if v == "N/A":
            return "N/A"
        try:
            return f"{float(v):.1f}"
        except (ValueError, TypeError):
            return str(v)

    parts = [
        f"浪高 {fmt(wave_height)} 米",
        f"天气 {weather_desc}",
        f"温度 {fmt(temperature)} 摄氏度",
        f"海温 {fmt(sst)} 摄氏度",
        f"风速 {fmt(wind_speed)} 米每秒",
        f"水深 {depth_str} 米",
    ]

    return "\n".join(parts)
