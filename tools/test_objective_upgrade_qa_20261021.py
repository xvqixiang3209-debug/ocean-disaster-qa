"""Opt-in eight real assistant calls; max two chains, no automatic retries.

Only loopback is allowed. Cookies, assistant tokens and conversation IDs remain
in memory; recordings use case aliases for conversational linkage.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlparse

import requests

APP=Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:sys.path.insert(0,str(APP))
from forecast_runtime.objective_scope import resolve_scope

CASES=[
    [('ZJ_NATURAL','给我一份浙江未来三天风浪分析')],
    [('ZS_THRESHOLD','从2026年10月21日20时起，给我舟山未来72小时风浪影响报告，说明浪高超过2米的时段。')],
    [('ZJJ_MAX','从2026年10月21日20时起，给我朱家尖未来72小时风浪简报，并生成海浪逐格最大值图。'),
     ('ZJJ_MAX_WIND','同一时段换成海面风逐格最大值图'),
     ('ZJJ_SNAPSHOT','改成2026年10月22日08时海浪图')],
    [('ZJJ_TOMORROW','从明天08时起，生成朱家尖未来24小时海况简报。')],
    [('ZS_HISTORY','从2026年10月20日08时起，生成舟山未来72小时风浪影响报告。')],
    [('ZJ_NO_COVERAGE','从2026年11月5日08时起，生成浙江未来72小时风浪报告。不得使用其他日期数据代替。')],
]
SECRET_KEYS={'token','session','session_id','session_token','owner','owner_id','cookie','authorization','x-assistant-token','conversation_id'}


def clean(value):
    if isinstance(value,dict):
        return {k:('<not_recorded>' if k.lower() in SECRET_KEYS else clean(v)) for k,v in value.items()}
    if isinstance(value,list):return [clean(item) for item in value]
    return value


def write(path,value):
    path.write_text(json.dumps(clean(value),ensure_ascii=False,indent=2),encoding='utf-8')


def metrics(reply,tool):
    meta=reply.get('answer_meta') or {}
    action=meta.get('map_action') or reply.get('map_action') or {}
    context=meta.get('objective_context') or {}
    diagnostics=reply.get('evidence_diagnostics') or {}
    budget=diagnostics.get('answer_budget') or {}
    return {'route':meta.get('route'), 'tool_kind':tool.get('kind'),
            'route_visibility':'not_exposed_by_public_api' if not meta.get('route') else 'reported',
            'used_mode':meta.get('used_mode') or reply.get('mode'),
            'generation_used':meta.get('generation_used'),
            'model_calls_observed':budget.get('calls'),
            'business_state':meta.get('business_state') or reply.get('business_state') or tool.get('state'),
            'answerability':meta.get('answerability') or reply.get('answerability'),
            'scope':tool.get('scope') or context.get('scope'),
            'bounds':tool.get('bounds') or action.get('bounds') or context.get('bounds'),
            'start':tool.get('start') or action.get('start') or context.get('start'),
            'end':tool.get('end') or context.get('end'),
            'hours':tool.get('hours') or action.get('hours') or context.get('hours'),
            'dataset_id':tool.get('dataset_id') or action.get('dataset_id') or context.get('dataset_id'),
            'map_action':action,'artifacts':meta.get('objective_artifacts') or reply.get('objective_artifacts') or tool.get('artifacts') or [],
            'data_acquisition':tool.get('data_acquisition'),
            'language_state':meta.get('objective_language_state')}


def run_chain(base,chain,folder):
    rows=[]
    with requests.Session() as session:
        session.trust_env=False
        initial=session.get(base+'/api/assistant/session',timeout=(10,30))
        initial.raise_for_status()
        token=initial.json()['token']
        conversation=None
        for index,(identifier,question) in enumerate(chain):
            began=datetime.now(timezone(timedelta(hours=8))).isoformat()
            started=time.monotonic()
            row={'id':identifier,'question':question,'started_at':began,
                 'request':{'method':'POST','url':base+'/api/assistant/ask',
                            'json':{'question':question,'conversation_id':'<previous case conversation>' if conversation else None}},
                 'prior_case':chain[index-1][0] if index else None,
                 'requested_scope':resolve_scope('朱家尖' if identifier in {'ZJJ_MAX_WIND','ZJJ_SNAPSHOT'} else question),
                 'recording_policy':'No credentials/cookies/tokens/conversation IDs recorded; no automatic retries.'}
            write(folder/(identifier+'.request.json'),row)
            try:
                response=session.post(base+'/api/assistant/ask',json={'question':question,'conversation_id':conversation},
                                      headers={'X-Assistant-Token':token},timeout=(15,600))
                row['http_status']=response.status_code
                reply=response.json()
                conversation=reply.get('conversation_id') or conversation
                tool={}
                data_url=reply.get('data_url') or (reply.get('answer_meta') or {}).get('data_url')
                if data_url and re.fullmatch(r'/api/forecast/evidence/[a-fA-F0-9_-]{8,96}',data_url):
                    data_response=session.get(base+data_url,timeout=(10,60))
                    row['tool_http_status']=data_response.status_code
                    if data_response.ok:tool=data_response.json()
                row.update(actual_reply=reply,tool_result=tool,user_visible_answer=reply.get('answer',''),
                           metrics=metrics(reply,tool))
                (folder/(identifier+'.answer.txt')).write_text(reply.get('answer',''),encoding='utf-8')
            except Exception as exc:
                # Exception strings can contain requests internals, so record
                # only their public type and do not retry paid work.
                row['error_type']=type(exc).__name__
            row['elapsed_seconds']=round(time.monotonic()-started,3)
            row['completed_at']=datetime.now(timezone(timedelta(hours=8))).isoformat()
            write(folder/(identifier+'.json'),row)
            rows.append(clean(row))
            summary=row.get('metrics') or {}
            print(json.dumps({'id':identifier,'http':row.get('http_status'),
                'seconds':row['elapsed_seconds'],'state':summary.get('business_state'),
                'scope_id':(summary.get('scope') or {}).get('id'),'start':summary.get('start'),
                'map_type':(summary.get('map_action') or {}).get('type'),
                'error_type':row.get('error_type')},ensure_ascii=False),flush=True)
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--base',default='http://127.0.0.1:8774')
    args=parser.parse_args()
    if not args.run:
        print(json.dumps({'calls':8,'chains':CASES},ensure_ascii=False));return
    url=urlparse(args.base)
    if url.hostname not in {'127.0.0.1','localhost'} or url.scheme!='http' or url.path not in {'','/'}:
        raise ValueError('Only the local preview service is allowed.')
    folder=APP/'tmp/objective_upgrade_20261021/questions'
    folder.mkdir(parents=True,exist_ok=True)
    if any((folder/(key+'.json')).exists() for chain in CASES for key,_ in chain):
        raise FileExistsError('Actual QA recordings already exist; paid calls will not be repeated.')
    write(folder/'manifest.json',{'started_at':datetime.now().astimezone().isoformat(),'base':args.base,
          'authorized_full_qa_calls':8,'max_parallel_chains':2,'automatic_retries':0,'chains':CASES})
    rows=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run_chain,args.base,chain,folder) for chain in CASES]
        for future in as_completed(futures):rows.extend(future.result())
    rows.sort(key=lambda r:next(i for i,(key,_) in enumerate([case for chain in CASES for case in chain]) if key==r['id']))
    write(folder/'summary.json',{'completed_at':datetime.now().astimezone().isoformat(),'actual_requests':len(rows),
          'max_parallel_chains':2,'automatic_retries':0,'rows':rows})


if __name__=='__main__':main()
