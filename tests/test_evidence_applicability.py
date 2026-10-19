"""Class regressions: substitute places, dates, facilities and question forms."""
from datetime import datetime
import pytest

from marine_kb.evidence_applicability import (assess_page, coverage_matrix, parse_date,
    question_context, reconcile_notices, CN)
from marine_kb.web_supplement import public_search_queries
from marine_kb.web_supplement import OfficialWebSupplement, REFERENCE_PAGES
from typhoon.web_search import SearchOutcome
from marine_kb.evidence_selection import select_evidence

NOW = datetime(2026, 10, 18, 12, tzinfo=CN)


def page(title, body, published='2026-10-18T07:00:00+08:00', **kwargs):
    return dict(title=title, content=body, published_at=published, **kwargs)


@pytest.mark.parametrize('asked,other', [('朱家尖','松兰山'),('松兰山','渔寮'),('渔寮','基湖'),('基湖','朱家尖')])
@pytest.mark.parametrize('action', ['开放','关闭','复航'])
def test_other_site_cannot_establish_status(asked, other, action):
    result=assess_page(f'2026年10月18日{asked}浴场是否{action}？',
        page(f'{other}浴场{action}公告', f'{other}浴场于2026年10月18日至2026年10月19日{action}。'), True, NOW)
    assert result['use'] != 'direct'


@pytest.mark.parametrize('object_a,object_b', [('客运船','休闲渔船'),('休闲渔船','客运船'),('浴场','客运船')])
def test_business_objects_not_interchangeable(object_a,object_b):
    result=assess_page(f'今天舟山{object_a}开放吗？',
        page(f'舟山{object_b}开放通知', f'2026年10月18日至2026年10月19日舟山{object_b}开放。'),True,NOW)
    assert result['object_match']=='mismatch'
    assert result['use']=='excluded'


def test_same_city_different_bathing_facility():
    result=assess_page('2026年10月18日朱家尖南沙浴场开放吗？',
        page('朱家尖东沙浴场开放公告','2026年10月18日至2026年10月19日朱家尖东沙浴场开放。'),True,NOW)
    assert result['named_object_match']=='mismatch'
    assert result['use']=='excluded'


def test_recent_publication_expired_forecast():
    result=assess_page('2026年10月18日舟山海浪如何？',page('舟山海浪预报',
        '有效时段2026年10月17日00时至2026年10月17日23时，浪高1米。'),True,NOW)
    assert result['temporal']=='expired'
    assert result['use']=='excluded'


def test_old_publication_covering_target_is_not_discarded_by_72_hours():
    result=assess_page('2026年10月18日舟山海浪如何？',page('舟山海浪预报',
        '有效时段2026年10月18日至2026年10月19日，浪高1米。',published='2026-10-14'),True,NOW)
    assert result['temporal']=='covers_target'
    assert result['use']=='direct'


def test_forecast_partial_validity_is_not_full():
    result=assess_page('2026年10月18日至2026年10月20日舟山海浪如何？',page('舟山海浪预报',
        '有效时段2026年10月18日至2026年10月19日，浪高1米。'),True,NOW)
    assert result['temporal']=='partly_covers_target'
    assert result['use']=='reference'


@pytest.mark.parametrize('kind,title', [('policy','浙江省海洋灾害应急预案'),('operation','朱家尖南沙浴场开放公告'),('warning','舟山海浪警报')])
def test_future_publication_relative_to_historical_cutoff(kind,title):
    result=assess_page('截至2026年10月17日21时30分，朱家尖南沙浴场开放吗？',
        page(title,'适用于朱家尖南沙。有效时段2026年10月17日至2026年10月19日。'),True,NOW)
    assert result['temporal']=='after_cutoff'
    assert result['use']=='excluded'


def test_read_timestamp_does_not_establish_publication():
    result=assess_page('今天舟山海浪预报？',page('舟山海浪预报','今天舟山海浪较大。',published=None,
        read_at=NOW.isoformat()),True,NOW)
    assert result['temporal']=='publication_unknown'
    assert result['use']=='reference'


@pytest.mark.parametrize('region', ['浙江','福建','广东','上海'])
def test_old_policy_not_expired_just_for_age(region):
    result=assess_page(f'{region}现行海洋灾害应急预案？',page(f'{region}海洋灾害应急预案',
        f'本预案适用于{region}。由指挥部批准终止。',published='2019-01-01'),True,NOW)
    assert result['use']=='reference'
    assert result['temporal']=='version_unverified'


def test_explicit_verified_effectiveness_metadata():
    result=assess_page('浙江现行应急预案？',page('浙江海洋灾害应急预案', '本预案适用于浙江。',
        published='2019-01-01',metadata={'effectiveness':'current_verified'}),True,NOW)
    assert result['temporal']=='current_verified'
    assert result['use']=='direct'


def test_national_rule_framework_does_not_claim_local_opening():
    p=page('国家海洋灾害应急预案','本预案适用于全国。省级单位组织应急工作。',published='2025-01-01')
    result=assess_page('今天朱家尖南沙浴场开放吗？',p,True,NOW)
    assert result['geography']=='national_framework'
    assert result['use']=='reference'


def test_general_definitions_can_use_national_or_other_region_science():
    result=assess_page('浙江居民如何理解风暴增水？',page('福建海洋知识科普',
        '风暴增水是由风和气压造成的海面水位升高。',published='2010-01-01'),False,NOW)
    assert result['use']=='direct'


def test_local_midnight_and_timezone_offsets():
    assert parse_date('2026-10-18T00:00:00+08:00')==parse_date('2026-10-17T16:00:00Z')
    assert parse_date('2026年10月18日').utcoffset().total_seconds()==28800


def test_cutoff_and_forecast_target_are_separate():
    context=question_context('截至2026年10月17日21时30分，预报2026年10月18日至2026年10月20日舟山海浪？',True,NOW)
    assert context['as_of']==datetime(2026,10,17,21,30,tzinfo=CN)
    assert context['target_start'].day==18


def test_query_slots_retain_full_date_place_and_each_object():
    query=public_search_queries('2026年10月18日朱家尖南沙浴场开放吗？休闲渔船可以出海吗？', True,
        ['南沙浴场开放吗？','休闲渔船可以出海吗？'])
    assert all('2026年10月18日' in row and '朱家尖' in row for row in query)
    assert any('浴场' in row for row in query)
    assert any('休闲渔船' in row for row in query)


def hit(title,text,**extra):
    p=page(title,text,**extra)
    return {'title':title,'text':text,'url':'https://www.example.gov.cn/'+title,
            'metadata':{'source_type':'public_web','applicability':assess_page(
                '2026年10月18日朱家尖南沙浴场开放吗？',p,True,NOW)}}


def test_notice_precedence_same_facility_only():
    old=hit('朱家尖南沙浴场关闭公告','朱家尖南沙浴场自2026年10月17日起关闭。',published='2026-10-17T06:00+08:00')
    other=hit('朱家尖东沙浴场开放公告','朱家尖东沙浴场自2026年10月18日起开放。')
    reconcile_notices([old,other])
    assert old['metadata']['applicability']['temporal']!='superseded_in_retrieved_evidence'
    new=hit('朱家尖南沙浴场开放公告','朱家尖南沙浴场自2026年10月18日起开放。')
    reconcile_notices([old,new])
    assert old['metadata']['applicability']['use']=='excluded'
    assert old['metadata']['applicability']['superseded_by']==new['url']


def test_subquestion_coverage_requires_decision_and_announcement_text():
    question='浙江五停什么条件启动？由谁决定？怎么公告？'
    parts=['启动条件是什么？','由谁决定？','怎么公告？']
    hits=[{'title':'浙江五停指引','text':'五停启动条件为预警达到相应等级。'}]
    matrix=coverage_matrix(question,parts,hits)
    assert matrix[0]['state']=='candidate_evidence'
    assert matrix[1]['state']==matrix[2]['state']=='not_found'


def test_selection_keeps_facets_with_different_sources():
    hits=[{'title':'五停启动条件','text':'五停启动条件按预警等级采取措施。'},
          {'title':'五停决定权限','text':'五停由属地政府决定，由指挥部批准。'},
          {'title':'五停公告发布','text':'五停通过网站和广播向社会发布通告。'}]
    selected=select_evidence('五停条件由谁决定怎么公告？',hits,
        ['启动条件是什么？','由谁决定？','怎么公告？'],limit=3)
    assert len(selected)==3


@pytest.mark.parametrize('facility', ['海州湾','大梅沙','金沙湾','碧海湾'])
def test_new_facilities_do_not_require_question_specific_code(facility):
    result=assess_page(f'2026年10月18日{facility}浴场开放吗？',
        page('松兰山浴场开放公告','松兰山浴场2026年10月18日至2026年10月19日开放。'),True,NOW)
    assert result['use']=='excluded'


def test_current_warning_is_a_time_instant_not_a_whole_day():
    result=assess_page('当前舟山海浪警报？',page('舟山海浪警报',
        '预警有效时段2026年10月18日07时至2026年10月18日19时。'),True,NOW)
    assert result['temporal']=='covers_target'
    assert result['use']=='direct'


def test_future_24_hour_duration_uses_issuance_time():
    result=assess_page('当前舟山海浪警报？',page('舟山海浪警报','预计未来24小时舟山海浪较大。'),True,NOW)
    assert result['temporal']=='covers_target'


def test_explicit_repeal_status_is_not_confused_with_repealing_previous_version():
    current=assess_page('浙江现行海洋灾害应急预案？',page('浙江海洋灾害应急预案',
        '有效性：有效。本预案自2025年1月1日起实施，原2019年预案同时废止。',published='2025-01-01'),True,NOW)
    retired=assess_page('浙江现行海洋灾害应急预案？',page('浙江海洋灾害应急预案',
        '有效性：废止。本预案适用于浙江。',published='2019-01-01'),True,NOW)
    assert current['use']=='direct'
    assert retired['use']=='excluded'


def test_difference_word_is_not_an_administrative_district():
    context=question_context('台风、风暴潮、海浪、海啸到底有什么区别？作为浙江沿海居民，我该关心哪一个？')
    assert context['places']==['浙江']


def test_geography_prefix_does_not_disable_primary_definition_pages():
    seen=[]
    class Search:
        def search_with_status(self,*args,**kwargs):
            return SearchOutcome(state='unavailable')
    class Reader:
        def enrich(self,rows):
            seen.extend(row['url'] for row in rows)
            return [dict(row,read_status='success',content='台风是热带气旋。风暴潮是海面异常升高。海啸由地震引起。海浪是波动。'*2) for row in rows]
    OfficialWebSupplement(Search(),Reader(),enabled=True).fetch(
        '浙江居民怎么理解台风、风暴潮、海浪、海啸的区别？',
        subquestions=['四种现象有什么区别？','浙江居民应关注什么？'])
    for term in ('台风','风暴潮','海啸','海浪'):
        assert REFERENCE_PAGES[term][1] in seen


def test_comparison_binds_jurisdiction_to_each_subquestion():
    source=page('浙江海洋灾害应急预案','有效性：有效。本预案适用于浙江。由指挥部决定。',published='2025-01-01')
    result=assess_page('浙江和福建分别由谁决定响应终止？',source,False,NOW,subquestion='浙江由谁决定响应终止？')
    assert result['geography']=='exact'
    assert result['use']=='direct'


def test_scope_limits_reach_model_and_block_false_full_coverage():
    import json
    from marine_kb.qa import MarineQuestionService
    from test_marine_qa import FakeStore,source
    question='今天朱家尖南沙浴场开放吗？'
    assessment=assess_page(question,page('朱家尖南沙浴场开放公告','朱家尖南沙浴场曾经开放，未说明有效时段。'),True,NOW)
    item=source('朱家尖南沙浴场曾经开放，未说明有效时段。',source_id='web:other',chunk_id='web:other',
        title='朱家尖南沙浴场开放公告',url='https://example.gov.cn/notice',status='public_web',
        metadata={'external_allowed':True,'source_type':'public_web','applicability':assessment})
    requests=[]
    def caller(model,system,prompt,**kwargs):
        payload=json.loads(prompt);requests.append(payload)
        assert payload['evidence'][0]['metadata']['applicability']['use']=='reference'
        assert payload['evidence_coverage'][0]['candidate_citations']==[]
        if len(requests)==1:
            return json.dumps({'answerability':'full','answer':'朱家尖南沙浴场开放。[资料1]',
                'evidence_quotes':[{'citation':1,'passage_id':1}],
                'subquestion_coverage':[{'id':1,'status':'full','citations':[1]}]})
        assert payload['validation_feedback']=='subquestion_evidence_scope_mismatch'
        return json.dumps({'answerability':'partial',
            'answer':'资料没有说明开放有效时段，不能据此确认朱家尖南沙浴场今天开放。[资料1]',
            'evidence_quotes':[{'citation':1,'passage_id':1}],
            'missing_information':['缺少朱家尖南沙浴场当日正式开放公告。'],
            'subquestion_coverage':[{'id':1,'status':'partial','citations':[1],
                'missing_information':['缺少对应浴场当日公告。']}]})
    qa=MarineQuestionService(FakeStore(),model_caller=caller,external_enabled=True,structured_answers=True)
    result=qa._organized_answer(question,qa.model_name,[item],
        {'as_of':NOW.isoformat(),'subquestions':[{'id':1,'question':question}]})
    assert result['answer_meta']['answerability']=='partial'
    assert result['answer_meta']['generation_attempts']==2
    assert result['web_sources'][0]['metadata']['applicability']['use']=='reference'


def test_one_way_route_cannot_be_replaced_by_reverse_direction():
    result=assess_page('2026年10月18日白峰至鸭蛋山航线客运复航了吗？',
        page('舟山鸭蛋山至白峰航线客运复航公告',
             '2026年10月18日至2026年10月19日，鸭蛋山至白峰航线客运复航。'),True,NOW)
    assert result['named_object_match']=='mismatch'
    assert result['use']=='excluded'


@pytest.mark.parametrize('route', ['白峰至鸭蛋山航线','白峰到鸭蛋山的客运'])
def test_same_direction_route_allows_optional_city_prefix(route):
    result=assess_page(f'2026年10月18日{route}客运复航了吗？',
        page('舟山白峰至鸭蛋山航线客运复航公告',
             '2026年10月18日至2026年10月19日，白峰至鸭蛋山航线客运复航。'),True,NOW)
    assert result['named_object_match']=='matched'


def test_compound_facility_query_keeps_source_for_its_own_subquestion():
    class Search:
        def search_with_status(self,*args,**kwargs):
            return SearchOutcome(state='success',results=[{'title':'朱家尖南沙浴场开放公告','url':'https://example.gov.cn/nansha'}])
    class Reader:
        def enrich(self,rows):
            return [dict(row,read_status='success',published_at='2026-10-18T07:00:00+08:00',
                content='朱家尖南沙浴场于2026年10月18日至2026年10月19日开放，请游客留意现场管理安排。') for row in rows]
    hits,meta=OfficialWebSupplement(Search(),Reader(),enabled=True,reference_pages={}).fetch(
        '2026年10月18日朱家尖南沙浴场开放吗？朱家尖东沙浴场开放吗？',dynamic=True,
        subquestions=['朱家尖南沙浴场开放吗？','朱家尖东沙浴场开放吗？'])
    assert hits
    assert meta['web_evidence_coverage'][0]['candidate_citations']
    assert not meta['web_evidence_coverage'][1]['candidate_citations']
