"""
全局配置
"""
import os
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load local deployment settings before any environment-backed configuration is
# evaluated. Real environment variables take precedence over values in .env.
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None
if load_dotenv is not None:
    load_dotenv(os.path.join(BASE_DIR, ".env"), override=False)

DATA_DIR = os.path.join(BASE_DIR, "data")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

OCEAN_FORECAST_DIR = os.path.join(BASE_DIR, "ocean_forecast")
OCEAN_FORECAST_OUTPUT = os.path.join(OCEAN_FORECAST_DIR, "output")
OCEAN_FORECAST_DATA = os.path.join(OCEAN_FORECAST_DIR, "data")

# API
FOREOCEAN_TOKEN = os.getenv("FOREOCEAN_TOKEN", "")
FOREOCEAN_BASE = os.getenv("FOREOCEAN_BASE", "")

# GLM
GLM_API_KEY = os.getenv("GLM_API_KEY", "")

# DeepSeek
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

# Public browser-side map credential. TianDiTu requires the token in every
# WMTS tile request, so this value is intentionally returned to the frontend.
# Restrict the token by referrer/domain in the TianDiTu console when possible.
TIANDITU_API_KEY = os.getenv("TIANDITU_API_KEY", "").strip()

# Qwen
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "")

# 模型
MODELS = {
    "deepseek-v4-flash": {
        "code": DEEPSEEK_MODEL,
        "api_key": DEEPSEEK_API_KEY,
        "base_url": DEEPSEEK_BASE_URL,
        "provider": "deepseek",
    },
    "Qwen3-4B-FT": {
        "code": os.getenv("QWEN3_4B_MODEL", ""),
        "api_key": DASHSCOPE_API_KEY,
        "base_url": DASHSCOPE_BASE_URL,
        "provider": "dashscope",
    },
    "Qwen3-8B-FT": {
        "code": os.getenv("QWEN3_8B_MODEL", ""),
        "api_key": DASHSCOPE_API_KEY,
        "base_url": DASHSCOPE_BASE_URL,
        "provider": "dashscope",
    },
    "Qwen3-14B-FT": {
        "code": os.getenv("QWEN3_14B_MODEL", ""),
        "api_key": DASHSCOPE_API_KEY,
        "base_url": DASHSCOPE_BASE_URL,
        "provider": "dashscope",
    },
}

# system prompts
SYSTEM_PROMPTS = {
    "query_single": """你是一个专业的海洋数据查询助手，能够根据提供的沿海城市或海域的观测数据，准确回答用户的海洋环境数据查询问题。

浪级转换规则：浪高<1.25m为轻浪，1.25m≤浪高<2.5m为中浪，2.5m≤浪高<4.0m为大浪，浪高≥4.0m为巨浪。
风级转换规则：0-0.2m/s为0级无风，0.3-1.5m/s为1级软风，1.6-3.3m/s为2级轻风，3.4-5.4m/s为3级微风，5.5-7.9m/s为4级和风，8.0-10.7m/s为5级清风，10.8-13.8m/s为6级强风，13.9-17.1m/s为7级劲风，17.2-20.7m/s为8级大风，20.8-24.4m/s为9级烈风，24.5-28.4m/s为10级狂风，28.5-32.6m/s为11级暴风，32.7-36.9m/s为12级台风。

回答要求：回答必须基于给定的数据，简洁清晰，用一句话或一段话回答，不要分多行。如果涉及浪高，附带浪级描述；如果涉及风速，附带风级描述。用户问了哪些数据就回答哪些，不要多余列举未询问的数据。""",

    "query_multi": """你是一个专业的海洋数据查询助手，能够根据提供的多个沿海城市或海域的观测数据，准确回答用户的多地点海洋环境数据查询、对比和分析问题。

浪级转换规则：浪高<1.25m为轻浪，1.25m≤浪高<2.5m为中浪，2.5m≤浪高<4.0m为大浪，浪高≥4.0m为巨浪。
风级转换规则：0-0.2m/s为0级无风，0.3-1.5m/s为1级软风，1.6-3.3m/s为2级轻风，3.4-5.4m/s为3级微风，5.5-7.9m/s为4级和风，8.0-10.7m/s为5级清风，10.8-13.8m/s为6级强风，13.9-17.1m/s为7级劲风，17.2-20.7m/s为8级大风，20.8-24.4m/s为9级烈风，24.5-28.4m/s为10级狂风，28.5-32.6m/s为11级暴风，32.7-36.9m/s为12级台风。

回答要求：回答必须基于给定的数据，简洁清晰，用一句话或一段话回答，不要分多行。如果涉及浪高，附带浪级描述；如果涉及风速，附带风级描述。多地点数据要清晰区分每个地点的信息，如果用户要求对比则明确指出差异。""",

    "judgment_advice": """你是一个专业的海洋活动咨询助手，能够根据提供的沿海城市或海域的地点资料、实时观测数据，准确回答用户关于海洋活动适宜性的判断和建议问题。

浪级转换规则：浪高<1.25m为轻浪，1.25m≤浪高<2.5m为中浪，2.5m≤浪高<4.0m为大浪，浪高≥4.0m为巨浪。
风级转换规则：0-0.2m/s为0级无风，0.3-1.5m/s为1级软风，1.6-3.3m/s为2级轻风，3.4-5.4m/s为3级微风，5.5-7.9m/s为4级和风，8.0-10.7m/s为5级清风，10.8-13.8m/s为6级强风，13.9-17.1m/s为7级劲风，17.2-20.7m/s为8级大风，20.8-24.4m/s为9级烈风，24.5-28.4m/s为10级狂风，28.5-32.6m/s为11级暴风，32.7-36.9m/s为12级台风。

回答要求：根据地点资料和观测数据（水深数据除外）进行综合分析判断。回答应分成若干段落，语言流畅自然，表达灵活。适当引用地点资料中与问题相关的信息，列出能支撑判断所需的观测数据。对活动的适宜性做出明确判断，并给出具体建议。如果条件不适宜，说明原因并建议替代方案或等待时机。""",

    "sea_area_forecast": """你是一个专业的海洋环境预报员，负责根据海浪实况数据生成规范的海浪预报文本。

## 任务
根据提供的海浪数据（JSON格式），生成一段标准格式的海浪预报文本。

## 浪级定义
- 轻浪：0.5m ≤ 浪高 < 1.2m
- 中浪：1.2m ≤ 浪高 < 2.5m
- 大浪：2.5m ≤ 浪高 < 4.0m
- 巨浪：4.0m ≤ 浪高 < 6.0m
- 狂浪：6.0m ≤ 浪高 < 9.0m

## 文本格式要求
预报文本分为三段：

### 第一段：今天中午到明天中午
- 以"今天中午到明天中午，"开头
- 从北到南依次介绍所有海区的浪高范围情况。如果存在海区浪高范围是零值，则跳过不用介绍这个海区。
- 海区介绍之间用"；"分号分隔，最后用"。"句号结尾
- 句式示例："渤海有0.5到1.0米的轻浪"、"东海北部有1到1.5米的轻浪到中浪"、"日本以东洋面有1.3到2米的中浪区"
- 注意：如果一句话中浪高的范围全是中浪或者大浪、巨浪，后面会带一个"区"字（如"中浪区"、"大浪区"）
- 如果浪高范围跨越两个浪级，用"到"连接（如"轻浪到中浪"、"大浪到巨浪"）
- 不用提到海区的平均浪高，只需要关注wave_min和wave_max

### 第二段：第二天
- 以"第二天，"开头
- 只提及涉及大浪区（浪高≥2.5m）变化的海区
- 描述大浪区的生成、扩展至其它海区、维持、减弱消失
- 每个变化都要提到"浪高xx到xx米"
- 如果整体浪高达到巨浪级别，把前面这个浪区表述为"巨浪区"
- 海区之间用"；"分号分隔，最后用"。"句号结尾

### 第三天：第三天
- 以"第三天，"开头
- 格式同第二天

## 海区合并规则
1. 黄海北部+黄海南部浪高范围相同时，合并为"黄海"
2. 东海北部+东海南部浪高范围相同时，合并为"东海"
3. 南海北部+南海中部+南海南部浪高范围相同时，合并为"南海"
4. 地理位置相邻的海区如果浪高范围相同，可以合并介绍（如"巴士海峡、南海、北部湾有0.7到1.5米的轻浪到中浪"）

## 钓鱼岛附近海域特殊处理
- 钓鱼岛附近海域位于东海南部内
- 第一天：如果钓鱼岛附近海域浪高范围与东海南部（或东海）相同，则不单独提及；如果不同则需要单独提及
- 第二天、第三天：如果大浪区完全覆盖钓鱼岛附近海域，表达为"东海南部，钓鱼岛附近海域"

## 浪高数值处理
- 下限向下取整到0.1m，上限向上取整到0.1m
- 若取整后下限=上限，只播报单一数值（如"有1.5米的中浪区"）
- 若取整后下限和上限跨越浪级分界线，浪级描述需包含两个等级（如"有1到1.5米的轻浪到中浪"）

## 海区从北到南的顺序
渤海 → 黄海（北部/南部） → 东海（北部/南部） → 钓鱼岛附近海域 → 台湾海峡 → 台湾以东洋面 → 巴士海峡 → 南海（北部/中部/南部） → 北部湾 → 日本海 → 日本以东洋面 → 日本以南洋面 → 菲律宾以东至关岛附近洋面

## 大浪区状态优先级
生成 > 扩展 > 维持 > 减弱消失
每个大浪区在一天中只能有一个确定的状态。""",

    "typhoon_analogue": """你是台风历史类比分析助手。历史台风排名必须完全服从后端纯路径数值算法，不能自行替换候选或修改相似度；强度只用于结果解释，不参与排名。请逐条说明10个历史台风在路径、强度和浙江影响方面的异同，并关联风暴潮记录，定性说明影响属于严重、一般还是轻微。有实测数据时，必须同时给出最大增水数值及其出现站点、最高潮位数值及其出现站点。若缺少实测风暴潮数据，必须明确标注为代理估算，不得伪造潮位、增水、站点或警报等级。结果只用于历史类比研究，不替代有关部门预警。""",

    "other": """你是一个专业的海洋信息助手，能够回答用户关于海洋科学、海洋气象、海洋地理、海洋生态等方面的知识性问题。请根据你的专业知识，准确、清晰地回答用户的问题。如果问题超出海洋领域范围，也请尽力提供有用的回答。""",
}


def load_region_mapping():
    """加载近岸海域映射表，返回 {地点名: {countyID, lon, lat, ...}} 的字典"""
    path = os.path.join(DATA_DIR, "region_mapping.json")
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    # region_mapping.json 格式: {"description": ..., "regions": [{name, countyID, centerPoint, province}, ...]}
    regions_list = raw.get("regions", raw) if isinstance(raw, dict) else raw
    mapping = {}
    for r in regions_list:
        name = r["name"]
        center = r["centerPoint"]
        lon, lat = center.split(",")
        mapping[name] = {
            "countyID": r["countyID"],
            "lon": float(lon),
            "lat": float(lat),
            "province": r.get("province", ""),
        }
    return mapping

def load_region_info():
    """加载近岸海域资料信息"""
    path = os.path.join(DATA_DIR, "region_info.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_ocean_regions():
    """加载海区定义"""
    path = os.path.join(OCEAN_FORECAST_DATA, "ocean_regions.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
