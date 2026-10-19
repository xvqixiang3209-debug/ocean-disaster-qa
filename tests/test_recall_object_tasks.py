"""Class regressions and changed-wording cases, independent of the 160 IDs."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from datetime import datetime
import pytest

from marine_kb.retrieval import search, rank_candidates
from marine_kb.evidence_selection import select_evidence
from marine_kb.storage import KnowledgeStore
from marine_kb.business_validation import validate_business_delivery
from forecast_runtime.task_plan import activity_tasks, single_target_question
from forecast_runtime.answer_facts import delivery_facts, fact_text, delivery_table
from forecast_runtime.storage import BEIJING


@pytest.mark.parametrize('question',[
    '台风只在夏天有吗？几月到几月要特别防？',
    '浙江的热带气旋通常集中在哪些月份？',
    '福建秋冬还需要防范台风吗？',
    '一年四季都会出现台风吗？',
    '台风高发是什么时候？',
])
def test_season_profile_retrieves_meaning_not_an_incidental_month(tmp_path,question):
    store=KnowledgeStore(tmp_path/'kb.sqlite3')
    for i,(title,body) in enumerate([
        ('年度台风损失记录','夏天的台风造成房屋倒塌，秋季开展防台工作。'),
        ('台风季节科普摘要','台风全年都可能生成，常年活跃月份集中在夏秋季。')]):
        sid,_=store.register(filename=title,relative_path=title,sha256=str(i),size=len(body),raw_path=tmp_path/title,category='科普')
        store.finish(sid,status='approved',quality_status='ok',metadata={'external_allowed':True},warnings=[],chunks=[{'text':body,'locator':'段落1'}])
    hits=search(store,question)
    assert hits and hits[0]['title']=='台风季节科普摘要'
    assert select_evidence(question,hits,limit=1)[0]['title']=='台风季节科普摘要'


@pytest.mark.parametrize('question',[
    '2026年10月18日朱家尖南沙和东沙能游泳吗？',
    '2026年10月18日松兰山岸钓可以吗；渔寮赶海可以吗？',
    '2026年10月18日南沙与东沙分别能赶海吗？地点是舟山朱家尖。',
])
def test_multiple_objects_become_separate_requests(question):
    plan=activity_tasks(question)
    assert plan['strategy']=='per_location' and len(plan['tasks'])==2
    for task in plan['tasks']:
        assert '2026年10月18日' in task['question']
        assert task['object'] in task['question']
        assert all(other['object'] not in task['question'] for other in plan['tasks'] if other!=task)


@pytest.mark.parametrize('place',['广东省朱家尖南沙','广州市朱家尖南沙','连云港市朱家尖南沙'])
def test_conflicting_external_admin_name_never_borrows_a_zhejiang_grid(place):
    from forecast_runtime.activity_locations import resolve_activity_location
    runtime=SimpleNamespace(coasts=lambda:[])
    value=resolve_activity_location(runtime,'2026年10月18日'+place+'能游泳吗？')
    assert value['coordinate_conflict'] and '不套用' in value['coordinate_note']


def test_valid_explicit_zhejiang_admin_name_still_resolves():
    from forecast_runtime.activity_locations import resolve_activity_location
    value=resolve_activity_location(SimpleNamespace(coasts=lambda:[]),'浙江省舟山市朱家尖南沙')
    assert value['key']=='zhujiajian' and value['site_id']=='nansha' and not value.get('coordinate_conflict')


def test_different_dates_and_activities_are_not_combined():
    tasks=activity_tasks('2026年10月18日松兰山岸钓可以吗；2026年10月19日渔寮游泳可以吗？')['tasks']
    assert len(tasks)==2
    assert '18日' in tasks[0]['question'] and '19日' not in tasks[0]['question'] and '游泳' not in tasks[0]['question']
    assert '19日' in tasks[1]['question'] and '18日' not in tasks[1]['question'] and '岸钓' not in tasks[1]['question']


def test_reference_object_is_not_queried_as_a_requested_destination():
    q='2026年10月18日朱家尖南沙浴场能游泳吗？东沙浴场开放的消息能作为依据吗？'
    assert activity_tasks(q)['tasks']==[]
    assert '东沙' not in single_target_question(q)
    assert '南沙' in single_target_question(q)


def test_same_object_different_dates_retains_both_tasks():
    tasks=activity_tasks('2026年10月18日松兰山岸钓；2026年10月19日松兰山岸钓')['tasks']
    assert len(tasks)==2 and '18日' in tasks[0]['question'] and '19日' in tasks[1]['question']


def test_comma_separates_object_specific_activity_predicates():
    tasks=activity_tasks('2026年10月18日松兰山游泳，渔寮岸钓可以吗？')['tasks']
    assert len(tasks)==2 and '岸钓' not in tasks[0]['question'] and '游泳' not in tasks[1]['question']


def test_new_registry_coasts_need_no_question_specific_code():
    coasts=[{'Id':'X','name':'甲乙镇','Prefecture':'宁波市','County':'象山县'},
            {'Id':'Y','name':'丙丁镇','Prefecture':'温州市','County':'苍南县'}]
    tasks=activity_tasks('2026年10月18日甲乙镇赶海，丙丁镇岸钓',coasts=coasts)['tasks']
    assert [task['object_key'] for task in tasks]==['coast:X','coast:Y']
    assert '岸钓' not in tasks[0]['question'] and '赶海' not in tasks[1]['question']


def _result(name,a,b):
    return {'kind':'activity','state':'ready','task_object':name,'start':'2026-10-18T08:00:00+08:00',
            'end':'2026-10-18T18:00:00+08:00','products':[{'product':'WAV','state':'ready','unit':'m',
                'minimum':{'value':a},'maximum':{'value':b},'batch':'2026-10-17T20:00:00+08:00'}]}


def test_object_value_range_and_date_bindings_reject_swapped_or_dropped_rows():
    result={'kind':'bundle','items':[_result('甲浴场',.4,.8),_result('乙浴场',1.2,1.6)]}
    facts=delivery_facts(result)
    good='| 甲浴场 | 10月18日 | 浪高 | 0.4—0.8米[资料1] |\n| 乙浴场 | 10月18日 | 浪高 | 1.2—1.6米[资料1] |'
    validate_business_delivery(good,{'required_business_facts':facts},{1})
    for bad in [good.replace('甲浴场','丙浴场'),good.replace('0.4—0.8','1.2—1.6'),
                good.replace('10月18日','10月19日'),good.replace('0.4—0.8','0.8')]:
        with pytest.raises(ValueError):validate_business_delivery(bad,{'required_business_facts':facts},{1})
    assert '甲浴场' in fact_text(facts) and '乙浴场' in fact_text(facts)


def test_bundle_fallback_keeps_successful_and_failed_children():
    first=_result('甲浴场',.4,.8)
    failed={**first,'products':[],'task_object':'乙浴场','state':'unavailable','reason':'该地海况文件暂不可读。'}
    bundle={'kind':'bundle','items':[first,failed]}
    answer=delivery_table(bundle,delivery_facts(bundle))
    assert '甲浴场' in answer and '0.4 米' in answer and '0.8 米' in answer
    assert '乙浴场' in answer and '文件暂不可读' in answer
    validate_business_delivery(answer,{'required_business_facts':delivery_facts(bundle)},{1})


def test_pending_source_and_wrong_storm_stay_ineligible():
    rows=[{'source_id':'x','chunk_id':1,'title':'台风季节科普','text':'台风全年可能发生，夏秋是活跃期。',
           'status':'pending_review','metadata':{}}]
    assert rank_candidates('台风只在夏天吗？',rows)==[]
    rows[0]['status']='approved'
    assert not rank_candidates('2019年利奇马台风发生在哪个季节？',rows)[0]['retrieval_details']['direct_evidence'] if rank_candidates('2019年利奇马台风发生在哪个季节？',rows) else True


def test_runtime_executes_both_children_even_when_one_fails():
    from forecast_runtime.service import ForecastRuntime
    runtime=SimpleNamespace(coasts=lambda:[],base_result=lambda a,b:{'start':a.isoformat(),'end':b.isoformat()})
    calls=[]
    def query(runtime,question,a,b):
        calls.append((question,a,b))
        if '渔寮' in question:raise OSError('unavailable file')
        return {'kind':'activity','state':'ready'}
    with patch('forecast_runtime.service.query_activity',side_effect=query):
        result=ForecastRuntime.query.__wrapped__(runtime,'2026年10月18日松兰山游泳；2026年10月19日渔寮岸钓可以吗？')
    assert result['kind']=='bundle' and result['state']=='ready'
    assert [row['state'] for row in result['items']]==['ready','unavailable']
    assert calls[0][1].day==18 and calls[1][1].day==19
    assert '岸钓' not in calls[0][0] and '游泳' not in calls[1][0]


def test_concept_comparison_does_not_request_a_grid_point():
    from forecast_runtime.routing import numeric_request
    from forecast_runtime.service import ForecastRuntime
    q='有效波高和肉眼看到的最大一阵浪有什么不同？'
    assert not numeric_request(q)
    runtime=SimpleNamespace(config=lambda:{'enabled':True})
    assert not ForecastRuntime.matches(runtime,q)
    assert numeric_request('2026年10月18日朱家尖最大有效波高多少？有效波高是什么意思？')


def test_child_search_preserves_event_identity_and_never_assigns_an_ambiguous_pronoun():
    from marine_kb.query_intents import child_queries
    assert child_queries('2019年利奇马造成哪些损失？防御措施有哪些？',[{'question':'防御措施有哪些'}])==['2019年利奇马 防御措施有哪些']
    assert child_queries('比较2019年利奇马与2013年菲特',[{'question':'它的损失多少'}])==[]


def test_wrong_object_prose_cannot_be_hidden_by_an_appended_correct_table():
    from marine_kb.business_validation import validate_object_claims
    facts=delivery_facts({'kind':'bundle','items':[_result('甲浴场',.4,.8),_result('乙浴场',1.2,1.6)]})
    validate_object_claims('甲浴场有效波高0.8米[资料1]。',facts)
    validate_object_claims('甲浴场有效波高0.8米，高潮水位370厘米[资料1]。',facts)
    temp=_result('甲浴场',24.22,24.38)
    temp['products'][0].update(product='SST',unit='Celsis D')
    validate_object_claims('甲浴场营业不代表游泳开放。游泳适用条款水温20℃至28℃为适宜[资料1]。',delivery_facts(temp))
    validate_object_claims('甲浴场适用游泳规则，水温20℃至28℃为适宜[资料1]。',delivery_facts(temp))
    with pytest.raises(ValueError,match='object_claim_mismatch'):
        validate_object_claims('甲浴场有效波高1.6米[资料1]。',facts)


def test_source_temperature_alias_retains_assumption_and_display_unit():
    from forecast_runtime.answer_facts import fact_table
    result=_result('甲浴场',24.22,24.38)
    result['products'][0].update(product='SST',unit='Celsis D')
    facts=delivery_facts(result)
    assert facts[0]['source_unit']=='Celsis D' and facts[0]['unit_assumed'] and facts[0]['unit']=='℃'
    table=fact_table(facts)
    assert '单位按现有假设解释' in table
    validate_business_delivery(table,{'required_business_facts':facts},{1})


def test_model_explanation_keeps_independent_trusted_data_view():
    import json
    from marine_kb.qa import MarineQuestionService
    from forecast_runtime.answer_facts import fact_text
    facts=delivery_facts({'kind':'bundle','items':[_result('甲浴场',.4,.8),_result('乙浴场',1.2,1.6)]})
    text=fact_text(facts)+'甲浴场与乙浴场尚未取得活动开放公告，不能确认允许游泳。'
    raw=json.dumps({'answerability':'partial','answer':'两浴场尚未取得活动开放公告，不能确认允许游泳[资料1]。',
                    'evidence_quotes':[{'citation':1,'passage_id':1}],'missing_information':['活动开放公告']})
    qa=MarineQuestionService(None,external_enabled=True,model_caller=Mock(return_value=raw))
    hit={'source_id':'forecast:case','text':text,'metadata':{'source_type':'business_forecast','external_allowed':True}}
    meta={'as_of':'2026-10-18','subquestions':[],'required_business_facts':facts,'tool_data_view':True,'business_state':'ready'}
    result=qa._organized_answer('两浴场能游泳吗？','test',[hit],meta)
    assert result['answer_meta']['used_mode']=='llm'
    assert '本次查询数据' in result['answer'] and '甲浴场' in result['answer'] and '乙浴场' in result['answer']
    validate_business_delivery(result['answer'],meta,{1})


def test_complete_model_table_is_not_duplicated():
    import json
    from marine_kb.qa import MarineQuestionService
    from forecast_runtime.answer_facts import fact_table
    facts=delivery_facts(_result('甲浴场',.4,.8))
    answer=fact_table(facts)
    raw=json.dumps({'answerability':'partial','answer':answer,
                    'evidence_quotes':[{'citation':1,'passage_id':1}],'missing_information':[]})
    qa=MarineQuestionService(None,external_enabled=True,model_caller=Mock(return_value=raw))
    hit={'source_id':'forecast:case','text':answer,'metadata':{'source_type':'business_forecast','external_allowed':True}}
    meta={'as_of':'2026-10-18','subquestions':[],'required_business_facts':facts,'tool_data_view':True,'business_state':'ready'}
    result=qa._organized_answer('甲浴场的浪高多少？','test',[hit],meta)
    assert result['answer'].count('| 甲浴场 |')==1
