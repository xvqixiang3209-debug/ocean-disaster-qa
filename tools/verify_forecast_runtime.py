"""Authorized live DeepSeek checks for actual forecast data and follow-ups."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sys
import time

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--live',action='store_true')
    parser.add_argument('--groups',nargs='+',choices=['tide','coastal','grid','typhoon','compound','path'])
    args=parser.parse_args()
    if not args.live:parser.error('--live is required to use the configured model')
    from server import app
    groups={
        'tide':['坎门站明天几点高潮，潮位多少？','那后天呢？'],
        'coastal':['浙江未来72小时哪些岸段超警？','最高的是哪个岸段？'],
        'grid':['舟山海域未来72小时风速和浪高是多少？','它的最大浪高是什么时候？'],
        'typhoon':['现在有几个台风在活动？最新位置和强度是多少？'],
        'compound':['SHENJIAMEN站明天的天文潮和浪高各是多少？'],
        'path':['当前台风未来预报路径如何？不同机构分别怎么预报？']}
    if args.groups:groups={key:groups[key] for key in args.groups}
    def verify(group,questions):
        client=app.test_client();token=client.get('/api/assistant/session').json['token'];cid=None;rows=[]
        for question in questions:
            start=time.monotonic()
            response=client.post('/api/assistant/ask',json={'question':question,'conversation_id':cid},headers={'X-Assistant-Token':token})
            data=response.json;cid=data.get('conversation_id') or cid
            row={'group':group,'question':question,'status':response.status_code,'seconds':round(time.monotonic()-start,2),**data};rows.append(row)
            print(json.dumps({k:row.get(k) for k in ['group','question','status','mode','trial','business_state','context_used','resolved_question','seconds']},ensure_ascii=False),flush=True)
        return rows
    rows=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(verify,group,questions) for group,questions in groups.items()]
        for future in futures:rows.extend(future.result())
    target=APP/'tmp'/('forecast_trial_live_'+str(int(time.time())))
    target.mkdir(parents=True,exist_ok=True)
    (target/'answers.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print('REPORT='+str(target/'answers.json'),flush=True)


if __name__=='__main__':main()
