"""Expose the same scoped, versioned policy selection used by activity checks."""
from __future__ import annotations

import json
import re

from .activity_rules import load_rules


def policy_digest(policy):
    source=policy.get('source') or {}
    return {**{key:policy.get(key) for key in ('id','title','kind','status','version','published_at',
                'effective_from','effective_to','scope','summary','not_applicable','requirements','numeric_rules')},
            'source':{key:source.get(key) for key in ('url','publisher','paragraph')},
            'checked_at':(source.get('verification') or {}).get('checked_at')}


def policy_question(question):
    activity=bool(re.search(r'赶海|游泳|浴场|岸钓|矶钓|船钓|海钓|涉海活动',question))
    policy=bool(re.search(r'规则|规定|标准|依据|阈值|条件|手续|管理要求',question))
    dated=bool(re.search(r'今天|明天|后天|当日|预报|\d{1,2}月\d{1,2}日|20\d{2}[-/]',question))
    if re.search(r'现在|当前|本次',question) and re.search(r'能否|能去|可以去|适宜|适合|海况|浪高|风速',question):dated=True
    return activity and policy and not dated


def policy_scope(runtime,question):
    from .recreation import resolve_location
    protected=[(name,identity) for name,identity in (
        ('马鞍列岛','ma_an_islands'),('中街山列岛','zhongjieshan_islands')) if name in question]
    if len(protected)==1 and '朱家尖' not in question:
        name,identity=protected[0]
        return {'key':'policy:'+identity,'name':name+'保护区','country':'CN','province':'浙江省',
                'protected_area_id':identity,'scope_kind':'explicitly_named_policy_area_only',
                'not_a_forecast_sampling_point':True,
                'coordinate_note':'按用户明确提到的保护区查询规则，没有推算具体坐标是否位于批准边界内。'}
    location=resolve_location(runtime,question)
    if location and not location.get('coordinate_conflict'):
        return location
    if location:
        return {'key':'unresolved','coordinate_conflict':True,'country':'CN','province':None,
                'reason':location.get('coordinate_note')}
    cities={'嘉兴':'嘉兴市','杭州':'杭州市','宁波':'宁波市','舟山':'舟山市','台州':'台州市','温州':'温州市'}
    city=next((name for short,name in cities.items() if short in question),None)
    if city or '浙江' in question:
        return {'key':'zhejiang' if not city else 'region:'+city,'province':'浙江省','city':city,
                'name':city or '浙江省','scope_kind':'administrative_policy_scope_only',
                'not_a_forecast_sampling_point':True}
    return {'key':'national','name':'全国通用依据','country':'CN','province':None,
            'scope_kind':'national_policy_scope_only','not_a_forecast_sampling_point':True}


def query_policies(runtime,question,start,end):
    from .recreation import requested_activities,LABELS
    scope=policy_scope(runtime,question)
    items=[]
    for activity in requested_activities(question):
        rules=load_rules(getattr(runtime,'activity_rules',None),location=scope,
                         activity=activity,start=start,end=end)
        selection=rules.get('policy_selection') or {}
        # Inactive drafts/retired examples never enter active answer evidence.
        excluded=[{key:row.get(key) for key in ('id','title','status','reason')}
                  for row in selection.get('excluded_policies',[])]
        items.append({'activity':activity,'label':LABELS[activity],
                      'eligible_policies':selection.get('eligible_policies',[]),
                      'excluded_policy_reasons':excluded,
                      'unverified_reference_count':len(selection.get('reference_policies',[])),
                      'state':selection.get('state','no_matching_rule')})
    return {**runtime.base_result(start,end),'kind':'activity_rules','state':'ready','trial':False,
            'assumptions':[],'label':'涉海活动官方依据查询','location':scope,'items':items,
            'catalog_version':rules.get('version'),'catalog_fingerprint':rules['fingerprint'],
            'query_nature':'policy_scope_and_effective_version_not_live_opening_status',
            'limitations':['规则的适用不代表当天场地已开放或船舶获准出航。',
                '未核实全文、失效、征求意见稿和跨地区条款不作为本次活动数值判据。',
                '规则没有提供的活动阈值保留缺项，不恢复旧演示阈值。']}


def policy_summary(result):
    compact={**result,'items':[{**row,'eligible_policies':[policy_digest(p) for p in row['eligible_policies']]}
                             for row in result['items']]}
    return '按地点、活动、查询日期匹配的官方依据；这是规则查询，不是当天活动许可。\n'+json.dumps(
        compact,ensure_ascii=False,separators=(',',':'))
