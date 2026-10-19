"""Additional live questions with changed places/dates/forms; no runtime rules."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime
import hashlib,json
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'tmp/qa_full160_phase162_20261018_final'
CASES=[
 ('概念解释','作为温州沿海居民，台风和风暴潮是一回事吗？海浪又有什么不同？'),
 ('概念解释','有效波高是什么意思？为什么宁波海边肉眼看到的大浪会比预报波高高？'),
 ('概念解释','风暴增水和天文潮高潮叠加，为什么朱家尖会更容易出现高水位？'),
 ('季节规律','福建的台风只在夏季出现吗？秋冬还需要防范吗？'),
 ('响应程序','浙江海洋灾害应急响应什么时候能降级？什么时候能终止？分别由谁决定、怎么公布？'),
 ('响应程序','福建与浙江的海洋灾害响应终止程序分别是什么？能不能直接用同一套流程？'),
 ('响应程序','宁波市停止户外活动由哪个部门决定？启动条件和公告渠道各是什么？'),
 ('时间截止','截至2026年10月17日21时30分，当时发布的舟山海浪预警是什么？不要使用之后发布的信息。'),
 ('时间截止','截至2026年10月16日，朱家尖南沙浴场是否有10月18日开放的正式公告？'),
 ('时间截止','查询2026年10月18日08时至18时朱家尖的风浪潮情况，分别说明预报起报时间和有效时段。'),
 ('场所对象','2026年10月18日朱家尖南沙浴场能游泳吗？东沙浴场的开放消息可以作为依据吗？'),
 ('场所对象','2026年10月18日象山松兰山适合岸钓吗？坐休闲渔船船钓还需要核实什么？'),
 ('场所对象','2026年10月18日苍南渔寮海边可以赶海和游泳吗？请分别说明已知数据与现场条件。'),
 ('场所对象','2026年10月18日嵊泗基湖浴场是否开放？没有公告时能不能直接根据浪高判断开放？'),
 ('场所对象','2026年10月18日连云港市海州湾浴场可以游泳吗？请核对对应浴场。'),
 ('船舶航线','当前舟山客运船恢复航行了，就表示休闲渔船也能出海船钓吗？分别需要什么依据？'),
 ('船舶航线','2026年10月18日半升洞到普陀山的客运是否复航？能不能用别的航线复航公告代替？'),
 ('多子问题','2026年10月18日朱家尖赶海、游泳、岸钓和船钓分别需要什么条件？哪些已有数据，哪些还不能确认？'),
 ('多子问题','宁波有海浪预警时，是否自动停课、停航和关闭浴场？请分别说明决定依据。'),
 ('版本效力','2019年发布的海洋灾害应急预案是否一定失效？遇到新版和旧版同时能搜到时怎样确定适用文件？'),
]


def main():
    def ask(entry):
        index,(family,question)=entry;path=RUN/f'T-{index:03}.json'
        if path.exists():return json.loads(path.read_text(encoding='utf8'))
        s=requests.Session();s.trust_env=False
        start=datetime.now().astimezone().isoformat()
        token=s.get('http://127.0.0.1:8769/api/assistant/session',timeout=30).json()['token']
        response=s.post('http://127.0.0.1:8769/api/assistant/ask',json={'question':question},
                        headers={'X-Assistant-Token':token},timeout=(15,300))
        try:reply=response.json()
        except ValueError:reply={'error':'Non JSON'}
        value={'id':f'T-{index:03}','family':family,'question':question,'started_at':start,
               'completed_at':datetime.now().astimezone().isoformat(),'http_status':response.status_code,'reply':reply,
               'answer_sha256':hashlib.sha256(reply.get('answer','').encode()).hexdigest(),'expert_accepted':False}
        path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
        return value
    rows=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(ask,entry) for entry in enumerate(CASES,1)]):
            row=future.result();rows.append(row)
            print(json.dumps({'completed':len(rows),'id':row['id'],'http':row['http_status']},ensure_ascii=False),flush=True)
    rows.sort(key=lambda r:r['id'])
    (RUN/'transfer_answers.json').write_text(json.dumps({'items':rows,'note':'另20条真实接口变体测试，未与原160题混算。'},ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':
    import sys
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf8')
    main()
