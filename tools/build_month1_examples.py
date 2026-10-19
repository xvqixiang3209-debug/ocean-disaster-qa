"""Export 40 evidence-review candidates, not approved training answers."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path

APP = Path(__file__).resolve().parents[1]


def build(report_path, catalog_path, output, count=40):
    raw = report_path.read_bytes()
    report = json.loads(raw)
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    # Round robin: every candidate question is represented before adding a
    # second source for it. A question/source pair is an example, not a new
    # independent question. All fragments remain pending human review.
    cases = report['candidates']
    pairs = [(case, source) for index in range(max(len(case['sources']) for case in cases))
             for case in cases for source in case['sources'][index:index+1]][:count]
    if len(pairs) != count:
        raise ValueError('可追溯证据不足，不生成虚构样例')
    examples = [{
        'id': f'M1-EX-{index:03d}', 'development_case_id': case['id'],
        'requirement_ids': case['requirement_ids'], 'question': case['question'],
        'candidate_evidence_excerpt': source.get('excerpt'),
        'source_id': source['source_id'], 'chunk_id': source['chunk_id'],
        'source_title': source['title'], 'locator': source['locator'],
        'source_review_status': source['status'], 'quality_warning': source.get('quality_warning'),
        'partition': 'development_review', 'review_status': 'pending_expert_review',
        'expert_reference_answer': None, 'reviewer': None, 'training_allowed': False,
        'independent_acceptance_eligible': False,
    } for index, (case, source) in enumerate(pairs, 1)]
    development_ids = {qid for case in report['candidates'] + report['boundaries'] for qid in case['requirement_ids']}
    payload = {
        'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'report_sha256': hashlib.sha256(raw).hexdigest(), 'example_count': len(examples),
        'candidate_question_count': len({row['development_case_id'] for row in examples}),
        'expert_approved_count': 0, 'model_training_performed': False,
        'note': '40条是问题—证据片段审核候选，覆盖20个开发问题；不是40个独立问题或已审核训练问答。原题备注不作事实答案。',
        'split_rule': '开发问题及同族改写不得作为独立终验题；其余原题先保留未分配，由专家按角色/主题及问题族核定，不能仅按编号随机拆分。',
        'requirement_groups': [{'id': item['id'], 'role': item['role'], 'category_id': item['category_id'],
            'partition': 'development_touched' if item['id'] in development_ids else 'reserved_unassigned',
            'independent_family_review_required': True} for item in catalog['items']],
        'examples': examples,
    }
    if output.exists():
        previous = json.loads(output.read_text(encoding='utf-8'))
        if any(row.get('expert_reference_answer') or row.get('reviewer') or row.get('review_status') != 'pending_expert_review'
               for row in previous.get('examples', [])):
            raise ValueError('已有人工标注；请选择新输出路径，不能覆盖人工工作')
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({key: payload[key] for key in ('example_count', 'candidate_question_count', 'expert_approved_count', 'model_training_performed')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=APP / 'docs/month1_acceptance_results.json')
    parser.add_argument('--catalog', type=Path, default=APP / 'data/requirements/catalog.json')
    parser.add_argument('--output', type=Path, default=APP / 'docs/month1_development_examples.json')
    args = parser.parse_args()
    build(args.report, args.catalog, args.output)
