"""Regression tests for historical numeric evidence and disaster action wording."""
import pytest
from marine_kb.query_intents import focused_queries, intents
from marine_kb.web_supplement import focused_public_text, public_search_queries


def _history_body():
    # Artificial fixture: all dates/numbers below are test data, not facts.
    intro = "风暴潮预警减少因灾死亡人数，经济损失仍有上升趋势，因此需加强预警。"
    event = "2008年某次风暴潮影响甲省、乙省，两省合计损失12.34亿元，死亡56人，失踪7人。"
    history = "历史记载的20世纪个例中有多次淹死万人以上的风暴潮；不同时间的记录范围不完全一致。"
    return "\n\n".join(["风暴潮科普", intro, "背景介绍。" * 600, event, history])


def test_historical_casualty_comparison_preserves_dated_event_and_geography():
    q = "按全国死亡人数和直接经济损失比较，历史上最严重的风暴潮是哪一次？"
    result = focused_public_text(_history_body(), q, [
        "按死亡人数比较，历史上最严重的是哪一次？",
        "按直接经济损失比较，历史上最严重的是哪一次？",
    ], limit=1000)
    assert "2008年" in result
    assert "甲省、乙省，两省合计损失12.34亿元，死亡56人，失踪7人" in result
    assert "多次淹死万人以上" in result
    assert "直接经济损失12.34" not in result
    assert "全国第一" not in result


def test_ordinary_definition_still_selects_definition():
    body = "\n\n".join(["无关内容。" * 600, "风暴潮是风暴天气导致的海面异常升降。", "某年损失9亿元。"])
    result = focused_public_text(body, "风暴潮是什么？", limit=300)
    assert "风暴潮是风暴天气导致的海面异常升降" in result


@pytest.mark.parametrize("action", ["必须做什么", "应当做什么", "应该做什么", "该做什么", "需要采取什么行动", "要怎么做"])
def test_action_question_searches_guidance_and_keeps_place(action):
    q = f"假设温州海域发布海浪红色警报，这是什么意思？沿海居民{action}？"
    queries = public_search_queries(q, False, ["海浪红色警报是什么意思？", f"沿海居民{action}？"])
    action_queries = [query for query in queries if "防御" in query or "避险" in query]
    assert action_queries
    assert all("温州" in query for query in action_queries)
    assert not any("舟山" in query for query in queries)
    assert "defensive_actions" in intents(q)
    assert any("防御指南" in query for query in focused_queries(q))


def test_action_question_without_explicit_subquestions_is_not_definition_only():
    queries = public_search_queries("假设福建海浪红色警报，含义是什么，居民应该做什么？", False)
    assert any("防御" in query or "避险" in query for query in queries)
    assert all("福建" in query for query in queries)


def test_action_profile_requires_disaster_subject():
    assert "defensive_actions" not in intents("预算审批应该做什么？")


def test_action_focus_retains_operative_text_instead_of_only_definition():
    action = "公众应远离危险岸段，停止涉海活动，听从当地部门安排及时撤离。"
    body = "\n\n".join(["海浪警报是海浪灾害信息。", "一般背景。" * 600, action])
    assert action in focused_public_text(body, "海浪警报下居民必须做什么？", limit=400)
