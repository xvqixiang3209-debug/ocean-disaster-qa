"""
意图识别
"""
import json
try:
    from zai import ZhipuAiClient
except ImportError:  # The local typhoon agent can run without an LLM SDK.
    ZhipuAiClient = None
from config import GLM_API_KEY

# 初始化GLM客户端
glm_client = ZhipuAiClient(api_key=GLM_API_KEY) if ZhipuAiClient and GLM_API_KEY else None

# 意图识别提示词
INTENT_SYSTEM_TEMPLATE = """你是一个意图识别助手。用户会向你提问关于海洋的问题，你需要判断用户的问题属于以下哪种类型：

1. **query** - 查询类问题：用户想查询某个或某些沿海城市/海域的海洋环境数据（浪高、天气、温度、海温、风速、水深等）。例如："丹东的浪高多少？"、"帮我看看烟台和龙口的天气"、"青岛和大连哪个浪高更高？"
2. **judgment_advice** - 判断建议类问题：用户想了解某个沿海城市/海域是否适合进行某种海洋活动（如游泳、冲浪、出海、旅游等），需要你给出判断和建议。例如："今天适合去北戴河游泳吗？"、"想带老人去三亚度假，现在条件怎么样？"、"大连适合出海钓鱼吗？"
3. **sea_area_forecast** - 海区预报类问题：用户想了解某个日期的海浪预报（覆盖渤海、黄海、东海、南海等18个海区的预报）。例如："今天海浪预报怎么样？"、"生成2026年5月1日的海浪预报"、"昨天的海浪预报"
4. **typhoon_analogue** - 台风历史类比问题：用户希望根据当前或即将到来的台风强度和路径，寻找最相似的历史台风。例如："找两个和当前台风最相似的历史台风"、"这个台风和历史上哪个台风路径最像？"
5. **other** - 其他类型问题：不属于以上类型的海洋相关问题或一般性问题。例如："什么是厄尔尼诺现象？"、"台风是怎么形成的？"、"你好"

可查询的地点列表：{location_list}

请严格按照以下JSON格式返回结果，不要输出任何其他内容：
{{"intent": "query", "locations": ["地点1", "地点2"]}}
或
{{"intent": "judgment_advice", "locations": ["地点1"]}}
或
{{"intent": "sea_area_forecast", "date": "2026-05-05"}}
或
{{"intent": "typhoon_analogue"}}
或
{{"intent": "other"}}

注意：
- 对于 query 和 judgment_advice 类型，必须提取出用户提到的地点名称，放在 locations 数组中
- 对于 sea_area_forecast 类型，如果用户提到具体日期则提取日期（格式 YYYY-MM-DD），如果没有提到日期则 date 设为 "today"
- locations 中的地点名称必须从上面的地点列表中选择，如果用户提到的地点不在列表中，尽量匹配最接近的地点
- 如果用户问的是"今天"、"昨天"等相对日期，对于 sea_area_forecast 类型，date 设为 "today" 或 "yesterday"
"""


def recognize_intent(user_message, region_mapping):
    """
    意图
    返回: dict, 例如 {"intent": "query", "locations": ["丹东", "大连"]}
    """
    location_list = "、".join(region_mapping.keys())
    system_prompt = INTENT_SYSTEM_TEMPLATE.format(location_list=location_list)

    # Keep the core typhoon workflow usable in an offline environment.
    if _looks_like_typhoon_analogue(user_message):
        return {"intent": "typhoon_analogue"}
    if glm_client is None:
        return _fallback_intent(user_message, region_mapping)

    try:
        response = glm_client.chat.completions.create(
            model="glm-4.7",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.1,
            thinking={"type": "disabled"},
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        result = json.loads(content)
        return result
    except json.JSONDecodeError as e:
        print(f"[Intent Error] JSON parse failed: {e}, raw: {content}")
        return {"intent": "other"}
    except Exception as e:
        print(f"[Intent Error] {e}")
        return _fallback_intent(user_message, region_mapping)


def _looks_like_typhoon_analogue(message):
    text = str(message or "")
    has_typhoon = "台风" in text or "热带气旋" in text
    has_similarity = any(word in text for word in ("相似", "最像", "类比")) or ("路径" in text and "对比" in text)
    return has_typhoon and has_similarity


def _fallback_intent(message, region_mapping):
    """Small deterministic fallback used only when GLM is unavailable."""
    if _looks_like_typhoon_analogue(message):
        return {"intent": "typhoon_analogue"}
    locations = [name for name in region_mapping if name in str(message)]
    knowledge_words = ("台风", "风暴潮", "增水", "灾情", "措施", "公报", "标准", "规范", "预案", "历史", "原理", "什么是")
    if locations and not any(word in str(message) for word in knowledge_words):
        advice_words = ("适合", "建议", "能不能", "可以去", "游泳", "冲浪", "出海", "钓鱼")
        intent = "judgment_advice" if any(word in str(message) for word in advice_words) else "query"
        return {"intent": intent, "locations": locations}
    return {"intent": "other"}
