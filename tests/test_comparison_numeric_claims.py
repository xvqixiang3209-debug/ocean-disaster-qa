"""Comparison operands and signed deltas cannot borrow another object's values."""
import pytest
from marine_kb.business_validation import _comparison_present,validate_business_delivery


FACT={'kind':'comparison','metric':'最大风暴增水','left':'2019年利奇马','right':'2013年菲特',
      'left_value':312,'right_value':300,'difference':12,'unit':'cm','citation':1}
CORRECT='最大风暴增水：2019年利奇马312 cm，2013年菲特300 cm；前者比后者高12 cm[资料1]。'


@pytest.mark.parametrize('answer',[
    CORRECT,
    '最大风暴增水：2013年菲特300 cm，2019年利奇马312 cm；前者比后者低12 cm[资料1]。',
    '最大风暴增水：2019年利奇马312 cm，2013年菲特300 cm；后者比前者低12 cm[资料1]。',
    '最大风暴增水：2019年利奇马312 cm，2013年菲特300 cm；2013年菲特比2019年利奇马低12 cm[资料1]。',
    '最大风暴增水：2019年利奇马3.12米，2013年菲特3米；前者比后者高0.12米[资料1]。',
    '最大风暴增水：2019年利奇马3.12米，2013年菲特300 cm；前者比后者高12厘米[资料1]。',
])
def test_bound_operands_and_exact_same_dimension_conversions(answer):
    assert _comparison_present(answer,FACT)
    validate_business_delivery(answer,{'required_business_facts':[FACT]},{1})


@pytest.mark.parametrize('answer',[
    '最大风暴增水：2019年利奇马比2013年菲特高12 cm[资料1]。',
    '最大风暴增水：2019年利奇马300 cm，2013年菲特300 cm；前者比后者高12 cm[资料1]。',
    '最大风暴增水：2019年利奇马312 cm，2013年菲特312 cm；前者比后者高12 cm[资料1]。',
    '最大风暴增水：2019年利奇马312 cm，2013年菲特300 cm；前者比后者低12 cm[资料1]。',
    '最大风暴增水：2019年利奇马312 cm，2013年菲特300 cm；前者比后者高13 cm[资料1]。',
    '最大风暴增水：2019年利奇马312米，2013年菲特300米；前者比后者高12 cm[资料1]。',
    '最大风暴增水：2019年利奇马312 cm，2013年菲特300 cm；前者比后者高12米[资料1]。',
    '最大风暴增水：2019年利奇马312 cm，2013年菲特300 cm；后者比前者高12 cm[资料1]。',
    '最大风暴增水：2019年利奇马312，2013年菲特300；前者比后者高12 cm[资料1]。',
])
def test_missing_operands_wrong_values_units_and_relation_do_not_pass(answer):
    assert not _comparison_present(answer,FACT)
    with pytest.raises(ValueError):validate_business_delivery(answer,{'required_business_facts':[FACT]},{1})


@pytest.mark.parametrize('wrong',[
    '最大风暴增水：2019年利奇马300 cm，2013年菲特300 cm；前者比后者高12 cm[资料1]。',
    '最大风暴增水：2019年利奇马312 cm，2013年菲特300 cm；前者比后者低12 cm[资料1]。',
    '最大风暴增水：2019年利奇马312 cm，2013年菲特300 cm；前者比后者高13 cm[资料1]。',
])
def test_later_correct_authoritative_table_cannot_hide_a_wrong_comparison(wrong):
    with pytest.raises(ValueError,match='comparison_(?:operand|delta)_mismatch'):
        validate_business_delivery(wrong+'\n\n'+CORRECT,{'required_business_facts':[FACT]},{1})


def test_money_comparison_accepts_exact_wanyuan_yiyuan_conversion():
    fact={**FACT,'metric':'直接经济损失','left_value':76.22,'right_value':23.3759,'difference':52.8441,'unit':'亿元'}
    answer='直接经济损失：2019年利奇马762200万元，2013年菲特233759万元；前者比后者高528441万元[资料1]。'
    validate_business_delivery(answer,{'required_business_facts':[fact]},{1})


def test_zero_difference_keeps_both_values():
    fact={**FACT,'metric':'死亡含失踪人数','left_value':0,'right_value':0,'difference':0,'unit':'人'}
    answer='死亡含失踪人数：2019年利奇马0人，2013年菲特0人；前者比后者相同，差值为0人[资料1]。'
    validate_business_delivery(answer,{'required_business_facts':[fact]},{1})


def test_same_comparison_at_another_station_does_not_supply_required_record():
    fact={**FACT,'metric':'海门最高潮位','station':'海门'}
    answer=CORRECT.replace('最大风暴增水','澉浦最高潮位')
    assert not _comparison_present(answer,fact)

def test_subset_metric_normalizes_parentheses_without_dropping_scope():
    from marine_kb.business_validation import validate_business_delivery
    fact={'kind':'historical_metric','metric':'已解析测站记录的最大风暴增水（非区域全事件最大值）',
          'value':376,'unit':'cm','location':'2006年桑美','event_name':'桑美','season':2006,
          'station':'鳌江站','citation':1}
    text='| 2006年桑美，鳌江站 | 已解析测站记录的最大风暴增水（非区域全事件最大值） | 376 cm [资料1] |'
    validate_business_delivery(text,{'required_business_facts':[fact]},[1])
    for wrong in (text.replace('（非区域全事件最大值）',''),text.replace('376','720'),text.replace('鳌江','坎门')):
        with pytest.raises(ValueError,match='business_delivery_missing_historical_metric'):
            validate_business_delivery(wrong,{'required_business_facts':[fact]},[1])


