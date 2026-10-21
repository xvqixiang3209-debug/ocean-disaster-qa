from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import pytest
from forecast_runtime.objective_bridge import objective_question,requested_window,query_objective,answer_objective

NOW=datetime(2026,10,20,21,43,tzinfo=timezone(timedelta(hours=8)))

@pytest.mark.parametrize('q',[
    '生成浙江未来72小时海浪海面风和气压影响报告',
    '绘制浙江明天的客观预报风场图',
    '从2026-10-21 08:00起生成浙江影响报告，使用数据集ID ecmwf_202610192000_247997aeca80',
])
def test_product_intent(q):assert objective_question(q)

@pytest.mark.parametrize('q',['什么是海浪预警？','浙江海洋预警等级如何划分？','相似台风有哪些？','什么是海平面气压，如何制作风场图？'])
def test_does_not_steal_knowledge_or_analogue(q):assert not objective_question(q)

@pytest.mark.parametrize('q,expected',[
    ('生成浙江未来72小时影响报告',NOW),
    ('从今天20时起，生成浙江未来72小时报告',NOW.replace(hour=20,minute=0)),
    ('从2026-10-21 08:00起，生成浙江未来72小时报告',NOW.replace(day=21,hour=8,minute=0)),
    ('从明天8时起生成浙江未来72小时报告',NOW.replace(day=21,hour=8,minute=0)),
])
def test_start_and_72_hours_are_exact(q,expected):
    start,hours=requested_window(q,NOW)
    assert start==expected and hours==72

def test_dataset_identifier_passed_to_same_report_not_substituted():
    class Reports:
        def report(self,**kwargs):
            assert kwargs['dataset_id']=='ecmwf_202610192000_247997aeca80'
            return {'state':'ready','objective_result':{'dataset_id':kwargs['dataset_id']},'summary':'实际风速10 m/s','artifacts':[]}
    r=SimpleNamespace(objective=object(),objective_reports=Reports(),_local=SimpleNamespace(request_now=NOW))
    result=query_objective(r,'生成浙江未来72小时报告，使用数据集ID ecmwf_202610192000_247997aeca80')
    assert result['end']=='2026-10-23T21:43:00+08:00'

def test_requested_date_does_not_fall_back_on_invalid_clock():
    with pytest.raises(ValueError):requested_window('从今天25时起生成浙江未来72小时报告',NOW)

def test_same_chat_bridge_keeps_real_table_and_artifacts_without_model():
    records={}
    class Reports:
        def report(self,**kwargs):
            return {'state':'ready','objective_result':{'dataset_id':'test'},'summary':'北部最大有效波高3.1 m，2026-10-21 08:00[资料1]。',
                    'evidence':'NC计算原始结果：北部最大有效波高3.1 m，2026-10-21 08:00。',
                    'artifacts':[{'filename':'report.html','url':'/api/objective/reports/'+'a'*32+'/report.html'}]}
    runtime=SimpleNamespace(objective=object(),objective_reports=Reports(),_local=SimpleNamespace(request_now=NOW),
        config_id=lambda:'test-config',store=SimpleNamespace(state=lambda key,value:records.update({key:value})))
    qa=SimpleNamespace(external_enabled=False,model_caller=None,_public_sources=lambda q,h:h)
    result=answer_objective(runtime,'生成浙江未来72小时风浪影响报告',qa,{},False)
    assert '3.1 m' in result['answer']
    assert result['answer_meta']['objective_artifacts'][0]['filename']=='report.html'
    assert result['answer_meta']['route']=='objective_forecast'
    assert records


def _package(state='ready'):
    start=NOW.replace(hour=20,minute=0)
    values={
        'wave': ('m',[1.5,2.5,3.5]),
        'wind': ('m/s',[10,15,20]),
        'pressure': ('hPa',[1012,1008,1004]),
    }
    regions=[]
    for index,name in enumerate(['北部参考统计框','中部参考统计框','南部参考统计框']):
        fields={}
        for field,(unit,numbers) in values.items():
            key='minimum' if field=='pressure' else 'maximum'
            fields[field]={'unit':unit,key:{'value':numbers[index],'lon':121+index*.5,'lat':30-index,
                                          'time':(start+timedelta(hours=60+index)).isoformat()}}
        regions.append({'id':['north','central','south'][index],'name':name,'fields':fields})
    names=['report.html','report.json','series.csv','wave_peak.png','wind_peak.png','pressure_peak.png']
    return {'state':state,'objective_result':{
        'dataset_id':'ecmwf_fixture_20261020','report_id':'1'*32,
        'start':start.isoformat(),'end':(start+timedelta(hours=72)).isoformat(),'hours':72,
        'regions':regions,'state':state},
        'report_id':'1'*32,'summary':'已取得最大波高3.5 m、最大10米风速20 m/s、最低气压1004 hPa。',
        'evidence':'程序原始统计：北部波高1.5 m，中部2.5 m，南部3.5 m；已登记同一窗口和网格位置。',
        'artifacts':{name:{'filename':name,'url':'/api/objective/reports/'+'1'*32+'/'+name,'sha256':'a'*64} for name in names}}


def _runtime(package):
    records={}
    class Reports:
        def report(self,**kwargs):
            self.last_kwargs=kwargs
            return package
    reports=Reports()
    runtime=SimpleNamespace(objective=object(),objective_reports=reports,_local=SimpleNamespace(request_now=NOW),
                            config_id=lambda:'test',store=SimpleNamespace(state=lambda key,value:records.update({key:value})))
    return runtime,reports,records


class ModelQA:
    external_enabled=True
    model_caller=object()
    model_name='test-model'
    crag_enabled=False
    def __init__(self,answer,meta=None):
        self.answer=answer
        self.meta=meta or {'used_mode':'llm','answerability':'full'}
        self.calls=[]
    def _public_sources(self,question,hits):return hits
    def _organized_answer(self,question,model,hits,meta):
        self.calls.append({'question':question,'hits':hits,'meta':meta})
        return {'answer':self.answer,'answer_meta':dict(self.meta),'rag_sources':hits,'web_sources':[]}


def test_generated_artifacts_are_in_model_evidence_and_public_answer():
    runtime,_,_= _runtime(_package())
    qa=ModelQA('南部的已取得波高极值较高；比较仅针对参考框[资料1]。')
    answer=answer_objective(runtime,'生成浙江未来72小时海浪海面风气压报告和图件',qa,{},False)
    assert len(qa.calls)==1
    body=qa.calls[0]['hits'][0]['text']
    assert '程序已生成' in body and 'report.html' in body and 'wave_peak.png' in body
    assert '/api/objective/reports/'+'1'*32+'/series.csv' in body
    assert '[完整HTML报告](' in answer['answer'] and '[风速峰值时刻空间图](' in answer['answer']
    assert '资料解读' in answer['answer'] and '南部的已取得波高极值较高' in answer['answer']
    assert len(qa.calls[0]['meta']['subquestions'])==1
    assert qa.calls[0]['meta']['subquestions'][0]['kind']=='explanation'
    assert qa.calls[0]['meta']['normalized_question']==qa.calls[0]['question']


def test_false_artifact_gap_does_not_override_program_delivery():
    runtime,_,_= _runtime(_package())
    qa=ModelQA('没有提供图件和报告下载链接，无法提供下载方式[资料1]。',
               {'used_mode':'llm','answerability':'partial','missing_information':['报告未提供']})
    answer=answer_objective(runtime,'生成浙江未来72小时风浪报告',qa,{},False)
    assert '没有提供图件' not in answer['answer']
    assert '[完整HTML报告]' in answer['answer']
    assert '3.5 m' in answer['answer'] and '1004 hPa' in answer['answer']
    assert answer['answer_meta']['objective_language_state']=='contradicts_generated_artifacts'
    assert answer['answer_meta']['answerability']=='full'
    assert answer['answer_meta']['objective_language_missing']==['报告未提供']


def test_false_artifact_gap_cannot_pass_even_with_full_model_label():
    runtime,_,_=_runtime(_package())
    qa=ModelQA('图件和报告的具体下载链接、文件名或获取步骤未在资料中给出。')
    answer=answer_objective(runtime,'生成浙江未来72小时风浪报告',qa,{},False)
    assert '未在资料中给出' not in answer['answer']
    assert answer['answer_meta']['objective_language_state']=='contradicts_generated_artifacts'


def test_supported_science_dropped_by_unknown_facet_is_not_published_as_fragment():
    runtime,_,_= _runtime(_package())
    qa=ModelQA('可确认的核心结论：这是一份模式报告。',
               {'used_mode':'llm','answerability':'full','phase2':{
                   'decisions':[{'status':'supported','drop_rule':'typed_grounding_unknown_facet'}]}})
    answer=answer_objective(runtime,'生成浙江未来72小时风浪报告',qa,{},False)
    assert '可确认的核心结论' not in answer['answer']
    assert '3.5 m' in answer['answer'] and '20 m/s' in answer['answer']
    assert answer['answer_meta']['objective_language_state']=='supported_scientific_content_lost_during_binding'


def test_regional_followup_has_three_real_value_time_position_tuples_and_winner():
    runtime,reports,_= _runtime(_package())
    qa=SimpleNamespace(external_enabled=False,model_caller=None,_public_sources=lambda q,h:h)
    current='北部、中部、南部哪个浪高最大？给出具体数值、时间和位置。'
    canonical='浙江客观预报，数据集ID ecmwf_fixture_20261020，从2026年10月20日20时00分起未来72小时。本轮问题：'+current
    answer=answer_objective(runtime,canonical,qa,{'current_turn_question':current},False)
    assert reports.last_kwargs['dataset_id']=='ecmwf_fixture_20261020'
    assert reports.last_kwargs['start']=='2026-10-20T20:00:00+08:00'
    assert all(value in answer['answer'] for value in ['1.5 m','2.5 m','3.5 m','北部参考统计框','中部参考统计框','南部参考统计框'])
    assert '2026-10-23 08:00:00' in answer['answer'] and '东经 121°' in answer['answer']
    assert '最高的是：南部参考统计框为3.5 m' in answer['answer']
    assert '| 最低海平面气压 |' not in answer['answer']
    context=answer['answer_meta']['objective_context']
    from forecast_runtime.objective_scope import resolve_scope
    assert context=={'dataset_id':'ecmwf_fixture_20261020','start':'2026-10-20T20:00:00+08:00',
                     'end':'2026-10-23T20:00:00+08:00','hours':72,
                     'scope':resolve_scope('浙江'),'bounds':resolve_scope('浙江')['bounds']}


def test_no_data_response_clears_inherited_objective_slots():
    package=_package('no_data')
    runtime,_,_=_runtime(package)
    qa=SimpleNamespace(external_enabled=False,model_caller=None,_public_sources=lambda q,h:h)
    answer=answer_objective(runtime,'生成浙江未来72小时风浪报告',qa,
                            {'objective_context':{'dataset_id':'old','start':'old','end':'old'}},False)
    assert answer['answer_meta']['objective_context'] is None


def test_untrusted_artifact_url_never_enters_evidence_or_downloads():
    package=_package()
    package['artifacts']['malicious']={'filename':'report.html','url':'javascript:alert(1)'}
    package['artifacts']['outside']={'filename':'secret.env','url':'/api/objective/reports/'+'1'*32+'/secret.env'}
    runtime,_,_=_runtime(package)
    qa=ModelQA('程序数据为模式预报[资料1]。')
    answer=answer_objective(runtime,'生成浙江未来72小时海浪报告',qa,{},False)
    assert 'javascript:' not in answer['answer']
    assert 'secret.env' not in qa.calls[0]['hits'][0]['text']
    assert len(answer['answer_meta']['objective_artifacts'])==6


def test_single_interpretation_contract_discards_stale_multi_facet_plan():
    runtime,_,_=_runtime(_package())
    qa=ModelQA('该对照仅适用于所选参考框。')
    answer_objective(runtime,'生成浙江未来72小时风浪报告',qa,
                     {'subquestions':[{'id':9,'question':'如何提供报告下载？'}],
                      'facet_contract':[{'fid':'f9'}],'crag_evidence_plan':{'facets':[{'id':9}]}},False)
    meta=qa.calls[0]['meta']
    assert 'facet_contract' not in meta and 'crag_evidence_plan' not in meta
    assert len(meta['subquestions'])==1 and meta['subquestions'][0]['id']==1


def test_language_or_web_failure_preserves_program_tables_files_and_context():
    runtime,_,_=_runtime(_package())
    class BrokenQA(ModelQA):
        def _organized_answer(self,*args,**kwargs):
            raise TimeoutError('private backend detail')
    answer=answer_objective(runtime,'生成浙江未来72小时风浪报告',BrokenQA(''),{},True)
    assert '3.5 m' in answer['answer'] and '[完整HTML报告]' in answer['answer']
    assert 'private backend detail' not in str(answer)
    assert answer['answer_meta']['objective_language_state']=='model_unavailable'
    assert answer['answer_meta']['objective_language_error_type']=='TimeoutError'
    assert answer['answer_meta']['objective_context']['hours']==72
