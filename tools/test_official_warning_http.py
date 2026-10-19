"""Real local API acceptance, with isolated conversations and saved actual responses."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import time
import requests

CASES=[
 ('W01','截至2026年10月17日12时，国家海洋预报台对秦皇岛发布了哪些风暴潮警报？等级、有效时间和覆盖范围是什么？',1,'黄色'),
 ('W02','截至2026年10月17日20时，国家海洋预报台对秦皇岛有哪些风暴潮警报？等级、发布时间和影响地区是什么？',1,'蓝色'),
 ('W03','2026年7月10日浙江发布了哪些海浪和风暴潮预警？各是什么等级，何时发布，覆盖哪些地区？',5,None),
 ('W04','截至2026年9月6日12时，国家海洋预报台对浙江有哪些海浪警报？是否已经解除？',1,'蓝色'),
 ('W05','截至2026年9月6日20时，国家海洋预报台对浙江的海浪警报解除了吗？什么时候解除的？',1,'解除'),
 ('W06','截至2026年10月17日，浙江当前发布了哪些海洋灾害预警？覆盖哪些海域？',0,None),
 ('W07','2026年1月2日国家海洋预报台海啸信息发布记录有哪些？地震位置和对我国沿岸的影响是什么？',2,'信息'),
 ('W08','2026年10月17日国家海洋预报台发布了哪些海洋灾害预警？等级、发布时间、有效时段和海域是什么？',None,None),
 ('W09','2026年10月20日浙江有哪些海浪预警？',None,None),
 ('W10','2026年10月17日嵊泗县有哪些风暴潮警报？覆盖哪些岸段？',0,None),
 ('W11','截至2026年7月10日20时，国家海洋预报台对温州发布了什么风暴潮警报？有效时段和参考站数值是什么？',1,'橙色'),
 ('W12','截至2026年7月10日20时，国家海洋预报台对宁波发布了什么风暴潮警报？有效时段和参考站数值是什么？',1,'蓝色'),
 ('W13','截至2025年10月23日12时，国家海洋预报台对浙江有哪些风暴潮警报？等级、时段和影响范围是什么？',1,None),
 ('W14','2026年10月17日舟山朱家尖有哪些海洋灾害预警？具体覆盖哪里？',0,None),
]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--base',default='http://127.0.0.1:8769')
    parser.add_argument('--output',required=True);parser.add_argument('--ids',default='')
    args=parser.parse_args();root=Path(args.output);root.mkdir(parents=True,exist_ok=False)
    base=args.base.rstrip('/');selected=set(args.ids.split(',')) if args.ids else None
    status=requests.get(base+'/api/assistant/status',timeout=10).json()
    (root/'build.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    def run(case):
        ident,question,expected_count,expected_level=case
        session=requests.Session();token=session.get(base+'/api/assistant/session',timeout=15).json()['token']
        started=time.monotonic()
        try:
            response=session.post(base+'/api/assistant/ask',json={'question':question},headers={'X-Assistant-Token':token},timeout=240)
            actual=response.json();evidence=None
            if actual.get('data_url'):evidence=session.get(base+actual['data_url'],timeout=30).json()
            checks={'http_200':response.status_code==200,'not_trial':actual.get('trial') is False,
                    'warning_route':actual.get('evidence_diagnostics',{}).get('route')=='official_warnings'}
            if ident=='W09':checks['future_clarification']=actual.get('answerability')=='needs_clarification'
            else:
                checks['evidence_saved']=bool(evidence and evidence.get('kind')=='official_warnings')
                checks['table_or_explicit_empty']=bool(not evidence or not evidence['total'] or '本次匹配公告清单' in actual.get('answer',''))
                if expected_count is not None:checks['expected_count']=bool(evidence and evidence['total']==expected_count)
                if expected_level:
                    checks['expected_local_level']=bool(evidence and all((i['is_cancellation'] if expected_level=='解除' else i.get('is_information') if expected_level=='信息' else expected_level in i['matched_levels']) for i in evidence['items']))
                if ident in {'W01','W02','W04','W05','W11','W12'}:checks['deepseek_used']=actual.get('mode')=='llm'
                if ident in {'W06','W10','W14'}:checks['no_false_zero']=bool(evidence and not evidence['coverage_complete'] and actual.get('missing_information'))
                if ident=='W14':checks['no_region_expansion']=bool(evidence and evidence['query']['regions']==['朱家尖'])
                if ident=='W08':checks['nonempty_national_list']=bool(evidence and evidence['total']>0)
            result={'id':ident,'question':question,'tested_at':datetime.now().astimezone().isoformat(),'http_status':response.status_code,
                    'seconds':round(time.monotonic()-started,2),'checks':checks,'passed':all(checks.values()),'actual':actual,'evidence':evidence}
            # A referential follow-up uses the same browser-owned conversation.
            if ident=='W01' and actual.get('conversation_id'):
                follow=session.post(base+'/api/assistant/ask',json={'question':'这些警报覆盖哪里？什么时候发布的？','conversation_id':actual['conversation_id']},headers={'X-Assistant-Token':token},timeout=240)
                result['followup']={'question':'这些警报覆盖哪里？什么时候发布的？','http_status':follow.status_code,'actual':follow.json()}
                follow_data=result['followup']['actual']
                result['checks']['followup_grounded']=bool(follow.status_code==200 and follow_data.get('context_used') and follow_data.get('data_url') and follow_data.get('evidence_diagnostics',{}).get('route')=='official_warnings')
                result['passed']=all(result['checks'].values())
        except Exception as exc:result={'id':ident,'question':question,'passed':False,'error_type':type(exc).__name__}
        (root/(ident+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:result.get(k) for k in ['id','passed','seconds','checks','error_type']},ensure_ascii=False),flush=True)
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,[c for c in CASES if not selected or c[0] in selected]))
    summary={'build':status['version'],'passed':sum(r['passed'] for r in results),'total':len(results),'results':results}
    (root/'results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if summary['passed']==summary['total'] else 1

if __name__=='__main__':raise SystemExit(main())
