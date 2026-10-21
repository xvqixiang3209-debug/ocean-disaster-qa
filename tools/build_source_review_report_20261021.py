"""Build a local, source-bound review report from retained actual results."""
import csv
import hashlib
import html
import json
import re
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

from tools.build_l2l5_results import render

APP = Path(__file__).resolve().parents[1]
RUN = APP / 'tmp/source_review_20261021'
OUT = APP / 'exports/资料核查审核与数值归档_20261021'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def csv_file(name, fields, rows):
    with (OUT / name).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def safe_url(value):
    value = str(value or '')
    if value.startswith('/'):
        return 'http://127.0.0.1:8774' + value
    if value.startswith(('https://', 'http://')) and not re.search(
            r'token=|signature=|access.key|credential=', value, re.I):
        return value
    return ''


def qa(path):
    row = read(path)
    reply = row.get('actual_reply', row.get('reply', {}))
    sources = []
    for source in reply.get('sources', []):
        sources.append({key: source.get(key) for key in (
            'citation_id', 'source_id', 'title', 'filename', 'locator', 'review_label')})
        sources[-1]['url'] = safe_url(source.get('url') or (source.get('doc_card') or {}).get('url'))
    return {'question': row['question'], 'answer': reply.get('answer', ''),
            'answerability': reply.get('answerability'),
            'http_status': row.get('http_status', row.get('status')),
            'seconds': row.get('seconds'), 'sources': sources,
            'subquestion_coverage': reply.get('subquestion_coverage', []),
            'record': str(path.relative_to(APP))}


CONFIRMATIONS = [
    ('D01', '数值产品字典', '数据提供同事', '880份Excel的Surge、Waterlevel单位、正负号、高程基准、缺测码；集合表是成员、均值还是分位数。',
     '全部原值和单元格已恢复；未知字段保留unknown。', '提供一份适用于相同产品结构的字典即可，不需逐表审批。'),
    ('D02', '时间绑定', '数据提供同事', '05/15:00等原时间标签对应的年、月、时区和有效时刻；文件名中的批次时间与表内日期的关系。',
     '83,104行均为day_clock_only；不依据文件名擅补有效日期。', '给一个完整批次的正确时间轴示例及通用规则。'),
    ('D03', '台风与场景', '数据提供同事', '历史Excel文件TY编号对应实况事件还是再预报/试算场景。',
     '原编号保留；未据此推断某台风因果影响。', '提供产品场景与台风事件映射。'),
    ('D04', '岸段阈值版本', '岸段数据负责人', '武原街道1008、西塘桥街道1009、乍浦镇1010、独山港镇1011共13个阈值数值冲突，选定现行业务版本及单位、基准、生效日期。',
     'GeoJSON、SQL两版各198岸段均保留；冲突未覆盖。', '在岸段差异明细中标明采用值和依据。'),
    ('D05', '站点与行政区对应', '站点/岸段负责人', '健跳、石塘同名多站及未匹配标签；18个岸段名称/县区字段差异。',
     '已用显式ADMC和SQL映射完成79,200次岸段列对应；歧义站点未强配。', '确认稳定站号；核对温峤、七都、磐石等归属。'),
    ('T01', '正式标准全文', '资料负责人', 'HY/T0273-2019、HY/T0273.2-2023、HY/T0273.3-2021、HY/T0273.5-2021正式全文。',
     '已核对官方标准身份和草案的正式后继，未取得公开可用全文；未批准草案规则。', '提供合法取得的正式文本或可公开下载地址。'),
    ('T02', '地方文件版本', '业务/资料负责人', '浙江海洋观测站点运行维护管理规定、海洋智能网格预报业务规定（试行）、八张风险清单机制、自然资源厅自然灾害防御应急响应工作指南的发文号和正式版本。',
     '原件保留；尚未找到足够的正式版本证据。', '提供签发文本或发文链接，并说明是否仍适用。'),
    ('T03', '历史资料状态', '资料负责人', '2014年浙江海洋灾害公报正式发布稿；2017年海洋观测资料管理办法的当前效力状态。',
     '2014送审稿未冒充正式稿；2017办法可按该年版本引用，未宣称当前有效。', '提供正式稿/效力依据；历史版本问答可以继续使用。'),
    ('T04', '整篇技术资料复核', '专业人员或原件提供方', '140个隔离页含空白页、公式、表格、流程图等；337个OCR页仍未逐页核准。',
     '120个待尝试页已实际OCR，43页新增可用文字；已目视核对的53个节选单元独立入库。', '优先提供可编辑原稿/带文字层正式PDF；再逐页核对公式及表格关系。'),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    integrity = read(RUN / 'integrity_after.json')
    numeric = read(RUN / 'numeric/numeric_recovery_acceptance.json')
    recovery = read(RUN / 'ocr/recovery_plan.json')
    versions = read(RUN / 'version_audit.json')
    public = read(RUN / 'public_sources.json')['public_sources']
    result = read(RUN / 'application_result.json')
    log_bytes = (APP / 'tmp/source_review_full_tests_20261021.txt').read_bytes()
    log = log_bytes.decode('utf-16' if log_bytes.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig', errors='replace')
    assert '2118 passed, 134 subtests passed' in log, log[-300:]
    node_tests = read(RUN / 'node_tests.json')
    assert len(node_tests) == 6 and all(row['exit_code'] == 0 for row in node_tests)
    db = sqlite3.connect('file:' + (APP / 'data/marine_kb/knowledge.sqlite3').as_posix() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    catalogue = [dict(row) for row in db.execute('select * from sources order by category,filename,source_id')]
    current = {row['source_id']: row for row in catalogue}
    assert len(catalogue) == 2842
    assert dict(Counter(row['status'] for row in catalogue)) == integrity['status_counts']
    db.close()
    changes = {row['source_id']: row for row in result['items']}
    recovery_by_id = {row['source_id']: row for row in recovery['documents']}
    text_rows = []
    for row in versions:
        audit = row['version_audit']
        repaired = recovery_by_id[row['source_id']]
        now = current[row['source_id']]
        meta = json.loads(now['metadata'])
        text_rows.append({'source_id': row['source_id'], '文件名': row['filename'],
                         '原SHA256': row['sha256'], '原状态': row['status'], '当前状态': now['status'],
                         '本轮动作': changes[row['source_id']]['decision'],
                         '版本核对': audit['version_status'],
                         '新增OCR页': len(repaired.get('newly_recovered_pages', [])),
                         '切片数': now['chunk_count'],
                         '已核对节选ID': '; '.join(meta.get('approved_excerpt_source_ids', [])),
                         '正式替代来源ID': '; '.join(meta.get('formal_replacement_source_ids', [])),
                         '官方依据': '\n'.join(str(x.get('evidence_url') or x.get('url') or '') for x in audit.get('evidence', [])),
                         '仍需确认': audit.get('needs_confirmation', '')})
    csv_file('文字专项核查台账.csv', list(text_rows[0]), text_rows)
    catalogue_rows = []
    for row in catalogue:
        meta = json.loads(row['metadata'])
        catalogue_rows.append({'source_id': row['source_id'], '文件名': row['filename'], '类别': row['category'],
                               '状态': row['status'], '质量状态': row['quality_status'], '切片数': row['chunk_count'],
                               '来源类型': meta.get('source_type', ''), 'SHA256': row['sha256']})
    csv_file('资料总台账.csv', list(catalogue_rows[0]), catalogue_rows)
    fields = ['编号', '类别', '确认人', '需要确认', '已自动完成', '回复方式']
    csv_file('待确认事项.csv', fields, [dict(zip(fields, row)) for row in CONFIRMATIONS])
    coast_names = {'1008': '武原街道', '1009': '西塘桥街道', '1010': '乍浦镇', '1011': '独山港镇', '1085': '大陈镇'}
    differences = []
    for row in numeric['conflicts']:
        def value(v):
            return json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v
        differences.append({'岸段ID': row['coast_id'], '名称（已知）': coast_names.get(row['coast_id'], ''),
                            '字段': row['field'], '差异类别': row['kind'],
                            'GeoJSON原值': value(row['geojson_value']), 'SQL原值': value(row['sql_value'])})
    csv_file('岸段差异明细.csv', list(differences[0]), differences)
    ndb = sqlite3.connect('file:' + (APP / 'data/marine_kb/numeric_archive.sqlite3').as_posix() + '?mode=ro', uri=True)
    ndb.row_factory = sqlite3.Row
    numeric_files = [dict(row) for row in ndb.execute('select * from numeric_files order by filename')]
    ndb.close()
    # No credentials or raw filesystem locations are included in the workbook inventory.
    allowed = [key for key in numeric_files[0] if key not in {'raw_path', 'metadata_json'}]
    csv_file('数值归档清单.csv', allowed, numeric_files)

    selected = [('station', '站点迁建与灾害例外', 'questions/verified/station.json'),
                ('data', '资料汇交、答复及交付期限', 'questions/verified/data.json'),
                ('wave', '警报时效与更新频率', 'questions/final/wave.json'),
                ('quality', '标准身份及适用范围', 'questions/final/quality.json'),
                ('terms', '有效波高、最大波高与潮差', 'terms_live_question_verified.json'),
                ('numeric', '两个历史数值表同时查询', 'numeric_live_question.json')]
    cases = []
    for key, title, filename in selected:
        row = qa(RUN / filename)
        row.update(id=key, title=title)
        before = RUN / 'questions/before' / (key + '.json')
        if before.exists():
            row['before'] = qa(before)
        cases.append(row)
    assert all(row['http_status'] == 200 for row in cases)
    actual_count = len(list((RUN / 'questions').glob('*/*.json'))) - len(list((RUN / 'questions').glob('*/summary.json')))
    actual_count += sum((RUN / name).exists() for name in ['terms_live_question.json', 'terms_live_question_final.json', 'terms_live_question_verified.json', 'numeric_live_question.json'])
    states = dict(Counter(row['answerability'] for row in cases[:5]))
    payload = {'generated_at': datetime.now().astimezone().isoformat(), 'preview': 'http://127.0.0.1:8774/',
               'review_counts': {'original_registrations': 2797, 'current_registrations': 2842,
                                'text_originals_checked': 50, 'originals_approved_with_version_scope': 2,
                                'official_text_additions': 5, 'identity_only_cards': 31,
                                'reviewed_excerpt_sources': 9, 'reviewed_excerpt_units': 53,
                                'new_approved_sources': 45, 'pending_text_originals': 48, 'pending_structural_originals': 881},
               'integrity': integrity,
               'ocr': {key: recovery[key] for key in ['source_count', 'physical_pdf_pages', 'newly_recovered_page_count', 'page_status_counts']},
               'numeric': numeric['summary'],
               'tests': {'python_passed': 2118, 'subtests_passed': 134, 'node_groups_passed': 6,
                         'actual_http_questions_including_reruns': actual_count,
                         'distinct_text_questions': 5, 'distinct_numeric_questions': 1,
                         'latest_text_answerability': states, 'not_a_160_question_rerun': True},
               'cases': cases, 'confirmations': [dict(zip(fields, row)) for row in CONFIRMATIONS],
               'limitations': ['full/partial是程序交付状态，不等于独立专家逐句准确率。',
                               '身份卡只支持名称、编号、状态、日期，不代替正文。',
                               '审核过的节选不等于整篇OCR已批准。',
                               '数值valid表示可解析原值，不代表单位、时间和业务基准已核定。',
                               '回答仍有重复；警报更新频率的直接条款仍待补齐。']}
    (OUT / '实际问答与核查结果.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copy2(APP / 'tmp/source_review_full_tests_20261021.txt', OUT / 'Python测试日志.txt')
    shutil.copy2(RUN / 'node_tests.json', OUT / 'Node测试日志.json')
    evidence = APP / 'data/marine_kb/review_evidence/20261021'
    for manifest in (RUN / 'ocr').glob('*_manifest.json'):
        target = evidence / 'excerpt_manifests' / manifest.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest, target)

    def esc(value):
        return html.escape(str(value))
    def table(headers, rows):
        return '<div class="scroll"><table><thead><tr>' + ''.join('<th>'+esc(x)+'</th>' for x in headers) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join('<td>'+esc(x)+'</td>' for x in row)+'</tr>' for row in rows) + '</tbody></table></div>'
    sections = []
    sections.append('<section><h2>本轮完成与计数口径</h2><p>已处理全部50份待审文字原件，补做OCR、核对官方版本，并把可核验的正文和节选接入检索。随后恢复880份Excel原值，建立站点、岸段及阈值差异台账。原始文件未改写，原来明确设置的资料外发权限未改变。</p>' + table(
        ['项目', '本轮前', '本轮后 / 含义'], [
            ['来源登记', '2797', '2842，新增45条经限定范围审核的来源'],
            ['已批准', '38', '85；包括身份卡和节选，不是85篇全文标准'],
            ['待审核', '931', '929＝48份文字原件＋880份Excel＋1份岸段GeoJSON'],
            ['已索引历史资料', '1817', '1817，保留原历史参考规则'],
            ['切片', '25101', '25349；FTS同步25349条，孤链0'],
            ['旧历史台风库', '452份 / 40502切片', '未迁移、未删除；统一管理入口仍按来源分别展示']]) +
        '<p>待审数没有大幅下降，是因为保留了“整篇原件待审”的事实：9份已核对节选作为独立来源使用；880份表的原值已经能查询，但单位、日期和阈值版本缺项仍保留。无需把所有记录一键改成批准。</p></section>')
    sections.append('<section><h2>文字恢复与审核范围</h2><p>46份PDF共835个物理页。120个此前未尝试的页面已完成OCR，新增43页可用文字；隔离页由183降至140。目前358页有可用文字层、337页为未逐页核准OCR、140页隔离。隔离页含空白、公式、密集表格和流程图，不能把它们全称为丢失正文。</p>' + table(
        ['可用来源', '数量', '批准边界'], [
            ['原有2017年站点/资料管理办法', '2份原件', '对照官方文本核准该版本；不据此宣称现行有效'],
            ['官方正式正文/公报', '5份新增来源', '条例2023、站点办法2019、资料办法2017、应急预案2026、2020公报正式版'],
            ['标准身份卡', '31条新增来源', '只支持标准名称、编号、公布/实施日期和目录状态'],
            ['目视核对节选', '9份、53单元', 'GB19721.1/.2、GB39420、GB14914.6及5份历史防台通知的指定页/条款']]) +
        '<p>4份草案已找到正式后继标准身份，但尚未取得公开可用正式全文；草案未被批准为正式规则。2012版条例、2017版站点办法和2020年公报征求意见稿已标注后续正式版本。2014年公报送审稿仍保留待审。</p><p>2026应急预案附件含嵌入流程图，公报含复杂图表；未把未读图内内容自动当作正文事实。海浪方向在不同标准的表述也分别保留，没有用它们推断NC的MWD定义。</p></section>')
    official_items = ''.join('<li><a href="'+esc(row['url'])+'">'+esc(row['title'])+'</a>；归档版本/发布日期：'+esc(row.get('document_issued_at'))+'</li>' for row in public)
    sections.append('<section><h2>核查使用的官方来源</h2><ul>'+official_items+'</ul><p>下载原文、官方目录证据和核对图片保存在本机 review_evidence/20261021；不同年份版本分别保留。目录“现行”只证明该标准身份状态，不证明本地扫描件每一个公式均已正确识别。</p></section>')
    sections.append('<section><h2>数值表格与198岸段</h2><p>880份工作簿、880张表、83,104行、10,634,268个原数值单元格全部恢复，解析错误0。独立归档库保存文件SHA、表名、行列位置、站点/岸段ID、原时间标签和原值。当前主问答支持同时点名多个历史工作簿查询，不再只返回第一个表。</p><p>400份198岸段表共79,200次岸段列登记已依据ADMC及明确SQL别名对应。这个数字不是79,200个岸段或测站；其余480份表使用61/62/71/72列地点。站点表登记83行，同名“健跳”“石塘”仍有多个站号，未强行合并。</p>' + table(
        ['差异', '数量', '处置'], [
            ['四色阈值实际数值冲突', '13字段 / 4岸段', '武原1008、西塘桥1009、乍浦1010、独山港1011；两版保留'],
            ['岸段名称/县区差异', '18字段', '记录原值和显式映射，等待版本确认'],
            ['缺测编码差异', '2字段', '大陈1085黄色/橙色：null与-99999均按无效/缺失处理，不作为0'],
            ['全部差异', '33字段 / 23岸段', '不是33个阈值冲突']]) +
        '<p>所有工作簿时间仍为“日/时分”而缺少年月时区，单位、高程基准和集合统计含义未找到。已检查工作簿内批注和属性：没有可用于补齐这些口径的说明。天文潮TXT的厘米说明不能直接套用这些Excel。现阶段原值可以准确查，正式超警计算仍需产品字典。</p></section>')
    sections.append('<section><h2>代码修复与测试边界</h2><p>本轮修复了指定文件/标准条款召回被标题和日期挤出、期限句没有重复题目关键词而被降级、复合子问查询预算挤占、中文期限单位漏认、出处子问归属，以及“标准发布时效”误进实时预警清单等通用问题。增加正式身份卡日期比对、来源权限不扩张的事务审核、原值只读接口和管理页审核范围标签。</p><p>2118项Python测试、134项子测试及6组Node测试通过。原2797条登记完整保留，2780份独立原文件的大小与SHA全部匹配，原身份字段及显式权限修改0。两次变更前数据库备份均保留。</p><p>真实主问答HTTP测试共'+str(actual_count)+'次，包含调试重测；实际覆盖5个不同文字问题和1个不同多表数值问题。本轮不是160题复测。最新5个文字问题程序状态：'+esc(states)+'；数值题仍为partial，因为真实业务口径尚缺。程序full不是专家准确率。</p></section>')
    summary_rows = []
    for row in cases:
        summary_rows.append([row['title'], row.get('before', {}).get('answerability', '无本轮前测'), row['answerability'], 'HTTP '+str(row['http_status'])])
    sections.append('<section><h2>真实问答前后对照</h2>'+table(['问题类型', '本轮前', '最新实际状态', '接口'], summary_rows)+
                    '<p>站点与资料期限题按明确要求的2017版本回答。海浪题已区分24h预测时效与更新频率，但缺少警报更新频率的直接条款，保留partial。术语回答仍可能重复；若已有定义却又出现待补齐，属于回答恢复/交付检查问题，不能归为数据缺失。</p></section>')
    for row in cases:
        sources_html = '<ul>' + ''.join('<li>资料'+esc(src.get('citation_id'))+'：'+('<a href="'+esc(src['url'])+'">'+esc(src.get('title') or src.get('filename'))+'</a>' if src['url'] else esc(src.get('title') or src.get('filename')))+'；'+esc(src.get('locator') or '')+'</li>' for src in row['sources']) + '</ul>'
        before = ''
        if row.get('before'):
            before = '<details><summary>展开本轮改造前的实际回答</summary>'+render(row['before']['answer'])+'</details>'
        sections.append('<section><h3>'+esc(row['title'])+'</h3><p class="question">'+esc(row['question'])+'</p><p class="meta">HTTP '+str(row['http_status'])+' · '+esc(row['answerability'])+' · 下面逐字保留模型/程序实际回答，未在报告中人工改写。</p>'+render(row['answer'])+'<details><summary>本次实际引用来源</summary>'+sources_html+'</details>'+before+'</section>')
    sections.append('<section><h2>需要确认的事项</h2><p>已确认的“-99999为无效值”已经落实，不再重复询问。其余事项按产品或版本集中确认，原始文件无需逐份重复审核。</p>'+table(fields, CONFIRMATIONS)+'<p>目前还存在部分回答内容重复及过度保守的缺项提示；这些属于代码/回答组织遗留，不需要你补造资料。</p></section>')
    sections.append('<section><h2>使用与交付文件</h2><p><a href="http://127.0.0.1:8774/">打开地图问答</a> · <a href="http://127.0.0.1:8774/knowledge.html">打开知识库管理</a>。刷新已打开页面即可查看新状态。“已核对节选”和“身份已核、全文待核”会分别显示。</p><p>可直接复制：分别查询Surge_detem_TY0216_2026041310.xlsx和Waterlevel_detem_TY0216_2026041310.xlsx的大戢山05/15:00原值，能直接相减当成业务差值吗？</p><ul>'+''.join('<li><a href="'+esc(name)+'">'+esc(name)+'</a></li>' for name in ['待确认事项.csv', '文字专项核查台账.csv', '资料总台账.csv', '数值归档清单.csv', '岸段差异明细.csv', '实际问答与核查结果.json', 'Python测试日志.txt'])+'</ul><p>资料备份：data/marine_kb/review_backups/text-review-20261021-132547 与 numeric-review-20261021-132934。恢复整篇原件不等于批准；审批记录保存范围、核对方式、原件SHA和审核前快照。</p></section>')
    page = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';"><title>资料核查、审核与数值归档报告</title><style>body{max-width:1140px;margin:32px auto;padding:0 22px;color:#163642;background:#f3f7f8;font:16px/1.85 'Microsoft YaHei',sans-serif}h1{font-size:29px}h2{font-size:23px}h3{font-size:21px}section{padding:24px;background:white;border:1px solid #d5e2e6;border-radius:9px;margin:20px 0}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #d5e2e6;padding:9px;vertical-align:top;text-align:left;overflow-wrap:anywhere}th{background:#e6f1f3}a{color:#00768a;overflow-wrap:anywhere}p{margin:12px 0}.scroll{overflow-x:auto}.meta{font-size:14px;color:#5a7078}.question{padding:14px;background:#eaf3f5;border-radius:6px}details{border-top:1px solid #dae6e9;margin-top:20px;padding-top:14px}summary{cursor:pointer;color:#00768a}code{overflow-wrap:anywhere}li{margin:5px 0}</style></head><body><h1>资料核查、审核与数值归档报告</h1><p class="meta">2026年10月21日 · 既有资料原件、官方版本及真实系统问答联合核查</p>'''+''.join(sections)+'</body></html>'
    (OUT / '核查审核与问答测试报告.html').write_text(page, encoding='utf-8')
    files = {path.name: {'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in OUT.iterdir() if path.is_file() and path.name != '文件校验.json'}
    (OUT / '文件校验.json').write_text(json.dumps(files, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(OUT / '核查审核与问答测试报告.html'), 'files': len(files), 'tests': payload['tests']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
