import json
import pytest

from marine_kb.claim_grounding import draft_claims, apply_attribution
from marine_kb.qa import _decode_model_answer


def decode(candidate, evidence):
    return _decode_model_answer(json.dumps(candidate, ensure_ascii=False), evidence)


def test_existing_passage_ids_supply_exact_source_without_model_copying():
    text='风暴潮是风暴引起的海面非周期性异常升降。'
    evidence=[{'text':text,'passages':[{'id':1,'text':text}]}]
    raw=json.dumps({'answerability':'partial','answer':'风暴潮是海面异常升降[资料1]。'})
    review={'claims':[{'id':1,'status':'supported','refs':[{'citation':1,'passage_id':1}]}]}
    result,audit=apply_attribution(raw,json.dumps(review),evidence,decode)
    assert '海面异常升降' in result[0]
    assert audit['selected_evidence_quotes']==[{'citation':1,'quote':text}]


def test_descriptive_table_keeps_headers_and_separately_supported_rows():
    texts=['台风是热带气旋的一种。','风暴潮是海面非周期性异常升降。','另一个现象在24小时内发生。']
    evidence=[{'text':s,'passages':[{'id':1,'text':s}]} for s in texts]
    table='| 名称 | 本质 |\n|---|---|\n| 台风 | 热带气旋的一种[资料1] |\n| 风暴潮 | 海面异常升降[资料2] |\n| 错误项 | 在1小时内发生[资料3] |'
    raw=json.dumps({'answerability':'partial','answer':table})
    _,claims=draft_claims(raw)
    assert len(claims)==3 and all(c['table_header']=='| 名称 | 本质 |' for c in claims)
    review={'claims':[{'id':n,'status':'supported','refs':[{'citation':n,'passage_id':1}]} for n in (1,2,3)]}
    result,audit=apply_attribution(raw,json.dumps(review),evidence,decode)
    assert result[0].count('| 名称 | 本质 |')==1
    assert '| 台风 | 热带气旋的一种[资料1] |' in result[0]
    assert '| 风暴潮 | 海面异常升降[资料2] |' in result[0]
    assert '错误项' not in result[0]
    assert audit['decisions'][2]['status']=='rejected_by_deterministic_check'


@pytest.mark.parametrize('table',[
    '| 项目 | 结果 |\n|---|---|\n| A | 2 |',
    '| 站点 | 面积 |\n|---|---|\n| A | 2 |',
    '| 站点 | 面积 |\n|---|---|\n| A | 2未知单位 |',
])
def test_numeric_tables_do_not_acquire_guessed_units_during_generic_recovery(table):
    raw=json.dumps({'answerability':'partial','answer':'本次证据尚未完成核对。\n\n'+table})
    _,claims=draft_claims(raw)
    assert not any('table_header' in c for c in claims)


def test_inline_area_unit_enters_claims_but_must_have_same_row_and_dimension_evidence():
    raw=json.dumps({'answer':'| 站点 | 面积 |\n|---|---|\n| A | 2平方公里 |'})
    _,claims=draft_claims(raw)
    assert len(claims)==1 and claims[0]['table_header']=='| 站点 | 面积 |'
    review={'claims':[{'id':1,'status':'supported','refs':[{'citation':1,'passage_id':1}]}]}
    for text in ('A站长度为2公里。','B站面积为2平方公里。','A站面积为3平方公里。'):
        evidence=[{'text':text,'passages':[{'id':1,'text':text}]}]
        assert apply_attribution(raw,json.dumps(review),evidence,decode) is None
    text='A站面积为2000000平方米。'
    evidence=[{'text':text,'passages':[{'id':1,'text':text}]}]
    result,audit=apply_attribution(raw,json.dumps(review),evidence,decode)
    assert '2平方公里' in result[0] and audit['retained_claims']==1


def test_explicit_unit_table_recovers_original_rows_and_rejects_wrong_value():
    text='西北太平洋和南海采用2分钟平均风。热带风暴最大平均风速为17.2至24.4 m/s。台风为32.7至41.4 m/s。'
    evidence=[{'text':text,'passages':[{'id':1,'text':text}]}]
    valid='| 等级 | 最大平均风速（m/s，2分钟平均） |'
    raw=json.dumps({'answerability':'partial','answer':valid+'\n|---|---|\n| 热带风暴 | 17.2～24.4 |\n| 台风 | 32.7～99.9 |'},ensure_ascii=False)
    _,claims=draft_claims(raw)
    assert len(claims)==2
    review={'claims':[{'id':n,'status':'supported','refs':[{'citation':1,'passage_id':1}]} for n in (1,2)]}
    result,audit=apply_attribution(raw,json.dumps(review),evidence,decode)
    assert '17.2～24.4[资料1]' in result[0] and '99.9' not in result[0]
    assert audit['decisions'][1]['status']=='rejected_by_deterministic_check'


def test_numeric_header_context_is_cited_and_kept_without_changing_values():
    text='采用2分钟平均风，热带风暴为17.2至24.4 m/s。'
    evidence=[{'text':text,'passages':[{'id':1,'text':text}]}]
    raw=json.dumps({'answerability':'partial','answer':'| 等级（2分钟平均） | 风速（m/s） |\n|---|---|\n| 热带风暴 | 17.2～24.4 |'})
    review={'claims':[{'id':1,'status':'supported','refs':[{'citation':1,'passage_id':1}]}]}
    result,_=apply_attribution(raw,json.dumps(review),evidence,decode)
    assert '等级（2分钟平均）' in result[0] and '17.2～24.4' in result[0]


def test_no_unit_can_be_borrowed_from_another_table_or_source():
    text='A站水位为2米。B站水位为3厘米。'
    evidence=[{'text':text,'passages':[{'id':1,'text':text}]}]
    raw=json.dumps({'answerability':'partial','answer':'| 站点 | 水位（米） |\n|---|---|\n| B | 3 |'})
    review={'claims':[{'id':1,'status':'supported','refs':[{'citation':1,'passage_id':1}]}]}
    assert apply_attribution(raw,json.dumps(review),evidence,decode) is None


def test_unknown_bare_column_blocks_only_that_row():
    raw=json.dumps({'answerability':'partial','answer':'本次依据尚未齐备。\n\n| 站点 | 水位（米） | 未知列 |\n|---|---|---|\n| A | 2 | 4 |'})
    _,claims=draft_claims(raw)
    assert not any('table_header' in c for c in claims)


def test_unknown_passage_id_is_still_rejected():
    text='风暴潮是海面非周期性异常升降。'
    evidence=[{'text':text,'passages':[{'id':1,'text':text}]}]
    raw=json.dumps({'answerability':'partial','answer':'风暴潮是海面异常升降[资料1]。'})
    review={'claims':[{'id':1,'status':'supported','refs':[{'citation':1,'passage_id':9}]}]}
    assert apply_attribution(raw,json.dumps(review),evidence,decode) is None


def test_unambiguous_single_reference_object_and_string_labels_are_syntax_only():
    text='风暴潮是海面非周期性异常升降。'
    evidence=[{'text':text,'passages':[{'id':1,'text':text}]}]
    raw=json.dumps({'answerability':'partial','answer':'风暴潮是海面异常升降[资料1]。'})
    review={'claims':[{'id':1,'status':'supported','refs':{'citation':'资料1','passage_id':'1'}}]}
    result,_=apply_attribution(raw,json.dumps(review),evidence,decode)
    assert result[2]=={1} and '海面异常升降' in result[0]


def test_bounded_nonnumeric_limitation_synonym_survives_but_affirmation_does_not():
    text='风暴潮是海面非周期性异常升降。'
    evidence=[{'text':text,'passages':[{'id':1,'text':text}]}]
    raw=json.dumps({'answerability':'partial','answer':'风暴潮是海面异常升降[资料1]。\n现有资料未提供本地业务评分，不能把论文指标当作本地准确率。\n现有资料表明今天沿海安全，可以游泳。'})
    review={'claims':[{'id':1,'status':'supported','refs':[{'citation':1,'passage_id':1}]},
                      {'id':2,'status':'limitation','refs':[]},{'id':3,'status':'limitation','refs':[]}]}
    result,_=apply_attribution(raw,json.dumps(review),evidence,decode)
    assert '不能把论文指标' in result[0] and '可以游泳' not in result[0]
