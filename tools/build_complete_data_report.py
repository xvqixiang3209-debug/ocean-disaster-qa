"""Build the v172/v173 comparison from preserved real runs, never ideal answers.

Run only after the 160-response run, audits and end identity have been saved.
Missing/changed inputs fail closed; optional development probes are labelled.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BEIJING = timezone(timedelta(hours=8))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_autonomous_report import readable_answer, record_block, stats

DEFAULT_OLD = ROOT / 'tmp/qa_full160_phase172_20261018_final'
DEFAULT_NEW = ROOT / 'tmp/qa_full160_phase173_20261018_final'
DEFAULT_WORK = ROOT / 'tmp/complete_answer_v173_20261018'
DEFAULT_OUTPUT = ROOT / 'exports/数值表格与历史比较修复_160题实测报告_20261019.html'
GAP_LABELS = {
    'answer_omission_or_generation_validation': '回答遗漏或生成校验',
    'official_notice_opening_or_action_status': '当天公告与执行状态',
    'rules_thresholds_or_document_validity': '规则阈值与文件效力',
    'units_datum_mapping_and_comparability': '单位、基准、归属与可比性',
    'missing_model_site_assets_or_observations': '模型、现场台账与观测',
    'missing_local_dated_data_or_archives': '地方时点数据或档案',
    'retrieval_or_source_access': '检索与来源访问',
    'unclassified': '未分类缺项措辞',
}


def esc(value):
    return html.escape(str(value))


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def manifest_digest(value):
    # run_full160_qa fingerprints the canonical JSON, not pretty-print bytes.
    return text_digest(json.dumps(value, ensure_ascii=False, sort_keys=True))


def demand(condition, message):
    if not condition:
        raise ValueError(message)


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(text, encoding='utf-8')
    temp.replace(path)


def save(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def json_details(label, value):
    return '<details><summary>' + esc(label) + '</summary><pre>' + esc(
        json.dumps(value, ensure_ascii=False, indent=2)) + '</pre></details>'


def table(headers, rows):
    return '<div class="table-scroll"><table><thead><tr>' + ''.join(
        '<th>' + esc(value) + '</th>' for value in headers) + '</tr></thead><tbody>' + ''.join(
        '<tr>' + ''.join('<td>' + esc(value) + '</td>' for value in row) + '</tr>'
        for row in rows) + '</tbody></table></div>'


def by_id(rows, label, expected=None):
    result = {row['id']: row for row in rows}
    demand(len(result) == len(rows), label + ' contains duplicate IDs')
    if expected is not None:
        demand(set(result) == set(expected), label + ' ID set does not match the 160 questions')
    return result


def answer(row):
    value = (row.get('reply') or {}).get('answer', '')
    demand(isinstance(value, str), 'Actual answer must be a string: ' + str(row.get('id')))
    return value


def full_run(folder):
    data = load(folder / 'answers.json')
    manifest = load(folder / 'manifest.json')
    identity = load(folder / 'run_identity.json')
    rows = data['items']
    demand(len(rows) == 160, str(folder) + ' is not a complete 160-question run')
    actual = by_id(rows, str(folder))
    expected = by_id(manifest['items'], str(folder / 'manifest.json'), actual)
    for key, row in actual.items():
        demand(row['question'] == expected[key]['question'], key + ' actual question differs from manifest')
        demand(row.get('manifest_sha256') == identity.get('manifest_sha256'), key + ' row manifest SHA differs from run identity')
        demand(row.get('run_id') == identity.get('run_id'), key + ' belongs to another run')
        answer(row)
    demand(identity.get('manifest_sha256') == manifest_digest(manifest), 'Manifest canonical SHA mismatch: ' + str(folder))
    demand(identity.get('transport') == 'real HTTP', 'Run is not identified as real HTTP: ' + str(folder))
    return data, manifest, identity, actual


def checked_audit(path, source, rows, sha_field):
    value = load(path)
    demand(value.get(sha_field) == digest(source), 'Audit input SHA mismatch: ' + str(path))
    demand(value.get('question_count') == 160, 'Audit is not for 160 questions: ' + str(path))
    indexed = by_id(value['items'], str(path), rows)
    for key, row in rows.items():
        demand(indexed[key].get('answer_sha256') == text_digest(answer(row)), 'Audit answer SHA mismatch: ' + key)
    return value, indexed


def load_content_reviews(work, source, rows):
    """Optional non-expert reviews remain tied to the exact saved answers."""
    grouped = {}
    inputs = []
    expected_sha = digest(source)
    for path in sorted(work.glob('content_review_*.json')):
        value = load(path)
        demand(value.get('source_answers_sha256') == expected_sha,
               'Content review refers to another response file: ' + str(path))
        review_rows = value.get('items')
        demand(isinstance(review_rows, list), 'Content review must contain items: ' + str(path))
        by_id(review_rows, str(path))
        for review in review_rows:
            key = review['id']
            demand(key in rows, 'Unknown ID in content review: ' + key)
            if review.get('question') is not None:
                demand(review['question'] == rows[key]['question'], 'Content review question mismatch: ' + key)
            if review.get('answer_sha256') is not None:
                demand(review['answer_sha256'] == text_digest(answer(rows[key])), 'Content review answer SHA mismatch: ' + key)
            grouped.setdefault(key, []).append({'source_file': path.name, **review})
        inputs.append((path, value))
    return grouped, inputs


def coverage_block(item):
    rows = []
    for part in item.get('parts', []):
        missing = part.get('missing_information') or []
        if not isinstance(missing, list):
            missing = [missing]
        rows.append((part.get('id', ''), part.get('question', ''), part.get('model_status', ''),
                     part.get('span_integrity', 'not_recorded'), '；'.join(map(str, missing))))
    body = '<p>整题模型自评：' + esc(item.get('model_answerability')) + '；程序业务状态：' + esc(
        item.get('business_state')) + '；完整性审阅：' + esc(item.get('completeness_review')) + '。</p>'
    body += table(['子问', '实际子问题', '模型自评', '原文片段完整性', '实际缺项'], rows) if rows else '<p>没有保存子问题覆盖记录。</p>'
    body += '<p>回答中记录的缺项：</p><ul>' + ''.join(
        '<li>' + esc(gap.get('text', '')) + '</li>' for gap in item.get('gaps', [])) + '</ul>'
    body += json_details('完整自动覆盖记录、逐子问答案原文片段及缺项标签', item)
    return body


def program_audit_block(item):
    rows = []
    for fact in item.get('typed_fact_checks', []):
        value = fact.get('value')
        if fact.get('kind') == 'comparison':
            value = str(fact.get('left_value')) + ' - ' + str(fact.get('right_value')) + ' = ' + str(fact.get('difference'))
        rows.append((fact.get('index'), fact.get('kind'), fact.get('required'),
                     fact.get('metric', ''), fact.get('location') or fact.get('event_name') or '',
                     value if value is not None else '', fact.get('unit', ''), fact.get('check', '')))
    body = '<p>程序事实交付检查：' + esc(item.get('typed_delivery_check')) + '；必交付量值事实：' + esc(
        item.get('required_measurement_fact_count', 0)) + '。</p>'
    body += table(['序号', '事实类型', '必交付', '指标', '对象', '值或差值', '单位', '逐项检查'], rows) if rows else '<p>该题没有返回可由此审计器检查的结构化程序事实；文献中的数值另见引用与原文审查。</p>'
    return body + json_details('程序事实完整核对记录（含时间、引用及两侧对象绑定）', item)


class PreservedHTML(HTMLParser):
    """Recover raw answer text, including duplicate/empty responses."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.parts = []
        self.values = []
        self.case_ids = []
        self.all_ids = []
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            self.all_ids.append(attrs['id'])
        if tag == 'article' and 'case' in attrs.get('class', '').split():
            self.case_ids.append(attrs.get('id'))
        if tag == 'pre' and 'actual' in attrs.get('class', '').split():
            self.active = True
            self.parts = []
        if tag == 'a' and attrs.get('href'):
            self.hrefs.append(attrs['href'])

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'pre' and self.active:
            self.values.append(''.join(self.parts))
            self.active = False


def verify_html(document, expected_answers, ids, output):
    parser = PreservedHTML()
    parser.feed(document)
    demand(Counter(parser.values) == Counter(expected_answers), 'Original answer text/counter mismatch in HTML')
    demand(Counter(parser.case_ids) == Counter(ids), 'HTML question IDs do not match the 160 actual IDs')
    demand(len(set(parser.all_ids)) == len(parser.all_ids), 'Duplicate HTML anchor IDs')
    for href in parser.hrefs:
        if href.startswith(('https://', 'http://', 'mailto:')):
            continue
        if href.startswith('#'):
            demand(href[1:] in parser.all_ids, 'Broken HTML anchor: ' + href)
        elif '?' not in href and '#' not in href:
            demand((output.parent / href).is_file(), 'Missing linked artifact: ' + href)
    return {'raw_answer_count': len(parser.values), 'distinct_raw_answer_count': len(set(parser.values)),
            'raw_answer_counter_exact_match': True, 'question_count': len(parser.case_ids),
            'question_ids': parser.case_ids, 'question_id_counter_exact_match': True,
            'local_artifact_links_exist': True}


CSS = '''body{max-width:1180px;margin:28px auto;padding:0 22px;color:#183743;background:#f5f9fa;font:16px/1.75 "Microsoft YaHei",sans-serif}h1,h2,h3{line-height:1.5}section,article{background:#fff;padding:22px;margin:18px 0;border:1px solid #d7e4e7;border-radius:10px}table{border-collapse:collapse;width:100%}.table-scroll,.rendered-answer{overflow-x:auto}th,td{border:1px solid #cfdee1;padding:8px;vertical-align:top;text-align:left;overflow-wrap:anywhere}th{background:#e9f3f3}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.65 Consolas,"Microsoft YaHei",monospace}pre.actual{background:#f4f8f9;padding:14px;font:15px/1.8 "Microsoft YaHei",sans-serif}summary{cursor:pointer;color:#087f83;font-weight:600;margin:10px 0}a{color:#087d9a}.note{background:#fff3dc;padding:14px;border-left:4px solid #cf9136}.badge{font-weight:600;color:#087a7d}.rendered-answer p{margin:10px 0}.rendered-answer code{background:#f0f4f5;padding:1px 4px}nav{display:flex;flex-wrap:wrap;gap:8px;position:sticky;top:0;padding:10px 0;background:#f5f9fa;z-index:1}input{padding:10px;flex:1;min-width:250px;border:1px solid #bdd0d2}button{padding:8px;cursor:pointer;background:white;border:1px solid #bdd0d2;border-radius:4px}.scope{font-size:14px;color:#4c6269}@media print{nav{display:none}body{max-width:none;padding:0}section,article{break-inside:avoid;border-radius:0}}'''


def build(old, new, work, output):
    before, old_manifest, old_identity, old_rows = full_run(old)
    after, manifest, identity, new_rows = full_run(new)
    demand(set(new_rows) == set(old_rows), 'Before/after ID sets differ')
    for key in new_rows:
        demand(new_rows[key]['question'] == old_rows[key]['question'], 'Before/after actual question differs: ' + key)
    end_identity = load(work / 'identity_end.json')
    demand(end_identity.get('version') == identity.get('running_version'), 'End/start running version mismatch')
    runtime_hashes = identity.get('runtime_source_sha256') or {}
    demand(bool(runtime_hashes), 'Runtime hash manifest is empty')
    demand(end_identity.get('runtime_source_sha256') == runtime_hashes, 'Runtime changed during the final run')
    for name, expected in runtime_hashes.items():
        candidate = (ROOT / name).resolve()
        demand(candidate.is_relative_to(ROOT), 'Runtime hash path escapes workspace: ' + name)
        demand(digest(candidate) == expected, 'Current runtime differs from tested code: ' + name)

    old_audit_path = work / 'audit172_compatibility.json'
    old_summary_path = work / 'summary172_compatibility.json'
    old_audit, old_audit_rows = checked_audit(old_audit_path, old / 'answers.json', old_rows, 'input_answers_sha256')
    old_summary, old_summary_rows = checked_audit(old_summary_path, old / 'answers.json', old_rows, 'source_sha256')
    audit, audit_rows = checked_audit(new / 'computation_audit.json', new / 'answers.json', new_rows, 'input_answers_sha256')
    summary, summary_rows = checked_audit(new / 'complete_answer_summary.json', new / 'answers.json', new_rows, 'source_sha256')
    passages, passage_rows = checked_audit(new / 'selected_passage_audit.json', new / 'answers.json', new_rows, 'input_answers_sha256')
    for key, row in new_rows.items():
        selections = sum(len(source.get('supporting_passages', [])) for source in (row.get('reply') or {}).get('sources', []))
        demand(passage_rows[key]['selected_passages'] == selections, 'Selected-passage count differs from actual answer: ' + key)
    # The input file must not silently change after its analysis was generated.
    input_hashes = {str(path.relative_to(ROOT)): digest(path) for path in (
        old / 'answers.json', old / 'manifest.json', old / 'run_identity.json',
        new / 'answers.json', new / 'manifest.json', new / 'run_identity.json',
        old_audit_path, old_summary_path, new / 'computation_audit.json',
        new / 'complete_answer_summary.json', new / 'selected_passage_audit.json', work / 'identity_end.json')}
    a, b = stats(before), stats(after)
    expected_answers = [answer(row) for row in before['items']] + [answer(row) for row in after['items']]
    content_reviews, review_inputs = load_content_reviews(work, new / 'answers.json', new_rows)
    for path, _ in review_inputs:
        input_hashes[str(path.relative_to(ROOT))] = digest(path)

    def link(path, label):
        path = path.resolve()
        demand(path.is_file(), 'Cannot link nonexistent evidence: ' + str(path))
        return '<a href="' + esc(Path(os.path.relpath(path, output.parent)).as_posix()) + '">' + esc(label) + '</a>'

    def section(title, body, key=None):
        return '<section' + (' id="' + esc(key) + '"' if key else '') + '><h2>' + esc(title) + '</h2>' + body + '</section>'

    def metric(audit_value, key):
        return sum(row.get(key, 0) for row in audit_value['items'])

    metrics = [
        ('完整保留的实际请求数', len(old_rows), len(new_rows)),
        ('HTTP 200', a['http200'], b['http200']),
        ('正文引用与返回来源同时存在', a['grounded_count'], b['grounded_count']),
        ('模型自评full（未经专家验收）', old_summary['model_answerability_counts'].get('full', 0), summary['model_answerability_counts'].get('full', 0)),
        ('带必交付程序量值的问题', sum(r.get('required_measurement_fact_count', 0) > 0 for r in old_audit['items']), sum(r.get('required_measurement_fact_count', 0) > 0 for r in audit['items'])),
        ('必交付程序量值条数（含历史值及差值）', old_audit.get('required_measurement_fact_count', 0), audit.get('required_measurement_fact_count', 0)),
        ('逐项程序事实检查pass', old_audit['typed_fact_check_counts'].get('pass', 0), audit['typed_fact_check_counts'].get('pass', 0)),
        ('逐项程序事实检查fail', sum(n for k, n in old_audit['typed_fact_check_counts'].items() if k.startswith('fail:')), sum(n for k, n in audit['typed_fact_check_counts'].items() if k.startswith('fail:'))),
        ('回答仍保留校验错误记录（含恢复成功）', len(a['errors']), len(b['errors'])),
        ('已保存子问答案片段核对pass', old_summary.get('part_span_integrity_counts', {}).get('pass', 0), summary.get('part_span_integrity_counts', {}).get('pass', 0)),
        ('已保存子问答案片段核对fail', old_summary.get('part_span_integrity_counts', {}).get('fail', 0), summary.get('part_span_integrity_counts', {}).get('fail', 0)),
    ]
    if a['latency_seconds'] and b['latency_seconds']:
        metrics.extend([('请求耗时中位数（秒）', a['latency_seconds']['median'], b['latency_seconds']['median']),
                        ('请求耗时P95（秒）', a['latency_seconds']['p95'], b['latency_seconds']['p95'])])
    review_labels = {'delivered': '完整交付', 'partial': '部分交付',
                     'missing': '核心未交付', 'not_applicable': '不适用'}
    top_review_counts = Counter()
    for key, entries in content_reviews.items():
        states = {str(item.get('delivery_status', '未标注')) for item in entries}
        top_review_counts[next(iter(states)) if len(states) == 1 else '复核意见不一致'] += 1
    headline = '<p>本轮首先核对实际问题要求的内容是否真正进入回答。开发逐题复核把“完整交付”定义为所问内容已在限定口径下交付，“部分交付”表示已有实质内容但仍有子问或数据缺项，“核心未交付”表示主要所问结果尚未给出。</p>'
    headline += table(['最终新轮开发逐题复核', '题数'], [
        (review_labels[state], top_review_counts.get(state, 0)) for state in ('delivered', 'partial', 'missing')]
        + [(review_labels.get(state, state), count) for state, count in top_review_counts.items()
           if state not in {'delivered', 'partial', 'missing'}]
        + [('尚未复核', 160 - len(content_reviews))])
    headline += '<p class="note">这组计数只取最终新轮实际答案的逐题开发复核，不使用候选轮分数。它衡量回答交付，不是专家正确率；完整交付仍保留来源版本、地域、时段和试算假设等适用范围。概念与方法题按问题本身核对，不因没有数值就判失败。</p>'
    headline += '<p>本轮目标是把所问地点、时段和业务指标的实际值交付到回答，并解释差值和缺项。共保存新版160题与基线160题的实际问题及回答；每题均附缺项、来源、子问题覆盖和程序事实审计。</p>'
    headline += table(['直接可统计的项目', old_identity['running_version'], identity['running_version']], metrics)
    headline += '<p class="note">HTTP成功、带引用、模型自评full、原文片段存在和程序数值交付分别统计。它们均不是160题“完整正确率”；本报告没有专家逐题验收结论，也没有用单独重试替换整轮失败记录。</p>'
    headline += '<p>基线保存记录按本轮审计器重新核对，原始回答未改写。程序量值条数包括历史值和差值；覆盖范围或计数型事实单列，文献数值也不等同于程序计算。</p>'
    headline += '<p>前后使用相同的160题具体问句，但执行时间、联网检索结果和模型生成可能变化；这是两个实际运行批次的对照，差异不能全部归因于某一项代码修改。</p>'

    family_index = {}
    manifest_lookup = by_id(manifest['items'], 'manifest category index')
    for key in new_rows:
        requirement = manifest_lookup[key]
        category_key = (str(requirement.get('category_id') or '未标分类'), str(requirement.get('category') or '未标分类'))
        family = family_index.setdefault(category_key, {'total': 0, 'counts': Counter(), 'examples': {}})
        family['total'] += 1
        entries = content_reviews.get(key, [])
        states = {str(item.get('delivery_status', '未标注')) for item in entries}
        state = next(iter(states)) if len(states) == 1 else '复核意见不一致' if states else '未复核'
        family['counts'][state] += 1
        family['examples'].setdefault(state, []).append(key)
    family_rows = []
    for (category_id, category_name), family in sorted(family_index.items()):
        examples = '；'.join(review_labels.get(state, state) + '：' + '、'.join(ids[:3])
                             for state, ids in family['examples'].items())
        family_rows.append((category_id + ' ' + category_name, family['total'],
                            family['counts'].get('delivered', 0), family['counts'].get('partial', 0),
                            family['counts'].get('missing', 0), family['counts'].get('未复核', 0),
                            sum(count for state, count in family['counts'].items() if state not in {'delivered', 'partial', 'missing', '未复核'}), examples))
    family_body = '<p>分类直接沿用160题台账的category_id/category，覆盖不同问法所代表的问题类型。计数仅取最终新轮逐题开发复核；代表题号按实际交付状态列出，未复核题不推断结论。</p>'
    family_body += table(['原台账类型', '样例题数', '完整交付', '部分交付', '核心未交付', '未复核', '其他/复核不一致', '代表题号'], family_rows)
    family_body += '<p class="note">这是160个固定实例在各类型中的实际表现，不代表整类问题已经全部通过，更不能代替改变地点、日期、事件、单位和追问方式后的独立业务验收。</p>'

    focus = ''
    for key, title in [('Q-037', '数值分级表'), ('R-007', '历史与预报影响对照')]:
        row = new_rows[key]
        focus += '<h3><a href="#' + key + '">' + key + ' ' + title + '</a></h3><p>' + esc(row['question']) + '</p>'
        focus += '<p>实际模型状态：' + esc((row.get('reply') or {}).get('answerability')) + '；程序交付核对：' + esc(audit_rows[key].get('typed_delivery_check')) + '。</p>'
        focus += '<div class="rendered-answer">' + readable_answer(answer(row)) + '</div>'
        focus += json_details('这题的实际缺项与覆盖记录', summary_rows[key])
    focus += '<p class="note">R-007的海况预报场与“蔷琵”的台风场景归属仍需核定。岸段预报峰值与历史验潮站峰值属于不同采样集合；数值对照不能推出总体灾损偏重或偏轻，也不能产生尚未计算的淹没、人口转移或经济损失。</p>'

    changes = '<ol><li>数值表格恢复保留原表头、单位、区间和出处，逐行核对；校验失败时记录具体未保留行，不再把表格全部丢弃后只回答通用解释。</li><li>短且具有明确单位的表格资料保留完整原段，避免截掉单位、表头或相邻数值；对于正文明确引用的附录，沿同一来源跟随取得对应附录，不能从其他文件借表头、定义或单位拼接。</li><li>读取已选官方热带气旋标准表1的真实区间和等级名称，将用户明确的2分钟平均风速逐值归类；保留用户输入、原表、计算不等式和结果。阵风、其他平均时距、未注明单位、缺表或矛盾表不套用该口径。</li><li>历史资料按事件标题和章节边界提取，保留实际测站、年月、量纲、基准和统计地域；旧报告测站子集不冒充全事件峰值，城市损失不冒充全省总量，多个城市合计不拆给单个城市。</li><li>历史对历史分别提供两侧原值和同口径差值；绝对潮位仅在同站且基准明确时作差。历史对预报使用截至查询时刻已起报的数据，标出有效时段、可用岸段和缺测，不把后来起报场倒灌到过去。</li><li>子问题覆盖保存实际回答片段；程序事实交付核对值、单位、对象、时段、地域及比较方向。模型状态与可核对交付分别报告，原始失败和恢复记录均保留。</li><li>“找历史上最相似的台风，再问它的损失”保留路径相似检索，损失只连接同一匹配事件已有的真实报告；明确两次事件的指标比较继续走历史比较。包含活动子问时分别查询并保留该活动的日期。</li><li>原问明确是假设情景时保留情景语气，不把改写后的具体地点日期认定为已发生事实；历史数值问句的选段保留直接相关原表或段落，使已取得的数据进入回答。首次整轮暴露的回归原样归档，修复后另跑完整160题。</li></ol>'

    gaps = '<p>下表按最终记录里的缺项措辞自动归类，一题可归入多个类别；它是复核入口，并非经人工证实的互斥根因。尤其不能把所有partial都解释为外部缺数据。</p>'
    gaps += table(['缺项措辞分类', '基线题数', '新版题数'], [
        (GAP_LABELS.get(key, key), old_summary.get('gap_type_question_counts', {}).get(key, 0), summary.get('gap_type_question_counts', {}).get(key, 0))
        for key in sorted(set(old_summary.get('gap_type_question_counts', {})) | set(summary.get('gap_type_question_counts', {})))])
    gaps += '<p>没有记录缺项的题号：' + esc('、'.join(summary.get('no_stated_gap_ids', [])) or '无') + '。没有记录缺项不等于已证明完整；具体要求、真实数据和答复仍需逐题复核。</p>'
    gaps += '<ul><li>当天开放、停复航、预警执行和赔付结果，需要对应地点、对象、有效日期的实际公告或台账。公开规则不能证明某天已经执行。</li><li>淹没深度、受影响人数和经济损失，需要同基准边界、精细地形、堤闸泵、当地资产人员台账及经过校验的计算，现有人口格网或海况峰值不能替代。</li><li>预报准确率、概率及提前量，需要配对预报与观测、明确事件和评分口径；程序读出数值不能证明预报误差已通过业务验收。</li><li>NC的SWL解释、时间基点和垂直基准仍保留已说明的试算口径；历史比较的同站映射、台风归属和各年灾损价格口径独立核对。</li></ul>'

    review_body = '<p>内容复核逐题对照实际问题与实际回答，区分已交付的具体值、外部数据缺口、检索或代码问题，以及合理的适用范围限制。它是本轮开发复核，未经业务专家验收，不把模型partial一概当成缺数据。具体数据项true表示至少一项所问数值、时间、地点清单或状态已交付；是否全部交付另看交付状态。null表示题目主要询问一般概念或方法，不用无数字判失败。</p>'
    review_status_counts = Counter()
    if review_inputs:
        review_table = []
        for key in new_rows:
            if key not in content_reviews:
                continue
            entries = content_reviews[key]
            states = {str(item.get('delivery_status', '未标注')) for item in entries}
            review_status_counts[next(iter(states)) if len(states) == 1 else '复核意见不一致'] += 1
            for item in entries:
                def review_text(field):
                    text = item.get(field, '')
                    return '；'.join(map(str, text)) if isinstance(text, list) else str(text)
                review_table.append((key, review_labels.get(item.get('delivery_status'), item.get('delivery_status')), item.get('has_requested_concrete_data'),
                                     review_text('missing_data'), review_text('retrieval_or_code_issue'),
                                     review_text('scope_limits'), review_text('observation')))
        review_body += '<p>已复核' + str(len(content_reviews)) + '/160题；其余题没有从自动标签推定结论。按唯一题号统计复核交付状态：' + esc({review_labels.get(key, key): count for key, count in review_status_counts.items()}) + '。</p>'
        review_body += table(['题号', '交付状态', '至少一项所问具体数据已交付', '缺数据', '检索/代码/生成问题', '范围限制', '实际观察'], review_table)
        review_body += '<p>' + ' · '.join(link(path, path.stem) for path, _ in review_inputs) + '</p>'
    else:
        review_body += '<p>尚未提供单独的内容复核文件；本文没有把自动指标改写成人工或专家评分。</p>'

    reviewed_gaps = '<p>以下只使用最终新轮开发复核中已记录的具体观察，一题可能兼有多种障碍，不能将三个类别的题数相加当成互斥根因。未检索到资料表示本次没有取得，不能据此断言公开数据不存在。</p>'
    problem_groups = {'代码或检索仍需修复': [], '外部资料或业务数据缺口': [], '过期批次与时效门禁': []}
    for key, entries in content_reviews.items():
        for item in entries:
            issue = item.get('retrieval_or_code_issue') or []
            missing = item.get('missing_data') or []
            issue = issue if isinstance(issue, list) else [str(issue)]
            missing = missing if isinstance(missing, list) else [str(missing)]
            for text in issue:
                group = '过期批次与时效门禁' if any(word in text for word in ['批次过期', '过期批次', '实时过期', '跨日', '时效门禁']) else '代码或检索仍需修复'
                problem_groups[group].append((key, text))
            is_stale = any(row[0] == key for row in problem_groups['过期批次与时效门禁'])
            for text in missing:
                if is_stale and any(word in text for word in ['新批次', '当前查询', '过期']):
                    problem_groups['过期批次与时效门禁'].append((key, text))
                else:
                    problem_groups['外部资料或业务数据缺口'].append((key, text))
    for label, records in problem_groups.items():
        records = list(dict.fromkeys(records))
        reviewed_gaps += '<h3>' + esc(label) + ' · ' + str(len({key for key, _ in records})) + '题</h3>'
        reviewed_gaps += table(['最终新轮题号', '逐题复核的具体观察'], records) if records else '<p>最终复核文件未记录此类问题；不据此证明该类问题全部消除。</p>'
    reviewed_gaps += '<p>过期批次拒绝计算是时效控制，不等于原始数据文件不存在，也不能靠放宽门禁获得“完整答案”。当日问题需要新的有效批次；历史回溯需要明确截至时刻，并使用此前已发布且覆盖目标时段的数据。检索已取得但未交付的内容，需要继续修复证据选择或生成；未取得的现场公告、观测和资产人员台账则需补齐对应数据。</p>'

    audits_body = '<p>结构化事实逐项结果：' + esc(audit.get('typed_fact_check_counts')) + '；整题程序交付：' + esc(audit.get('typed_delivery_counts')) + '。</p>'
    audits_body += table(['检查', '本轮实际记录'], [
        ('时段冲突题号', audit.get('forecast_period_mismatch_ids', [])),
        ('方括号引用编号冲突题号', audit.get('citation_id_mismatch_ids', [])),
        ('正文提及资料编号未返回来源的题号', audit.get('source_mention_mismatch_ids', [])),
        ('台风快照截至时间冲突题号', audit.get('typhoon_snapshot_cutoff_mismatch_ids', [])),
        ('子问原文片段完整性失败题号', summary.get('span_integrity_failure_ids', [])),
        ('保存选用原段的问题数', passages.get('questions_with_saved_selections')),
        ('保存选用原段数', passages.get('selection_count')),
        ('本地原切片匹配数', passages.get('local_chunk_matches')),
        ('原段哈希冲突', passages.get('hash_mismatches', [])),
        ('本地原切片内容冲突', passages.get('local_source_mismatches', [])),
    ])
    audits_body += '<p>方括号引用编号检查与正文“资料N”提及检查单独统计；正文提及不计作必交付事实的正规引用。编号存在检查也不能发现全部引用错配，例如编号存在但该条来源不支持所述事实，仍需看逐题内容复核。</p>'
    audits_body += '<p>原段哈希和本地切片核对证明保存内容一致；它们不证明语义蕴含、文件现行效力或业务结论正确。</p>'
    audits_body += table(['恢复统计', '基线', '新版'], [(key, old_summary.get('table_recovery', {}).get(key), summary.get('table_recovery', {}).get(key)) for key in sorted(set(old_summary.get('table_recovery', {})) | set(summary.get('table_recovery', {})))])
    audits_body += '<p>旧记录没有保存逐行恢复计数时，not_recorded代表未记录，不能解释为当时没有表格或所有表行均失败。</p>'
    table_review_path = work / 'numeric_table_recovery_audit.json'
    table_review = load(table_review_path)
    demand(table_review.get('source_answers_sha256') == digest(new / 'answers.json'),
           'Table review belongs to another response file')
    table_review_rows = []
    for item in table_review['items']:
        key = item['id']
        demand(key in new_rows and item.get('answer_sha256') == text_digest(answer(new_rows[key])),
               'Table review answer SHA mismatch: ' + key)
        table_review_rows.append((key, item['table_rows_considered'], item['table_rows_retained'],
                                  item['table_rows_rejected'], item.get('manual_observation', '')))
    input_hashes[str(table_review_path.relative_to(ROOT))] = digest(table_review_path)
    considered_rows = sum(row[1] for row in table_review_rows)
    retained_rows = sum(row[2] for row in table_review_rows)
    rejected_rows = sum(row[3] for row in table_review_rows)
    audits_body += '<p class="note">进入核对与最终保留分别统计。最终新轮' + str(considered_rows) + '行进入表格恢复核对，' + str(retained_rows) + '行进入正文，' + str(rejected_rows) + '行未通过校验；没有跳过不代表全部恢复。逐行恢复结论和仍未交付的子问如下。</p>'
    audits_body += table(['题号', '进入核对', '正文保留', '拒绝', '实际复核'], table_review_rows)
    audits_body += '<p>' + link(table_review_path, '最终新轮逐行恢复决定与正文复核') + '</p>'
    audits_body += json_details('新版完整性自动汇总（不含160条重复详情）', {k: v for k, v in summary.items() if k != 'items'})
    audits_body += json_details('新版程序交付审计汇总', {k: v for k, v in audit.items() if k != 'items'})

    extras = '<p>以下是额外问法的真实HTTP开发检查，参与了开发和修复，不作为独立专家验收集，也不并入160题统计。</p>'
    acceptance_count = 0
    acceptance_paths = sorted(work.glob('acceptance_candidate*/answers.json'))
    if (work / 'acceptance/answers.json').is_file():
        acceptance_paths.append(work / 'acceptance/answers.json')
    for acceptance_path in acceptance_paths:
        acceptance = load(acceptance_path)
        extra_rows = acceptance['items']
        by_id(extra_rows, 'Additional development probes')
        acceptance_count += len(extra_rows)
        extras += '<h3>' + esc(acceptance_path.parent.name) + ' · ' + str(len(extra_rows)) + '次真实请求</h3>'
        extras += link(acceptance_path, '本轮额外问法实际记录（保留失败及修复前结果）')
        for row in extra_rows:
            expected_answers.append(answer(row))
            extras += '<h3>' + esc(str(row['id']) + ' ' + row['question']) + '</h3>' + record_block(row, '开发检查实际回答')
        if acceptance.get('identity'):
            extras += json_details('额外问法当时运行身份（可能早于最终冻结）', acceptance['identity'])
        input_hashes[str(acceptance_path.relative_to(ROOT))] = digest(acceptance_path)
    if not acceptance_paths:
        extras += '<p>尚未提供额外问法answers.json；本报告未伪造这部分结果。</p>'

    candidate_runs = []
    candidate_body = '<p>开发过程中发现相似台风与损失复合问题被历史比较路由抢占，Q-014由基线的路径相似检索退化为无数据。该问题触发独立修复与整轮复测；首次完整160题结果原样保留，不用最终轮或单题重试覆盖。</p>'
    candidate_prefix = new.name.removesuffix('_final')
    for folder in sorted(new.parent.glob(candidate_prefix + '*candidate*')):
        if not folder.is_dir() or folder.resolve() == new.resolve():
            continue
        run, candidate_manifest, candidate_identity, candidate_rows = full_run(folder)
        demand(set(candidate_rows) == set(new_rows), 'Candidate ID set differs: ' + str(folder))
        demand(all(candidate_rows[key]['question'] == new_rows[key]['question'] for key in new_rows),
               'Candidate actual questions differ from final round: ' + str(folder))
        for name in ['answers.json', 'manifest.json', 'run_identity.json']:
            input_hashes[str((folder / name).relative_to(ROOT))] = digest(folder / name)
        candidate_runs.append({'directory': str(folder), 'question_count': len(candidate_rows),
                               'run_id': candidate_identity.get('run_id'),
                               'running_version': candidate_identity.get('running_version'),
                               'answers_sha256': digest(folder / 'answers.json')})
        candidate_body += '<h3>' + esc(folder.name) + ' · 独立保留的160题</h3><p>'
        candidate_body += ' · '.join(link(folder / name, label) for name, label in [
            ('answers.json', '本候选轮全部160题实际回答'), ('manifest.json', '本候选轮实际问句'),
            ('run_identity.json', '本候选轮版本与源代码哈希')]) + '</p>'
        candidate_body += json_details('候选轮身份与实际统计（不并入最终160题）',
                                      {'identity': candidate_identity, 'stats': stats(run)})
        row = candidate_rows['Q-014']
        candidate_body += '<h3>Q-014 回归实录</h3>' + record_block(row, '候选轮实际回答，保留回归原貌')
        expected_answers.append(answer(row))
    for path in sorted((work / 'candidate1_review').glob('*.json')):
        candidate_body += '<p>' + link(path, '候选第一轮开发复核：' + path.name) + '</p>'
        input_hashes[str(path.relative_to(ROOT))] = digest(path)
    if not candidate_runs:
        candidate_body += '<p>尚无已归档的完整候选轮；本报告未生成或替代候选答案。</p>'

    program_probes = '<p>以下记录直接调用本地计算模块并检验确定性结果，未调用语言模型，也不是HTTP整轮回答。它们与上面的实际网页接口答案分别保存，不能相互替换。</p>'
    probe_count = 0
    for name in ['comparison_actual_program_probes_final.json', 'comparison_jurisdiction_program_probes.json']:
        path = work / name
        if not path.is_file():
            continue
        data = load(path)
        rows = data['items'] if isinstance(data, dict) else data
        program_probes += '<h3>' + esc(name) + '</h3>' + link(path, '程序原值、来源与差值JSON')
        input_hashes[str(path.relative_to(ROOT))] = digest(path)
        for row in rows:
            probe_count += 1
            text = row.get('answer', '')
            expected_answers.append(text)
            program_probes += '<details><summary>' + esc(row['question']) + '</summary><div class="rendered-answer">' + readable_answer(text) + '</div>'
            program_probes += '<details><summary>程序原文逐字记录</summary><pre class="actual">' + esc(text) + '</pre></details>'
            program_probes += json_details('程序原值、范围、差值、来源和核对结果', {k: v for k, v in row.items() if k != 'answer'}) + '</details>'
    wind_replay = work / 'wind_interval_actual_source_replay.json'
    if wind_replay.is_file():
        program_probes += '<h3>官方原表风速区间归类的程序重放</h3><p>使用保存的真实选段验证程序区间判断，不是新HTTP或模型回答。</p>'
        program_probes += link(wind_replay, '原表、用户输入、区间比较和程序结果')
        program_probes += json_details('程序重放完整记录', load(wind_replay))
        input_hashes[str(wind_replay.relative_to(ROOT))] = digest(wind_replay)

    public_data = '<p>公开资料的获取、页面正文、PDF/OCR原件核对和受阻记录分别保存。抓到正文不代表已核实现行效力，目录发现也不等于相应业务数据已可计算。</p>'
    for name, label in [('research_continue/fetch_manifest.json', '公开来源获取清单'),
                        ('research_continue/integration_ready.json', '公开资料核对与接入候选记录'),
                        ('research_continue/verified_sources.json', '公开正文核对记录'),
                        ('research_continue/ningbo_policy_verified_original.json', '宁波保险原件核对'),
                        ('comparison_jurisdiction_catalog.json', '历史灾害实际统计地域核对')]:
        path = work / name
        if path.is_file():
            public_data += '<p>' + link(path, label) + '</p>'
            public_data += json_details(label, load(path))
            input_hashes[str(path.relative_to(ROOT))] = digest(path)

    manifest_rows = by_id(manifest['items'], 'manifest')
    cases = []
    for row in after['items']:
        key = row['id']
        item = summary_rows[key]
        badge = '模型自评 ' + str(item.get('model_answerability')) + ' · 程序交付 ' + str(audit_rows[key].get('typed_delivery_check'))
        case = '<article class="case" id="' + esc(key) + '"><h3 class="question">' + esc(key + ' ' + row['question']) + '</h3><p class="badge">' + esc(badge) + '</p>'
        requirement = manifest_rows[key]
        case += '<p class="scope">需求分类：' + esc(requirement.get('category', '')) + '；预期交付：' + esc(requirement.get('expected_output') or requirement.get('visible_result', '')) + '。</p>'
        case += json_details('原始问题、具体化说明与本题需求口径', requirement)
        case += record_block(row, '新版实际回答（逐字原文、来源及原始状态）')
        case += record_block(old_rows[key], '基线v1.7.2实际回答（逐字原文）')
        case += '<details><summary>新版逐子问自动覆盖、回答片段与缺项</summary>' + coverage_block(item) + '</details>'
        case += '<details><summary>基线逐子问自动覆盖与缺项</summary>' + coverage_block(old_summary_rows[key]) + '</details>'
        case += '<details><summary>新版程序事实逐项交付审计</summary>' + program_audit_block(audit_rows[key]) + '</details>'
        case += '<details><summary>基线程序事实逐项交付审计</summary>' + program_audit_block(old_audit_rows[key]) + '</details>'
        case += json_details('本题业务计算原始范围、批次、来源与缺测记录', row.get('business_evidence') or {})
        case += json_details('本题实际选用原段核对', passage_rows[key]) + '</article>'
        if key in content_reviews:
            case = case.removesuffix('</article>') + json_details('本题内容复核：实际数据交付、外部缺项及程序问题（非专家验收）', content_reviews[key]) + '</article>'
        cases.append(case)

    artifact_links = '<p>' + ' · '.join(link(path, label) for path, label in [
        (old / 'answers.json', '基线160题原始记录'), (new / 'answers.json', '新版160题原始记录'),
        (new / 'manifest.json', '实际具体问题清单'), (new / 'run_identity.json', '新版运行身份及哈希'),
        (work / 'identity_end.json', '整轮结束运行身份'), (new / 'complete_answer_summary.json', '完整性自动汇总'),
        (new / 'computation_audit.json', '程序事实逐项审计'), (new / 'selected_passage_audit.json', '选用原段审计'),
        (old_audit_path, '基线按当前审计器复核'), (old_summary_path, '基线完整性重新汇总')]) + '</p>'
    artifact_links += '<p>新版冻结运行文件数量：' + str(len(runtime_hashes)) + '；开始、结束与当前磁盘哈希一致。160题ID、实际问题和清单一致；正文原文按多重集合逐条核对，重复答案和空答案同样计数。</p>'

    document = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>数值表格与历史比较修复：160题实测报告</title><style>' + CSS + '</style></head><body>'
    document += '<h1>数值表格与历史比较修复：160题实测报告</h1><p>生成时间：' + datetime.now(BEIJING).isoformat(timespec='seconds') + '；运行版本：' + esc(identity['running_version']) + '；运行编号：' + esc(identity.get('run_id')) + '。</p>'
    document += section('实际结果与基线对照', headline)
    document += section('按原台账问题类型查看交付情况', family_body)
    document += section('本轮解决的问题', changes)
    document += section('重点题的实际输出', focus)
    document += section('最终复核：代码问题、数据缺口与时效门禁', reviewed_gaps)
    document += section('还缺什么、哪些需要继续核查', gaps)
    document += section('逐题内容复核：具体数据与实际缺口', review_body)
    document += section('自动覆盖与程序事实审计', audits_body)
    document += section('联网资料及原件核对', public_data)
    document += section('完整候选轮与回归复测记录', candidate_body)
    document += section('额外问法的真实HTTP检查', extras)
    document += section('历史比较的本地程序值检查', program_probes)
    document += section('原始记录与一致性检查', artifact_links)
    document += '<h2 id="all-questions">160题逐题实际回答与基线</h2><p>点击展开新版、基线及逐项检查。搜索匹配题号、提问、实际回答和缺项；自动覆盖标签并非专家结论。</p><nav><input id="search" aria-label="题号、地点或关键词" placeholder="输入题号、地点或关键词"><button id="expand">展开新版回答</button><button id="collapse">收起所有回答</button><span id="count">160 / 160题</span></nav><main id="cases">' + ''.join(cases) + '</main>'
    document += '''<script>document.querySelector('#search').addEventListener('input',e=>{const q=e.target.value.trim().toLowerCase();let n=0;document.querySelectorAll('#cases article.case').forEach(a=>{a.hidden=!a.textContent.toLowerCase().includes(q);if(!a.hidden)n++});document.querySelector('#count').textContent=n+' / 160题'});document.querySelector('#expand').addEventListener('click',()=>document.querySelectorAll('#cases article.case>details.answer-block:first-of-type').forEach(d=>{if(!d.parentElement.hidden)d.open=true}));document.querySelector('#collapse').addEventListener('click',()=>document.querySelectorAll('#cases details').forEach(d=>d.open=false));</script></body></html>'''
    verification = verify_html(document, expected_answers, list(new_rows), output)
    for name, expected in input_hashes.items():
        demand(digest(ROOT / name) == expected, 'Input changed while rendering: ' + name)
    evaluation = {'generated_at': datetime.now(BEIJING).isoformat(timespec='seconds'),
                  'before': a, 'after': b, 'baseline_version': old_identity['running_version'],
                  'running_version': identity['running_version'], 'run_id': identity.get('run_id'),
                  'runtime_hash_count': len(runtime_hashes), 'same_160_actual_questions': True,
                  'start_end_disk_runtime_hashes_match': True, 'expert_accepted': False,
                  'actual_HTTP_development_probe_count': acceptance_count,
                  'preserved_candidate_full_runs': candidate_runs,
                  'separate_local_program_probe_count': probe_count,
                  'nonexpert_content_review_question_count': len(content_reviews),
                  'nonexpert_content_review_state_counts': dict(review_status_counts),
                  'manifest_category_delivery': [{'category_id': key[0], 'category': key[1],
                      'sample_question_count': value['total'], 'delivery_counts': dict(value['counts']),
                      'representative_ids': {state: ids[:3] for state, ids in value['examples'].items()}}
                      for key, value in sorted(family_index.items())],
                  'new_complete_answer_summary': {k: v for k, v in summary.items() if k != 'items'},
                  'new_computation_audit': {k: v for k, v in audit.items() if k != 'items'},
                  'new_selection_audit': {k: v for k, v in passages.items() if k != 'items'}}
    atomic_write(output, document)
    save(work / 'complete_data_evaluation.json', evaluation)
    save(work / 'report_verification.json', {
        **verification, 'expert_accepted': False, 'input_sha256': input_hashes,
        'output': str(output), 'output_sha256': digest(output),
        'baseline_manifest_sha256': digest(old / 'manifest.json'),
        'new_manifest_sha256': digest(new / 'manifest.json'),
        'baseline_manifest_canonical_sha256': manifest_digest(old_manifest),
        'new_manifest_canonical_sha256': manifest_digest(manifest),
        'actual_questions_match_manifest_and_baseline': True,
        'preserved_candidate_full_runs': candidate_runs,
        'runtime_version': identity['running_version'], 'runtime_source_sha256': runtime_hashes,
        'running_identity_unchanged_at_end': True, 'runtime_matches_current_disk': True,
        'saved_audits_match_actual_answer_hashes': True,
        'content_audit_findings': {key: audit.get(key, []) for key in (
            'forecast_period_mismatch_ids', 'citation_id_mismatch_ids', 'source_mention_mismatch_ids', 'typhoon_snapshot_cutoff_mismatch_ids')},
        'selection_hash_mismatches': passages.get('hash_mismatches', []),
        'selection_source_mismatches': passages.get('local_source_mismatches', []),
    })
    return {'output': str(output), 'verification': verification, 'expert_accepted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old', type=Path, default=DEFAULT_OLD)
    parser.add_argument('--new', type=Path, default=DEFAULT_NEW)
    parser.add_argument('--work', type=Path, default=DEFAULT_WORK)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(build(args.old.resolve(), args.new.resolve(), args.work.resolve(), args.output.resolve()), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
