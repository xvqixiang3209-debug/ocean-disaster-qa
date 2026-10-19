"""Explicit inputs classified against a selected official source table."""
import copy
import hashlib
import json

import pytest

from marine_kb.grounded_calculations import add_public_calculations
from marine_kb.qa import _decode_model_answer


QUESTION='按中国热带气旋等级标准，2分钟平均风速分别32.6、32.7、41.4、41.5、50.9、51.0米每秒应分在哪个等级？'
TABLE='''表 1 西北太平洋和南海热带气旋等级划分
热带气旋等级
底层中心附近最大平均风速(2 min平均风)
m/s kt km/h
底层中心附近
最大风力(级)
热带低压 10.8~17.1 22~33 39~62 6~7
热带风暴 17.2~24.4 34~47 63~88 8~9
强热带风暴 24.5~32.6 48~63 89~117 10~11
台风 32.7~41.4 64~80 118~149 12~13
强台风 41.5~50.9 81~99 150~183 14~15
超强台风 ≥51.0 ≥100 ≥184 16及以上
5.2 南太平洋及澳大利亚附近海域
表 2 南太平洋及澳大利亚附近海域热带气旋等级划分
底层中心附近最大平均风速(10 min平均风)
'''


def source(text=TABLE):
    return {'source_id':'official-standard','title':'GB/T 32935—2025 热带气旋等级',
            'text':text,'url':'https://www.cma.gov.cn/standard.pdf','locator':'第8页表1',
            'metadata':{'source_type':'public_web'}}


def test_boundary_values_use_original_ranges_and_preserve_input_provenance():
    hit=source();original=copy.deepcopy(hit)
    records=add_public_calculations(QUESTION,[hit]);assert hit==original
    calc=records[-1]['metadata']['calculation']
    assert [r['category'] for r in calc['results']]==['强热带风暴','台风','台风','强台风','强台风','超强台风']
    assert calc['user_inputs']==['32.6','32.7','41.4','41.5','50.9','51.0']
    assert calc['average_minutes']==2 and not calc['rounding_applied']
    assert calc['source_quote_sha256']==hashlib.sha256(calc['source_quote'].encode()).hexdigest()
    assert '表 2' not in calc['source_quote'] and '10 min' not in calc['source_quote']
    assert calc['source_id']==hit['source_id'] and calc['source_url']==hit['url']
    assert '| 输入风速（m/s，2分钟平均） | 等级 | 所用风速下界（m/s） | 上界（m/s） |' in records[-1]['text']
    assert '| 32.6 | 强热带风暴 | 24.5 | 32.6 |' in records[-1]['text']
    assert '| 51.0 | 超强台风 | 51.0 | 无上界 |' in records[-1]['text']


def test_no_hardcoded_threshold_or_category():
    changed=TABLE.replace('强热带风暴 24.5~32.6','测试类别甲 24.5~32.7').replace('台风 32.7~41.4','测试类别乙 32.8~41.4')
    calc=add_public_calculations(QUESTION,[source(changed)])[-1]['metadata']['calculation']
    assert calc['results'][0]['category']=='测试类别甲' and calc['results'][1]['category']=='测试类别甲'


@pytest.mark.parametrize('question',[
    QUESTION.replace('米每秒','公里每小时'),
    QUESTION.replace('米每秒','节'),
    QUESTION.replace('米每秒',''),
    QUESTION.replace('2分钟','10分钟'),
    QUESTION.replace('平均风速','阵风风速'),
    QUESTION.replace('中国','南太平洋'),
    QUESTION.replace('中国',''),
    QUESTION.replace('32.6、32.7、41.4、41.5、50.9、51.0','32.65'),
    QUESTION.replace('32.6、32.7、41.4、41.5、50.9、51.0','负32.6'),
    QUESTION.replace('32.6、32.7、41.4、41.5、50.9、51.0','32.6至32.7'),
    QUESTION+'另有10分钟平均风速40米每秒。',
])
def test_ambiguous_or_wrong_input_is_not_classified(question):
    assert len(add_public_calculations(question,[source()]))==1


@pytest.mark.parametrize('text',[
    '按中国标准划分热带气旋等级。',
    TABLE.replace('m/s kt km/h','kt m/s km/h'),
    TABLE.replace('2 min平均风','10 min平均风'),
    TABLE.replace('32.7~41.4','32.6~41.4'),
    TABLE.replace('超强台风 ≥51.0 ≥100 ≥184 16及以上',''),
])
def test_missing_table_units_ambiguity_or_missing_class_is_rejected(text):
    assert len(add_public_calculations(QUESTION,[source(text)]))==1


def test_conflicting_selected_tables_and_untrusted_host_are_rejected():
    other=source(TABLE.replace('台风 32.7~41.4','测试类别 32.7~41.4'))
    assert len(add_public_calculations(QUESTION,[source(),other]))==2
    malicious=source();malicious['url']='https://www.cma.gov.cn.evil.example/standard.pdf'
    assert len(add_public_calculations(QUESTION,[malicious]))==1


def test_derived_source_passes_existing_quote_and_quantity_gates():
    row=add_public_calculations(QUESTION,[source()])[-1]
    evidence=[{'text':row['text'],'metadata':row['metadata'],'passages':[{'id':1,'text':row['text']}]}]
    answer='；'.join(r['input']+' m/s属于'+r['category']+'[资料1]' for r in row['metadata']['calculation']['results'])+'。'
    model={'answerability':'full','answer':answer,'evidence_quotes':[{'citation':1,'passage_id':1}]}
    assert _decode_model_answer(json.dumps(model,ensure_ascii=False),evidence)[0]==answer
    model['answer']='60.1 m/s属于超强台风[资料1]。'
    with pytest.raises(ValueError):_decode_model_answer(json.dumps(model,ensure_ascii=False),evidence)


def test_each_value_with_explicit_same_unit():
    q='按西北太平洋热带气旋等级，2分钟平均风速为32.6米每秒、32.7米每秒，应分在哪个等级？'
    r=add_public_calculations(q,[source()])[-1]
    assert r['metadata']['calculation']['user_inputs']==['32.6','32.7']


def test_interior_value_and_natural_reached_word():
    q='按中国热带气旋等级标准，2分钟平均风速为33.0米每秒，达到哪个等级？'
    calc=add_public_calculations(q,[source()])[-1]['metadata']['calculation']
    assert calc['results'][0]['category']=='台风' and calc['user_inputs']==['33.0']
