import pytest

from forecast_runtime.official_warnings import warning_question


@pytest.mark.parametrize('question', [
    '按GB/T 19721.2-2017这份标准，海浪警报时效是多少？这是否表示每24小时更新一次？',
    '依据GB∕T 19721.1—2017，风暴潮警报有什么发布要求？',
    '海浪警报时效是多少？是否每24小时更新一次？',
    'HY/T 0376-2023风暴潮预警报制作规范规定哪些要素？',
    '海浪警报的更新频率和时效有什么区别？',
])
def test_provisions_do_not_query_live_publication_list(question):
    assert warning_question(question) is False


@pytest.mark.parametrize('question', [
    '截至2026年10月21日浙江已经发布哪些海浪警报？',
    '今天舟山有哪些海洋预警，等级和覆盖海域是什么？',
    '查询2026年10月17日海浪警报发布记录',
    '按GB/T 19721.2-2017解释警报时效，并查询今天浙江已发布哪些海浪警报',
    '今天浙江发布了哪些海洋预警，是否已经启动应急响应？',
])
def test_actual_publication_and_mixed_requests_retain_business_route(question):
    assert warning_question(question) is True
