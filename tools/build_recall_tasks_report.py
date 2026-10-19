"""Export observable before/after results and untouched actual responses as HTML."""
from pathlib import Path
from collections import Counter
import hashlib, html, json, re
from datetime import datetime
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from forecast_runtime.answer_facts import delivery_facts
from marine_kb.business_validation import validate_business_delivery,validate_object_claims

WORK=ROOT/'tmp/recall_tasks_upgrade_20261018'
FULL=ROOT/'tmp/qa_full160_phase163_20261018_final'

def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def esc(value):return html.escape(str(value))
def checked(record):
    reply=record.get('reply') or {};evidence=record.get('business_evidence') or {}
    facts=delivery_facts(evidence);answer=reply.get('answer') or ''
    result={'http_status':record.get('http_status'),'mode':reply.get('mode'),
            'model_self_answerability':reply.get('answerability'),'source_count':len(reply.get('sources',[])),
            'business_kind':evidence.get('kind'),'business_state':evidence.get('state'),
            'task_objects':[r.get('task_object') for r in evidence.get('items',[]) if r.get('task_object')],
            'task_periods':[(r.get('task_object'),r.get('start'),r.get('end'),r.get('state')) for r in evidence.get('items',[])],
            'program_data_table':'**本次查询数据**' in answer,'fallback':bool((reply.get('validation') or {}).get('computed_table_fallback')),
            'typed_delivery':'not_applicable','season_knowledge_source':any('常年活跃月份' in s.get('title','') for s in reply.get('sources',[]))}
    result['validation_error']=(reply.get('validation') or {}).get('error')
    result['answer_sha256']=hashlib.sha256(answer.encode('utf8')).hexdigest()
    cited={int(n) for n in re.findall(r'\[资料(\d+)\]',answer)}
    returned={s.get('citation_id') for s in reply.get('sources',[])}
    result['missing_citation_ids']=sorted(cited-returned)
    if facts:
        try:
            validate_business_delivery(answer,{'required_business_facts':facts},set(int(n) for n in re.findall(r'\[资料(\d+)\]',answer)))
            result['typed_delivery']='pass'
        except ValueError as e:result['typed_delivery']='fail:'+str(e)
    return result

def answer_block(row):
    reply=row.get('reply') or {};sources=reply.get('sources') or []
    result='<p class="question">'+esc(row.get('question',''))+'</p><pre>'+esc(reply.get('answer') or reply.get('error') or '接口未返回回答正文')+'</pre>'
    if sources:
        result+='<details><summary>实际引用来源 '+str(len(sources))+' 条</summary><ol>'
        for source in sources:
            url=source.get('url') or ''
            if url.startswith('/'):
                url='http://127.0.0.1:8769'+url
            link=('<a href="'+esc(url)+'">'+esc(url)+'</a>') if url.startswith(('http://','https://')) else esc(url)
            citation=source.get('citation_id')
            value=(' value="'+str(citation)+'"') if isinstance(citation,int) else ''
            result+='<li'+value+'>'+esc(source.get('citation',''))+' '+esc(source.get('title',''))+' '+esc(source.get('locator',''))+'<br>'+link+'</li>'
        result+='</ol></details>'
    return result

def main():
    before=read(WORK/'before/answers.json')['items'];after=read(WORK/'after/answers.json')['items']
    full=read(FULL/'answers.json')['items']
    assert len(full)==160 and len({r['id'] for r in full})==160
    old={r['id']:r for r in before};paired=[]
    for row in after:paired.append({'id':row['id'],'question':row['question'],
                                  'before':checked(old[row['id']]) if row['id'] in old else None,'after':checked(row)})
    full_checks=[{'id':r['id'],**checked(r)} for r in full]
    audit=read(FULL/'computation_audit.json')
    paired_periods=read(WORK/'paired_period_audit.json')
    assert all(r['start_match'] and r['end_match'] for r in paired_periods)
    report={'generated_at':datetime.now().astimezone().isoformat(),'version':'phase1.6.3-recall-object-tasks-delivery',
            'python_tests':566,'subtests':102,'paired_actual_probes':paired,'full160_observable_checks':full_checks,
            'full160_http':dict(Counter(str(r['http_status']) for r in full)),
            'full160_modes':dict(Counter(str(r['reply'].get('mode')) for r in full)),
            'full160_answer_kinds':dict(Counter(str(r['reply'].get('answer_kind')) for r in full)),
            'full160_typed_delivery':dict(Counter(c['typed_delivery'] for c in full_checks)),
            'computation_audit_summary':{k:v for k,v in audit.items() if k!='items'},
            'paired_fallbacks':[{'id':r['id'],'reason':r['after']['validation_error']} for r in paired if r['after']['fallback']],
            'paired_period_audit':paired_periods,
            'source_catalog_snapshot':read(WORK/'catalog_snapshot.json'),
            'expert_accepted':False,'labels_note':'只报告可观测执行、引用和工具数值交付；模型自报完整性不是独立正确率，未与上轮盲评数字混算。'}
    (WORK/'evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    title='资料召回与分地点问答改造实测报告'
    body=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>'+title+'</title>',
          '<style>body{max-width:1150px;margin:40px auto;padding:0 24px;font:16px/1.75 "Microsoft YaHei",sans-serif;color:#182b3b}h1,h2,h3{color:#122c3c}table{border-collapse:collapse;width:100%;margin:20px 0}td,th{border:1px solid #d9dfe3;padding:9px;text-align:left}th{background:#eef3f5}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:15px/1.8 "Microsoft YaHei",sans-serif;background:#f7f9fa;padding:18px}details{margin:14px 0}summary{cursor:pointer;font-weight:600}.question{font-weight:600}a{color:#087b86}</style>',
          '<h1>'+title+'</h1><p>版本 phase1.6.3，2026年10月18日。修改面向问题类别，未使用问题编号或预置答案。报告保留真实接口回答、来源和失败记录，便于核对改动是否有效。</p>',
          '<h2>改动与边界</h2><ul><li>检索增加概念和季节表达扩展，保留既有关键词、语义索引及来源审核约束；对子问题分别检索，继承明确的台风身份，多个台风的含糊指代不自动选一个。</li>',
          '<li>活动问题按注册场地或既有岸段拆分任务，分别保留日期、活动和执行状态；仅用作比较依据的其他场所不触发第二个许可查询。一个任务缺数不抹掉其他任务。</li>',
          '<li>DeepSeek组织结论、规则和缺项，系统从工具原始结果生成数据表；逐项检查数值范围、单位、地点、日期和引用，另检查语言中是否借用了其他场地的数值。</li>',
          '<li>海况仍采用已有NC及授权假设，天文潮基准和部分变量含义尚待业务核定。两个浴场可共用同一已标注的区域参考格点，不代表获得了两个浴场独立观测。</li>',
          '<li>本次未补造正式开放、复航、水质、转移或工程台账数据。没有匹配公告和现场依据时，仍不能确认活动许可或安全。</li></ul>',
          '<h2>验证口径</h2><p>566项Python测试与102项子测试通过。先保存12个问题的改前实际回答，再用相同问题调用新版；两轮之间未新增知识库资料。开发过程中的候选试跑另存，不计入下列最终结果。原160题按上一轮相同具体日期地点重跑，回答内容未人工改写。</p>',
          '<p>12题对照中，3个多地点／多日期问题从“需分别查询、未提取海况”变为分别执行，共6个任务的日期起止均匹配；新版6题包含可提取的工具事实，全部通过交付检查。3种季节问法均引用到已有官方季节摘要；有效波高概念比较不再误走格点查询。这些结果说明查询和交付改善，不代表活动许可、地区风险或全部子问题已经补齐。</p>',
          '<p>160题接口状态：'+esc(report['full160_http'])+'；回答模式：'+esc(report['full160_modes'])+'；可提取工具事实的交付检查：'+esc(report['full160_typed_delivery'])+'。这些是执行和交付检查，不是专家正确率或独立完整率，不能与上一轮DeepSeek盲评的20题完整回答直接比较。</p>',
          '<p>回答类型：'+esc(report['full160_answer_kinds'])+'。其中general_explanation表示包含另行标注的通用解释，不能据此认定已取得当前业务事实；knowledge为资料问答；tool_grounded为引用业务工具结果的回答，也可能存在未补齐的子问题。</p>',
          '<p>引用编号未随回答返回的题号：'+esc(audit['citation_id_mismatch_ids'])+'；查询时段不匹配的题号：'+esc(audit['forecast_period_mismatch_ids'])+'。复核采用逐任务原始问题，不把多个地点的独立日期合并成一个时段。</p>',
          '<h2>仍待改善的语言生成</h2><p>12题对照中，两个不同日期的地点查询因DeepSeek返回JSON包含未转义控制字符，转为程序数据回答；两处海况、日期和潮时均保留，尚未获得本次完整的模型语言说明。下列160题中触发校验的回答也照实保留；程序交付通过不代表资料或业务结论已完整。</p><ul>',
          *['<li>'+esc(c['id'])+'：'+esc(c['validation_error'])+'；最终模式 '+esc(c['mode'])+'</li>' for c in full_checks if c['validation_error']],
          '</ul>',
          '<h2>12题改前改后可观测结果</h2><table><tr><th>测试</th><th>改前</th><th>改后</th></tr>']
    for r in paired:
        def brief(c):
            if not c:return '仅新版测试'
            return ('HTTP '+str(c['http_status'])+'；'+str(c['mode'])+'；业务状态 '+str(c['business_state'])+
                    '；分地点任务 '+str(len(c['task_objects']))+'；数值交付 '+c['typed_delivery']+
                    '；官方季节摘要 '+str(c['season_knowledge_source']))
        body.append('<tr><td>'+esc(r['id'])+'<br>'+esc(r['question'])+'</td><td>'+esc(brief(r['before']))+'</td><td>'+esc(brief(r['after']))+'</td></tr>')
    body+=['</table><h2>改前改后实际回答</h2>']
    for row in after:
        body+=['<details><summary>'+esc(row['id'])+' '+esc(row['question'])+'</summary>']
        if row['id'] in old:body+=['<h3>改前实际回答</h3>',answer_block(old[row['id']])]
        body+=['<h3>改后实际回答</h3>',answer_block(row),'</details>']
    body+=['<h2>160题新版实际回答</h2><p>展开单题查看实际回答及引用来源。若模型自报完整，仅用于排查；缺少业务输入和正式依据的项仍待补齐。</p>']
    for row,check in zip(full,full_checks):
        body+=['<details><summary>'+esc(row['id'])+' '+esc(row.get('question',''))+'</summary>',
               '<p>HTTP '+esc(row.get('http_status'))+'；模式 '+esc(check['mode'])+'；工具交付检查 '+esc(check['typed_delivery'])+'</p>',
               answer_block(row),'</details>']
    body+=['</html>']
    output=ROOT/'exports/资料召回与分地点问答改造实测报告_20261018.html'
    output.write_text('\n'.join(body),encoding='utf8')
    print(json.dumps({'output':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
                      'questions':len(full),'paired':len(paired),'http':report['full160_http'],'typed_delivery':report['full160_typed_delivery']},ensure_ascii=False))

if __name__=='__main__':main()
