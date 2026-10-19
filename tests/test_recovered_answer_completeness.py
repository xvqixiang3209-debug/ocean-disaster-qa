import json
import pytest
from marine_kb.answer_completeness import decode_review


def test_separate_actual_answer_spans_and_missing_items_are_preserved():
    answer='海门站最大增水312厘米[资料1]。\n本次没有另一事件的同站增水数据。'
    parts=[{'id':1,'question':'海门站最大增水多少？'},{'id':2,'question':'比另一次高多少？'}]
    raw={'parts':[{'id':1,'status':'full','answer_spans':['海门站最大增水312厘米[资料1]。'],
                   'citations':[1],'missing_information':[]},
                  {'id':2,'status':'insufficient','answer_spans':[],'citations':[],
                   'missing_information':['缺少另一事件的同站增水，无法计算差值。']}]}
    coverage,rows=decode_review(json.dumps(raw),answer,parts,{1})
    assert coverage=='partial' and rows[0]['status']=='full' and rows[1]['status']=='insufficient'


@pytest.mark.parametrize('change',[
    lambda r:r['parts'][0].update(answer_spans=['海门站最大增水999厘米[资料1]。']),
    lambda r:r['parts'][0].update(citations=[2]),
    lambda r:r['parts'][0].update(status='full',missing_information=['仍缺阈值']),
    lambda r:r['parts'][0].update(status='partial',missing_information=[]),
    lambda r:r['parts'][0].update(id=9),
    lambda r:r['parts'][0].update(answer_spans=[],citations=[]),
])
def test_coverage_review_cannot_rewrite_answer_or_assert_without_spans(change):
    text='海门站最大增水312厘米[资料1]。'
    raw={'parts':[{'id':1,'status':'full','answer_spans':[text],'citations':[1],'missing_information':[]}]}
    change(raw)
    with pytest.raises(ValueError):decode_review(json.dumps(raw),text,[{'id':1,'question':'最大增水多少？'}],{1})
