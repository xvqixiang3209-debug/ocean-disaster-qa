import pytest
from forecast_runtime.impact_inputs import impact_inputs_question,standalone_impact_inputs_question


@pytest.mark.parametrize('question',[
 '我住宁波市鄞州区咸祥镇沿海住宅，2026年10月18日至21日一楼和地下车位会不会进水？要不要提前挪车？',
 '2026年10月19日朱家尖沿海住宅会不会进水？应该如何提前挪车？',
 '2026年10月19日温岭会海水倒灌吗？应该怎么准备？',
 '2026年10月19日临海住宅地下车库是否会被海水灌入？',
 '2026年10月19日海水会淹到地下室吗？',
])
def test_dated_property_outcomes_keep_existing_boundary_input_route(question):
 assert impact_inputs_question(question)
 assert standalone_impact_inputs_question(question)


@pytest.mark.parametrize('question',[
 '2026年10月19日家里水管破裂进水了怎么处理？',
 '2026年10月19日厨房进水会不会漏到楼下？',
 '假设2026年10月19日沿海住宅进水，如何清理消毒？',
 '截至2026年10月19日，如何计算海水倒灌的淹没范围？',
 '宁波沿海住宅历史上进水过吗？',
])
def test_plumbing_hypotheses_and_methods_are_not_promoted_to_live_tide_queries(question):
 assert not impact_inputs_question(question)
