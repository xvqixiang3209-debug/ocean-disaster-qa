"""End-to-end numeric gates, including literal and dimensional validation."""
import json
import pytest
from marine_kb.qa import _decode_model_answer


def decode(answer,source,metadata=None):
    evidence=[{'text':source,'passages':[{'id':1,'text':source}],'metadata':metadata or {}}]
    raw=json.dumps({'answerability':'full','answer':answer,'evidence_quotes':[{'citation':1,'passage_id':1}]},ensure_ascii=False)
    return _decode_model_answer(raw,evidence)


@pytest.mark.parametrize('answer,source',[
    ('更新间隔为30分钟[资料1]。','配置的网格同步间隔为1800秒。'),
    ('潮位为2.31米[资料1]。','同一站点潮位为231厘米。'),
    ('总量为0.1亿元[资料1]。','同一口径金额为1000万元。'),
    ('预报时效为72小时[资料1]。','The forecast has a 3 d horizon.'),
    ('时间间隔为0.5小时[资料1]。','间隔为30分钟。'),
    ('人口为1,234人[资料1]。','人口统计为1234人。'),
    ('同一测点水位为-2米[资料1]。','同一测点水位为-200厘米。'),
])
def test_exact_explicit_unit_conversion_survives_both_gates(answer,source):
    assert decode(answer,source)[0]==answer


@pytest.mark.parametrize('answer,source',[
    ('更新间隔为31分钟[资料1]。','配置的网格同步间隔为1800秒。'),
    ('潮位为2.31米[资料1]。','潮位为300厘米。'),
    ('预报时效为72小时[资料1]。','A 3 m water level.'),
    ('人口为30人，间隔为30分钟[资料1]。','人口为2人，间隔1800秒。'),
    ('有30个站，间隔30分钟[资料1]。','有2个站，间隔1800秒。'),
    ('同一测点水位为2米[资料1]。','同一测点水位为-200厘米。'),
    ('网格更新为30分钟[资料1]。','{"unknown_field": 1800}'),
])
def test_conversion_does_not_license_wrong_values_counts_sign_or_unknown_unit(answer,source):
    with pytest.raises(ValueError):decode(answer,source)


def test_unknown_structured_units_cannot_skip_literal_gate():
    with pytest.raises(ValueError):decode('预报时效为72小时[资料1]。','{"arbitrary":3}',{'source_type':'business_forecast'})
    with pytest.raises(ValueError):decode('预报时效为30分钟[资料1]。','{"arbitrary":30}',{'source_type':'business_forecast'})


def test_unit_header_cannot_be_borrowed_between_documents():
    from marine_kb.quantities import validate_quantity_units
    sources=[{'text':'有效波高（米）\n4≤Hs＜6'}, {'text':'Hs为有效波高。另有9厘米的独立记录。'}]
    with pytest.raises(ValueError):validate_quantity_units('有效波高下限4米[资料1][资料2]。',sources)


def test_partial_json_with_explicit_schema_unit_still_supports_conversion():
    assert decode('网格同步间隔为30分钟[资料1]。','"配置的网格同步间隔秒":1800, "最近同步":"2026-10-18"',{'source_type':'system_runtime'})
    with pytest.raises(ValueError):decode('网格同步间隔为30分钟[资料1]。','"unknown_interval":1800, "最近同步":"2026-10-18"',{'source_type':'system_runtime'})


def test_bounded_program_calculation_keeps_typed_input_count_with_selected_formula():
    source='用户明确家庭人数为7人。按每人3天总量3升计算，家庭至少21升。计算式7 × 3升/人 = 21升。'
    evidence=[{'text':source,'passages':[{'id':1,'text':source}],
               'metadata':{'source_type':'program_calculation','calculation':{'people':7,'family_total_litres':'21'}}}]
    value={'answerability':'full','answer':'按7人换算，最低总量21升[资料1]。',
           'evidence_quotes':[{'citation':1,'quote':'计算式7 × 3升/人 = 21升。'}]}
    assert _decode_model_answer(json.dumps(value,ensure_ascii=False),evidence)
    value['answer']='按7人换算，最低总量22升[资料1]。'
    with pytest.raises(ValueError):_decode_model_answer(json.dumps(value,ensure_ascii=False),evidence)
