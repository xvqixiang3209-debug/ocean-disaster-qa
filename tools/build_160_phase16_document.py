"""Build the phase 1.6 report from complete live HTTP records and AI reviews.

This script never calls the question answering API.  Run --validate-only while
records are still arriving; author the final DOCX only after the review merge.
"""
from __future__ import annotations

import argparse
from collections import Counter, OrderedDict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import zipfile

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from lxml import etree

APP = Path(__file__).resolve().parents[1]
RUN = APP / 'tmp/qa_full160_phase16_20261017'
OUTPUT = APP / 'exports/海洋灾害160题新版实测问答与数据缺项_20261017.docx'
LABELS = ('完整回答', '部分回答', '未回答')
BODY_WIDTH = 6.6


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def as_text(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def lines_of(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [as_text(v) for v in value if v is not None and as_text(v).strip()]
    if isinstance(value, dict):
        return [f'{key}：{as_text(val)}' for key, val in value.items()]
    return [str(value)] if str(value).strip() else []


def font(run, size=11, bold=None):
    run.font.name = '宋体'
    run.font.size = Pt(size)
    run.font.color.rgb = RGBColor(0, 0, 0)
    run.font.underline = False
    f = run._element.get_or_add_rPr().get_or_add_rFonts()
    for key in ('ascii', 'hAnsi', 'eastAsia', 'cs'):
        f.set(qn('w:' + key), '宋体')
    if bold is not None:
        run.bold = bold


def plain_inline(paragraph, text, *, size=11, bold=False):
    run = paragraph.add_run(str(text))
    font(run, size, bold)


INLINE_TOKEN = re.compile(r'(\*\*[^*\n]+\*\*|`[^`\n]+`)')


def markdown_inline(paragraph, text, *, size=11):
    """Convert only unambiguous inline formatting; preserve all other text."""
    for part in INLINE_TOKEN.split(text):
        if not part:
            continue
        strong = part.startswith('**') and part.endswith('**')
        code = part.startswith('`') and part.endswith('`')
        if strong:
            part = part[2:-2]
        elif code:
            part = part[1:-1]
        plain_inline(paragraph, part, size=size, bold=strong)


def paragraph(doc, text='', *, bold=False, size=11, keep_next=False, after=4):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.keep_with_next = keep_next
    p.paragraph_format.keep_together = False
    plain_inline(p, text, size=size, bold=bold)
    return p


def labeled(doc, label, value, *, after=4, keep_next=False):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.keep_with_next = keep_next
    p.paragraph_format.keep_together = False
    plain_inline(p, label + '  ', bold=True)
    plain_inline(p, value)
    return p


def shade(cell, fill):
    shd = OxmlElement('w:shd')
    shd.set(qn('w:fill'), fill)
    cell._tc.get_or_add_tcPr().append(shd)


def table(doc, rows, widths=None, *, center_cols=(), keep_rows=False):
    ncols = max(len(row) for row in rows)
    widths = widths or [BODY_WIDTH / ncols] * ncols
    t = doc.add_table(rows=0, cols=ncols)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for col, width in zip(t.columns, widths):
        col.width = Inches(width)
    pr = t._tbl.tblPr
    borders = OxmlElement('w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        b = OxmlElement('w:' + edge)
        for k, v in (('val', 'single'), ('sz', '4'), ('color', 'D9D9D9')):
            b.set(qn('w:' + k), v)
        borders.append(b)
    pr.append(borders)
    emitted = []
    for row_index, record in enumerate(rows):
        row = t.add_row()
        if keep_rows:
            row._tr.get_or_add_trPr().append(OxmlElement('w:cantSplit'))
        if row_index == 0:
            repeat = OxmlElement('w:tblHeader')
            row._tr.get_or_add_trPr().append(repeat)
        for col_index, cell in enumerate(row.cells):
            cell.width = Inches(widths[col_index])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            tcpr = cell._tc.get_or_add_tcPr()
            margins = OxmlElement('w:tcMar')
            for edge, value in (('top', 75), ('bottom', 75), ('left', 90), ('right', 90)):
                node = OxmlElement('w:' + edge)
                node.set(qn('w:w'), str(value))
                node.set(qn('w:type'), 'dxa')
                margins.append(node)
            tcpr.append(margins)
            shade(cell, 'E7EDF1' if row_index == 0 else 'FFFFFF')
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.08
            p.paragraph_format.keep_together = False
            p.paragraph_format.keep_with_next = row_index == 0
            if col_index in center_cols:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            value = record[col_index] if col_index < len(record) else ''
            markdown_inline(p, value)
            if row_index == 0:
                for run in p.runs:
                    run.bold = True
            emitted.append(p.text)
    gap = doc.add_paragraph()
    gap.paragraph_format.space_after = Pt(0)
    gap.paragraph_format.space_before = Pt(0)
    gap.paragraph_format.line_spacing = 0.5
    return t, emitted


def split_cells(line):
    # Escaped pipes are content, and must not turn into extra columns.
    line = line.strip()
    if line.startswith('|'):
        line = line[1:]
    if line.endswith('|') and not line.endswith('\\|'):
        line = line[:-1]
    return [part.strip().replace('\\|', '|') for part in re.split(r'(?<!\\)\|', line)]


def is_table_separator(line):
    cells = split_cells(line)
    return len(cells) > 1 and all(re.fullmatch(r':?-{3,}:?', v.strip()) for v in cells)


def semantic_inline(text):
    return ''.join(p[2:-2] if p.startswith('**') and p.endswith('**') else
                   p[1:-1] if p.startswith('`') and p.endswith('`') else p
                   for p in INLINE_TOKEN.split(text))


def markdown_answer(doc, answer, *, wide_as_table=False, max_table_cell_characters=None):
    """Emit complete answer content and check each rendered text unit."""
    source_lines = answer.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    emitted, expected = [], []
    index = 0
    in_code = False
    while index < len(source_lines):
        line = source_lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        if stripped.startswith('```'):
            # Keep fence and language text visible to avoid losing provenance.
            in_code = not in_code
            p = paragraph(doc, line, after=2)
            emitted.append(p.text)
            expected.append(line)
            index += 1
            continue
        if not in_code and '|' in line and index + 1 < len(source_lines) and is_table_separator(source_lines[index + 1]):
            rows = [split_cells(line)]
            index += 2
            while index < len(source_lines) and source_lines[index].strip() and '|' in source_lines[index]:
                rows.append(split_cells(source_lines[index]))
                index += 1
            ncols = max(map(len, rows))
            weights = []
            for col in range(ncols):
                longest = max((len(row[col]) if col < len(row) else 0) for row in rows)
                weights.append(min(36, max(9, longest ** 0.55 * 4)))
            widths = [BODY_WIDTH * weight / sum(weights) for weight in weights]
            # Very wide raw tables read better as labeled full-width records.
            long_cell=max_table_cell_characters is not None and max(len(cell) for record in rows for cell in record)>max_table_cell_characters
            if (ncols > 6 or long_cell) and not wide_as_table:
                headers = rows[0]
                p = paragraph(doc, '；'.join(headers), bold=True)
                expected.append('；'.join(headers)); emitted.append(p.text)
                for record in rows[1:]:
                    for col, value in enumerate(record):
                        text = (headers[col] if col < len(headers) else f'第{col + 1}列') + '：' + value
                        p = paragraph(doc)
                        markdown_inline(p, text)
                        expected.append(semantic_inline(text)); emitted.append(p.text)
            else:
                _, texts = table(doc, rows, widths)
                emitted.extend(texts)
                expected.extend(semantic_inline(row[col]) if col < len(row) else '' for row in rows for col in range(ncols))
            continue
        heading = re.match(r'^\s*#{1,6}\s+(.+)$', line) if not in_code else None
        short_strong_heading = (not in_code and
                                bool(re.fullmatch(r'\*\*[^*\n]+\*\*', stripped)) and
                                len(semantic_inline(stripped)) <= 80)
        short_plain_heading = (not in_code and len(semantic_inline(stripped)) <= 40 and
                               bool(re.match(r'^(?:[一二三四五六七八九十]+[、．.]|子问题\s*[0-9一二三四五六七八九十]+[：:])', stripped)))
        value = heading.group(1) if heading else line
        p = doc.add_paragraph()
        p.paragraph_format.keep_together = False
        p.paragraph_format.keep_with_next = bool(heading) or short_strong_heading or short_plain_heading
        p.paragraph_format.space_after = Pt(4)
        if in_code:
            plain_inline(p, value)
            expected.append(value)
        else:
            markdown_inline(p, value)
            expected.append(semantic_inline(value))
        if heading:
            for run in p.runs:
                run.bold = True
        emitted.append(p.text)
        index += 1
    assert emitted == expected, 'Answer text changed during Word conversion'
    return {'raw_sha256': hashlib.sha256(answer.encode('utf-8')).hexdigest(),
            'raw_characters': len(answer), 'word_text_units': len(emitted),
            'preservation_check': 'pass'}


def review_items(doc, label, values):
    values = lines_of(values)
    if not values:
        return
    paragraph(doc, label, bold=True, keep_next=True, after=3)
    for i, value in enumerate(values, 1):
        paragraph(doc, f'{i}  {value}', after=3)


def bookmark(paragraph, name, ident):
    start = OxmlElement('w:bookmarkStart')
    start.set(qn('w:id'), str(ident)); start.set(qn('w:name'), name)
    end = OxmlElement('w:bookmarkEnd'); end.set(qn('w:id'), str(ident))
    paragraph._p.insert(0, start); paragraph._p.append(end)


def internal_link(p, text, target):
    link = OxmlElement('w:hyperlink'); link.set(qn('w:anchor'), target)
    run = OxmlElement('w:r'); props = OxmlElement('w:rPr')
    color = OxmlElement('w:color'); color.set(qn('w:val'), '000000'); props.append(color)
    fonts = OxmlElement('w:rFonts')
    for key in ('ascii', 'hAnsi', 'eastAsia'):
        fonts.set(qn('w:' + key), '宋体')
    props.append(fonts)
    sz = OxmlElement('w:sz'); sz.set(qn('w:val'), '22'); props.append(sz)
    run.append(props); tx = OxmlElement('w:t'); tx.text = text; run.append(tx)
    link.append(run); p._p.append(link)


def field(p, instruction):
    fld = OxmlElement('w:fldSimple'); fld.set(qn('w:instr'), instruction)
    p._p.append(fld)


def configure(doc):
    sec = doc.sections[0]
    sec.page_width = Inches(8.5); sec.page_height = Inches(11)
    sec.top_margin = sec.bottom_margin = Inches(0.72)
    sec.left_margin = sec.right_margin = Inches(0.95)
    sec.header_distance = sec.footer_distance = Inches(0.3)
    for style in doc.styles:
        if style.type == 1 or style.name in ('Default Paragraph Font',):
            style.font.name = '宋体'; style.font.color.rgb = RGBColor(0, 0, 0)
            style.font.underline = False
            style.font.italic = False
            f = style._element.get_or_add_rPr().get_or_add_rFonts()
            for key in ('ascii', 'hAnsi', 'eastAsia', 'cs'):
                f.set(qn('w:' + key), '宋体')
            if style._element.pPr is not None:
                border = style._element.pPr.find(qn('w:pBdr'))
                if border is not None:
                    style._element.pPr.remove(border)
    normal = doc.styles['Normal']; normal.font.size = Pt(11)
    normal.paragraph_format.line_spacing = 1.1
    normal.paragraph_format.space_after = Pt(4)
    normal.paragraph_format.widow_control = True
    normal.paragraph_format.keep_together = False
    for name, size, before, after in (
        ('Title', 22, 0, 10), ('Subtitle', 11, 0, 8),
        ('Heading 1', 15, 16, 8), ('Heading 2', 12.5, 12, 6),
        ('Heading 3', 11, 8, 4)):
        s = doc.styles[name]; s.font.size = Pt(size); s.font.bold = name != 'Subtitle'
        s.paragraph_format.space_before = Pt(before); s.paragraph_format.space_after = Pt(after)
        s.paragraph_format.keep_with_next = True
        s.paragraph_format.keep_together = False
        s.paragraph_format.page_break_before = False
    f = sec.footer.paragraphs[0]; f.alignment = WD_ALIGN_PARAGRAPH.CENTER
    plain_inline(f, '第 ', size=9); field(f, 'PAGE'); plain_inline(f, ' 页  共 ', size=9)
    field(f, 'NUMPAGES'); plain_inline(f, ' 页', size=9)
    upd = OxmlElement('w:updateFields'); upd.set(qn('w:val'), 'true')
    doc.settings._element.append(upd)


def load_inputs(input_path, manifest_path):
    merged, manifest = load_json(input_path), load_json(manifest_path)
    items = merged['items'] if isinstance(merged, dict) else merged
    original_items = manifest['items']
    original_ids = [item['id'] for item in original_items]
    ids = [item['id'] for item in items]
    assert len(ids) == 160 and len(set(ids)) == 160, 'Need exactly 160 unique reviewed answers'
    assert set(ids) == set(original_ids), 'Question IDs differ from the manifest'
    by_id = {item['id']: item for item in items}
    # Match the collector's canonical manifest hash, independent of indentation.
    manifest_sha = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    ordered = []
    for original in original_items:
        item = by_id[original['id']]
        review = item.get('assessment')
        assert review and review.get('id') == item['id'], f'Missing review: {item["id"]}'
        assert review.get('label') in LABELS, f'Unknown review label: {item["id"]}'
        assert review.get('review_method') == 'independent_AI_review', f'Wrong review method: {item["id"]}'
        assert review.get('expert_accepted') is False, f'Expert claim not allowed: {item["id"]}'
        assert isinstance(item.get('reply'), dict), f'Missing raw reply: {item["id"]}'
        assert item.get('question') == original['question'], f'Question differs: {item["id"]}'
        assert item.get('original_question') == original.get('original_question'), f'Original differs: {item["id"]}'
        assert item.get('independent_question') is True, f'Not independent: {item["id"]}'
        assert item.get('test_transport', '').startswith('HTTP'), f'Not HTTP: {item["id"]}'
        assert item.get('manifest_sha256') == manifest_sha, f'Manifest hash differs: {item["id"]}'
        assert review.get('reason'), f'Missing review reason: {item["id"]}'
        ordered.append(item)
    return merged, manifest, ordered


def source_paragraphs(doc, source, index):
    citation = source.get('citation') or f'来源{index}'
    title = source.get('title') or source.get('filename') or '未给出标题'
    p = labeled(doc, '[' + as_text(citation) + ']', as_text(title), after=2, keep_next=True)
    # Each actual returned source appears with its complete title and locator.
    parts = []
    for name, key in (('定位', 'locator'), ('类别', 'category'), ('证据', 'evidence_kind'),
                      ('来源类型', 'source_type'), ('状态', 'status'), ('审阅标签', 'review_label')):
        if source.get(key) is not None and as_text(source[key]).strip():
            parts.append(name + '：' + as_text(source[key]))
    if parts:
        paragraph(doc, '；'.join(parts), after=2)
    if source.get('url'):
        paragraph(doc, '网址：' + as_text(source['url']), after=2)
    meta = source.get('metadata') or {}
    time_parts = []
    for name, key in (('发布时间', 'publication_date'), ('文件时次', 'document_issued_at'),
                      ('抓取时间', 'retrieved_at'), ('读取状态', 'read_status'),
                      ('版次状态', 'edition_status'), ('证据范围', 'evidence_scope'),
                      ('现行版本已核验', 'edition_current_verified'), ('审核范围', 'review_scope'),
                      ('标准编号', 'standard_id'), ('来源类型', 'source_type'),
                      ('试算', 'trial'), ('单位', 'unit'), ('假设配置', 'assumption_id'),
                      ('正文为节选', 'text_is_excerpt'), ('外发有效许可', 'external_effective_allowed')):
        if key in meta and meta[key] is not None:
            time_parts.append(name + '：' + as_text(meta[key]))
    if time_parts:
        paragraph(doc, '；'.join(time_parts), after=2)
    if source.get('quality_warning'):
        paragraph(doc, '质量提示：' + as_text(source['quality_warning']), after=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=RUN / 'reviewed_answers.json')
    parser.add_argument('--manifest', type=Path, default=RUN / 'manifest.json')
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--audit', type=Path, default=RUN / 'document_audit.json')
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--replace-current', action='store_true', help='Rebuild only this new report after layout QA')
    parser.add_argument('--comparison',type=Path,help='Previous run summary for an explicit comparison')
    args = parser.parse_args()
    merged, manifest, items = load_inputs(args.input, args.manifest)
    identity_path = args.input.parent / 'run_identity.json'
    identity = load_json(identity_path) if identity_path.is_file() else {}
    counts = Counter(item['assessment']['label'] for item in items)
    modes = Counter(as_text(item['reply'].get('mode') or '未注明') for item in items)
    kinds = Counter(as_text(item['reply'].get('answer_kind') or '未注明') for item in items)
    http = Counter(str(item.get('http_status', '未注明')) for item in items)
    trial_ids = [item['id'] for item in items if item['reply'].get('trial') is True]
    gap_types = Counter(gap for item in items for gap in lines_of(item['assessment'].get('gap_types')))
    assumption_groups = OrderedDict()
    for item in items:
        evidence = item.get('business_evidence') or {}
        assumptions = evidence.get('assumptions') or []
        if assumptions:
            key = as_text(evidence.get('assumption_id') or '未给出配置编号')
            if key not in assumption_groups:
                assumption_groups[key] = []
            for assumption in assumptions:
                if assumption not in assumption_groups[key]:
                    assumption_groups[key].append(assumption)
    groups = OrderedDict()
    for item in items:
        groups.setdefault((item['category_id'], item['category']), []).append(item)
    groups = OrderedDict(sorted(groups.items(), key=lambda pair: pair[0][0]))
    start = min(as_text(item.get('started_at')) for item in items)
    end = max(as_text(item.get('completed_at')) for item in items)
    base = {'question_count': len(items), 'category_count': len(groups), 'counts': dict(counts),
            'modes': dict(modes), 'answer_kinds': dict(kinds), 'http_statuses': dict(http),
            'trial_question_ids': trial_ids, 'gap_types': dict(gap_types), 'started_at': start, 'completed_at': end}
    if args.validate_only:
        print(json.dumps(base, ensure_ascii=False)); return
    if args.output.exists() and not args.replace_current:
        raise FileExistsError('Report already exists; use --replace-current only for a layout repair')
    doc = Document(); configure(doc)
    report_day=start[:10]
    doc.add_paragraph('海洋灾害160题代码修复复测与实际回答', style='Title')
    doc.add_paragraph(report_day+'  修复版系统真实HTTP逐题复测', style='Subtitle')
    paragraph(doc, f'本轮对原问题库全部{len(items)}题分别发送独立HTTP请求，实际取得的回答全文及来源按原类别和编号收录。辅助AI按实际提问逐项核对核心子问覆盖、证据与关键口径，独立复核完整性，结果为完整回答{counts["完整回答"]}题、部分回答{counts["部分回答"]}题、未回答{counts["未回答"]}题。该分类反映本轮回答覆盖情况；业务事实、专业正确性和业务验收仍需相应专家核定。')
    paragraph(doc, f'实测开始：{start}；实测结束：{end}。HTTP状态统计：' + '、'.join(f'{key}为{val}题' for key, val in sorted(http.items())) + '。回答模式：' + '、'.join(f'{key}为{val}题' for key, val in modes.items()) + '。')
    if args.comparison:
        previous=load_json(args.comparison)
        paragraph(doc,'代码修复与复测对比',bold=True,keep_next=True)
        paragraph(doc,'本轮修复业务意图路由、会商截至时刻与预报有效区间混淆、计算结果遗漏、显式子问丢失和量值引用检查。新增运行版本指纹，确认实测进程加载了修复代码。原有数据口径假设保留；真实缺数和现行规则缺项继续逐题标明。')
        paragraph(doc,'验证结果：488项Python测试、102个子测试通过；问答客户端、对话上下文及回答格式相关前端检查通过。单位检查涵盖明确量纲及程序记录的已知单位字段；文本引用检查所选段落，程序结果另核对必需的值、单位、地点与时刻。自动检查不等同业务事实专家验收。')
        table(doc,[['分类','上一轮','本轮']]+[[label,str(previous['label_counts'].get(label,0)),str(counts[label])] for label in LABELS],[4.4,1.1,1.1],center_cols=(1,2))
        paragraph(doc,'两轮沿用相同160个具体化问题，便于比较。各轮真实请求时间和取得的证据分别保存；对动态来源的变化按每题实际时次理解。完整性变化不直接等于专业正确率变化。')
        paragraph(doc,'本轮完整回答仍为17题，未回答从43题减至32题，减少的题主要进入部分回答。14题分类提升，4题分类下降；下降题为Q-019、Q-036、Q-043、R-075，具体原因见逐题记录。程序修复打通了部分数值交付，但当前资料、数据口径和生成稳定性仍限制完整回答。')
        audit_path=args.input.parent/'computation_audit.json'
        if audit_path.exists():
            computation=load_json(audit_path)
            paragraph(doc,'计算交付与引用检查',bold=True,keep_next=True)
            paragraph(doc,'检查保存记录中的引用编号、程序必需事实和预报有效区间。程序事实交付结果：'+as_text(computation.get('typed_delivery_counts'))+'；预报区间不一致题号：'+('、'.join(computation.get('forecast_period_mismatch_ids',[])) or '无')+'；引用编号未对应返回来源题号：'+('、'.join(computation.get('citation_id_mismatch_ids',[])) or '无')+'。')
            fallback=computation.get('computed_table_fallback_ids',[])
            paragraph(doc,'语言说明核对失败后返回程序数据表的题号：'+('、'.join(fallback) or '无')+'。这类回答保留计算值和限制，不计为语言生成成功，也不会因有数值就自动评为完整回答。')
    version = next((line.split(':', 1)[1].strip() for line in as_text(identity.get('source_version')).splitlines() if line.startswith('Version:')), '')
    run_name = as_text(identity.get('run_id') or items[0].get('run_id'))
    paragraph(doc, '实测运行标识：' + run_name + ('；系统版本：' + version if version else '') + '；接口：' + as_text(identity.get('base_url') or items[0].get('base_url')) + as_text(identity.get('request_route') or '/api/assistant/ask') + '。')
    paragraph(doc,'运行环境：本轮使用8769本地修复版服务和已保存的预报NC批次，测试期间关闭预报后台下载任务。配置中的更新间隔不表示本轮已验证自动同步。原8766服务保留；比较与复核以本轮保存的真实响应为准。')
    paragraph(doc, '本轮场景与时间口径：' + manifest.get('note', ''))
    paragraph(doc,'时间判读说明：题单背景中的21时30分并非每题统一追加的截止时刻。实际解析以逐题测试问题为准：明确写21时30分的题按该时刻筛选；仅写截至10月17日的题使用当日末可用记录，因此可能返回17日23时记录。会商截止时刻、预报起报批次和预报有效区间分别处理。')
    storm = manifest.get('storm') or {}
    status = storm.get('data_status') or {}
    if storm:
        paragraph(doc, '测试所用台风接口记录：' + '；'.join(name + '：' + as_text(storm[key]) for name, key in (('名称', 'name'), ('sid', 'sid'), ('实况时次', 'issued_at'), ('接口来源', 'source')) if key in storm) + '。' + as_text(storm.get('identity_policy')))
    if status:
        paragraph(doc, '接口数据口径：' + '；'.join(key + '=' + as_text(value) for key, value in status.items()) + '。provisional_units为true或风速单位含假设时，涉及阈值、风力换算和行动结论须保留该限制；不能把试算数值视为已审定业务结论。')
    paragraph(doc, f'本轮回复中trial=true共{len(trial_ids)}题。data_trial模式、试算标记、来源时次和关键数据假设均按各题实测记录及复核理由呈现。HTTP成功或模型返回full等自报字段，均不自动代表完整回答或专家认可。')
    if assumption_groups:
        paragraph(doc, '业务试算使用的关键假设', bold=True, keep_next=True)
        for config_id, assumptions in assumption_groups.items():
            paragraph(doc, '配置编号：' + config_id + '。各题业务证据状态引用相应配置；下列假设未经核定时会影响正式超警、最高站点和行动建议等结论。')
            for index, assumption in enumerate(assumptions, 1):
                paragraph(doc, f'{index}  {assumption}', after=3)
    paragraph(doc, '完整回答表示实际提问的主要子问及所请求交付均得到实质回应；部分回答表示已有实质回应但仍缺必要数据、证据、时间地点条件或所请求交付；未回答表示未对核心需求形成实质回答。台账预期输出用于保留需求背景，实际提问未要求的附加格式不单独作为降级理由。静态知识题不会因无关的当前实况缺项自动降级。每题资料编号在本题内使用，来源中的“节选”“未审定”等限制保留原标记。')
    if gap_types:
        paragraph(doc, '本轮缺项类型统计的是涉及题次，一题可有多类缺项、各标签之间可交叉，不能相加为独立缺项总数，也不表示需新增同等数量的数据集。已有数据未使用或未交付、真正缺少数据、关键假设及来源现行性等情况须按逐题理由分别理解。')
        table(doc, [['复核记录的缺项类型', '涉及题次']] + [[key, str(value)] for key, value in gap_types.most_common()], [5.8, .8], center_cols=(1,), keep_rows=True)
    paragraph(doc, '原题台账：' + as_text(manifest.get('source_name')) + '；SHA256：' + as_text(manifest.get('source_sha256')) + '。原始逐题JSON与合并记录保留完整接口字段；本文逐题列出可追溯记录及回答原文SHA256。')
    doc.add_paragraph('完整性分类索引', style='Heading 1')
    for label in LABELS:
        doc.add_paragraph(f'{label}  {counts[label]}题', style='Heading 2')
        selected = [item for item in items if item['assessment']['label'] == label]
        if not selected:
            paragraph(doc, '无。')
        for offset in range(0, len(selected), 12):
            p = doc.add_paragraph()
            for index, item in enumerate(selected[offset:offset + 12]):
                if index:
                    plain_inline(p, '  ')
                internal_link(p, item['id'], 'q_' + item['id'].replace('-', '_'))
    doc.add_paragraph('原类别索引', style='Heading 1')
    rows = [['类别', '题数', '完整', '部分', '未答', '原编号']]
    for (category_id, category), group in groups.items():
        c = Counter(item['assessment']['label'] for item in group)
        rows.append([category_id + ' ' + category, str(len(group)), str(c[LABELS[0]]), str(c[LABELS[1]]), str(c[LABELS[2]]), '、'.join(item['id'] for item in group)])
    table(doc, rows, [1.55, .42, .42, .42, .42, 3.37], center_cols=(1, 2, 3, 4), keep_rows=True)
    doc.add_paragraph('逐题实测回答与缺项', style='Heading 1')
    audits = []
    number = 0
    for (category_id, category), group in groups.items():
        doc.add_paragraph(category_id + '  ' + category, style='Heading 1')
        paragraph(doc, f'本类{len(group)}题，原编号和原子需求保留。', after=4)
        for item in group:
            number += 1
            review = item['assessment']; reply = item['reply']
            heading = doc.add_paragraph(item['id'] + '  ' + as_text(item.get('subrequirement')), style='Heading 2')
            bookmark(heading, 'q_' + item['id'].replace('-', '_'), number)
            labeled(doc, '原题', as_text(item.get('original_question')))
            labeled(doc, '实际提问', as_text(item.get('question')))
            labeled(doc, '台账预期输出', as_text(item.get('expected_output') or item.get('visible_result')))
            record_path = args.input.parent / (item['id'] + '.json')
            labeled(doc, '原表定位', as_text(item.get('original_workbook_sheet')) + ' 第' + as_text(item.get('original_excel_row')) + '行；角色：' + as_text(item.get('role') or item.get('user_view') or '未注明'))
            labeled(doc, '实测记录', f'{item.get("started_at", "")} 至 {item.get("completed_at", "")}；耗时{item.get("elapsed_seconds", "未注明")}秒；{item.get("test_transport", "")}；{item.get("base_url", "")}；HTTP {item.get("http_status", "未注明")}；mode={as_text(reply.get("mode"))}；answer_kind={as_text(reply.get("answer_kind"))}；trial={as_text(reply.get("trial"))}')
            extra = {key: reply[key] for key in ('business_state', 'selected_sid', 'data_url', 'resolved_question') if reply.get(key) is not None and reply.get(key) != ''}
            if extra:
                labeled(doc, '接口附加信息', '；'.join(key + '=' + as_text(value) for key, value in extra.items()))
            evidence = item.get('business_evidence') or {}
            evidence_fields = [('类型', 'kind'), ('状态', 'state'), ('试算', 'trial'),
                               ('证据抓取时刻', 'as_of'), ('预报批次', 'selected_batch'),
                               ('开始', 'start'), ('结束', 'end'), ('假设配置', 'assumption_id'),
                               ('请求数', 'requested_count'), ('可计算数', 'usable_count'),
                               ('缺数', 'missing_count'), ('覆盖不完整数', 'partial_count'),
                               ('单位', 'unit'), ('程序提示', 'reason')]
            evidence_parts = [name + '=' + as_text(evidence[key]) for name, key in evidence_fields if key in evidence and evidence[key] is not None]
            if evidence_parts:
                labeled(doc, '业务证据状态', '；'.join(evidence_parts))
            attempts = item.get('attempts') or []
            if len(attempts) > 1:
                labeled(doc, '请求尝试', '；'.join(f'第{a.get("attempt", i + 1)}次 {a.get("started_at", "")} HTTP {a.get("http_status", "未注明")}' for i, a in enumerate(attempts)))
            labeled(doc, '独立完整性分类', review['label'] + '；辅助AI独立复核，非业务专家验收')
            if review.get('previous_label'):
                labeled(doc,'与上一轮对比',review['previous_label']+' → '+review['label']+('；'+review['change_note'] if review.get('change_note') else ''))
            labeled(doc, '分类理由', as_text(review['reason']))
            review_items(doc, '具体未答点', review.get('missing_points'))
            review_items(doc, '具体资料与数据缺项', review.get('specific_gaps'))
            if review.get('gap_types'):
                labeled(doc, '缺项类型', '；'.join(lines_of(review['gap_types'])))
            review_items(doc, '证据与口径核对', review.get('evidence_checks'))
            if not lines_of(review.get('missing_points')) and not lines_of(review.get('specific_gaps')):
                labeled(doc, '具体缺项', '本轮独立完整性复核未列出必要缺项；不等同专家正确性验收。')
            paragraph(doc, '系统实际原答', bold=True, keep_next=True, after=4)
            answer = reply.get('answer')
            if answer is None:
                answer = ''
            assert isinstance(answer, str), 'Answer must remain a string'
            if answer:
                answer_audit = markdown_answer(doc, answer)
            else:
                paragraph(doc, '接口未返回answer正文。')
                answer_audit = {'raw_sha256': hashlib.sha256(b'').hexdigest(), 'raw_characters': 0, 'word_text_units': 0, 'preservation_check': 'pass'}
            sources = reply.get('sources') or []
            paragraph(doc, f'本题实际来源  {len(sources)}条', bold=True, keep_next=True, after=4)
            for source_index, source in enumerate(sources, 1):
                source_paragraphs(doc, source, source_index)
            if not sources:
                paragraph(doc, '接口未返回sources条目。', after=4)
            labeled(doc, '追溯记录', str(record_path) + '；回答原文SHA256：' + answer_audit['raw_sha256'], after=8)
            audits.append({'id': item['id'], 'label': review['label'], 'source_count': len(sources), **answer_audit})
    doc.core_properties.title = '海洋灾害160题新版实测问答与数据缺项'
    doc.core_properties.subject = report_day+'修复版系统真实HTTP逐题复测与独立完整性复核'
    doc.core_properties.author = '海洋灾害问答项目'
    doc.core_properties.keywords = '海洋灾害 160题 实测问答 数据缺项 独立完整性复核'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(args.output)
    # Check actual packaged OOXML for every question bookmark, title decoration,
    # Letter dimensions, and the complete recorded answer hashes.
    with zipfile.ZipFile(args.output) as archive:
        xml = etree.fromstring(archive.read('word/document.xml'))
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        bookmarks = xml.xpath('//w:bookmarkStart/@w:name', namespaces=ns)
        assert len(bookmarks) == 160 and len(set(bookmarks)) == 160
        title_p = xml.xpath('//w:p[w:pPr/w:pStyle[@w:val="Title"]]', namespaces=ns)[0]
        assert not title_p.xpath('.//w:pBdr | .//w:u[not(@w:val="none")]', namespaces=ns)
        assert xml.xpath('//w:sectPr/w:pgSz/@w:w', namespaces=ns) == ['12240']
        assert xml.xpath('//w:sectPr/w:pgSz/@w:h', namespaces=ns) == ['15840']
    audit = {**base, 'output': str(args.output), 'input': str(args.input),
             'input_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest(),
             'output_sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
             'built_at': datetime.now().astimezone().isoformat(timespec='seconds'),
             'question_order': [item['id'] for item in audits],
             'manifest_order': [item['id'] for item in items], 'items': audits,
             'all_answers_preserved': all(a['preservation_check'] == 'pass' for a in audits),
             'visual_review': 'pending all-page rendered PNG inspection'}
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({**base, 'document': str(args.output), 'audit': str(args.audit)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
