"""Explicit general explanations, separate from verified business facts."""
import json
import re

GUIDANCE_PROMPT = (
    '你是海洋灾害问答助手，负责在业务事实不足时仍回答用户能理解、能使用的通用知识和方法。'
    '仅返回JSON：{"scope":"general","explanation":"中文说明","missing_inputs":["需要补充的业务输入"]}。'
    '这是通用知识说明，不是本地资料原文，不伪造引用、网址、电话或声称已经联网验证。'
    '先识别用户真正想做的事，直接解释概念、原因、方法、流程框架或准备步骤，不把整篇写成资料不足清单。'
    '复合问题逐项解释；已有核验回答用来确定不必重复的范围，不能改变其中数字。'
    '已有核验回答明确提供的信息视为已知，不再声称缺失；优先补充尚未回答的子问题。'
    '基础定义、物理机制、统计原理和方法可以用通俗语言解释，不因没有专门定义文件就拒答。'
    '假设、情景问题按假设讨论，不要求证明情景现在发生；概括行动时区分一般措施和地方正式命令。'
    '数据题没有结果时说明怎么算、输入是什么、从哪个类型的主管单位核实；不编造本次具体数值。'
    '行政程序只给通用办理框架，不编造现行权限、响应对应关系、法律条款、期限、处罚或赔付比例。'
    '不自行补充某地已出台某项制度、文件版本或地方权限等具体事实，这些只能由核验资料回答。'
    '绝不判断某地此刻安全、确定登陆地点、当前预警、转移命令、复航时间、停电恢复时间、赔付结论。'
    '不生成具体业务数量、风速浪高潮位、损失金额、准确率、联系电话、消毒剂配比或医疗处方。'
    '涉及就医、触电、海上遇险时给求助和避险原则，不作诊断，不让用户冒险自行处置。'
    '不要把缺少现场数据说成概念无法解释，不要杜撰历史案例或用其他地区制度代替本地规定。'
    '篇幅通常200至500字，简单问题更短；用必要的短段或编号，避免每题套同一组栏目。'
)

def explanation_question(question):
    return bool(re.search(r'假设|情景|什么是|是什么|是什么意思|区别|定义|原理|如何|怎么|方法|判据|流程|程序|规范|标准|注意|要求|为什么|知识|智能体|系统',question))

def guidance_payload(question, result):
    meta=result.get('answer_meta') or {}
    return {'question':question,'current_time':meta.get('as_of'),'unresolved_subquestions':[p['question'] for p in meta.get('subquestion_coverage',[]) if p.get('status')!='full'],
            'verified_answer':result.get('answer','') if meta.get('used_mode')=='llm' else '',
            'known_gaps':meta.get('missing_information',[]),
            'instruction':'解释所问的通用知识或处理路径，明确实际业务结论仍需输入；不要补造缺失数值。'}

def decode_guidance(raw):
    value=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',raw).strip())
    answer=value.get('explanation')
    if value.get('scope')!='general' or not isinstance(answer,str) or not 20<=len(answer)<=5000:
        raise ValueError('invalid_general_explanation')
    if re.search(r'\[资料|https?://|(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)0\d{2,3}[-－]\d{7,8}',answer):
        raise ValueError('general_explanation_cannot_invent_sources')
    if re.search(r'\d+(?:\.\d+)?\s*(?:m/s|hPa|厘米|cm|米|亿元|万元|毫升|mg|毫克|小时内|个工作日)',answer,re.I):
        raise ValueError('general_explanation_contains_business_quantity')
    from .quantities import quantities
    if quantities(answer) or re.search(r'\d+(?:\.\d+)?\s*(?:升|毫升|L\b|小时内|天内|天恢复)',answer,re.I):
        raise ValueError('general_explanation_contains_business_quantity')
    if re.search(r'(?:现在|当前|本次|今天|此次).{0,18}(?:已经|已)(?:发布|启动|解除|恢复|停运)|保证安全|肯定安全|一定赔付',answer):
        raise ValueError('general_explanation_contains_live_claim')
    gaps=[x[:300] for x in value.get('missing_inputs',[]) if isinstance(x,str) and x.strip()][:6]
    return answer.strip(),gaps

def system_question(question):
    return bool(re.search(r'你能做|你不能|你的数据|你给的|当前这个系统|本系统|(?:当前|这个|本).{0,18}系统.{0,12}(?:能做|不能做|能力|数据|预报|更新|误差)|(?:这个|本|当前).{0,6}系统.{0,60}(?:接入|网格文件|文件数|多久|配置|缓存|采集|同步)|系统.*(?:能力|数据来源|更新频率)',question))

def system_hit(qa):
    from datetime import datetime
    snapshot_at=datetime.now().astimezone().isoformat(timespec='seconds')
    runtime=getattr(qa,'forecast_data',None)
    data={'知识问答':'本地获准资料与官方网页共同检索，由配置的DeepSeek组织回答并显示引用；通用知识补充单独标明。',
          '连续对话':'保留有限轮次上下文用于理解追问；对话历史不作事实证据。',
          '当前限制':'没有已接入的正式警报签发、响应决定、转移名单、实时停复运或抢修进度，不能代替行政机关决策。',
          '准确率':'当前没有完成独立预报误差或160题专家正确率验收，不给出准确率数值。',
          '知识资料更新':'经本地导入、解析和审核流程后更新，不声称资料实时同步。',
          '网格数据来源':'资料提供方智能网格下载接口与OSS文件，按原批次存储后查询。',
          '台风来源':'浙江海洋监测预报相关业务接口；活动列表查询采用缓存，缓存有效期600秒。'}
    if runtime:
        status=runtime.status()
        data.update({'预报接入状态':{'产品':status.get('products'), '站点登记数':status.get('import',{}).get('sites'),
                  '岸段登记数':status.get('import',{}).get('coasts'),'网格文件登记数':status.get('grid_files'),
                  '配置的网格同步间隔秒':runtime.config()['sync_interval_seconds'],'最近同步':status.get('sync',{}).get('finished_at')},
             '数值能力':'可按站点查天文潮、按位置查已接入网格、按岸段做同刻天文潮与增水叠加及已知阈值比较，全部属于假设口径试算。',
             '试算假设':runtime.config()['notes']})
        if status.get('spatial'):
            data['空间数据查询']=status['spatial']
            data['活动判断能力']='可按指定地点日期查询浙江已登记岸段及浴场参考点的海况、潮汐和官方活动规则。按地域与活动匹配规则，未核定阈值不作超限判定，场地开放和现场条件独立核对。宽泛省市问题先明确地点，参考点不是浴场实测位置。'
    data['快照采集时间']=snapshot_at
    data['快照时间范围']='这是采集时正在运行的代码及配置，不是历史配置归档；不能据此声称过去某日也使用相同配置或已接入同样数量的文件。'
    return {'source_id':'system:runtime','chunk_id':'capabilities','title':'当前系统运行配置与已接入能力',
            'filename':'系统运行说明','locator':'本次服务配置快照','status':'approved','category':'系统说明',
            'text':json.dumps(data,ensure_ascii=False), 'metadata':{'source_type':'system_runtime','external_allowed':True,'retrieved_at':snapshot_at,'evidence_scope':'current_configuration_snapshot'},
            'retrieval_details':{'direct_evidence':True}}
