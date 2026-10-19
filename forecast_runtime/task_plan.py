"""Bounded activity tasks derived from explicit places and clauses.

Coordinates remain owned by the existing registry/resolver. The planner never
invents a beach position, a forecast, or a permission, and records every task
which cannot be executed. Task state is separate from answer completeness.
"""
from __future__ import annotations
import re
from .activity_locations import ACTIVITY_SITES

SUBSITES = {'南沙':'nansha', '东沙':'dongsha', '乌石塘':'wushitang', '白山':'baishan'}
REFERENCE = r'作为.{0,8}依据|代替|替代|等同|代表.{0,12}(?:开放|游泳)|能否通用|可以通用'


def activity_tasks(question, max_tasks=6, coasts=()):
    """Return independent requests, retaining explicit per-clause dates.

An object mentioned solely to ask if its news can replace another object's
evidence is a reference object, not a second permission/data request.
"""
    from .activity_locations import _coast_aliases
    catalog={**ACTIVITY_SITES}
    for coast in coasts:
        catalog['coast:'+str(coast['Id'])]={'name':coast['name'],'aliases':_coast_aliases(coast),
                'city':coast.get('Prefecture') or '', 'county':coast.get('County') or ''}
    aliases=[alias for spec in catalog.values() for alias in spec['aliases']]+list(SUBSITES)
    split=r'[？?！!；;。]+|[，,](?=[^，,？?；;。]{0,24}(?:'+'|'.join(map(re.escape,aliases))+'))'
    clauses = [value.strip() for value in re.split(split, question) if value.strip()]
    mentions = []
    for index, clause in enumerate(clauses):
        found = []
        if re.search(r'朱家尖|舟山|普陀', question) and not re.search(r'广州|岱山.*东沙', clause):
            found += [(name, '朱家尖'+name, site) for name, site in SUBSITES.items() if name in clause]
        for key, spec in catalog.items():
            aliases = sorted((alias for alias in spec['aliases'] if alias in clause), key=len, reverse=True)
            if key.startswith('coast:') and aliases and any(aliases[0] in label or aliases[0] in alias for alias,label,_ in found):
                continue
            if aliases and not (key=='zhujiajian' and found):
                found.append((aliases[0], spec['name'], key))
        if not found:
            continue
        for alias, label, key in found:
            mentions.append({'clause':index,'alias':alias,'label':label,'key':key,
                             'reference_only':bool(re.search(REFERENCE, clause))})
    targets = [m for m in mentions if not m['reference_only']]
    if any(m['key'] in SUBSITES.values() for m in targets):
        targets=[m for m in targets if m['key']!='zhujiajian' or re.search(
            r'能|可以|赶海|游泳|下水|海钓|岸钓|船钓|预报|查询',clauses[m['clause']])]
    unique = list(dict.fromkeys((m['key'],m['clause']) for m in targets))
    if len(unique)<=1:
        return {'version':1,'strategy':'single_location','tasks':[],
                'reference_objects':[m['label'] for m in mentions if m['reference_only']]}
    # Only a leading date/cutoff is shared. Dates on another location's clause
    # never leak into this task. A local explicit date overrides the shared one.
    leading = re.match(r'^(.*?)(?='+ '|'.join(re.escape(m['alias']) for m in mentions) + r')', question)
    prefix = leading[1] if leading else ''
    for spec in catalog.values():
        for name in (*spec['aliases'],spec['city'],spec['city'][:-1],spec['county']):
            if name:prefix=prefix.replace(name,'')
    prefix=prefix.replace('浙江省','').replace('浙江','')
    shared_date = prefix if re.search(r'20\d{2}|今天|明天|后天|\d+月\d+日', prefix) else ''
    date_pattern = r'20\d{2}[-年/]\d{1,2}[-月/]\d{1,2}|今天|明天|后天|\d+月\d+日'
    # A trailing common predicate belongs to every member of a list, whereas
    # predicates explicitly attached to different clauses stay independent.
    tasks=[]
    for key, clause_index in unique:
        mention=next(m for m in targets if m['key']==key and m['clause']==clause_index)
        clause=clauses[mention['clause']]
        same=[m for m in targets if m['clause']==mention['clause']]
        for other in sorted(same,key=lambda m:len(m['alias']),reverse=True):
            if other['key']!=key:
                clause=clause.replace(other['alias'],'')
        # Remove unrelated parent region tokens before the selected site is
        # resolved; keep the target's complete registered name instead.
        for spec in catalog.values():
            if spec['name']!=mention['label']:
                for place in (spec['city'],spec['city'][:-1],spec['county']):
                    if place and place not in mention['label']:clause=clause.replace(place,'')
        if mention['label'].startswith('朱家尖') and mention['alias']!='朱家尖':clause=clause.replace('朱家尖','')
        clause=clause.replace(mention['alias'],mention['label'],1)
        if not re.search(date_pattern,clause) and shared_date:clause=shared_date+clause
        # Child clauses without a verb inherit only explicit shared activity
        # vocabulary, never another place's state or result.
        if not re.search(r'赶海|游泳|下水|海钓|岸钓|矶钓|礁钓|船钓|垂钓',clause):
            activities=list(dict.fromkeys(re.findall(r'赶海|游泳|岸钓|矶钓|礁钓|船钓|海钓|垂钓',question)))
            if activities:clause+='，查询'+ '、'.join(activities)
        tasks.append({'id':len(tasks)+1,'object':mention['label'],'object_key':key,
                      'question':clause.strip(' ，,、和与及'),'state':'planned' if len(tasks)<max_tasks else 'not_executed',
                      'reason':None if len(tasks)<max_tasks else '单次地点数量超过执行上限，请拆分剩余地点。'})
    return {'version':1,'strategy':'per_location','tasks':tasks,
            'reference_objects':[m['label'] for m in mentions if m['reference_only']]}


def single_target_question(question):
    """Omit reference-only clauses from point resolution, keeping the original
    question intact for retrieval and final answer generation."""
    clauses=[value for value in re.split(r'[？?；;。]+',question) if value.strip()]
    kept=[c for c in clauses if not re.search(REFERENCE,c)]
    return '；'.join(kept) if kept else question
