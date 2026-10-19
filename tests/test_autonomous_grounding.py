import json
import pytest

from marine_kb.qa import _decode_model_answer,_evidence_passages
from marine_kb.quantities import symbol_quantities,validate_quantity_units
from marine_kb.claim_grounding import draft_claims,apply_attribution


def decode(candidate,evidence):
    return _decode_model_answer(json.dumps(candidate,ensure_ascii=False),evidence)


WAVES='海浪级别 有效波高（米） 海浪级别 有效波高（米）\n微浪 Hs＜0.1 巨浪 4.0≤Hs＜6.0\n小浪 0.1≤Hs＜0.5 狂浪 6.0≤Hs＜9.0\n*Hs为有效波高。'


def test_explicit_table_symbol_binds_units_without_guessing_bare_numbers():
    rows=symbol_quantities(WAVES+'\n此处2026年有200人。')
    assert {(str(v),u) for v,u,_,_ in rows}>={('4.0','米'),('6.0','米'),('9.0','米')}
    assert not any(v==200 or v==2026 for v,_,_,_ in rows)
    validate_quantity_units('巨浪为4.0≤Hs<6.0米，狂浪为6.0≤Hs<9.0米[资料1]。',[{'text':WAVES}])
    with pytest.raises(ValueError):validate_quantity_units('狂浪上限为99米[资料1]。',[{'text':WAVES}])


@pytest.mark.parametrize('source',[WAVES.replace('（米）',''),WAVES.replace('*Hs为有效波高。',''),WAVES.replace('≤Hs＜','臆Hs约')])
def test_missing_unit_symbol_or_bad_ocr_does_not_invent_binding(source):
    with pytest.raises(ValueError):validate_quantity_units('狂浪上限为9.0米[资料1]。',[{'text':source}])


def test_table_header_and_definition_survive_passage_boundary():
    text='前言。'*100+WAVES
    passages=_evidence_passages(text)
    assert any(WAVES in p['text'] for p in passages)


def test_pdf_wrapped_unit_is_read_but_wrong_magnitude_still_fails():
    validate_quantity_units('重要信息在1小时内报送[资料1]。',[{'text':'重要信息在１小\n时内报送。'}])
    with pytest.raises(ValueError):validate_quantity_units('重要信息在10小时内报送[资料1]。',[{'text':'重要信息在１小\n时内报送。'}])


def test_explicit_english_duration_is_dimensionally_equivalent_not_a_number_guess():
    validate_quantity_units('预报时长为3天[资料1]。',[{'text':'An hourly sea level forecast over a 3 d horizon.'}])
    with pytest.raises(ValueError):validate_quantity_units('预报时长为30天[资料1]。',[{'text':'An hourly sea level forecast over a 3 d horizon.'}])
    with pytest.raises(ValueError):validate_quantity_units('预报时长为3天[资料1]。',[{'text':'A 3 m forecast surge height.'}])


def fixture():
    text='台风预警提示热带气旋风雨影响，风暴潮警报提示异常海水水位影响。县级在48小时内报送，市级汇总后在24小时内报送。'
    evidence=[{'text':text,'passages':[{'id':1,'text':text}]}]
    raw=json.dumps({'answerability':'partial','answer':'台风预警与风暴潮警报提示不同风险[资料1]。\n县级在48小时内报送[资料1]。\n市级在1小时内报送[资料1]。',
                    'evidence_quotes':[]},ensure_ascii=False)
    review={'claims':[
        {'id':1,'status':'supported','refs':[{'citation':1,'passage_id':1,'quote':'台风预警提示热带气旋风雨影响，风暴潮警报提示异常海水水位影响。'}]},
        {'id':2,'status':'supported','refs':[{'citation':1,'passage_id':1,'quote':'县级在48小时内报送，市级汇总后在24小时内报送。'}]},
        {'id':3,'status':'unsupported','refs':[]}]}
    return evidence,raw,review


def test_repair_keeps_deepseek_language_and_valid_nonnumeric_claims():
    evidence,raw,review=fixture()
    result,audit=apply_attribution(raw,json.dumps(review,ensure_ascii=False),evidence,decode)
    answer,coverage,refs=result
    assert '提示不同风险' in answer and '48小时' in answer and '1小时' not in answer
    assert refs=={1} and coverage=='partial' and audit['retained_claims']==2


@pytest.mark.parametrize('mutate',[
    lambda r:r['claims'][0].update(id=9),
    lambda r:r['claims'].pop(),
])
def test_repair_rejects_unknown_claim_identity_or_incomplete_audit(mutate):
    evidence,raw,review=fixture();mutate(review)
    with pytest.raises(ValueError):apply_attribution(raw,json.dumps(review,ensure_ascii=False),evidence,decode)


@pytest.mark.parametrize('mutate',[
    lambda r:r['claims'][0]['refs'][0].update(quote='模型新生成的资料中不存在的句子。'),
    lambda r:r['claims'][0]['refs'][0].update(citation=8),
    lambda r:r['claims'][0]['refs'][0].update(passage_id=99),
    lambda r:r['claims'][0].update(refs=[{}]*4),
])
def test_invalid_claim_reference_never_publishes_but_other_checked_claims_survive(mutate):
    evidence,raw,review=fixture();mutate(review)
    result,audit=apply_attribution(raw,json.dumps(review,ensure_ascii=False),evidence,decode)
    assert '提示不同风险' not in result[0] and '48小时' in result[0] and '1小时' not in result[0]
    assert audit['decisions'][0]['status']=='rejected_by_reference_check'


def test_model_verdict_cannot_bypass_numeric_gate():
    evidence,raw,review=fixture()
    review['claims'][2].update(status='supported',refs=review['claims'][1]['refs'])
    result,audit=apply_attribution(raw,json.dumps(review,ensure_ascii=False),evidence,decode)
    assert '1小时' not in result[0]
    assert audit['decisions'][2]['status']=='rejected_by_deterministic_check'


def test_repair_cannot_resurrect_rejected_citation_as_limitation():
    evidence,raw,review=fixture()
    for row in review['claims']:row.update(status='limitation',refs=[])
    assert apply_attribution(raw,json.dumps(review,ensure_ascii=False),evidence,decode) is None


def test_literature_progress_gets_academic_routes_not_daily_warning_query():
    from marine_kb.public_sources import research_question,primary_routes
    from marine_kb.web_supplement import public_search_queries
    q='截至2026年10月17日，海洋灾害预警技术有哪些最新进展？'
    assert research_question(q)
    assert any('2177/2026' in row['url'] for row in primary_routes(q))
    assert any('site:gmd.copernicus.org' in query for query in public_search_queries(q,True))
    assert not research_question('2026年10月18日朱家尖今天是否发布了海浪预警？')


def test_future_paper_cannot_bypass_cutoff_as_general_knowledge():
    from marine_kb.evidence_applicability import assess_page
    q='截至2026年10月17日，海洋灾害预警技术有哪些最新进展？如何理解？'
    page={'title':'Sea level forecasting with deep learning','content':'This method forecasts storm surge from tide gauges.',
          'published_at':'2026-10-18','url':'https://gmd.copernicus.org/paper'}
    result=assess_page(q,page,True,now='2026-10-18T18:00:00+08:00')
    assert result['temporal']=='after_cutoff' and result['use']=='excluded'


def test_historical_inundation_routes_to_cases_not_cleaning_advice():
    from marine_kb.web_supplement import public_search_queries
    from marine_kb.public_sources import primary_routes
    q='浙江省玉环市坎门街道这片岸段历史上被淹过吗？最严重是哪一次？'
    queries=public_search_queries(q)
    assert all('坎门' in query and '历史' in query for query in queries)
    assert not any('消毒' in row['title'] for row in primary_routes(q))


def test_review_before_cutoff_accepts_older_published_paper():
    from marine_kb.web_supplement import OfficialWebSupplement
    from typhoon.web_search import SearchOutcome
    class Search:
        def search_with_status(self,*a,**kw):return SearchOutcome(state='success',results=[])
    class Reader:
        def enrich(self,rows):
            return [dict(row,read_status='success',final_url=row['url'],published_at='2025-11-21',
                         content='Computing extreme storm surges in Europe using neural networks. Deep learning forecasts storm surge using observations. Model validation and errors describe limitations of the method.') for row in rows]
    q='截至2026年10月17日，海洋灾害预警技术有哪些最新进展？'
    hits,_=OfficialWebSupplement(Search(),Reader(),enabled=True).fetch(q,dynamic=True)
    assert any(h['metadata']['publication_date']=='2025-11-21' for h in hits)


def test_dated_flood_query_fetches_boundary_inputs_without_claiming_flood_result():
    from forecast_runtime.impact_inputs import query_impact_inputs,impact_inputs_question
    class Runtime:
        def coasts(self):return [{'name':'咸祥镇','County':'鄞州区','Prefecture':'宁波市'},
                                 {'name':'外滩街道','County':'江北区','Prefecture':'宁波市'}]
        def base_result(self,start,end):return {'start':start,'end':end}
        def coastal(self,start,end,region):
            assert region=='鄞州区'
            return {'state':'ready','kind':'coastal','results':[],'requested_count':1,'usable_count':1}
    q='2026年10月18日至21日宁波鄞州区会海水倒灌吗？哪些低洼地段会被淹？'
    assert impact_inputs_question(q)
    result=query_impact_inputs(Runtime(),q,'start','end')
    assert result['items'][0]['requested_metrics']=={'surge':True,'total':True}
    assert result['impact_assessment']['state']=='not_computed'
    assert not impact_inputs_question('历史上鄞州区被淹过吗？')
    assert not impact_inputs_question('如何计算海水倒灌淹没范围？')
    assert not impact_inputs_question('假设2026年10月18日海水倒灌，该怎么办？')
    multi=query_impact_inputs(Runtime(),q+'江北区呢？','start','end')
    assert multi['state']=='needs_location' and not multi['items']


def test_boundary_inputs_cannot_be_promoted_to_inundation_numbers():
    from marine_kb.business_validation import validate_business_delivery
    meta={'required_business_facts':[{'kind':'impact_scope','required':True}]}
    validate_business_delivery('本次未计算淹没，只提供近岸边界参考[资料1]。',meta,{1})
    with pytest.raises(ValueError):validate_business_delivery('该处不会被淹[资料1]。',meta,{1})
    with pytest.raises(ValueError):validate_business_delivery('未计算淹没。但预计淹没深度为2米[资料1]。',meta,{1})


def test_research_excerpt_keeps_results_region_limits_and_code_from_end():
    from marine_kb.research_excerpt import research_excerpt
    body=('Introduction: ocean forecast methods are useful.\n\n'*80+
          'The study area is the Adriatic Sea.\n\n'
          'Inputs include tide gauge observations and satellite altimetry data.\n\n'
          'The proposed method uses a Fourier decoder and neural networks.\n\n'
          'The model achieves a 28.0 percent reduction in mean absolute error.\n\n'
          'However, limitations remain in narrow bays with complex bathymetry.\n\n'
          'Code and data availability: https://github.com/author/project has the source code.\n\n')
    excerpt=research_excerpt(body)
    assert len(excerpt)<=2900
    assert all(value in excerpt for value in ['Adriatic','28.0','narrow bays','github.com/author/project'])
    assert '浙江' not in excerpt
