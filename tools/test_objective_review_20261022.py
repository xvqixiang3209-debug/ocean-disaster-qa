"""Opt-in local NC question regression; explicitly forbids network/model calls.

Keeps the operational source index untouched. Reports and the copied index are
written to a dated scratch directory, with real local NC opened read-only.
"""
from datetime import datetime, timedelta
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def run():
    from forecast_runtime.objective import ObjectiveForecast
    from forecast_runtime.objective_report import ObjectiveReport
    from forecast_runtime.objective_sources import ObjectiveSources,stamp
    from forecast_runtime.objective_bridge import answer_objective
    from forecast_runtime.storage import BEIJING
    from marine_kb.objective_context import product_question_plan
    from forecast_runtime.objective_scope import resolve_scope
    os.environ['MARINE_OBJECTIVE_INTERPRETATION']='off'
    work=ROOT/'tmp'/'claude_review_20261022'/'real_questions'
    work.mkdir(parents=True,exist_ok=True)
    (work/'index').mkdir(exist_ok=True)
    shutil.copyfile(ROOT/'data/forecast_runtime/objective/index.json',work/'index/index.json')
    engine=ObjectiveForecast(work/'index')
    now=datetime.now(BEIJING)
    catalog=engine.catalogue()['datasets']
    choices=[d for d in catalog if d.get('level')=='L5' and {'wave','wind'}<=set(d['fields']) and stamp(d['batch'])<=now]
    dataset=max(choices,key=lambda d:d['batch'])
    common=sorted(set(dataset['fields']['wave']['valid_times'])&set(dataset['fields']['wind']['valid_times']))
    full=[t for t in common if stamp(t)>=stamp(dataset['batch']) and stamp(t)+timedelta(hours=72)<=stamp(common[-1])]
    if not full:raise RuntimeError('latest registered L5 lacks any complete 72h window')
    start=next((stamp(t) for t in full if stamp(t)>=now),stamp(full[-1]))
    clock=start.strftime('%Y年%m月%d日%H时%M分')
    records={}
    def forbidden(*args,**kwargs):raise AssertionError('external/model call forbidden in offline regression')
    runtime=SimpleNamespace(objective=engine,_local=SimpleNamespace(request_now=now),
                            config_id=lambda:'offline-review-20261022',
                            store=SimpleNamespace(state=lambda k,v:records.update({k:v})))
    runtime.objective_reports=ObjectiveReport(engine,work/'reports',now=lambda:now)
    class LocalSources(ObjectiveSources):
        def ensure(self,*args,**kwargs):return {'state':'offline_local_only','network_executed':False}
    runtime.objective_sources=LocalSources(runtime)
    qa=SimpleNamespace(external_enabled=True,model_caller=forbidden,model_name='forbidden',
                       crag_enabled=True,joint_evidence=forbidden,_organized_answer=forbidden,
                       _public_sources=lambda q,hits:hits)
    rows=[]
    def question(identifier,text,previous=None):
        begin=time.perf_counter()
        plan=product_question_plan(text,[{'objective_context':previous}] if previous else None,now=now)
        if plan and plan.get('clarification'):
            response={'answer':plan['clarification'],'answer_meta':{'business_state':plan['objective_scope_resolution']['state'],
                'objective_scope_resolution':plan['objective_scope_resolution'],'map_actions':[],
                'map_action':{'type':'objective_clear','reason':plan['clarification'][:500]}}}
        else:
            response=answer_objective(runtime,(plan or {}).get('normalized_question') or text,qa,
                                      {**(plan or {}),'current_turn_question':text},False)
        meta=response['answer_meta']
        result=records.get('evidence:'+str(meta.get('business_evidence_id'))) or {}
        actions=meta.get('map_actions') or []
        for action in actions:
            assert action['dataset_id']==result['dataset_id'] and action['bounds']==result['bounds']
            if action.get('time_role')=='peak_of_this_field' and action['field_state']=='ready':
                field=result['fields'][action['field']]
                peak=field.get('minimum' if action['field']=='pressure' else 'maximum') or {}
                assert action['valid_time']==(peak.get('time') or peak.get('valid_time'))
        row={'id':identifier,'question':text,'normalized_question':(plan or {}).get('normalized_question') or text,
             'seconds':round(time.perf_counter()-begin,3),'response':response,
             'state':meta.get('business_state'),'scope':(result.get('scope') or {}).get('id'),
             'map_fields':[a['field'] for a in actions],
             'calculated_fields':{name:{key:value for key,value in info.items() if key in
                {'state','unit','maximum','minimum','missing_count','sea_count','intermittent_cell_count'}}
                for name,info in result.get('fields',{}).items()},
             'report_id':result.get('report_id'),'cache_hit':result.get('cache_hit'),
             'artifacts':meta.get('objective_artifacts') or [],'checks':'pass'}
        (work/(identifier+'.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf8')
        rows.append(row)
        return response,result
    first,_=question('01_parent_shengsi',f'绘制嵊泗{clock}海浪二维图')
    assert first['answer_meta']['objective_scope_resolution']['state']=='parent_fallback'
    assert '不代表嵊泗当地' in first['answer']
    second,_=question('02_follow_putuo','同一时刻换成普陀附近',first['answer_meta']['objective_context'])
    assert second['answer_meta']['objective_scope_resolution']['parents']=={'普陀':'舟山'}
    outside,_=question('03_xiamen',f'绘制厦门{clock}海浪二维图')
    assert outside['answer_meta']['business_state']=='out_of_region'
    assert outside['answer_meta']['map_action']['type']=='objective_clear'
    fourth,_=question('04_zhujiajian_wind_wave',f'{clock}朱家尖附近风浪二维图')
    assert [a['field'] for a in fourth['answer_meta']['map_actions']]==['wave','wind']
    fifth,_=question('05_follow_both','同一时刻风浪都显示',fourth['answer_meta']['objective_context'])
    assert [a['field'] for a in fifth['answer_meta']['map_actions']]==['wave','wind']
    six,six_result=question('06_ningbo_report',f'从{clock}起宁波未来72小时风浪简报，浪高超过2米的时段')
    assert len(six['answer_meta']['map_actions'])==2
    question('07_follow_wind_maximum','同一时段换成海面风逐格最大值图',six['answer_meta']['objective_context'])
    question('08_explicit_bounds',f'地图范围：122.33,29.82,122.53,30.02，{clock}海浪图')
    ninth,_=question('09_default_notice',f'{clock}海浪二维图')
    assert '未指定地点' in ninth['answer']
    absent=(stamp(common[-1])+timedelta(days=10)).strftime('%Y年%m月%d日%H时')
    tenth,_=question('10_uncovered',f'从{absent}起朱家尖未来72小时风浪简报')
    assert tenth['answer_meta']['business_state']=='no_data'
    question('11_change_from_zhujiajian','同一时刻换成嵊泗',fourth['answer_meta']['objective_context'])
    unknown,_=question('12_unknown_island','同一时刻换成珍珠岛',fourth['answer_meta']['objective_context'])
    assert unknown['answer_meta']['business_state']=='needs_scope'
    question('13_report_cache_reuse',f'从{clock}起宁波未来72小时风浪简报，浪高超过2米的时段')
    assert rows[-1]['report_id']==six_result['report_id'] and rows[-1]['cache_hit'] is True
    summary={'executed_at':now.isoformat(),'dataset_id':dataset['dataset_id'],'batch':dataset['batch'],
             'selected_native_time':start.isoformat(),'question_count':len(rows),'network_calls':0,
             'model_calls':0,'synthetic_data':False,'cases':rows}
    (work/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in summary.items() if k!='cases'},ensure_ascii=False))
    print(json.dumps([{k:r.get(k) for k in ('id','state','scope','map_fields','report_id','cache_hit','seconds')} for r in rows],ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    args=parser.parse_args()
    if not args.run:parser.error('requires --run; reads real local NC and writes scratch reports')
    run()
