import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from marine_kb.model_json import model_json
from marine_kb.question_contract import question_contract
from marine_kb.public_sources import primary_routes
from marine_kb.evidence_selection import select_evidence
from marine_kb.web_supplement import OfficialWebSupplement,public_search_queries,trusted_url
from marine_kb.quantities import validate_quantity_units,table_quantities
from marine_kb.qa import _decode_model_answer,_evidence_passages
from typhoon.web_reader import _ArticleParser
from typhoon.web_search import SearchOutcome
from forecast_runtime.service import live_typhoon_question
from forecast_runtime.answer_facts import delivery_facts,fact_table
from marine_kb.grounded_calculations import add_public_calculations
from marine_kb.business_validation import validate_business_delivery


def test_footer_layout_wrapper_does_not_hide_main_article():
    parser=_ArticleParser()
    parser.feed('<html><body><div class="flex-wrap-footer"><header>导航栏目</header><main><h1>论文题目</h1><blockquote>真实论文摘要与方法，应进入正文且保持原文。</blockquote></main><footer>联系地址</footer></div></body></html>')
    result=parser.extract()
    assert '真实论文摘要' in result['content'] and '联系地址' not in result['content']


def test_scholarly_publication_date_is_retained():
    parser=_ArticleParser();parser.feed('<meta name="citation_date" content="2024/01/29"><main>具有明确发表日期的论文正文和方法介绍。</main>')
    assert parser.extract()['published_at']=='2024/01/29'


def test_national_guide_query_not_forced_to_a_district():
    queries=public_search_queries('鄞州区三口之家应急储备多少水和粮？')
    assert any('家庭' in q and '清单' in q and '鄞州' not in q for q in queries)
    current=public_search_queries('2026年10月18日朱家尖南沙浴场开放吗？休闲渔船可以出海吗？',True,['南沙浴场开放吗？','休闲渔船可以出海吗？'])
    assert all('2026年10月18日' in q and '朱家尖' in q for q in current)


def test_academic_domain_requires_method_context_and_rejects_spoof():
    assert not trusted_url('https://arxiv.org/abs/2401.15884')
    assert trusted_url('https://arxiv.org/abs/2401.15884',True)
    assert not trusted_url('https://arxiv.org.evil.example/a',True)
    assert not trusted_url('https://arxiv.org:8080/a',True)


class Search:
    def __init__(self):self.subjects=[]
    def search_with_status(self,query,**kwargs):
        self.subjects.append(kwargs.get('subject'))
        return SearchOutcome(state='success',attempts=[{'state':'success'}],results=[{'title':'台风来了，这份安全攻略请收好_福建省人民政府门户网站','url':'https://www.fj.gov.cn/news/article.html'}])


class Reader:
    def enrich(self,rows):
        return [dict(row,read_status='success',final_url=row['url'],content='台风眼过境风会暂时减弱，不等于台风已经过去；居民不要因暂时风小而外出。') for row in rows]


def test_portal_suffix_is_an_article_not_a_homepage():
    search=Search();hits,meta=OfficialWebSupplement(search,Reader(),enabled=True,reference_pages={}).fetch('台风眼过境风突然停了是过去了吗？')
    assert hits and not meta['web_excluded_sources']
    assert search.subjects and all(s=='' for s in search.subjects)


def test_supply_evidence_need_not_repeat_typhoon():
    page=next(row for row in primary_routes('台风前储备多少饮用水？') if 'mem.gov.cn' in row['url'])
    hit={**page,'text':'饮用水：保障每人3天基本饮水需求，至少3升/人。食品保障每人3天需求。','metadata':{'primary_scope':'general_safety'}}
    assert select_evidence('台风前储备多少饮用水？',[hit])


def test_english_method_paper_not_rejected_for_missing_chinese_marine_words():
    page=next(row for row in primary_routes('海洋灾害RAG知识库怎么构建？') if '2401.15884' in row['url'])
    hit={**page,'text':'Corrective Retrieval Augmented Generation evaluates retrieved documents and retrieves web evidence when retrieval quality is poor.','metadata':{'primary_scope':'research_method'}}
    assert select_evidence('海洋灾害RAG知识库怎么构建？',[hit])


def test_source_reader_completes_unattempted_candidates_with_bound():
    class ManySearch:
        def search_with_status(self,*args,**kwargs):
            return SearchOutcome(state='success',results=[{'title':'台风科普','url':f'https://www.cma.gov.cn/article{i}.html'} for i in range(4)])
    class BoundedReader:
        def __init__(self):self.calls=[]
        def enrich(self,rows):
            self.calls.append(len(rows))
            return [dict(row,read_status='success' if i<2 else 'not_attempted',final_url=row['url'],content='台风是热带气旋，台风眼风较弱但随后眼壁风会增强，不能因此外出。' if i<2 else '') for i,row in enumerate(rows)]
    reader=BoundedReader();_,meta=OfficialWebSupplement(ManySearch(),reader,enabled=True,reference_pages={}).fetch('什么是台风？')
    assert len(reader.calls)==2 and reader.calls[1]==reader.calls[0]-2
    assert not meta['web_body_read_failures']


def test_model_newline_json_recovery_keeps_grounding_checks():
    raw='{"answerability":"full","answer":"最大增水2米[资料1]。\n请核对来源。","evidence_quotes":[{"citation":1,"passage_id":1}]}'
    evidence=[{'text':'该次最大增水为2米，按原资料记载。','passages':[{'id':1,'text':'该次最大增水为2米，按原资料记载。'}]}]
    assert '\n' in model_json(raw)['answer']
    assert _decode_model_answer(raw,evidence)[1]=='full'
    with pytest.raises(ValueError):_decode_model_answer(raw.replace('2米','99米'),evidence)
    with pytest.raises(ValueError):model_json('{"answer": missing}')


def test_explicit_table_unit_headers_support_ranges_but_not_wrong_values():
    text='底层中心附近最大平均风速\nm/s kt km/h\n底层中心附近\n最大风力(级)\n热带风暴 17.2~24.4 34~47 63~88 8~9\n强热带风暴 24.5~32.6 48~63 89~117 10~11'
    assert table_quantities(text)
    validate_quantity_units('热带风暴17.2至24.4 m/s[资料1]',[{'text':text}])
    validate_quantity_units('热带风暴8至9级[资料1]',[{'text':text}])
    with pytest.raises(ValueError):validate_quantity_units('热带风暴99 m/s[资料1]',[{'text':text}])
    with pytest.raises(ValueError):validate_quantity_units('热带风暴24.4 m/s[资料1]',[{'text':text.replace('m/s kt km/h','数值 结果 编号')}])


def test_scenario_and_method_contracts_do_not_require_proof_of_occurrence():
    assert question_contract('假设鄞州区家里被淹，怎么清理？')['mode']=='scenario'
    assert question_contract('什么是风暴潮？')['mode']=='method_or_knowledge'
    assert question_contract('截至2026年10月17日开放了吗？')['mode']=='dated_facts'


@pytest.mark.parametrize('tail',['会不会突然加强？什么时候加强？','对宁波影响大吗？','将登陆宁波有依据吗？'])
def test_dated_storm_strength_and_impact_use_available_live_source(tail):
    assert live_typhoon_question('截至2026年10月17日，台风蔷琵'+tail)


def test_typhoon_facts_preserve_forecast_institution_and_issue_time():
    result={'kind':'typhoons','state':'ready','requested_forecast':True,'list':[{'typhoonID':202630,'nameCN':'蔷琵','points':[
        {'time':'2026-10-17 20:00:00','maxSpeed':55,'forecast':{'甲机构':[{'time':'2026-10-18 20:00:00','maxSpeed':60}],
             '乙机构':[{'time':'2026-10-18 20:00:00','maxSpeed':50}]}}]}]}
    facts=delivery_facts(result);assert len(facts)==3
    assert [f['value'] for f in facts]==[55,60,50]
    assert all(f['batch']=='2026-10-17 20:00:00' for f in facts)
    text=fact_table(facts);assert '甲机构' in text and '乙机构' in text and '60 m/s' in text
    validate_business_delivery(text,{'required_business_facts':facts},{1})


@pytest.mark.parametrize('people,phrase',[(3,'三口之家'),(5,'一家五口'),(7,'7人的家庭')])
def test_family_total_is_computed_from_people_and_source_total_not_daily(people,phrase):
    hit={'source_id':'official:guide','title':'全国基础版家庭清单','text':'饮用水：保障每人3天基本饮水需求，至少3升/人。',
         'metadata':{'source_type':'public_web'},'url':'https://www.mem.gov.cn/guide.html'}
    result=add_public_calculations(phrase+'台风前储备多少饮用水？',[hit])
    assert result[-1]['metadata']['calculation']['family_total_litres']==str(people*3)
    assert result[-1]['metadata']['calculation']['days']==3
    assert '不是每天3升' in result[-1]['text']
    assert len(add_public_calculations('家里储备多少水？',[hit]))==1
    assert len(add_public_calculations(phrase+'储备多少水？',[{**hit,'text':'每天3升/人'}]))==1


def test_numeric_identifier_targets_the_matching_source_paragraph():
    from marine_kb.web_supplement import focused_public_text
    body='本预案适用于本省水上交通救援。\n\n海上搜救部门职责是指导救援力量。\n\n'+'一般背景文字。'*200+'\n\n统一使用水上搜救专用电话号码12395，24小时守听。'
    text=focused_public_text(body,'12395是什么电话？',['12395是什么电话？'])
    assert '电话号码12395' in text


def test_versioned_product_overview_does_not_certify_current_rules():
    from marine_kb.governance import reference_scope_allowed
    item={'metadata':{'review_scope':'versioned_reference','edition_current_verified':False}}
    assert reference_scope_allowed('预警预报产品有哪些格式和业务标准？',item)
    assert not reference_scope_allowed('截至2026年10月17日现行标准有哪些？',item)
    assert not reference_scope_allowed('当前必须达到什么警戒潮位阈值？',item)


def test_source_concentration_and_wind_averaging_time_keep_units():
    evidence=[{'text':'有效氯500 mg/L，作用30分钟。中心最大平均风速采用2 min平均。'}]
    validate_quantity_units('原文为500毫克/升、30分钟；风速为2分钟平均[资料1]',evidence)
    with pytest.raises(ValueError):validate_quantity_units('有效氯5000 mg/L[资料1]',evidence)
    with pytest.raises(ValueError):validate_quantity_units('500 mg/L[资料1]',[{'text':'500厘米'}])


def test_primary_body_cache_keeps_original_read_time_and_does_not_cache_notices(tmp_path):
    from marine_kb.public_cache import PublicReferenceReader
    reader=Mock()
    reader.enrich.side_effect=lambda rows:[{**row,'read_status':'success','final_url':row['url'],
        'read_at':'2026-10-18T01:00:00+00:00','content':'官方原始科普正文及规则内容，保持出处、读取时间和原始内容哈希用于复核。'} for row in rows]
    cached=PublicReferenceReader(reader,tmp_path)
    row={'url':'https://www.mem.gov.cn/guide.html','_primary_scope':'general_safety'}
    cached.enrich([row]);result=cached.enrich([row])
    assert reader.enrich.call_count==1 and result[0]['read_from_cache']
    assert result[0]['read_at']=='2026-10-18T01:00:00+00:00'
    notice={'url':'https://www.mem.gov.cn/today.html'}
    cached.enrich([notice]);cached.enrich([notice])
    assert reader.enrich.call_count==3


def test_primary_body_cache_rejects_corrupted_body(tmp_path):
    from marine_kb.public_cache import PublicReferenceReader
    reader=Mock();reader.enrich.side_effect=lambda rows:[{**row,'read_status':'success','content':'这是一份可核对的官方知识正文，内容足够长并附有原始读取时间和说明。'} for row in rows]
    cached=PublicReferenceReader(reader,tmp_path)
    row={'url':'https://www.mem.gov.cn/guide.html','_primary_scope':'general_safety'}
    cached.enrich([row]);path=next(tmp_path.glob('*.json'));data=json.loads(path.read_text(encoding='utf8'))
    data['page']['content']='被改写的内容';path.write_text(json.dumps(data),encoding='utf8')
    cached.enrich([row]);assert reader.enrich.call_count==2


def test_table_numeric_cells_require_inline_citation_and_explicit_column_unit():
    from marine_kb.qa import _validate_model_answer
    source=[{'text':'热带风暴17.2至24.4 m/s，8至9级。'}]
    table='| 等级 | 风速(m/s) | 风力(级) |\n|---|---|---|\n| 热带风暴 | 17.2~24.4 | 8~9 [资料1] |'
    _validate_model_answer(table,source)
    _validate_model_answer(table.replace('17.2~24.4','17.2-24.4').replace('8~9','8-9'),source)
    with pytest.raises(ValueError,match='uncited_quantity'):_validate_model_answer(table.replace(' [资料1]','')+'\n依据见[资料1]。',source)
    with pytest.raises(ValueError,match='quantity_unit'):_validate_model_answer(table.replace('17.2~24.4','17.2~44.4'),source)


def test_coverage_critic_cannot_manufacture_a_span_or_mark_unknown_fields():
    from marine_kb.coverage_review import decode_review
    answer='这些资料支持按步骤核查来源，但没有当前开放信息。'
    result=decode_review(json.dumps({'verdict':'revise','issues':[{'kind':'unasked_gap','text':'正式工程规范','reason':'方法题未要求已采纳的规范'}]}),answer,['缺少正式工程规范'])
    assert result['issues'][0]['kind']=='unasked_gap'
    with pytest.raises(ValueError):decode_review(json.dumps({'verdict':'revise','issues':[{'kind':'unsupported_claim','text':'编造的新句子','reason':'错误'}]}),answer,[])


def test_failed_number_cannot_destroy_separate_supported_clause():
    from marine_kb.qa import _recover_supported_numbers
    evidence=[{'text':'县级在48小时内报送，市级汇总后在24小时内报送。','passages':[{'id':1,'text':'县级在48小时内报送，市级汇总后在24小时内报送。'}]}]
    raw=json.dumps({'answerability':'partial','answer':'重要信息在1小时内报送；县级在48小时内报送，市级汇总后在24小时内报送[资料1]。',
        'evidence_quotes':[{'citation':1,'passage_id':1}]},ensure_ascii=False)
    answer,coverage,refs=_recover_supported_numbers(raw,evidence)
    assert '48小时' in answer and '24小时' in answer and '1小时' not in answer
    assert coverage=='partial' and refs=={1}
    assert _recover_supported_numbers(raw.replace('48小时','99小时').replace('24小时','88小时'),evidence) is None


def test_typhoon_archive_uses_proven_retrieval_cutoff_and_never_a_future_snapshot(tmp_path):
    from forecast_runtime.storage import ForecastStore,BEIJING
    from datetime import datetime
    store=ForecastStore(tmp_path)
    value={'source':'浙江海洋预报网','source_url':'https://zjocean.org.cn/mapi_web/index.php/typhoon/index/activetyphoons',
        'retrieved_at':'2026-10-17T21:00:00+08:00','list':[{'typhoonID':202630,'nameCN':'蔷琵','points':[
         {'time':'2026-10-17 20:00:00','maxSpeed':52,'forecast':{'甲机构':[{'time':'2026-10-18 20:00:00','maxSpeed':60}]}}]}]}
    store.record_typhoon_snapshot(value,complete=False)
    assert store.typhoon_snapshot(datetime(2026,10,17,19,tzinfo=BEIJING),['202630']) is None
    result=store.typhoon_snapshot(datetime(2026,10,17,23,tzinfo=BEIJING),['202630'])
    assert result['historical_snapshot'] and result['list'][0]['points'][0]['forecast']['甲机构'][0]['maxSpeed']==60
    # A subset must never be advertised as the complete active-storm count.
    assert store.typhoon_snapshot(datetime(2026,10,17,23,tzinfo=BEIJING)) is None
    with pytest.raises(ValueError):store.record_typhoon_snapshot({**value,'source_url':'https://example.org/api'})


def test_typhoon_archive_keeps_earliest_capture_of_identical_source(tmp_path):
    from forecast_runtime.storage import ForecastStore,BEIJING
    from datetime import datetime
    store=ForecastStore(tmp_path)
    value={'source_url':'https://zjocean.org.cn/mapi_web/index.php/typhoon/index/activetyphoons',
           'retrieved_at':'2026-10-18T11:00:00+08:00','list':[{'typhoonID':202630,'points':[]}]}
    key=store.record_typhoon_snapshot(value,complete=True)
    assert key==store.record_typhoon_snapshot({**value,'retrieved_at':'2026-10-17T21:00:00+08:00'},complete=True)
    assert store.typhoon_snapshot(datetime(2026,10,17,23,tzinfo=BEIJING))['retrieved_at'].startswith('2026-10-17')


def test_safety_paraphrase_recalls_eye_and_self_rescue_paragraphs():
    from marine_kb.web_supplement import focused_public_text
    question='热带气旋眼区经过，天气暂时平静可以出门吗？'
    assert any('fj.gov.cn' in row['url'] for row in primary_routes(question))
    assert any('台风眼 防台避险' in query for query in public_search_queries(question))
    body='离岸流的定义，是海水向外流出的现象。\n\n'+('海边游玩应当注意安全，注意天气。\n\n'*8)+'如果被带离岸边，不要逆流游，保持漂浮并呼救，在体力允许时平行海岸游出。'
    selected=focused_public_text(body,'被卷出去以后如何自救？',limit=450)
    assert '不要逆流游' in selected and '平行海岸' in selected


def test_rescue_reference_has_national_plan_and_reporting_details():
    routes=primary_routes('海上遇险怎么求救，12395要报告哪些信息？')
    assert any('mot.gov.cn' in row['url'] for row in routes)
    assert any('wujiang.gov.cn' in row['url'] for row in routes)


def test_storm_agency_peak_wording_does_not_require_a_sea_grid_location():
    assert live_typhoon_question('截至2026年10月17日，接口标识202630的台风蔷琵，各机构预报何时最强、风速分别多少？')
    assert not live_typhoon_question('热带气旋强度分级中最强的等级叫什么？')


def test_evidence_method_paraphrase_discovers_primary_research_without_rag_acronym():
    from marine_kb.public_sources import research_question
    question='怎样让海洋专业问答逐句附证据，搜错资料时自动改检索？请依据研究论文说明方法。'
    assert research_question(question)
    assert any('2401.15884' in row['url'] for row in primary_routes(question))
    assert any('2310.11511' in row['url'] for row in primary_routes(question))


def test_observation_method_manual_and_annual_statistics_are_sources_not_answers():
    from typhoon.web_reader import WebPageReader
    routes=primary_routes('浮标数据出现尖峰或连续不变，应按哪些质控项目核查？')
    assert any('QARTOD_TS_Manual' in row['url'] and 'technical_reference'==row['_primary_scope'] for row in routes)
    reader=WebPageReader(enabled=True,allow_official_documents=True)
    assert reader._document_url_allowed(routes[0]['url'])
    assert not reader._document_url_allowed('https://cdn.ioos.noaa.gov.evil.example/fake.pdf')
    annual=primary_routes('2025年我国海洋灾害损失是多少？')
    assert annual and all('answer' not in row and 'value' not in row for row in annual)
    assert not primary_routes('2024年我国国民经济统计公报')


def test_undated_safety_permission_is_general_but_explicit_now_is_dated():
    assert question_contract('眼区经过天气暂时平静，可以出门收东西了吗？')['mode']=='method_or_knowledge'
    assert question_contract('眼区经过，宁波此刻可以出门吗？')['mode']=='dated_facts'


def test_quality_projects_outrank_a_standard_catalogue_for_specific_anomalies():
    from marine_kb.query_intents import focused_queries,support_score
    question='浮标观测数据出现尖峰或连续不变时，应按哪些质量控制项目核查？'
    assert any('尖峰检验' in q for q in focused_queries(question))
    assert any('恒定检验' in q for q in focused_queries(question))
    catalogue='海洋观测规范分为浮标潜标观测、数据处理与质量控制等部分。'
    operative='浮标温盐数据进行连续性检验，包含尖峰检验和恒定检验，恒定不变则数据可疑。'
    assert support_score(question,operative)>support_score(question,catalogue)


def test_same_local_document_retains_distinct_chapters_for_two_subquestions():
    hits=[{'source_id':'standard','chunk_id':i,'url':'/knowledge.html?source=standard',
        'title':'海洋观测数据质量控制规范','text':text,'metadata':{}}
        for i,text in enumerate(['浮标数据连续性检验包括尖峰检验和梯度检验，尖峰值需要与周围数据核对。',
                                  '潮位数据质量控制包括范围检验、增减水检验、调和常数分析检验。'])]
    selected=select_evidence('浮标和潮位数据质量控制项目有哪些？',hits,
        ['浮标质量控制有哪些项目？','潮位质量控制有哪些项目？'])
    assert len(selected)==2
    assert len(select_evidence('浮标和潮位数据质控有哪些规则？',hits))==2


def test_edition_excerpt_word_is_not_a_wind_speed_unit():
    from marine_kb.quantities import quantities
    assert quantities('GB/T14914.6—2021节选，说明按该版本解释。')==[]
    assert any(unit=='节' and value==20 for value,unit,_,_ in quantities('风速20节，按接口单位记录。'))


def test_annual_statistics_focus_not_consumed_by_repeated_year_in_introduction():
    from marine_kb.web_supplement import focused_public_text
    intro='2025年国民经济和社会发展统计公报，全年经济社会发展情况如下。'
    body='\n\n'.join([intro]*12+['因海洋灾害造成直接经济损失89亿元。'])
    focused=focused_public_text(body,'2025年全国海洋灾害损失是多少？',limit=150)
    assert '89亿元' in focused


def test_mixed_technical_objects_can_use_one_subject_per_local_chapter():
    from marine_kb.retrieval import relevance
    item={'title':'浮标观测质控方法','status':'approved','metadata':{},
        'text':'浮标温盐数据的质量控制包含格式检验、范围检验和连续性检验，用于标记可疑数据。'}
    assert relevance('浮标和潮位数据的质控规则是什么？异常值怎么判别？',item)['direct_evidence']
    assert not relevance('最高潮位是多少？',item)['direct_evidence']


def test_two_measurement_facets_need_not_be_in_the_same_historical_paragraph():
    from marine_kb.retrieval import relevance
    item={'title':'某次历史台风过程','status':'indexed','metadata':{},'text':'本次台风过程的最大增水为3米，增水记录已经核对。'}
    assert relevance('台风过程最大增水和最高潮位分别是多少？',item)['direct_evidence']
    assert not relevance('台风过程最高潮位是多少？',item)['direct_evidence']


def test_quality_catalogue_is_not_operative_quality_evidence():
    from marine_kb.query_intents import support_score
    q='浮标和潮位的数据质量控制方法是什么？'
    assert support_score(q,'海洋观测规范共六部分，包括浮标潜标观测、数据处理与质量控制。')==0
    assert support_score(q,'浮标温盐数据进行范围检验、缺测检验和连续性检验，标记可疑数据。')>0


def test_asof_partial_typhoon_snapshot_never_claims_full_active_count(tmp_path):
    from forecast_runtime.service import ForecastRuntime
    from forecast_runtime.storage import BEIJING
    from datetime import datetime
    from unittest.mock import patch
    r=ForecastRuntime(tmp_path)
    value={'source_url':'https://zjocean.org.cn/mapi_web/index.php/typhoon/index/activetyphoons',
        'retrieved_at':'2026-10-17T21:00:00+08:00','list':[{'typhoonID':202630,'nameCN':'蔷琵',
        'points':[{'time':'2026-10-17 20:00:00','maxSpeed':52}]}]}
    r.store.record_typhoon_snapshot(value,complete=False)
    with patch('forecast_runtime.service.local_now',return_value=datetime(2026,10,18,12,tzinfo=BEIJING)),patch.object(r.sync,'typhoons') as live:
        result=r.query('截至2026年10月17日，有几个台风在活动？')
    live.assert_not_called()
    assert result['historical_snapshot'] and not result['activity_count_verified']
    assert result['activity_scope_warning_required'] and json.loads(r.summary(result).split('\n',1)[1])['活动台风数'] is None
    facts=delivery_facts(result)
    assert any(f['kind']=='typhoon_scope' for f in facts)
    with pytest.raises(ValueError):validate_business_delivery('共有1个活动台风[资料1]',{'required_business_facts':facts},{1})
    validate_business_delivery(fact_table(facts),{'required_business_facts':facts},{1})


def test_missing_past_snapshot_does_not_query_a_later_live_activity_list(tmp_path):
    from forecast_runtime.service import ForecastRuntime
    from forecast_runtime.storage import BEIJING
    from datetime import datetime
    from unittest.mock import patch
    r=ForecastRuntime(tmp_path)
    with patch('forecast_runtime.service.local_now',return_value=datetime(2026,10,18,12,tzinfo=BEIJING)),patch.object(r.sync,'typhoons') as live:
        result=r.query('截至2026年10月15日，有几个台风在活动？')
    live.assert_not_called()
    assert result['state']=='historical_snapshot_unavailable' and result['list']==[]
    assert result['activity_scope_warning_required'] and json.loads(r.summary(result).split('\n',1)[1])['活动台风数'] is None


def test_current_date_activity_query_still_uses_live_source(tmp_path):
    from forecast_runtime.service import ForecastRuntime
    from forecast_runtime.storage import BEIJING
    from datetime import datetime
    from unittest.mock import patch
    r=ForecastRuntime(tmp_path)
    with patch('forecast_runtime.service.local_now',return_value=datetime(2026,10,18,12,tzinfo=BEIJING)),patch.object(r.sync,'typhoons',return_value={'list':[],'cache_stale':False,'state':'ready'}) as live:
        result=r.query('截至2026年10月18日，有几个台风在活动？')
    live.assert_called_once()
    assert result['activity_count_verified'] and result['snapshot_scope']=='complete_api_response'
