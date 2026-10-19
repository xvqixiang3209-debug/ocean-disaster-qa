"""Fetch available boundary inputs without pretending to run a flood model."""
import re


def impact_inputs_question(question):
    coastal_context=bool(re.search(r'海水|沿海|临海|海边|滨海|靠海|潮位|海潮',question))
    hazard=bool(re.search(r'淹没|被淹|受淹|淹到|淹进|倒灌|漫堤|哪些低洼',question))
    if not hazard and coastal_context:hazard=bool(re.search(r'进水|灌进|灌入|淹水',question))
    if not hazard:return False
    if re.search(r'历史|曾经|哪一次|哪一年|假设|情景|什么是|定义|原理',question):return False
    if not re.search(r'20\d{2}[-年/]\d{1,2}|未来|今天|明天|本次|这次|当前',question):return False
    method=bool(re.search(r'怎么|如何|需要哪些|哪些数据',question))
    outcome=bool(re.search(r'会不会|是否|能否|可能|会.{0,8}(?:淹|进水|倒灌|漫堤)|哪些低洼|哪里.{0,6}淹',question))
    # A parallel "how should I move the car?" does not turn an actual dated
    # flood question into a methods-only request. Rain/plumbing leaks without
    # coastal context never acquire a tide interpretation from this fallback.
    return not method or outcome


def standalone_impact_inputs_question(question):
    """The fallback input query must not erase explicitly requested tasks.

    Existing activity, spatial and numerical planners retain those tasks and
    their own time/location binding. None of them computes inundation.
    """
    if not impact_inputs_question(question):return False
    from .recreation import activity_question
    from .spatial_queries import spatial_question
    from .routing import grid_request,tide_request,analogue_question
    from .activity_policy_queries import policy_question
    return not any(check(question) for check in (activity_question,spatial_question,
                                                grid_request,tide_request,analogue_question,policy_question))


def query_impact_inputs(runtime,question,start,end):
    coasts=runtime.coasts()
    # The finest explicitly named registered county wins over its parent city.
    # No nearest county, invented place coordinate or entire-province default.
    scope=None;ambiguous=False;coast_id=None
    for field in ('name','County','Prefecture'):
        names={str(c.get(field) or '') for c in coasts}
        matched=[name for name in names if len(name)>=2 and (name in question or re.sub(r'[市县区]$','',name) in question)]
        if matched:
            ambiguous=len(matched)>1
            scope=max(matched,key=len);break
    assessment={'state':'not_computed','purpose':'boundary_inputs_only',
        'missing_inputs':['经过验证的本地淹没计算模型与海岸连通性',
                          '统一高程基准的裸地高程及海堤、闸泵、排水等边界资料'],
        'restriction':'岸段代表点潮位和阈值比较不能推断街道、小区、房屋的淹没范围、深度或转移人数。'}
    if re.search(r'转移.*(?:多少|人数)|人口|多少人',question):
        assessment['missing_inputs'].append('风险范围与实际人员台账')
    base={**runtime.base_result(start,end),'kind':'bundle','purpose':'impact_boundary_inputs',
          'impact_assessment':assessment,'items':[],'state':'needs_location'}
    from marine_kb.evidence_applicability import DATE
    from .task_plan import activity_tasks
    if len(list(DATE.finditer(question)))>1 and len(activity_tasks(question,coasts=coasts)['tasks'])>1:
        return {**base,'state':'needs_scope','reason':'复合问题含多个地点和日期，淹没边界输入尚未逐地点绑定时段；各活动数据继续按各自时段查询。'}
    if not scope:
        # Reuse the registry's checked aliases (南沙/东沙 -> 朱家尖 reference
        # coast), rather than guessing a coordinate or dropping its parent coast.
        from .activity_locations import resolve_activity_location
        location=resolve_activity_location(runtime,question)
        if location and location.get('coast_id') and not location.get('coordinate_conflict'):
            coast=next((c for c in coasts if str(c.get('Id'))==str(location['coast_id'])),None)
            if coast:
                scope=coast['name'];coast_id=coast['Id']
    if not scope:
        reason=('已提供坐标，但尚未关联到已登记岸段；不能从其他岸段推断该点的淹没结果。'
                if re.search(r'东经|北纬|经度|纬度',question) else '需要具体已登记县市或岸段，不能将全省代表点当作所问位置。')
        return {**base,'reason':reason}
    if ambiguous:
        return {**base,'reason':'本次包含多个影响地点；请逐地点明确时段，尚未选取其中一地代替其余地点。'}
    selected=[c for c in coasts if any(scope in str(c.get(k) or '') for k in ('name','County','Prefecture'))]
    if len(selected)>20:
        return {**base,'reason':'所问范围包含较多岸段，请明确县区或岸段后提取边界数据。','requested_scope':scope}
    result=runtime.coastal(start,end,region=scope,**({'coast_id':coast_id} if coast_id is not None else {}))
    result['requested_metrics']={'surge':True,'total':True}
    result['input_role']='近岸边界参考，非内陆淹没结果'
    return {**base,'requested_scope':scope,'items':[result],'state':result['state']}
