"""Audit preserved HTTP answers; export causes and exact before/after text.

Question IDs appear only in this offline evaluation tool, never answer routing.
"""
from collections import Counter
from datetime import datetime
from pathlib import Path
import hashlib,html,json,re,sqlite3,sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from forecast_runtime.answer_facts import delivery_facts

WORK=ROOT/'tmp/gap137_upgrade_20261018'
OLD=ROOT/'tmp/qa_full160_phase163_20261018_final'
NEW=ROOT/'tmp/qa_full160_phase164_20261018_final'
CATEGORIES={
 'scope_and_fallback':('提问范围与通用解释追加','代码／回答范围',
   '把概念、方法或假设问题额外要求为当前实况、正式统一规定、现场执行证明；旧版对部分回答统一追加通用解释。',
   '增加问题范围约定，已引用的部分回答不再一律追加无来源解释；复核未问到的缺项。实际状态和正式权限缺项仍保留。'),
 'generation_validation':('生成数值或校验交付','生成／校验；可能兼有缺数据',
   '生成数字与所选证据的数值、单位或段落不匹配。部分为表格表头丢失，部分确实是模型写出无据数字，原拦截有效。',
   '保留数值门槛；显式识别表格单位、核验表格每行引用，分句保留独立通过检查的数值，不因一条错误丢弃全部依据。'),
 'live_tool_routing':('已有接口未触发','代码／路由',
   '问加强、影响、响应时未调用已有台风接口，误称没有台风资料。中心强度数据本身不能替代浙江局地灾损或响应指令。',
   '按业务意图匹配实名台风，提取各机构风速及有效时次；保存原始接口快照支持截至时刻查询，未接入的局地影响输入仍列明。'),
 'operational_observations':('当次公告、实况与执行资料','主要缺时点性业务数据',
   '需要指定日期、地区和业务对象的预警、停复航、回港、安置点开放、人员安排、损失或观测记录。不能由历史公告推定。',
   '继续官方搜索及正文读取；搜索失败不等于不存在。网页可补公开公告，未公开或不稳定的数据仍需发布单位接口或档案。'),
 'local_model_and_assets':('精细台账、模型输入与业务检验','主要缺结构化数据／计算模型',
   '需要入口高程、堤闸泵、承灾体、转移台账、经过检验的水动力或损失模型，以及集合成员、配对观测预报等数据。',
   '已接入的NC、天文潮、地形或人口格网可提供部分输入；不能代替房屋入口和堤防现状，不能据文献生成本地损失或实际业务评分。'),
 'public_document_retrieval':('资料准入、公开原文与版本证据','检索／资料准入，部分兼缺业务数据',
   '通用知识、方法、标准、预案、保险、卫生等未取得足够相关正文或版本关系；部分原件已在库但OCR未复核。还包括年度统计、历史排名和截至日期目录等真实证据缺口。',
   '补通用意图词、官方原文路由、学术方法来源、正文段落选择、附件读取和缺项后二次检索；不能将这组全部算成已证实的代码错误或保证联网可补齐。'),
}

SPECIFIC={
 'Q-001':'已有定义依据却新增当天实况及未要求的频率数据，继而追加通用段。',
 'Q-003':'已有官方活跃月份依据，却把各地统一防台起止规定当成必须输入。',
 'Q-006':'用户问海平面上升的关系和原理，并未要求计算自家损失；精细模型输入是进一步定量业务的要求。',
 'Q-024':'历史岸段淹没题的数值单位检查失败；仍需对应岸段、事件及完整比较样本，不能以换标签宣称已完成最严重排名。',
 'Q-037':'热带气旋等级PDF表的单位表头与数据行分离，导致原文已有的风速范围无法通过引用段检查。',
 'Q-044':'生成历史极值数字未在所选原文出现；保留拦截，不能改成放行模型记忆。',
 'Q-042':'旧版缺全国年度统计；本次找到国家统计局2025年统计公报中海洋灾害直接经济损失89亿元，可补总额。分灾种损失仍待取得，2024年或2025年全部自然灾害总损失不能替代。',
 'Q-043':'“历史最严重”涉及全国可比样本及伤亡、经济损失口径，单次案例或局部档案不能证明全国纪录。',
 'R-007':'强度接口、历史利奇马风暴潮和浙江当前影响口径不同，数字支持失败；新增中心风速并不能直接回答浙江风暴潮偏重偏轻。',
 'R-020':'证据支持48小时和24小时，草稿混入无据1小时。修复为仅保留独立通过校验的片段，且保留版本限制和字段缺口。',
 'R-034':'不同预报机构路径不是一个模式的集合成员，不能直接作为集合离散度或敏感区。',
 'R-040':'天文潮预报不是潮位实测；缺实时站点、浮标、雷达、卫星原始观测和缺测日志。',
 'R-041':'质控标准原件已在本地，因OCR多页未核而整份待审，不能说完全没有资料。已核对GB/T14914.6—2021的4页并单独入库4条文字节选，支持尖峰、恒定及浮标温盐／潮位质控项目；原件其他页面及公式仍隔离。',
 'R-045':'评分公式可以通过规范／论文解释；浙江2023—2025实际评分仍需逐报逐次配对资料与统一计分口径。',
 'R-046':'红色警报规定提前量与浙江实际平均提前量不同；后一项需要历史发布及事件到达时间样本。',
 'R-059':'需要截至日期的标准编号、实施及废止替代关系。已入库官方文本不自动等于现行标准目录。',
 'R-060':'能力设计可依据论文归纳，不必存在题目同名的官方正式模块清单；不能把方法归纳称为已验收系统架构。',
 'R-061':'研究方法题可由检索、质量评估和引用核验文献支撑，不要求先取得已采纳的正式RAG规范。',
 'R-073':'多个中文官方原网页遭遇校验页、超时或错误，不能假装已读正文；新增独立NOAA官方安全知识作为备选。',
 'R-074':'用户明确未给门牌、房屋和车库入口高程、排水工况；区域格点或30米表面高程不能证明这栋房屋进水深度。',
 'R-075':'全国清单的字面口径为每人3天、至少3升/人。程序按3人乘3升得9升总量，不擅自解释为每天3升；不是高温或特殊人群的充分饮水保证。',
 'R-093':'恢复供水供电的具体时长需要故障和抢修进度；通用预计小时数没有来源时不能放行。',
}

PAPERS=[
 {'name':'Corrective Retrieval Augmented Generation (CRAG)','paper':'https://arxiv.org/abs/2401.15884',
  'code':'https://github.com/HuskyInSalt/CRAG','license':'本次未找到明确仓库许可证；未复制源码',
  'idea':'检索质量不足时改写查询、转向外部来源，再筛选证据。',
  'implementation':'已独立实现按未覆盖子问触发一次补充检索、正文筛选及覆盖回退保护；未训练论文的轻量检索评估器。'},
 {'name':'Adaptive-RAG (NAACL 2024)','paper':'https://aclanthology.org/2024.naacl-long.389/',
  'code':'https://github.com/starsuzi/Adaptive-RAG','license':'Apache-2.0',
  'idea':'按问题复杂度选择单次或多次检索。',
  'implementation':'当前按业务、时间和子问做规则与模型规划；未训练论文分类器。可用独立标注集进一步训练策略选择器。'},
 {'name':'Self-RAG (ICLR 2024)','paper':'https://arxiv.org/abs/2310.11511',
  'code':'https://github.com/AkariAsai/self-rag','license':'MIT',
  'idea':'检索后复核相关性、支持度和回答质量。',
  'implementation':'已增加有限次数的证据覆盖复核和重写，数值仍经程序核验；这是对方法的适配，未复现其训练与反思标记模型。'},
 {'name':'OceanGPT (ACL 2024)','paper':'https://aclanthology.org/2024.acl-long.184/',
  'code':'https://github.com/OceanGPT/OceanGPT','license':'MIT',
  'idea':'海洋领域指令数据、检索应用和OceanBench领域评测。',
  'implementation':'已检查rag.py等原代码以比较数据流；保留现有DeepSeek，未安装其模型或把合成问答当业务事实。领域评测是后续完善方向。'},
 {'name':'FathomGPT (UIST 2024)','paper':'https://arxiv.org/abs/2412.02784',
  'code':'','license':'未确认作者代码仓库',
  'idea':'自然语言驱动海洋图像、分类和测量数据查询。',
  'implementation':'可借鉴类型化查询与可视化接口；其FathomNet任务不等同于台风预报或淹没计算。'},
 {'name':'平阳风暴潮淹没模拟研究 (NHESS 2020)','paper':'https://nhess.copernicus.org/articles/20/2777/2020/',
  'code':'','license':'研究方法参考；未引入数值模型代码',
  'idea':'结合地形、水深、潮汐、台风、堤防等条件建立并验证淹没模型。',
  'implementation':'用于明确后续模型输入和验证方案；未据此声称已有浙江全省或具体住宅的经过验证的淹没产品。'},
]

def load(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def esc(value):return html.escape(str(value))
def dump(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
def missing(reply):
    return list(dict.fromkeys(reply.get('missing_information',[])+[
        value for row in reply.get('subquestion_coverage',[]) for value in row.get('missing_information',[])]))

def measures(record):
    a=record.get('reply') or {};text=a.get('answer','');sources=a.get('sources',[])
    refs={int(v) for v in re.findall(r'\[资料(\d+)\]',text)}
    returned={s.get('citation_id') for s in sources}
    facts=delivery_facts(record.get('business_evidence') or {})
    rows=a.get('subquestion_coverage',[])
    return {'id':record['id'],'http_status':record.get('http_status'),
        'answer_kind':a.get('answer_kind'),'model_self_answerability':a.get('answerability'),
        'source_count':len(sources),'returned_cited_sources':len(refs&returned),
        'has_visible_grounding':bool(refs&returned),'missing_returned_citations':sorted(refs-returned),
        'typed_fact_count':len(facts),'required_typed_fact_count':sum(f.get('required') is not False for f in facts),
        'has_program_facts':bool(facts),'subquestions':len(rows),
        'model_self_full_parts':sum(p.get('status')=='full' for p in rows),
        'validation':a.get('validation') or {},'evidence_diagnostics':a.get('evidence_diagnostics') or {},
        'missing_information':missing(a),'answer_sha256':hashlib.sha256(text.encode()).hexdigest()}

def aggregate(rows):
    vals=[measures(r) for r in rows]
    return {'question_count':len(vals),'http':dict(Counter(str(v['http_status']) for v in vals)),
        'answer_kinds':dict(Counter(str(v['answer_kind']) for v in vals)),
        'model_self_answerability':dict(Counter(str(v['model_self_answerability']) for v in vals)),
        'questions_with_visible_grounding':sum(v['has_visible_grounding'] for v in vals),
        'questions_with_program_facts':sum(v['has_program_facts'] for v in vals),
        'program_fact_count':sum(v['typed_fact_count'] for v in vals),
        'validation_error_count':sum(bool(v['validation'].get('error')) for v in vals),
        'citation_id_mismatch_ids':[v['id'] for v in vals if v['missing_returned_citations']],
        'no_visible_grounding_ids':[v['id'] for v in vals if not v['has_visible_grounding']]}

def block(record,data_file=None):
    a=record.get('reply') or {}
    result='<pre class="actual">'+esc(a.get('answer') or a.get('error') or '未返回回答正文')+'</pre>'
    result+='<p>实际缺项：'+esc('；'.join(missing(a)) or '模型未列出缺项；这不代表独立验收通过。')+'</p>'
    result+='<p>接口 '+esc(record.get('http_status'))+'；模式 '+esc(a.get('mode'))+'；模型自报 '+esc(a.get('answerability'))+'；校验 '+esc(a.get('validation'))+'</p>'
    if data_file is not None:
        relative='../'+data_file.relative_to(ROOT).as_posix()
        result+='<p><a href="'+esc(relative)+'">本题保存的实际接口及工具结果（JSON）</a></p>'
    if a.get('sources'):
        result+='<details><summary>实际返回来源 '+str(len(a['sources']))+' 条</summary><ul>'
        for s in a['sources']:
            url=s.get('url') or ''
            if url.startswith('/'):url='http://127.0.0.1:8769'+url
            link=' <a href="'+esc(url)+'">原文</a>' if url.startswith(('https://','http://')) else ''
            result+='<li>[资料'+esc(s.get('citation_id'))+'] '+esc(s.get('title',''))+' '+esc(s.get('locator',''))+link+'</li>'
        result+='</ul></details>'
    return result

def main():
    before=load(OLD/'answers.json')['items'];after=load(NEW/'answers.json')['items']
    old={r['id']:r for r in before};new={r['id']:r for r in after}
    assert len(old)==len(new)==160 and set(old)==set(new)
    assert all(old[k]['question']==new[k]['question'] for k in old)
    groups=load(WORK/'cause_groups.json');classified={k:cat for cat,ids in groups.items() for k in ids}
    expected={k for k,r in old.items() if r['reply'].get('answer_kind')=='general_explanation'}
    assert set(classified)==expected and sum(map(len,groups.values()))==137
    identity=load(NEW/'run_identity.json');integrity={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha
        for name,sha in identity['runtime_source_sha256'].items()}
    assert all(integrity.values()),'Runtime changed during final measurement'
    audit=load(NEW/'computation_audit.json')
    variants=load(WORK/'variants_release_scoped/answers.json')['items']
    assert all(v['http_status']==200 for v in variants)
    assert load(WORK/'variants_release_scoped/identity.json')['runtime_source_sha256']==identity['runtime_source_sha256']
    oldagg,newagg=aggregate(before),aggregate(after)
    errors=[measures(r) for r in after if (r.get('reply',{}).get('validation') or {}).get('error')]
    retained_error_ids=[r['id'] for r in errors if r['has_visible_grounding']]
    ungrounded_error_ids=[r['id'] for r in errors if not r['has_visible_grounding']]
    cause_rows=[]
    for row in before:
        k=row['id']
        if k not in classified:continue
        cat=classified[k];a=row['reply']
        mechanism=('已有带引用正文，部分回答被统一追加通用段' if a.get('sources') else
                   '生成内容被数值校验拦截后转为通用段' if (a.get('validation') or {}).get('error') else
                   '本轮未取得可用证据，转为通用段')
        cause_rows.append({'id':k,'question':row['question'],'category':cat,'label':CATEGORIES[cat][0],
            'cause_type':CATEGORIES[cat][1],'baseline_mechanism':mechanism,
            'reason':SPECIFIC.get(k,CATEGORIES[cat][2]),'action':CATEGORIES[cat][3],
            'before':measures(row),'after':measures(new[k]),
            'note':'主因人工归类；同题可以同时有检索和业务输入缺口，不表示该类已全部解决。'})
    cache=[]
    for path in sorted((ROOT/'data/marine_kb/public_reference_cache').glob('*.json')):
        value=load(path);page=value['page']
        cache.append({'url':value['url'],'saved_at':value['saved_at'],'read_at':page.get('read_at'),
                      'body_sha256':value['body_sha256'],'scope':page.get('_primary_scope')})
    report={'generated_at':datetime.now().astimezone().isoformat(),'version':identity['running_version'],
        'baseline_mechanism_counts':dict(Counter(r['baseline_mechanism'] for r in cause_rows)),
        'cause_counts':{CATEGORIES[k][0]:len(v) for k,v in groups.items()},'causes':cause_rows,
        'before_metrics':oldagg,'after_metrics':newagg,'computation_audit':{k:v for k,v in audit.items() if k!='items'},
        'variant_probes':[{'question':v['question'],**measures(v)} for v in variants],
        'validation_outcomes':{'retained_grounded_partial_ids':retained_error_ids,'ungrounded_error_ids':ungrounded_error_ids},
        'runtime_integrity':integrity,'source_catalog_after':load(WORK/'catalog_after.json'),
        'verified_excerpt_admission':load(WORK/'qc_verified_manifest.json'),
        'recovered_typhoon_sources':load(WORK/'typhoon_archive_recovery.json'),
        'public_reference_bodies':cache,'papers':PAPERS,'reviewed_author_code':load(WORK/'research/code_review.json'),
        'author_code_licenses':load(WORK/'research/licenses.json'),
        'expert_accepted':False,'evaluation_note':'类型标签策略已改变，general_explanation数量下降不能等同正确率上升。引用、程序事实交付及模型自报覆盖均不代替逐项业务验证。'}
    dump(WORK/'evaluation.json',report);dump(WORK/'cause_audit_137.json',cause_rows);dump(WORK/'research/bibliography.json',PAPERS)
    title='137题原因分类、证据补齐与160题新版实测'
    out=ROOT/'exports/137题原因分类与证据补齐改造报告_20261018.html'
    parts=['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+'</title>',
        '<style>body{max-width:1180px;margin:36px auto;padding:0 22px;font:16px/1.75 "Microsoft YaHei",sans-serif;color:#182d3a}h1,h2,h3{color:#123d4a}table{border-collapse:collapse;width:100%;margin:18px 0}td,th{border:1px solid #cbd8dd;padding:9px;text-align:left;vertical-align:top}th{background:#edf4f5}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:15px/1.8 "Microsoft YaHei",sans-serif;background:#f5f8f9;padding:18px}details{margin:13px 0;border-top:1px solid #dbe5e8;padding-top:10px}summary{cursor:pointer;font-weight:600}a{color:#007a85}input,select{font:inherit;padding:9px;margin:5px 5px 14px 0;max-width:95%}.note{background:#fff4df;padding:14px}.badge{color:#52646c;font-size:14px}.hidden{display:none}nav a{margin-right:14px}</style></head><body>',
        '<h1>'+title+'</h1><p>'+esc(identity['running_version'])+'；生成于 '+esc(report['generated_at'])+'。</p>',
        '<nav><a href="#causes">原因</a><a href="#changes">修改</a><a href="#results">实测</a><a href="#research">论文和代码</a><a href="#remaining">仍缺什么</a><a href="#questions">逐题核对</a></nav>',
        '<p class="note">137不是137题全部无依据。旧版137题中，119题已返回引用正文，但因某个子项未覆盖而统一追加通用解释；12题未取得可用证据；6题生成数值未通过校验。此次不以删除通用段作为业务完成，而保留来源、具体数值和未解决子项。</p>',
        '<h2 id="causes">一、137题主因分类</h2><p>人工按主要障碍分类，互斥计数共137题；次要障碍记录在逐题缺项中。特别是公开资料组不等于已证实全部是检索程序错误。</p><table><tr><th>主因</th><th>题数</th><th>判断与处理</th></tr>']
    for k,vals in groups.items():
        label,kind,reason,action=CATEGORIES[k]
        parts+=['<tr><td>'+esc(label)+'<br>'+esc(kind)+'</td><td>'+str(len(vals))+'</td><td>'+esc(reason)+'<br><b>本次：</b>'+esc(action)+'</td></tr>']
    parts+=['</table><h2 id="changes">二、实际代码和数据变更</h2><ol>',
        '<li>问题范围、业务路由与台风时间快照：加强／影响等意图可调用已有接口，按明确编号和名称核对对象；历史截至查询仅使用该时刻前已抓取的原始API快照，不用后来取得的列表反推过去活动总数。只有部分台风记录时明确完整数量未知，没存快照时不回填后续列表。已从此前真实工具返回恢复8条来源记录、去重为3份原始快照，并新增后续采集归档。没有把模型回答或题号写入运行时答案。</li>',
        '<li>资料发现与正文读取：补技术和安全词汇，区分通用指南与当次公告；按子问选原文操作段，修复门户名称误当首页、论文正文被页脚布局隐藏等问题。增加国家搜救预案、官方家庭清单、热带气旋分级标准、健康科普与研究原文。读取失败、验证页和访问限制保留诊断；未绕过。</li>',
        '<li>已有资料恢复：找到已在库但因OCR隔离的质控标准，渲染核对其PDF第13、14、16、21页，仅将核对过的文字及流程图标签独立保存为1条新来源、4条切片。原标准仍待审，未放行公式和未核页，也不把版本核对扩展为指定历史日期的现行认证。</li>',
        '<li>纠正检索与回答覆盖：首轮仍缺项时仅针对缺项补查一次，保留此前已回答子问；增加有界语义复核。研究归纳可以直接形成方案，不要求存在同名正式规范。复核模型仍会漏判，未把其结论称为专家判断。</li>',
        '<li>数值和引用：保留原文值、单位、表头、逐行引用及程序事实交付检查。单个错误数字不能连带丢掉独立通过核验的片段；原错误标志继续记录。对明确家庭人数与官方每人总量执行程序乘法，区别每天数量与多天总量。</li>',
        '<li>同文档证据保留与误判修复：本地章节不再按相同“打开原件”链接合并，只按具体切片去重；多对象问题不再要求每个片段同时出现全部字段，浮标、潮位、雷达等可分别保留证据，标准目录与具体检验正文分开排序。标准“2021节选”不再误判为2021节风速；段落选择不再把日期年份当作求救电话号码一类的强身份标记。</li>',
        '<li>补具体公共数据：新增国家统计局2025年统计公报的原站及统计部门转载来源。原文的全国海洋灾害直接经济损失为89亿元；它只支持该年度该口径总额，不能推出分灾种占比或主要灾种。新增QARTOD方法原文发现路径，但当前附件访问被站点规则拦截，未伪称已读到内容。</li>',
        '<li>公共原文缓存：仅为已成功读取的通用知识、版本化标准、研究方法、技术参考和年度统计保留正文、原始读取时间及哈希；不缓存当次营业、开放、停复航为长期事实。缓存不是资料审核通过或现行有效认证。</li></ol>',
        '<h2 id="results">三、同题160题实际HTTP测试</h2><p>与上一轮问题、地点及日期完全相同；通过网页同一 /api/assistant/ask 接口独立提问，保留失败及校验记录。运行前后 '+str(len(integrity))+' 个源码指纹一致。原2796条知识库来源记录未改，新增1条原页核对节选、4条切片；台风原始快照恢复和公共正文缓存也是本次明确的数据补齐。开发候选试跑和中止批次另存，不混入最终160题。</p>',
        '<table><tr><th>可观测指标</th><th>改前</th><th>改后</th><th>能说明什么</th></tr>']
    for label,key,note in [('HTTP状态','http','仅接口执行情况'),('回答中有引用且来源随答返回','questions_with_visible_grounding','不等于每句话都已获语义支持'),('返回可提取的程序事实题数','questions_with_program_facts','中心强度、海况等；不等于所有业务子项完整'),('程序事实条数','program_fact_count','含各机构、地点和有效时次；不是独立观测站数量'),('保留校验错误的题数','validation_error_count','部分已保留通过检查的片段，不隐藏发生过的错误'),('模型自报完整性','model_self_answerability','仅模型标签，不是专家正确率'),('回答类型','answer_kinds','旧版追加策略已改变，不能用类型变化计算提升率')]:
        parts+=['<tr><td>'+label+'</td><td>'+esc(oldagg[key])+'</td><td>'+esc(newagg[key])+'</td><td>'+note+'</td></tr>']
    parts+=['</table><p>程序事实交付检查：'+esc(audit['typed_delivery_counts'])+'；查询时段不匹配：'+esc(audit['forecast_period_mismatch_ids'])+'；方括号[资料N]引用编号未随答返回：'+esc(audit['citation_id_mismatch_ids'])+'；台风快照取得／观测时间晚于截至时刻：'+esc(audit.get('typhoon_snapshot_cutoff_mismatch_ids'))+'。这些检查不能验证NC含义、模型偏差、文件现行状态、所有裸编号描述或现场安全。</p>',
        '<p>仍保留'+str(len(errors))+'条生成校验错误记录：'+str(len(retained_error_ids))+'题保留通过核对的带引用部分（'+esc('、'.join(retained_error_ids))+'）；'+str(len(ungrounded_error_ids))+'题仍没有交付有引用依据的答案（'+esc('、'.join(ungrounded_error_ids))+'）。这些错误需要继续逐条分析，不能据接口成功或标签变化宣称幻觉率下降。其余无引用题号为'+esc('、'.join(k for k in newagg['no_visible_grounding_ids'] if k not in ungrounded_error_ids))+'，也不能一律认定只能由业务单位补数据。</p>',
        '<p>Python自动化：610项测试和102项子测试通过。最终160题逐项答案哈希、源码指纹、分类和原始HTTP记录保存在配套JSON文件中。下面正文逐字保留实际返回，未编辑成理想答案。</p>',
        '<h2 id="research">四、可借鉴的论文与作者代码</h2><p>已尝试Google Scholar检索，但当前工具未能读取其结果页；以下均直接核对论文原站及作者仓库。只阅读和借鉴方法，本次没有复制作者源码进入运行时，也未安装或替换现有大模型。</p><table><tr><th>论文／源码</th><th>适用思路</th><th>本项目状态和限制</th></tr>']
    for p in PAPERS:
        links='<a href="'+esc(p['paper'])+'">'+esc(p['name'])+'</a>'
        if p['code']:links+='<br><a href="'+esc(p['code'])+'">作者代码</a>'
        parts+=['<tr><td>'+links+'<br>'+esc(p['license'])+'</td><td>'+esc(p['idea'])+'</td><td>'+esc(p['implementation'])+'</td></tr>']
    parts+=['</table><h2 id="remaining">五、要达到有依据、有具体业务数据，仍需补齐的功能和输入</h2><table><tr><th>问题类型</th><th>数据／功能</th><th>获取与验收</th></tr>',
        '<tr><td>当天预警、停复航、回港、开放及抢修</td><td>对象明确、带发布／生效／解除时间的公告与原始状态</td><td>公开搜索可补一部分；需要稳定发布源接口和历史归档，逐条核对地区、航线、船型和时次。搜不到不能说没有或未发布。</td></tr>',
        '<tr><td>某户进水、地下车库、转移人数与损失</td><td>入口和地坪高程、裸地DEM、堤闸泵、排水、房屋人员台账及检验后的模型</td><td>公开地形和人口格网仅支持区域筛查。需要业务单位精细资料、模型率定和现场验证，不能用语言模型或文献实验数字补齐。</td></tr>',
        '<tr><td>某地区台风影响、与历史过程轻重比较</td><td>同地区、同有效时段的风浪潮预报与阈值，配对历史实测和影响统计</td><td>有的回答已返回中心强度，但地区海况工具与历史统计仍需联动。中心风速不能代替浙江风暴潮或港口、养殖区的具体影响。</td></tr>',
        '<tr><td>超警与涉海活动风险的正式业务验收</td><td>SWL含义、风变量口径、天文潮与高程基准、站点映射</td><td>当前数值链路保留此前用户授权的假设。仍需资料提供方核定并以观测检验；不能以数值量级合理代替基准和变量含义证明。</td></tr>',
        '<tr><td>当前标准、制度、分灾种损失及历史最严重</td><td>官方标准状态与替代关系、对应年度分灾种统计、可比历史全样本</td><td>2025全国总损失已找到国家统计局来源；分灾种仍缺。继续官方目录和原公告核验，不能借用2024或全部自然灾害数字，局部档案不能证明全国极值。</td></tr>',
        '<tr><td>预测好不好、提前量、集合不确定性</td><td>配对观测预报、发布记录、明确集合成员与统一检验口径</td><td>论文可解释方法和公式，不替代浙江实际年度评分；多机构确定性路径不能冒充集合成员。</td></tr>',
        '<tr><td>已有扫描标准的内容可用</td><td>按页核对OCR、表格／公式专门解析、正文和版本分开核验</td><td>已打通核对节选单独入库，原件不会随之整份批准；其余隔离页仍需处理。标准目录列出名称不等于正文检验方法已取得。</td></tr>',
        '<tr><td>回答完整、推理可靠、成本可控</td><td>逐断言语义支持核验、矛盾比较检查、独立标注集、缓存与延迟监测</td><td>本次仅增加部分覆盖复核及确定性数值检查，仍需独立内容验收。数字存在于来源不等于比较关系一定正确；方法题的额外缺项和冗余表达也未完全消除。</td></tr></table>',
        '<h2>六、5个改写问题探测</h2><p>同一最终版本还测试了人数变化、眼区同义表达、浮标异常质控、论文方法和各机构最强时次。它们用于排查类别泛化，属于开发探测，不是独立留出正确率评测。</p>']
    for row in variants:
        parts+=['<details><summary>'+esc(row['question'])+'</summary>',block(row,WORK/'variants_release_scoped'/(row['id']+'.json')),'</details>']
    parts+=['<h2 id="questions">七、逐题分类及真实改前改后回答</h2><p>默认显示全部160题；其中137题有原因分类。搜索题号、问题、缺项或回答原文。</p><input id="query" type="search" placeholder="例如：回港、R-075、缺少"><select id="category"><option value="">全部160题</option><option value="prior_other">原23题（非通用解释）</option>']
    for k in groups:parts+=['<option value="'+esc(k)+'">'+esc(CATEGORIES[k][0])+' '+str(len(groups[k]))+'题</option>']
    parts+=['</select><span id="shown"></span><div id="records">']
    notes={r['id']:r for r in cause_rows}
    for row in after:
        k=row['id'];cat=classified.get(k,'prior_other');note=notes.get(k)
        parts+=['<details class="record" data-category="'+esc(cat)+'"><summary>'+esc(k)+' '+esc(row['question'])+'</summary>',
            '<p class="badge">'+esc(CATEGORIES[cat][0] if cat in CATEGORIES else '原版非通用解释题，仍保留复测')+'</p>']
        if note:parts+=['<p><b>旧版机制：</b>'+esc(note['baseline_mechanism'])+'<br><b>原因：</b>'+esc(note['reason'])+'<br><b>处理：</b>'+esc(note['action'])+'</p>']
        parts+=['<h3>新版实际回答</h3>',block(row,NEW/(k+'.json')),'<details><summary>旧版实际回答</summary>',block(old[k],OLD/(k+'.json')),'</details></details>']
    parts+=['</div><script>const rows=[...document.querySelectorAll(".record")],q=document.getElementById("query"),c=document.getElementById("category");function filter(){let count=0;for(const row of rows){const show=(!c.value||row.dataset.category===c.value)&&row.textContent.toLowerCase().includes(q.value.toLowerCase());row.classList.toggle("hidden",!show);if(show)count++;}document.getElementById("shown").textContent="显示 "+count+" 题";}q.addEventListener("input",filter);c.addEventListener("change",filter);filter();</script></body></html>']
    out.write_text('\n'.join(parts),encoding='utf8')
    print(json.dumps({'report':str(out),'cause_count':len(cause_rows),'before':oldagg,'after':newagg},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
