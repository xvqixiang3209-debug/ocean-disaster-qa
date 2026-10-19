"""Export actual runs and limitations; never replace model answers with ideals."""
from collections import Counter
from datetime import datetime
from pathlib import Path
import hashlib,html,json,re,sqlite3,sys
from math import ceil
from statistics import median

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from forecast_runtime.answer_facts import delivery_facts

WORK=ROOT/'tmp/autonomous_v172_20261018'
OLD=ROOT/'tmp/qa_full160_phase164_20261018_final'
NEW=ROOT/'tmp/qa_full160_phase172_20261018_final'


def load(path):return json.loads(path.read_text(encoding='utf8'))
def esc(value):return html.escape(str(value))
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
def rel(path):return '../'+path.relative_to(ROOT).as_posix()


def readable_answer(text):
 """Render a small, escaped Markdown subset; retain the original below it.

 No raw HTML, scripts or model-generated link targets are executed. Ambiguous
 table column counts fall back to escaped text rather than shifting values.
 """
 def inline(value):
  value=esc(value)
  value=re.sub(r'`([^`\n]+)`',r'<code>\1</code>',value)
  return re.sub(r'\*\*([^*\n]+)\*\*',r'<strong>\1</strong>',value)
 def cells(line):return [c.strip() for c in line.strip().strip('|').split('|')]
 lines=str(text).splitlines();out=[];i=0
 while i<len(lines):
  line=lines[i].strip()
  if not line:i+=1;continue
  if line.startswith('```'):
   block=[];i+=1
   while i<len(lines) and not lines[i].strip().startswith('```'):block.append(lines[i]);i+=1
   out.append('<pre>'+esc('\n'.join(block))+'</pre>');i+=1;continue
  if line.startswith('|') and i+1<len(lines) and re.fullmatch(r'[|:\s-]+',lines[i+1]):
   header=cells(line);raw=[lines[i],lines[i+1]];rows=[];i+=2
   while i<len(lines) and lines[i].strip().startswith('|'):
    raw.append(lines[i]);rows.append(cells(lines[i]));i+=1
   if not header or any(len(row)!=len(header) for row in rows):
    out.append('<pre>'+esc('\n'.join(raw))+'</pre>');continue
   out.append('<table><thead><tr>'+''.join('<th>'+inline(c)+'</th>' for c in header)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+inline(c)+'</td>' for c in row)+'</tr>' for row in rows)+'</tbody></table>');continue
  heading=re.match(r'^#{1,6}\s+(.+)$',line)
  if heading:out.append('<h4>'+inline(heading[1])+'</h4>');i+=1;continue
  bullet=re.match(r'^(?:[-*•]|\d+\.)\s+(.+)$',line)
  if bullet:
   ordered=bool(re.match(r'^\d+\.',line));tag='ol' if ordered else 'ul';rows=[]
   while i<len(lines):
    item=re.match(r'^(?:\d+\.)\s+(.+)$' if ordered else r'^[-*•]\s+(.+)$',lines[i].strip())
    if not item:break
    number=re.match(r'^(\d+)\.',lines[i].strip())[1] if ordered else None
    rows.append('<li'+(' value="'+number+'"' if number else '')+'>'+inline(item[1])+'</li>');i+=1
   start=re.match(r'^(\d+)\.',line)[1] if ordered else None
   out.append('<'+tag+(' start="'+start+'"' if start else '')+'>'+''.join(rows)+'</'+tag+'>');continue
  paragraph=[line];i+=1
  while i<len(lines) and lines[i].strip() and not re.match(r'^(?:[|#]|```|[-*•]\s|\d+\.\s)',lines[i].strip()):
   paragraph.append(lines[i]);i+=1
  out.append('<p>'+'<br>'.join(inline(value) for value in paragraph)+'</p>')
 return ''.join(out)


def stats(data):
 rows=data['items'];errors=[];unbacked=[];facts=[]
 for row in rows:
  r=row.get('reply') or {};error=(r.get('validation') or {}).get('error')
  refs={int(n) for n in re.findall(r'\[资料(\d+)\]',r.get('answer',''))}
  cited={s.get('citation_id') for s in r.get('sources',[])}
  if error:errors.append({'id':row['id'],'error':error,'grounded_partial_retained':bool(refs&cited)})
  if not refs&cited:unbacked.append(row['id'])
  if row.get('business_evidence'):facts.append({'id':row['id'],'count':len(delivery_facts(row['business_evidence']))})
 times=sorted(r['elapsed_seconds'] for r in rows if isinstance(r.get('elapsed_seconds'),(int,float)))
 return {'question_count':len(rows),'http200':sum(r.get('http_status')==200 for r in rows),
   'latency_seconds':{'median':round(median(times),2),'p95':round(times[ceil(len(times)*.95)-1],2)} if times else {},
   'grounded_count':len(rows)-len(unbacked),'without_visible_grounding':unbacked,
   'answerability_self_report':dict(Counter((r.get('reply') or {}).get('answerability') for r in rows)),
   'kinds':dict(Counter((r.get('reply') or {}).get('answer_kind') for r in rows)),
   'errors':errors,'typed_fact_question_count':len(facts),'typed_fact_count':sum(f['count'] for f in facts),
   'attribution_recovery_count':sum(bool((r.get('reply') or {}).get('evidence_diagnostics',{}).get('claim_attribution_recovery')) for r in rows)}


def record_block(row,label):
 r=row.get('reply') or {};sources=r.get('sources',[])
 meta={k:r.get(k) for k in ('answer_kind','answerability','business_state','missing_information','subquestion_coverage','validation','evidence_diagnostics')}
 def source_label(source):
  title=esc(source.get('title',''));url=source.get('url','')
  if isinstance(url,str) and url.startswith(('https://','http://')):
   title='<a href="'+esc(url)+'" rel="noopener noreferrer">'+title+'</a>'
  return esc(source.get('citation',''))+' '+title+' · '+esc(source.get('locator',''))+' · '+esc(source.get('review_label') or source.get('status',''))
 source_rows=''.join('<li>'+source_label(s)+'</li>' for s in sources)
 return '<details class="answer-block"><summary>'+esc(label)+'</summary><div class="rendered-answer">'+readable_answer(r.get('answer',''))+'</div><details><summary>查看逐字原文</summary><pre class="actual">'+esc(r.get('answer',''))+'</pre></details><p>本次返回的来源：</p><ol>'+source_rows+'</ol><details><summary>原始状态、缺项与被拦截草稿（仅审计）</summary><pre>'+esc(json.dumps(meta,ensure_ascii=False,indent=2))+'</pre></details></details>'


def main():
 WORK.mkdir(exist_ok=True)
 before=load(OLD/'answers.json');after=load(NEW/'answers.json');audit=load(NEW/'computation_audit.json')
 passage_audit=load(NEW/'selected_passage_audit.json')
 probe_gate=load(WORK/'release_gate_before_full160.json')
 prior_causes=load(ROOT/'tmp/gap137_upgrade_20261018/cause_audit_137.json')
 assert len(prior_causes)==137
 cause_by_id={row['id']:row for row in prior_causes}
 cause_counts=Counter(row['label'] for row in prior_causes)
 assert len(after['items'])==160 and len({r['id'] for r in after['items']})==160
 identity=load(NEW/'run_identity.json');old_by={r['id']:r for r in before['items']}
 end_identity=load(WORK/'identity_end.json')
 assert end_identity['version']==identity['running_version']
 assert end_identity['runtime_source_sha256']==identity['runtime_source_sha256']
 assert all(r['question']==old_by[r['id']]['question'] for r in after['items'])
 assert not audit['forecast_period_mismatch_ids'] and not audit['citation_id_mismatch_ids'] and not audit['typhoon_snapshot_cutoff_mismatch_ids']
 assert not passage_audit['hash_mismatches'] and not passage_audit['local_source_mismatches']
 assert all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in identity['runtime_source_sha256'].items())
 a,b=stats(before),stats(after)
 # These are observed final-run limitations, not a guessed root-cause label
 # for every partial answer. Keep them separate from external data gaps.
 final_by={row['id']:row for row in after['items']}
 technical_review=[
  {'id':'Q-037','category':'回答生成与校验恢复','observation':'正文保留了分级依据及平均风时距，但恢复后的实际回答没有给出所问五级的具体风速阈值。来源已检索到，不能仅归为缺数据。','resolved':False},
  {'id':'R-007','category':'历史比较与结果表达','observation':'本次语言草稿被引用校验拒绝，实际只交付台风接口风速表，未完成蔷琵与利奇马的浙江风暴潮影响比较；表首查询时段显示处理时刻，不能当作历史业务有效时段。','resolved':False},
  {'id':'R-059','category':'标准状态数据与证据利用','observation':'已找到标准及替代关系，现行状态确认仍不完整；实际保留的是候选版本信息，不能列为完整现行目录。','resolved':False},
 ]
 for item in technical_review:
  item['actual_answer']=final_by[item['id']]['reply']['answer']
 save(WORK/'remaining_issue_review.json',technical_review)
 with sqlite3.connect(ROOT/'data/marine_kb/knowledge.sqlite3') as c:
  catalog=c.execute('select source_id,sha256,status,metadata,updated_at from sources order by source_id').fetchall()
 catalog_hash=hashlib.sha256(json.dumps(catalog,ensure_ascii=False).encode()).hexdigest()
 assert catalog_hash==load(ROOT/'tmp/autonomous_v165_20261018/catalog_before.json')['source_catalog_sha256']
 evaluation={'before':a,'after':b,'run_id':identity['run_id'],'runtime_hash_count':len(identity['runtime_source_sha256']),
             'knowledge_source_catalog_sha256':catalog_hash,'running_identity_unchanged_at_end':True,
             'audit':{k:v for k,v in audit.items() if k!='items'},
             'passage_audit':{k:v for k,v in passage_audit.items() if k!='items'},
             'prior_137_cause_groups':dict(cause_counts),'targeted_probe_gate':probe_gate,
             'observed_remaining_technical_issues':[{'id':x['id'],'category':x['category'],'observation':x['observation']} for x in technical_review],
             'expert_accepted':False}
 save(WORK/'evaluation.json',evaluation)
 blocks=[]
 for row in after['items']:
  r=row['reply'];missing='；'.join(r.get('missing_information') or [])
  refs={int(n) for n in re.findall(r'\[资料(\d+)\]',r.get('answer',''))}
  cited={s.get('citation_id') for s in r.get('sources',[])}
  grounded=bool(refs&cited)
  badge=('有引用、模型自评完整' if r.get('answerability')=='full' and grounded else '有引用、仍需核对完整性' if grounded else '缺少可展示引用')
  cause=cause_by_id.get(row['id'])
  context=('<p>较早137题审计中的主因：'+esc(cause['label'])+'；'+esc(cause['reason'])+'（这是旧版诊断，当前缺项见实际记录。）</p>') if cause else ''
  blocks.append('<article id="'+esc(row['id'])+'" data-filter="'+esc(badge)+'"><h3>'+esc(row['id']+' '+row['question'])+'</h3><p class="badge">'+badge+'</p>'+context+'<p>'+esc(missing)+'</p>'+record_block(row,'新版实际回答')+record_block(old_by[row['id']],'改造前实际回答')+'</article>')
 probes=[];probe_answers=[]
 for folder in [ROOT/'tmp/autonomous_v165_20261018/additional_cases',ROOT/'tmp/autonomous_v166_20261018/release_cases',
                ROOT/'tmp/autonomous_v168_20261018/release_cases_final2',ROOT/'tmp/autonomous_v168_20261018/conversation_final2',ROOT/'tmp/autonomous_v168_20261018/composite_cases_final',
                ROOT/'tmp/autonomous_v169_20261018/release_cases_final',ROOT/'tmp/autonomous_v169_20261018/conversation_final',ROOT/'tmp/autonomous_v169_20261018/composite_cases_final',ROOT/'tmp/autonomous_v169_20261018/definition_cases_final',
                ROOT/'tmp/autonomous_v170_20261018/release_cases',ROOT/'tmp/autonomous_v170_20261018/conversation',ROOT/'tmp/autonomous_v170_20261018/composite_cases',ROOT/'tmp/autonomous_v170_20261018/definition_cases',ROOT/'tmp/autonomous_v170_20261018/property_cases',
                ROOT/'tmp/autonomous_v171_20261018/release_cases',ROOT/'tmp/autonomous_v171_20261018/conversation',ROOT/'tmp/autonomous_v171_20261018/composite_cases',ROOT/'tmp/autonomous_v171_20261018/definition_cases',ROOT/'tmp/autonomous_v171_20261018/property_cases',ROOT/'tmp/autonomous_v171_20261018/warning_cases',
                WORK/'acceptance/core',WORK/'acceptance/conversation',WORK/'acceptance/multi_facet',WORK/'acceptance/definitions',WORK/'acceptance/property',WORK/'acceptance/warnings',WORK/'acceptance/retrieval_intents']:
  if (folder/'answers.json').exists():
   folder_records=load(folder/'answers.json')['items'];folder_blocks=[]
   for row in folder_records:
    probe_answers.append(row['reply']['answer'])
    folder_blocks.append('<article><h3>'+esc(row['id']+' '+row['question'])+'</h3>'+record_block(row,'查看实际回答')+'</article>')
   probes.append('<details><summary>'+esc(folder.parent.name+' / '+folder.name)+' · '+str(len(folder_records))+'次请求</summary>'+''.join(folder_blocks)+'</details>')
 # Preserve both the failed full-run answer and a successful repeat from the
 # same older version. A later success never replaces a failed checkpoint.
 repeat=load(ROOT/'tmp/autonomous_v168_20261018/definition_diagnostic.json')
 for label,row in [('v1.6.8完整测试中的原始Q-001',load(ROOT/'tmp/qa_full160_phase168_20261018_final/Q-001.json')),
                   ('v1.6.8同版独立复测',{'reply':repeat['reply']}),
                   ('v1.7.0完整测试中的原始Q-004',load(ROOT/'tmp/qa_full160_phase170_20261018_final/Q-004.json'))]:
  probe_answers.append(row['reply']['answer'])
  probes.append('<article><h3>'+esc(label)+'</h3>'+record_block(row,'查看实际回答')+'</article>')
 metrics=[('真实HTTP请求',a['http200'],b['http200']),('正文引用与返回来源同时存在',a['grounded_count'],b['grounded_count']),
          ('带结构化业务工具记录的问题',a['typed_fact_question_count'],b['typed_fact_question_count']),('程序事实条数（含范围限制）',a['typed_fact_count'],b['typed_fact_count']),
          ('最终记录中的校验错误（含部分恢复）',len(a['errors']),len(b['errors'])),('没有可展示引用',len(a['without_visible_grounding']),len(b['without_visible_grounding']))]
 if a['latency_seconds'] and b['latency_seconds']:
  metrics.extend([('请求耗时中位数（秒）',a['latency_seconds']['median'],b['latency_seconds']['median']),
                  ('请求耗时P95（秒）',a['latency_seconds']['p95'],b['latency_seconds']['p95'])])
 table='<table><tr><th>可直接统计的项目</th><th>v1.6.4</th><th>v1.7.2</th></tr>'+''.join('<tr><td>'+esc(k)+'</td><td>'+str(x)+'</td><td>'+str(y)+'</td></tr>' for k,x,y in metrics)+'</table>'
 papers=[
 ('北海风暴潮变分同化：收益与局限','https://nora.nerc.ac.uk/id/eprint/529769/','','论文CC BY 4.0；未确认作者求解器许可','已接入公开论文正文并区分分析检验与预报检验：论文中的地区、提前量及收益衰减条件不替代浙江业务校验。未在本地运行其数值模型。'),
 ('稀疏现场观测的隐式同化与全球增水预报','https://arxiv.org/abs/2404.05758','https://github.com/PatrickESA/StormSurgeCastNet','Apache-2.0','已读取论文、作者README和参数脚本并核对许可。只接入研究证据；没有安装权重、训练浙江模型或将论文指标写成本地准确率。'),
 ('ALCE：带引用回答的评估','https://aclanthology.org/2023.emnlp-main.398/','https://github.com/princeton-nlp/ALCE','MIT','借鉴逐句归因与引用质量分开检查；本项目独立实现原段选择修复，未安装论文的大型NLI模型，也未宣称复现论文评分。'),
 ('ReClaim：细粒度引用生成','https://arxiv.org/abs/2407.01796','','未纳入第三方源代码','借鉴把回答拆成可核对陈述的思路；模型判断后仍执行原文片段与数值量纲检查。'),
 ('Sufficient Context：区分证据不足与利用失败','https://arxiv.org/abs/2411.06037','','未纳入第三方源代码','用于区分检索没拿到依据和模型未正确利用依据；本次额外问法揭示的问题不再归为缺数据。'),
 ('HIDRA-D：稀疏观测支持海平面网格预报','https://gmd.copernicus.org/articles/19/2177/2026/gmd-19-2177-2026.html','https://github.com/rusmarko/HIDRA-D','GPL-3.0','已核对作者仓库和输入结构，作为研究证据接入。没有把亚得里亚海权重当浙江模型，也没有将其论文结果当本系统准确率。'),
 ('surgeNN：极端风暴增水神经网络','https://nhess.copernicus.org/articles/25/4593/2025/','https://github.com/Timh37/surgeNN','MIT','已核对ERA5与去潮验潮站输入、训练及评估代码说明。未训练浙江模型，尚缺匹配的长期观测资料。'),
 ('SFINCS：复合洪水计算','https://sfincs.readthedocs.io/en/latest/input.html','https://github.com/Deltares/SFINCS','GPL-3.0','已核对官方输入说明和许可。没有运行本地淹没模型；地形、垂直基准、边界、堤闸泵和验证资料尚未齐备。'),
 ]
 research=''.join('<tr><td><a href="'+p+'">'+esc(name)+'</a>'+('<br><a href="'+repo+'">作者代码</a>' if repo else '')+'</td><td>'+esc(license)+'</td><td>'+esc(note)+'</td></tr>' for name,p,repo,license,note in papers)
 errors=''.join('<li>'+esc(e['id']+'：'+e['error'])+('；已保留有据部分' if e['grounded_partial_retained'] else '；未形成有据回答')+'</li>' for e in b['errors'])
 report='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>自动迭代与160题实测报告</title><style>
body{max-width:1160px;margin:32px auto;padding:0 24px;color:#153342;background:#f7fafb;font:16px/1.7 "Microsoft YaHei",sans-serif}h1,h2,h3{line-height:1.5}section,article{background:white;padding:22px;margin:18px 0;border:1px solid #d8e6e8;border-radius:10px}table{border-collapse:collapse;width:100%;background:white}td,th{border:1px solid #cbd9dc;padding:9px;text-align:left;vertical-align:top}th{background:#e7f2f2}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.7 Consolas,"Microsoft YaHei",monospace}pre.actual{font:16px/1.8 "Microsoft YaHei",sans-serif;background:#f4f8f9;padding:16px}summary{cursor:pointer;color:#087f83;font-weight:600;margin:10px 0}.badge{color:#077c7d;font-weight:600}input{padding:10px;flex:1;min-width:min(260px,100%);border:1px solid #b8ccce}button{padding:8px 12px;cursor:pointer;border:1px solid #b8ccce;background:white;border-radius:5px}.rendered-answer{font-size:16px;line-height:1.8;overflow-x:auto}.rendered-answer p{margin:10px 0}.rendered-answer code{background:#f0f4f5;padding:1px 4px}.rendered-answer th,.rendered-answer td{overflow-wrap:anywhere}a{color:#087d9a}.note{background:#fff3dc;padding:16px}nav{display:flex;flex-wrap:wrap;gap:8px;position:sticky;top:0;background:#f7fafb;padding:12px;border-bottom:1px solid #cbd9dc}
</style><h1>自动迭代与160题实测报告</h1>'''
 report+='<p>生成时间：'+datetime.now().astimezone().isoformat(timespec='seconds')+'；版本：'+esc(identity['running_version'])+'；运行编号：'+esc(identity['run_id'])+'</p>'
 report+='<p class="note">本报告保存系统实际返回的文字。引用存在、接口成功、模型自评完整和业务正确是不同指标；尚未进行专家逐题验收或独立预报误差验收。</p>'
 history='<section><h2>为什么继续迭代</h2><p>原先提到的137题，是较早版本中仍含通用解释的集合，不等于137题完全没有资料。问题包括检索未命中、表格单位或引用误拦截、业务工具未被调用，以及确实缺少时点数据、精细台账或本地模型。</p><p>本轮先完成v1.6.5完整160题测试，再用不同问法发现家庭储水、海浪分级边界和研究综述的检索泛化问题；v1.6.6完成160题测试后，继续修复精确单位换算和同一规则下改变参数的追问。v1.6.7候选在58题检查点后因发现复合路由覆盖问题停止，保留中途记录，不计作完整测试。v1.6.8完成160题后，发现基础概念比较题在校验失败恢复时丢失了表格内容；同版单独复测成功，不能据此替换原来的失败记录。v1.6.9候选保留了逐行核对的描述性表格，在100题检查点后因发现沿海住宅“进水”漏路由而停止，没有计作完整测试。v1.7.0补齐这类问法及版本标签降级后完成160题，再发现四色含义题被科研核定路由抢占。v1.7.1区分基础分级含义与定量研究后完成160题，再从剩余无引用题中识别出渠道、标准目录及逐句恢复的程序问题。最终v1.7.2补齐这些问题族，隔离单句错误，接入同化研究正文，并增加未核实现行版本的确定性校验。最终代码通过731项测试及102个子测试，完成27次目标请求后冻结，再进行160题实测。</p><p><a href="'+rel(ROOT/'tmp/qa_full160_phase165_20261018_final/answers.json')+'">中间版v1.6.5实际记录</a> · <a href="'+rel(ROOT/'tmp/qa_full160_phase166_20261018_final/answers.json')+'">中间版v1.6.6实际记录</a></p></section>'
 report+='<section><h2>改造与实测结果</h2>'+table+'<p>程序事实条数包含覆盖范围和限制说明，不全是独立观测值。业务工具记录统计不含从文献引用的数值或家庭储水等独立小算式，不能理解成只有这些题有具体数据。校验错误记录保留初始错误；其中部分已通过逐句核对恢复为部分回答。</p><p>模型完整性自评：'+esc(b['answerability_self_report'])+'。</p><p>仍无可展示引用的题号：'+esc('、'.join(b['without_visible_grounding']))+'。</p></section>'
 report+='<p>冻结前另有'+str(probe_gate['probe_requests'])+'次目标请求：'+str(probe_gate['no_retained_validation_error_requests'])+'次没有保留校验错误，'+str(len(probe_gate['grounded_partial_recovery']))+'次在草稿校验失败后保留有据部分；完整原文均保留。这些属于开发检查，不能等同于全部完整正确的独立验收成绩。</p>'
 report+='<section><h2>原137题主因分类的衔接</h2><p>以下沿用上一轮逐题审计的主因分类，互斥计数合计137题。它是旧版问题的诊断依据，不等于新版仍有同样数量的未解题，也不能把检索组全部算作代码错误。</p><table><tr><th>旧版主要障碍</th><th>题数</th></tr>'+''.join('<tr><td>'+esc(label)+'</td><td>'+str(count)+'</td></tr>' for label,count in cause_counts.items())+'</table><p><a href="137题原因分类与证据补齐改造报告_20261018.html">上一轮完整原因报告</a>；本报告逐题保留旧版主因与本次实际缺项，供业务复核是否已解决。</p></section>'
 report+=history
 report+='<section><h2>完成的代码改造</h2><ol><li>关联显式表头、符号定义与数值区间，识别PDF单位换行和明确的英文时间单位；不按数值大小猜单位。统一字面数字检查和量纲检查，放行1800秒＝30分钟这类有原文输入的精确换算；单位不跨文档借用，未知JSON字段不能绕过单位核对。</li><li>引用段号修复只改选择，不修改回答事实。单句引用格式或原段错误只剔除对应句，其他独立核对成功的句子继续保留；未知陈述编号和不完整核对仍整体拒绝。混合草稿失败时，逐句选择原文并重新校验，保留有效内容，剔除无据数字；正常回答及恢复回答保存实际所选原段及其哈希。描述性比较表格按原始表头逐行核对并保留，数值表格不通过此通用恢复路径猜测单元格单位。</li><li>家庭应急、海浪分级边界、预警对比、四色警戒潮位含义与海洋研究等问题族共用意图词与公开来源路由；基础颜色含义不再优先送往科研公式论文，明确询问重现期、公式等问题仍保留研究来源，避免仅对原160题措辞有效。同一规则下改变家庭人数的连续追问已通过真实接口检查。</li><li>指定日期的倒灌、沿海住宅进水等同族问题提取所问县区或岸段的边界输入；同题中的“如何挪车”不覆盖实际影响子问，保留未运行淹没模型的限制；与游泳、岸钓、高程或不同日期任务并列时保留各自业务路由、地点和时间，不让倒灌任务覆盖其他子任务。</li><li>技术综述保留论文地区、输入、方法、结果、局限和代码来源，按发表日期过滤；当前系统配置快照不回填成过去的系统状态。</li><li>概念、方法类回答仅因文件版本未核定而误标full时，降低相应完整性标签并说明版本限制，保留已通过来源、数值及语义核对的解释；不放行其他地点、对象、时段或实际状态冲突。</li><li>官方预警发布渠道与发布技术判据分别检索；标准目录优先使用官方标准查询词，不把截至日期当成精确当天的天气词搜索，其他实时子问保留原日期。文件代替旧版不能自动证明现行有效，未核定时只按所列版本解释。</li><li>同化研究保留分析与真正预报的不同检验结果、地区和提前量，分别说明预报收益及其衰减，不能转写成浙江业务准确率。</li></ol></section>'
 report+='<section><h2>文献及开源代码核对</h2><table><tr><th>来源</th><th>代码许可</th><th>本次处理</th></tr>'+research+'</table><p>许可状态来自作者仓库核对，未复制这些求解器或训练代码到产品运行模块。第三方权重、数据许可及依赖还需分别核对。</p></section>'
 report+='<section><h2>仍需完善的业务条件</h2><ul><li>当天预警、停复航、场地开放及执行台账：需对应日期、地点、对象的正式正文或可保存的业务接口。搜索无结果不能证明没有发布。</li><li>淹没深度、范围及转移人数：需统一垂直基准、精细裸地地形、堤闸泵和排水边界、本地校验模型及实际人员台账。现有ASTER与2020人口格网不能直接替代。</li><li>预报准确率、提前量和概率：需配对预报—观测和明确评分口径，真正集合成员不能由多机构确定性路径拼造。</li><li>SWL、风、时间基点、潮位基准及阈值版本仍保留用户授权的假设口径；数值大小合理不能完成业务核定。</li><li>复杂问题的证据利用和措辞稳定性仍有模型波动，保留错误记录与额外问法测试，后续不只按160题自评分优化。</li></ul></section>'
 report+='<section><h2>本次仍可观察到的程序与回答问题</h2><p>以下是最终整轮实际输出的复核记录，未用单独重试替换原回答，也没有全部解释为外部数据不足。这些问题使当前版本仍不能作为160题全部正确完整的验收结果。</p><table><tr><th>题号</th><th>类型</th><th>本次现象</th></tr>'+''.join('<tr><td><a href="#'+esc(x['id'])+'">'+esc(x['id'])+'</a></td><td>'+esc(x['category'])+'</td><td>'+esc(x['observation'])+'</td></tr>' for x in technical_review)+'</table><p><a href="'+rel(WORK/'remaining_issue_review.json')+'">复核记录与实际原文</a></p></section>'
 report+='<section><h2>自动核对与原始记录</h2><p>冻结运行文件：'+str(len(identity['runtime_source_sha256']))+'个；知识来源目录哈希保持不变。程序交付检查：'+esc(audit['typed_delivery_counts'])+'；时段、引用编号和台风快照截至时间检查的冲突列表均为空。</p><p>保存实际选用原段的问题数：'+str(passage_audit['questions_with_saved_selections'])+'；原段数：'+str(passage_audit['selection_count'])+'；可对照本地原切片的匹配数：'+str(passage_audit['local_chunk_matches'])+'。原段哈希及本地原切片核对均无冲突；这项检查不能代替语义蕴含、文件现行效力或业务正确性验收。</p><p><a href="'+rel(NEW/'answers.json')+'">160题原始JSON</a> · <a href="'+rel(NEW/'computation_audit.json')+'">计算交付核对</a> · <a href="'+rel(NEW/'selected_passage_audit.json')+'">实际原段核对</a> · <a href="'+rel(WORK/'tests_final.txt')+'">731项测试与102个子测试日志</a> · <a href="'+rel(WORK/'evaluation.json')+'">前后指标JSON</a> · <a href="'+rel(WORK/'change_manifest.json')+'">变更或新增追踪运行文件</a> · <a href="'+rel(NEW/'run_identity.json')+'">运行版本与文件哈希</a></p><ul>'+errors+'</ul></section>'
 report+='<section><h2>额外问法的实际结果</h2><p>这些用例属于开发泛化检查；发现失败后参与了改造，不冒充未参与开发的独立验收集。</p>'+''.join(probes)+'</section>'
 report+='<h2>160题逐题实际回答与改造前对照</h2><p>逐题缺项是本次模型或程序的实际记录，仍需核对是否属于用户真正要求的范围；不把所有partial都归因于缺数据。</p><nav><input id="search" placeholder="输入题号、地点或关键词筛选" aria-label="筛选题目"><button id="expand">展开新版答案</button><button id="collapse">收起答案</button></nav><main id="cases">'+''.join(blocks)+'</main>'
 report+='''<script>document.querySelector('#search').addEventListener('input',e=>{let q=e.target.value.trim().toLowerCase();document.querySelectorAll('#cases article').forEach(a=>a.hidden=!a.textContent.toLowerCase().includes(q));});document.querySelector('#expand').addEventListener('click',()=>document.querySelectorAll('#cases article>details.answer-block:first-of-type').forEach(d=>d.open=true));document.querySelector('#collapse').addEventListener('click',()=>document.querySelectorAll('#cases details').forEach(d=>d.open=false));</script></html>'''
 path=ROOT/'exports/自动迭代_160题实测与文献改造报告_20261018.html';path.write_text(report,encoding='utf8')
 # Verify every original/new answer survives HTML escaping unchanged.
 from html.parser import HTMLParser
 class Texts(HTMLParser):
  def __init__(self):super().__init__(convert_charrefs=True);self.active=False;self.parts=[];self.values=[]
  def handle_starttag(self,tag,attrs):
   if tag=='pre' and ('class','actual') in attrs:self.active=True;self.parts=[]
  def handle_data(self,text):
   if self.active:self.parts.append(text)
  def handle_endtag(self,tag):
   if tag=='pre' and self.active:self.values.append(''.join(self.parts));self.active=False
 parsed=Texts();parsed.feed(report)
 expected=[row['reply']['answer'] for row in after['items']]+[row['reply']['answer'] for row in before['items']]+probe_answers
 assert all(answer in parsed.values for answer in expected)
 save(WORK/'report_verification.json',{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
    'actual_answer_blocks':len(parsed.values),'all_160_new_and_160_old_verbatim':True,'runtime_hashes_match':True,'expert_accepted':False})
 print(path);print(json.dumps(evaluation,ensure_ascii=False)[:1800])


if __name__=='__main__':main()
