"""Summarize saved answer delivery, exact answer spans and stated gaps.

This never generates answers or treats citations/model labels as expert truth.
Gap labels describe the saved wording; they are not independently verified causes.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from marine_kb.quantities import quantities,table_quantities

GAP_PATTERNS={
    'answer_omission_or_generation_validation':r'正文未|回答未|未交付|未输出|未列出|未给出.{0,12}(?:具体|数值|阈值|差值)|生成.{0,12}(?:失败|未通过)|引用.{0,12}(?:失败|未通过)|未通过.{0,8}(?:核对|核验|校验)|原生成内容|语言说明',
    'official_notice_opening_or_action_status':r'公告|公报发布|停航|复航|停复|开放|关闭|复开|救生|营业|启动.{0,8}响应|终止.{0,8}响应|正式.{0,8}(?:发布|预警|措施)',
    'rules_thresholds_or_document_validity':r'现行|有效版本|版本状态|生效|废止|修订|准入|阈值|适用规则|预案|标准|规范|法规|判据',
    'units_datum_mapping_and_comparability':r'单位|量纲|基准|映射|同站|同口径|可比|口径|场景|归属|平均风|时距|统计范围|采样',
    'missing_model_site_assets_or_observations':r'淹没|地形|地面|高程|海堤|堤防|闸泵|排水|房屋|人员台账|人口台账|转移人数|本地.{0,12}(?:模型|评分|验证|检验)|误差|观测|实测|配对样本|集合预报|地表|水质',
    'missing_local_dated_data_or_archives':r'缺少|缺失|缺数据|缺数|缺测|未提供|未取得|未保存|未接入|没有.{0,12}(?:数据|记录|资料)|尚无|历史记录|实时|当天|对应日期|完整列表|具体.{0,6}(?:时刻|时间|地点|站点)',
    'retrieval_or_source_access':r'联网|检索|搜索|读取失败|无法读取|验证码|验证页面|访问限制|访问规则|正文提取|无法访问',
}


def _strings(value):
    if isinstance(value,str):return [value] if value.strip() else []
    if isinstance(value,list):return [item for item in value if isinstance(item,str) and item.strip()]
    return []


def _nested_gaps(evidence):
    if not isinstance(evidence,dict):return []
    gaps=_strings(evidence.get('missing_information'))
    if evidence.get('state') in {'partial','no_data','needs_clarification','needs_location','unavailable','not_executed'}:
        gaps+=_strings(evidence.get('reason'))
    for item in evidence.get('items',[]) or []:gaps+=_nested_gaps(item)
    return gaps


def summarize_record(record):
    reply=record.get('reply') or ((record.get('attempts') or [{}])[-1].get('reply')) or {}
    answer=str(reply.get('answer') or '')
    diagnostics=reply.get('evidence_diagnostics') or {}
    sources=reply.get('sources') or []
    returned={source.get('citation_id') for source in sources}
    cited={int(n) for n in re.findall(r'\[资料(\d+)\]',answer)}
    parts=[]
    for part in reply.get('subquestion_coverage') or []:
        if not isinstance(part,dict):continue
        spans=part.get('answer_spans');recorded=isinstance(spans,list)
        actual=spans if recorded else []
        invalid=[i+1 for i,span in enumerate(actual) if not isinstance(span,str) or not span or span not in answer]
        refs={n for n in part.get('citations',[]) if type(n) is int}
        span_refs={int(n) for span in actual if isinstance(span,str) for n in re.findall(r'\[资料(\d+)\]',span)}
        parts.append({'id':part.get('id'),'question':part.get('question'),'model_status':part.get('status'),
                      'citations':sorted(refs),'missing_information':_strings(part.get('missing_information')),
                      'answer_spans_recorded':recorded,'answer_spans':actual,
                      'invalid_span_numbers':invalid,'citations_missing_from_returned_sources':sorted(refs-returned),
                      'citations_missing_from_spans':sorted(refs-span_refs) if recorded else None,
                      'span_integrity':'not_recorded' if not recorded else 'pass' if not invalid and refs<=span_refs and refs<=returned else 'fail'})
    missing=list(dict.fromkeys(_strings(reply.get('missing_information'))+
        [gap for p in parts for gap in p['missing_information']]+_nested_gaps(record.get('business_evidence'))))
    gaps=[{'text':text,'wording_labels':[name for name,pattern in GAP_PATTERNS.items() if re.search(pattern,text)] or ['unclassified']}
          for text in missing]
    recovery=diagnostics.get('claim_attribution_recovery') or {}
    decisions=recovery.get('decisions') or []
    table={key:recovery.get(key) for key in ('table_rows_seen','table_rows_considered','table_rows_skipped')}
    table['row_counts_recorded']=all(type(table[key]) is int for key in ('table_rows_seen','table_rows_considered','table_rows_skipped'))
    review=diagnostics.get('recovered_completeness_review') or reply.get('recovered_completeness_review')
    row={'id':record.get('id'),'question':record.get('question'),'category':record.get('category'),
         'http_status':record.get('http_status'),'mode':reply.get('mode'),'model_answerability':reply.get('answerability'),
         'business_kind':(record.get('business_evidence') or {}).get('kind'),'business_state':reply.get('business_state'),
         'answer_sha256':hashlib.sha256(answer.encode()).hexdigest(),'answer_length':len(answer),
         'returned_source_count':len(sources),'actually_cited_source_count':len(cited & returned),
         'missing_returned_citation_ids':sorted(cited-returned),
         'explicit_quantity_occurrences':len(quantities(answer)),
         'typed_table_endpoint_occurrences':len(table_quantities(answer)),
         'parts':parts,'part_model_status_counts':dict(Counter(part['model_status'] for part in parts)),
         'completeness_review':review or {'state':'not_recorded'},
         'full_parts_without_recorded_answer_spans':[part['id'] for part in parts if part['model_status']=='full' and not part['answer_spans_recorded']],
         'span_integrity_failure_parts':[part['id'] for part in parts if part['span_integrity']=='fail'],
         'validation':reply.get('validation'),
         'attribution_recovery':{'recorded':bool(recovery),'claim_count':recovery.get('claim_count'),
             'retained_claims':recovery.get('retained_claims'),'decision_counts':dict(Counter(d.get('status') for d in decisions)),**table},
         'gap_wording_labels':sorted({label for gap in gaps for label in gap['wording_labels']}),'gaps':gaps,
         'web_body_read_failure_count':len(diagnostics.get('web_body_read_failures') or []),
         'expert_accepted':False}
    return row


def summarize(path):
    source=json.loads(path.read_text(encoding='utf-8'))
    records=source if isinstance(source,list) else source.get('items')
    if not isinstance(records,list):raise ValueError('Expected a list or an answers.json object with items')
    rows=[summarize_record(record) for record in records]
    allparts=[part for row in rows for part in row['parts']]
    return {'note':'模型状态和引用数量均不是专家正确率；answer_spans仅核对是否为实际回答原文及其引用，不证明语义正确。缺口标签按保存的措辞归纳，可重叠，不自动断言实际根因。旧记录未保存answer_spans或表行计数时标为未记录，不补写结果。',
            'expert_accepted':False,'source_file':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'question_count':len(rows),'model_answerability_counts':dict(Counter(row['model_answerability'] for row in rows)),
            'part_model_status_counts':dict(Counter(part['model_status'] for part in allparts)),
            'part_span_integrity_counts':dict(Counter(part['span_integrity'] for part in allparts)),
            'completeness_review_state_counts':dict(Counter((row['completeness_review'] or {}).get('state','unspecified') for row in rows)),
            'business_kind_counts':dict(Counter(row['business_kind'] or 'none' for row in rows)),
            'gap_type_question_counts':dict(Counter(label for row in rows for label in row['gap_wording_labels'])),
            'no_stated_gap_ids':[row['id'] for row in rows if not row['gaps']],
            'no_cited_returned_source_ids':[row['id'] for row in rows if not row['actually_cited_source_count']],
            'span_integrity_failure_ids':[row['id'] for row in rows if row['span_integrity_failure_parts']],
            'table_recovery':{'attribution_recovery_question_count':sum(row['attribution_recovery']['recorded'] for row in rows),
                'row_count_recorded_questions':sum(row['attribution_recovery']['row_counts_recorded'] for row in rows),
                'actual_table_row_questions':sum(bool(row['attribution_recovery'].get('table_rows_seen')) for row in rows),
                'recovery_questions_without_row_counts':sum(row['attribution_recovery']['recorded'] and not row['attribution_recovery']['row_counts_recorded'] for row in rows),
                **{key:sum(row['attribution_recovery'][key] or 0 for row in rows) for key in ('table_rows_seen','table_rows_considered','table_rows_skipped')},
                'skipped_row_ids':[row['id'] for row in rows if row['attribution_recovery'].get('table_rows_skipped')]},
            'items':rows}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--answers',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();path=args.answers.resolve()
    result=summarize(path);output=args.output.resolve() if args.output else path.parent/'complete_answer_summary.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='items'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
