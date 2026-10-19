"""Small authorized live API conversation check; no browser or imported-file changes."""
import argparse
import json
from pathlib import Path
import sys
import time

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='Use the configured DeepSeek and official web search.')
    parser.add_argument('--groups',nargs='+',choices=['concept','procedure','selected'],default=['concept','procedure','selected'])
    args=parser.parse_args()
    if not args.live:parser.error('Pass --live only for an authorized model run.')
    from server import app,typhoon_knowledge_base
    client=app.test_client()
    token=client.get('/api/assistant/session').json['token']
    records=[]
    def ask(group,question,cid=None,sid=None):
        started=time.monotonic()
        response=client.post('/api/assistant/ask',json={'question':question,'conversation_id':cid,'selected_sid':sid},
                            headers={'X-Assistant-Token':token})
        data=response.json
        row={'group':group,'question':question,'status':response.status_code,'elapsed_seconds':round(time.monotonic()-started,2),**data}
        records.append(row)
        print(json.dumps({key:row.get(key) for key in ('group','question','status','mode','context_used','resolved_question','selected_sid','elapsed_seconds')},ensure_ascii=False),flush=True)
        return data.get('conversation_id') or cid
    if 'concept' in args.groups:
        cid=ask('concept','什么是风暴增水？它和天文潮有什么关系？')
        ask('concept','你刚才说的叠加，是不是把两个最大值直接相加？',cid)
    if 'procedure' in args.groups:
        cid=ask('procedure','浙江海洋灾害应急响应终止由谁决定？请分步骤说明。')
        cid=ask('procedure','第二步具体由谁批准？',cid)
        ask('procedure','那上海呢？',cid)
    candidates=typhoon_knowledge_base.candidate_summaries(require_intensity=False)
    selected=next((row for row in candidates if str(row.get('season'))=='2019' and row.get('name')=='LEKIMA'),None)
    if selected and 'selected' in args.groups:
        cid=ask('selected','最大增水是多少，出现在什么站？',sid=selected['sid'])
        cid=ask('selected','这个站还有哪些相关记录？',cid,sid=selected['sid'])
        ask('selected','什么是有效波高？',cid,sid=selected['sid'])
    directory=APP/'tmp'/('map_conversation_20261016_'+str(int(time.time())))
    directory.mkdir(exist_ok=True,parents=True)
    (directory/'answers.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    print('REPORT='+str(directory/'answers.json'),flush=True)

if __name__=='__main__':main()
