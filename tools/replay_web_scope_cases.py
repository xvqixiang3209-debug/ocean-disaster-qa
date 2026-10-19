"""Replay identical supplied search/body fixtures through old and new retrieval.

These are controlled regression fixtures, not live websites or accuracy scores.
"""
from datetime import datetime
import importlib.util,json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from marine_kb.evidence_applicability import CN
from marine_kb.web_supplement import OfficialWebSupplement
from typhoon.web_search import SearchOutcome


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def main():
    backup=ROOT/'tmp/web_retrieval_upgrade_20261018/before/marine_kb'
    old=module('marine_kb._before_web_scope',backup/'web_supplement.py')
    rank=module('marine_kb._before_evidence_rank',backup/'evidence_selection.py')
    old.select_evidence=rank.select_evidence
    original_fresh=old.fresh_public_page
    old.fresh_public_page=lambda page,now=None:original_fresh(page,datetime(2026,10,18,12,tzinfo=CN))
    def row(key,title,body,published='2026-10-18T07:00:00+08:00'):
        return {'url':'https://example.gov.cn/'+key+'.html','title':title,'content':body,
                'published_at':published,'read_status':'success'}
    fixtures=[
      ('同城不同浴场','2026年10月18日朱家尖南沙浴场开放吗？',
       [row('east','朱家尖东沙浴场开放公告','朱家尖东沙浴场在2026年10月18日至2026年10月19日开放，游客应遵守对应浴场管理安排。')],'east','not_direct'),
      ('不同地区规则','浙江海洋灾害应急响应由谁决定？',
       [row('fujian','福建省海洋灾害应急预案','本预案适用于福建省海洋灾害，由福建省指挥部决定启动响应，办公室发布相应通知。')],'fujian','not_direct'),
      ('客运与休闲渔船','2026年10月18日舟山休闲渔船可以复航吗？',
       [row('passenger','舟山客运船复航公告','舟山客运船2026年10月18日至2026年10月19日复航，具体客运航线和旅客乘船安排按公告执行。')],'passenger','not_direct'),
      ('单向航线','2026年10月18日白峰至鸭蛋山航线客运复航了吗？',
       [row('reverse','舟山鸭蛋山至白峰航线复航公告','2026年10月18日至2026年10月19日鸭蛋山至白峰航线客运复航，请旅客注意出发码头与航行方向。')],'reverse','not_direct'),
      ('近期发布但预报过期','2026年10月18日舟山海浪预报？',
       [row('expired','舟山海浪预报','舟山海浪预报有效时段为2026年10月17日至2026年10月17日，近岸海域有效波高为1米，后续预报另行更新。')],'expired','not_direct'),
      ('历史截至时刻','截至2026年10月17日21时30分，朱家尖南沙浴场是否开放？',
       [row('future','朱家尖南沙浴场开放公告','朱家尖南沙浴场在2026年10月18日至2026年10月19日开放，现场管理时间及区域按公告执行。')],'future','not_direct'),
      ('发布时间较早但仍覆盖','2026年10月18日舟山海浪预报？',
       [row('long','舟山海浪预报','舟山海浪预报有效时段为2026年10月18日至2026年10月19日，近岸有效波高为1米，请注意预报有效时段。','2026-10-14T07:00:00+08:00')],'long','direct'),
      ('当前警报有效期','当前舟山海浪警报？',
       [row('warning','舟山海浪警报','舟山海浪警报有效时段为2026年10月18日00时至2026年10月18日23时，近岸海域海浪较大，请注意防范。')],'warning','direct'),
      ('旧版预案状态','浙江现行海洋灾害应急预案？',
       [row('retired','浙江海洋灾害应急预案','有效性：废止。本预案原适用于浙江省海洋灾害应急工作，相关职责和程序按原有版本组织。','2019-01-01')],'retired','not_direct'),
      ('同一场所后续公告','2026年10月18日朱家尖南沙浴场是否开放？',
       [row('closed','朱家尖南沙浴场关闭公告','朱家尖南沙浴场自2026年10月17日起关闭，请游客留意浴场安全管理安排和后续开放通知。','2026-10-17T06:00:00+08:00'),
        row('reopened','朱家尖南沙浴场开放公告','朱家尖南沙浴场在2026年10月18日至2026年10月19日开放，请游客遵守浴场开放时段及现场管理安排。')],'closed','not_direct'),
    ]
    output=[]
    for family,question,pages,target,wanted in fixtures:
        class Search:
            def search_with_status(self,*args,**kwargs):
                return SearchOutcome(state='success',results=[{'url':p['url'],'title':p['title']} for p in pages])
        class Reader:
            def enrich(self,results):
                known={p['url']:p for p in pages}
                return [{**r,**known[r['url']]} for r in results if r['url'] in known]
        before,_=old.OfficialWebSupplement(Search(),Reader(),enabled=True,reference_pages={}).fetch(question,dynamic=True)
        after,_=OfficialWebSupplement(Search(),Reader(),enabled=True,reference_pages={}).fetch(question,dynamic=True)
        suffix='/'+target+'.html'
        old_present=any(h['url'].endswith(suffix) for h in before)
        new=[h for h in after if h['url'].endswith(suffix)]
        use=new[0]['metadata']['applicability']['use'] if new else 'excluded'
        passed=(use=='direct') if wanted=='direct' else use!='direct'
        output.append({'family':family,'question':question,'expected':wanted,
            'before_candidate_retained':old_present,'after_use':use,'passed':passed})
    out=ROOT/'tmp/qa_full160_phase162_20261018_final/scope_replay.json'
    out.write_text(json.dumps({'case_count':len(output),'passed':sum(r['passed'] for r in output),
        'note':'同一人工构造候选正文回放；不代表真实网站可用性、模型最终回答或正确率。','items':output},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(output,ensure_ascii=False))
    assert all(r['passed'] for r in output)


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf8')
    main()
