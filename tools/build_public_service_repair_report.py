"""Export captured HTTP answers without rewriting them or equating labels with accuracy."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
from html import escape
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
CAUSES=[
    ('检索词丢失业务对象','防蚊被改成泛化的洪涝消毒，网箱加固被混成撤离或海塘工程。','以问题类别保留蚊媒、孳生地、锚泊缆绳、池塘进排水等对象与动作；公开查询词不传出内部人名和项目内容。'),
    ('相似资料替代所问资料','政府救助与保险赔付被混用；标题相关就被当作子问题已覆盖。','政府财政救助与保险分别检索、选段和核对；两个子问题分别取得证据，不用保险表替代救助类别。'),
    ('原文读取与关键段落缺失','网页可被搜索发现，但运行中的正文读取遇到412、脚本页面或缺少正文，操作条款也可能在长文中被截掉。','接入实际可读取的官方替代页面、附件和目录；保留独立操作段，搜索摘要不作事实依据；访问失败仍如实记录。'),
    ('适用范围判断过严或错误','通用科普因异地来源被拒；全国救助规范被当成外地规定；查询渠道被送入实时预警清单。','一般方法与地方资格、当天状态分开；识别国家框架与服务目录；渠道/区别问法不查询当前预警清单。'),
    ('校验与回答修复损失信息','“不是地方现行标准”被误识别为肯定现行；“①户主”被当成1户数量；缺少段号或覆盖不匹配时退回无来源解释。','修复否定与户主序号识别；按实际段号仅修引用；覆盖不匹配时逐句核对保留有据部分，不能直接提升为完整。'),
    ('额外制造缺项','已能给出一般步骤，却追加未问的地方专用标准、完整渠道清单或精确金额。','传入用户实际范围和可精确定位的复核目标，限次修订；仍未消除的缺项在实际记录中保留，不美化为全部完成。'),
]
CHANGES=['marine_kb/public_service_intents.py','marine_kb/public_sources.py','marine_kb/web_supplement.py',
         'marine_kb/evidence_applicability.py','marine_kb/qa.py','marine_kb/crag.py',
         'marine_kb/coverage_review.py','marine_kb/question_contract.py','marine_kb/version_claims.py','marine_kb/quantities.py',
         'forecast_runtime/official_warnings.py','marine_kb/chat_api.py']
# Read-back notes for this captured experiment only; never used as QA answers.
READBACK={
 'M1':'风险与清积水、防叮咬已有实质回答。原文同时涉及鼠类和蚊虫，仍需核查回答是否将鼠传疾病与蚊媒疾病混写；模型full标签不代表医学验收。',
 'M2':'已给出花盆、庭院积水处理及个人防护；有官方实际来源。',
 'M3':'已回答蚊虫增多不能推出当地登革热暴发，并给防蚊步骤。将当地病例监测额外列成缺项，仍有范围扩大。',
 'C1':'台风预警与海洋预警已分别给出渠道，原先缺失的气象渠道已补上；系统标签仍偏保守。',
 'C2':'已分别给出气象与海洋渠道及入口，有实际网页来源。',
 'C3':'已给出路径与预警不能互代的解释和查询入口，但仍额外列当前发布状态等未问缺项。',
 'A1':'已用养殖技术手册回答网箱与池塘步骤，另说明保险合同及取证条件；个人是否获赔仍缺具体保单。',
 'A2':'已给出缆绳、框架、网衣检查步骤；仍把温州专用规程作为缺项，属于未消除的范围问题。',
 'A3':'已有塘基、排水及备用设备的实质步骤；地方专用规范属于多余缺项，进排水细部工艺仍未充分取证。',
 'G1':'已回答住房恢复重建、过渡期和冬春救助及国家层面办理路径，不再以保险赔付替代财政救助；地方具体办理信息仍缺。',
 'G2':'已给出户主向村委会申请及户报、村评、乡审、县定程序；规范版本核定与适用提示仍重复。',
 'G3':'本次最终输出严重退化，仅保留一个生效日期，未回答三类制度比较。已经取得相关官方资料，属于生成、校验后恢复与覆盖问题，不能归为单纯缺数据；不得算作完整回答。',
}


def formatted(answer):
    # Escape before applying the two purely presentational transformations.
    text=escape(str(answer))
    text=re.sub(r'\*\*([^*\n]+)\*\*',r'<strong>\1</strong>',text)
    return '<div class="answer">'+text+'</div>'


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run',type=Path,required=True);args=ap.parse_args()
    data=json.loads((args.run/'answers.json').read_text(encoding='utf-8'))
    build=json.loads((args.run/'runtime.json').read_text(encoding='utf-8'))
    test_log=(ROOT/'tmp/public_service_repair_20261019/tests_final.log').read_text(encoding='utf-8')
    test_summary=re.search(r'\d+ passed, \d+ subtests passed',test_log).group()
    assert len(data['items'])==12 and all(r.get('http_status')==200 for r in data['items'])
    for name,digest in build['runtime_source_sha256'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    old_path=ROOT/'exports/CRAG联合检索_160题实际回答与前后对照_20261019.json'
    old={r['id']:r for r in json.loads(old_path.read_text(encoding='utf-8'))['items']}
    items=[]
    for row in data['items']:
        reply=row['reply'];answer=reply.get('answer','')
        assert hashlib.sha256(answer.encode()).hexdigest()==row['answer_sha256']
        before=old.get(row.get('baseline_id'),{}).get('after')
        items.append({**{k:row[k] for k in ['id','family','question','baseline_id','http_status','started_at','completed_at','answer_sha256']},
            'answer':answer,'model_label':reply.get('answerability'), 'missing_information':reply.get('missing_information'),
            'engineering_readback':READBACK[row['id']],
            'sources':reply.get('sources',[]),'validation':reply.get('validation'),
            'evidence_diagnostics':reply.get('evidence_diagnostics'),
            'before':{'answer':before.get('answer'),'model_label':before.get('model_label'),
                      'review':before.get('review'),'sources':before.get('sources'),'answer_sha256':before.get('answer_sha256')} if before else None})
    counts=dict(Counter(r['model_label'] for r in items))
    evidence_counts={
        'http_success':len(items),
        'answers_with_sources':sum(bool(r['sources']) for r in items),
        'both_retrievals_executed':sum(bool((r['evidence_diagnostics'] or {}).get('web_search_executed')) and bool((r['evidence_diagnostics'] or {}).get('local_retrieval_executed')) for r in items),
        'validation_errors':dict(Counter((r.get('validation') or {}).get('error') for r in items if (r.get('validation') or {}).get('error'))),
    }
    record={'created_at':datetime.now().isoformat(timespec='seconds'),'scope':'四类公开服务问题，4条原失败问题和8条异地/复合/边界变体；本轮没有重跑160题。',
        'expert_accepted':False,'labels_note':'full/partial是系统模型标签，不等于专家认定、事实正确率或与豆包胜率。',
        'runtime':build,'counts_model_labels':counts,'execution':evidence_counts,
        'causes_and_changes':[{'cause':a,'observed':b,'change':c} for a,b,c in CAUSES],
        'code_files':CHANGES,'tests':test_summary,
        'research':[{'title':'Corrective Retrieval Augmented Generation','paper':'https://arxiv.org/abs/2401.15884','code':'https://github.com/HuskyInSalt/CRAG',
                     'adaptation':'借鉴质量评估、网页补检、关键段提炼；现有系统独立适配，未运行作者训练代码。'},
                    {'title':'Adaptive-RAG','paper':'https://aclanthology.org/2024.naacl-long.389/','code':'https://github.com/starsuzi/Adaptive-RAG',
                     'adaptation':'借鉴按复杂度分配检索流程；本次未部署作者分类器或宣称复现论文成绩。'}],
        'limitations':['地方补助资格、金额和个人保险是否赔付仍需当地文件、个人受灾核定与保险条款。',
                       '网页渠道说明不能证明某天有预警生效；疫情风险提示不能证明某地已经暴发疫情。',
                       '部分回答仍可能存在多余缺项、措辞重复或校验后子问不完整；实际原文、错误字段和缺项均完整列出。',
                       '没有同题同时间的豆包全量原始输出，不能给出客观胜率或断言其内部技术。'],
        'baseline_file':str(old_path),'baseline_sha256':hashlib.sha256(old_path.read_bytes()).hexdigest(),'items':items}
    target=ROOT/'exports/公开服务问答检索修复_原因与12题实际对照_20261019'
    target.with_suffix('.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    html=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>公开服务问答检索修复与实测</title><style>body{max-width:1100px;margin:36px auto;padding:0 24px;color:#173548;font:16px/1.8 system-ui,Arial}h1,h2,h3{line-height:1.4;color:#087d8e}table{border-collapse:collapse;width:100%;margin:20px 0}td,th{padding:10px;border:1px solid #bad3d9;text-align:left;vertical-align:top}.answer{white-space:pre-wrap;overflow-wrap:anywhere;padding:18px;background:#f2f8f9;border-left:3px solid #168b94}details{margin:18px 0}summary{cursor:pointer;font-weight:600}small{color:#526875}li{margin:8px 0}a{color:#007e95}.before{background:#fff8ed;padding:14px}.notice{background:#eaf4f6;padding:18px}</style>',
          '<h1>公开服务问答检索修复：原因、方法与实际回答</h1>',
          '<p class="notice">'+escape(record['scope'])+' 系统版本：'+escape(build['version'])+'。本报告保存HTTP实际回答，未人工改写回答正文。系统标签不作为事实正确率。</p>',
          '<h2>1. 实测与适用范围</h2><p>自动化测试：'+escape(test_summary)+'。真实问答执行指标：</p><pre>'+escape(json.dumps(evidence_counts,ensure_ascii=False,indent=2))+'</pre><p>系统自评标签：'+escape(str(counts))+'。这些标签没有经过专家验收。</p>',
          '<p class="notice">人工逐条读回（工程检查，非专家验收）：4条原失败问法均有实质改善。但G3复合救助比较在本次最终运行仍严重退化，不能算作已解决；部分变体仍有多余缺项。全部实际回答与失败诊断在下方保留。</p>',
          '<h2>2. 为什么公开资料仍没答好</h2><table><tr><th>原因</th><th>已观察到的表现</th><th>本次修改</th></tr>']
    html += ['<tr>'+''.join('<td>'+escape(v)+'</td>' for v in cause)+'</tr>' for cause in CAUSES]
    html += ['</table><h2>3. 文献和作者代码</h2>']
    for r in record['research']:
        html += ['<p><a href="'+r['paper']+'">'+escape(r['title'])+'</a> · <a href="'+r['code']+'">作者代码</a>。'+escape(r['adaptation'])+'</p>']
    html += ['<h2>4. 为什么截图里的豆包更清楚</h2><p>从用户提供的输出可观察到，豆包更常先给结论、按子问题列条件和步骤，并展示多篇网页来源。本系统之前的查询、选段和范围校验使生成模型拿不到关键证据，或在校验失败后丢掉已支持内容。调用DeepSeek本身不会自动完成检索、页面读取、证据合并与数值计算。豆包内部实现不可据截图确认，答得流畅、引用多也不能替代逐条核验。</p>',
             '<h2>5. 12题实际回答与原失败题对照</h2>']
    for row in items:
        html += ['<section><h3>'+escape(row['id']+' '+row['question'])+'</h3><small>模型标签：'+escape(str(row['model_label']))+'；HTTP '+str(row['http_status'])+'；回答SHA256：'+row['answer_sha256']+'</small>']
        if row['before']:
            html += ['<details class="before"><summary>修改前：'+escape(row['baseline_id'])+'（上一轮160题实际输出）</summary>'+formatted(row['before']['answer'])+'</details>']
        html += ['<p class="notice">工程读回：'+escape(row['engineering_readback'])+'</p>',formatted(row['answer']),'<p>系统保留的缺项：'+escape(json.dumps(row['missing_information'],ensure_ascii=False))+'</p>',
                 '<details><summary>本次引用来源与实际选段</summary><ol>']
        for s in row['sources']:
            url=s.get('url') or '';title=s.get('title') or s.get('filename') or '本地资料'
            link='<a href="'+escape(url,quote=True)+'">'+escape(title)+'</a>' if url.startswith(('https://','http://')) else escape(title)
            html += ['<li>'+link+'<pre class="answer">'+escape(json.dumps(s.get('selected_passages',s.get('excerpt','')),ensure_ascii=False,indent=2))+'</pre></li>']
        html += ['</ol></details><details><summary>校验、检索与覆盖诊断（原始字段）</summary><pre class="answer">'+escape(json.dumps({'validation':row['validation'],'diagnostics':row['evidence_diagnostics']},ensure_ascii=False,indent=2))+'</pre></details></section>']
    html += ['<h2>6. 保留的边界</h2><ul>']+['<li>'+escape(t)+'</li>' for t in record['limitations']]+['</ul><p>本轮未更改地方阈值、补助金额、个人保单或当前预警事实。代码修改见同名JSON的文件清单与运行源文件哈希。</p></html>']
    target.with_suffix('.html').write_text('\n'.join(html),encoding='utf-8')
    print(json.dumps({'report':str(target.with_suffix('.html')),'labels':counts,'execution':evidence_counts},ensure_ascii=False))


if __name__=='__main__':main()
