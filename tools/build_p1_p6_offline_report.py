"""Read private P0 recordings and report deterministic P1–P6 regressions.

This tool never calls a model, reader, network API or running service. Old
judge outputs are preserved as old opinions, never converted to typed-v2
judgments. Structural fixtures and their tests are reported separately.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import sys


APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from marine_kb.facet_contract import build_contract
from marine_kb.model_json import model_json
from marine_kb.replay import _unwire
from marine_kb.scope_gate import gate_answer
from marine_kb.typed_grounding import prepare_draft


DEFAULT_RECORDS = APP / 'tmp/replay/p0_20261020_005001_b167cdca'


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _models(path):
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        if row.get('record_type') != 'interaction' or row.get('kind') != 'model':
            continue
        request = _unwire(row['request'])
        args = request.get('args', [])
        payload = None
        if len(args) >= 3:
            try:
                payload = json.loads(args[2])
            except (TypeError, ValueError):
                pass
        response = None
        if row.get('outcome') == 'return':
            raw = _unwire(row['response'])
            try:
                response = model_json(raw[0] if isinstance(raw, tuple) else raw)
            except (ValueError, TypeError):
                pass
        rows.append({'seq': row['seq'], 'outcome': row.get('outcome'), 'payload': payload,
                     'response': response, 'request_sha256': row.get('request_sha256'),
                     'response_sha256': row.get('response_sha256')})
    return rows


def _parts(payload):
    values = payload.get('subquestions') or []
    return values if isinstance(values, list) else []


def _relation(answer, facets, evidence):
    try:
        from marine_kb.relation_guard import check
        return {'available': True, 'issues': check(answer, facets, evidence)}
    except (ImportError, FileNotFoundError, ValueError, KeyError) as exc:
        return {'available': False, 'error': type(exc).__name__, 'issues': []}


def _old_passages(evidence):
    return {(int(e.get('citation') or i+1), int(p.get('id', p.get('passage_id')))): sha(str(p.get('text') or ''))
            for i,e in enumerate(evidence or []) for p in e.get('passages', [])
            if type(p.get('id', p.get('passage_id'))) is int}


def _canonical_repeatability(evidence):
    try:
        from marine_kb.evidence_units import evidence_view
        left = evidence_view(evidence)
        right = evidence_view(evidence)
        def canonical(rows):
            return [(r['citation'], p.get('id', p.get('passage_id')), p['sha256'])
                    for r in rows for p in r.get('passages', [])]
        a, b = canonical(left), canonical(right)
        valid = all(p['sha256'] == sha(p['text']) for r in left for p in r.get('passages', []))
        return {'available': True, 'same_input_same_ids_and_hashes': a == b, 'hashes_match_text': valid,
                'passage_count': len(a), 'scope': 'deterministic_repeatability_only_no_new_model_stage'}
    except (ImportError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return {'available': False, 'error': type(exc).__name__}


def build_case(result_path):
    recorded = json.loads(result_path.read_text(encoding='utf-8'))
    case = recorded.get('case') or {}
    case_id = case.get('case_id') or result_path.name.removesuffix('.result.json')
    question = case.get('question') or ''
    reply = recorded.get('reply') or {}
    answer = str(reply.get('answer') or '')
    cassette = result_path.with_name(case_id + '.cassette.jsonl')
    if not cassette.exists():
        raise FileNotFoundError('missing_private_cassette:' + case_id)
    models = _models(cassette)
    normal = next((r['response'] for r in models if isinstance(r.get('response'), dict) and 'subquestions' in r['response']), {})
    planner = normal.get('subquestions') or []
    if planner and isinstance(planner[0], str):
        planner = [{'id': i+1, 'question': text} for i, text in enumerate(planner)]
    contract = build_contract(question, planner)
    drafts = []
    old_reviews = []
    scope_results = []
    legacy_claim_reviews = []
    relation_results = []
    last_evidence = []
    last_generation_passages = {}
    same_ids = []
    canonical = {'available': False, 'error': 'no_generation_evidence'}
    for model in models:
        payload = model.get('payload') or {}
        value = model.get('response') or {}
        if not isinstance(value, dict):
            continue
        if isinstance(value.get('answer'), str) and value['answer']:
            view = prepare_draft(value, contract)
            exact = all(view['draft']['answer'][a['start']:a['end']] == a['text'] for a in view['atoms'])
            drafts.append({'seq': model['seq'], 'draft_sha256': view['draft_sha256'],
                           'raw_draft_sha256': view['raw_draft_sha256'],
                           'literal_newline_normalized': view['literal_newline_normalized'],
                           'atom_count': len(view['atoms']), 'all_atoms_exact_contiguous': exact,
                           'bound_atoms': sum(bool(a['facet_ids']) for a in view['atoms']),
                           'unresolved_atoms': sum(not a['facet_ids'] for a in view['atoms']),
                           'inherited_heading_atoms': sum(a['binding'] == 'inherited_heading' for a in view['atoms'])})
            last_evidence = payload.get('evidence') or last_evidence
            last_generation_passages = _old_passages(last_evidence)
            canonical = _canonical_repeatability(last_evidence)
            relation_results.append({'seq': model['seq'], 'draft_sha256': view['draft_sha256'],
                                     **_relation(view['draft']['answer'], contract, last_evidence)})
        if 'verdict' in value and isinstance(value.get('issues'), list):
            issues = value['issues']
            old_reviews.append({'seq': model['seq'], 'verdict': value['verdict'], 'issues': issues,
                                'provenance': 'actual_recorded_v1_model_response'})
            candidate_answer = str(payload.get('answer') or '')
            scoped = gate_answer(candidate_answer, payload.get('missing_information') or [], contract, issues,
                                 subquestion_coverage=payload.get('subquestion_coverage'))
            scope_results.append({'seq': model['seq'], 'input_answer_sha256': sha(candidate_answer),
                                  'kept_gaps': scoped['kept'], 'removed_gaps': scoped['removed'],
                                  'removed_answer_units': scoped.get('removed_answer_units', []),
                                  'answer_after_deterministic_scope_gate': scoped['answer'],
                                  'answerability_upgrade_allowed': scoped['answerability_upgrade_allowed'],
                                  'provenance': 'deterministic_application_to_recorded_input_not_new_model_answer'})
        if isinstance(value.get('claims'), list) and isinstance(payload.get('claims'), list):
            # Keep the model's old decisions intact. Typed atom acceptance needs
            # a new typed judge response, which was not authorized or fabricated.
            claims_by_id = {c['id']: c for c in payload['claims']}
            legacy_claim_reviews.append({'seq': model['seq'], 'decisions': value['claims'],
                                        'claim_count': len(payload['claims']),
                                        'typed_v2_acceptance_inferred': False})
            reconstructed = {'answer': '\n'.join(c['text'] for c in payload['claims'])}
            parsed = prepare_draft(reconstructed, contract)
            winter = [a for a in parsed['atoms'] if '户报' in a['text']]
            for atom in winter:
                atom.pop('text', None)  # exact span remains local; report only ownership
            legacy_claim_reviews[-1]['procedure_binding'] = winter
            selected = _old_passages(payload.get('evidence') or [])
            common = set(selected) & set(last_generation_passages)
            same_ids.append({'seq': model['seq'], 'common_passages': len(common),
                             'same_old_id_same_hash': all(selected[k] == last_generation_passages[k] for k in common),
                             'scope': 'actual_recorded_v1_generation_vs_grounding_only'})
    return {'case_id': case_id, 'question': question, 'phase': case.get('phase'), 'repeat': case.get('repeat'),
            'old_http_status': recorded.get('http_status'), 'old_actual_answer': answer,
            'old_actual_answer_sha256': sha(answer), 'old_system_label': reply.get('answerability'),
            'old_missing_information': reply.get('missing_information') or [],
            'model_calls_recorded': len(models), 'recorded_model_errors': sum(m['outcome'] != 'return' for m in models),
            'facet_contract': contract, 'recorded_draft_structures': drafts,
            'old_coverage_reviews': old_reviews, 'deterministic_scope_gate': scope_results,
            'legacy_claim_reviews': legacy_claim_reviews, 'relation_guard_on_recorded_drafts': relation_results,
            'recorded_passage_identity_checks': same_ids, 'new_evidence_view_repeatability': canonical,
            'new_v2_model_answer': None,
            'evaluation_limit': '未调用新版模型；旧标签不是正确率；结构校验不等于回答质量验收。'}


def build_report(record_dir=DEFAULT_RECORDS, *, catalog_path=None):
    record_dir = Path(record_dir).resolve()
    paths = sorted(record_dir.glob('*.result.json'))
    if not paths:
        raise ValueError('no_private_recorded_results')
    cases = [build_case(p) for p in paths]
    labels = Counter(str(c['old_system_label']) for c in cases)
    http = Counter(str(c['old_http_status']) for c in cases)
    drafts = [d for c in cases for d in c['recorded_draft_structures']]
    scope = [s for c in cases for s in c['deterministic_scope_gate']]
    relation = [r for c in cases for r in c['relation_guard_on_recorded_drafts']]
    canonical = [c['new_evidence_view_repeatability'] for c in cases if c['new_evidence_view_repeatability'].get('available')]
    catalog = Path(catalog_path) if catalog_path else APP / 'data/requirements/catalog.json'
    corpus = {}
    if catalog.exists():
        data = json.loads(catalog.read_text(encoding='utf-8'))
        corpus = {k: data.get(k) for k in ('question_count', 'category_count')}
    summary = {'recorded_complete_questions': len(cases), 'recorded_http_status': dict(http),
               'old_system_labels_not_accuracy': dict(labels),
               'recorded_model_interactions': sum(c['model_calls_recorded'] for c in cases),
               'actual_generated_drafts_parsed': len(drafts), 'atoms_checked': sum(d['atom_count'] for d in drafts),
               'drafts_with_all_exact_atoms': sum(d['all_atoms_exact_contiguous'] for d in drafts),
               'scope_review_calls_examined': len(scope),
               'exact_reviewed_gaps_removed': sum(len(s['removed_gaps']) for s in scope),
               'complete_limitation_units_removed': sum(len(s['removed_answer_units']) for s in scope),
               'relation_alarm_drafts': sum(bool(r['issues']) for r in relation),
               'relation_alarm_count': sum(len(r['issues']) for r in relation),
               'canonical_evidence_views_checked': len(canonical),
               'canonical_views_repeatable': sum(c['same_input_same_ids_and_hashes'] and c['hashes_match_text'] for c in canonical),
               'new_model_calls': 0, 'new_web_calls': 0, 'new_production_service_calls': 0,
               'new_typed_model_answers': 0}
    return {'schema_version': 1, 'title': 'P1–P6代码改造：24次录制的离线结构回归与问题对照',
            'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
            'record_dir': str(record_dir), 'scope': '实际旧录制输入上的确定性程序回归；未作新版真实问答评测',
            'summary': summary, 'requirement_corpus': corpus, 'cases': cases,
            'limits': ['没有生成或伪造新版DeepSeek输出，旧逐句supported意见没有转换成typed-v2意见。',
                       '24次包含1次已录制连接失败，未额外补跑。',
                       '数值准确、业务正确性、独立盲评和真实时延/费用需另行验收。',
                       '本报告的结构检查和规则警报不等于新版160题已测试或全部正确。']}


def render_html(report):
    esc = lambda x: html.escape(str(x))
    summary = ''.join('<tr><th>'+esc(k)+'</th><td>'+esc(v)+'</td></tr>' for k,v in report['summary'].items())
    body = []
    for c in report['cases']:
        gate = c['deterministic_scope_gate'][-1] if c['deterministic_scope_gate'] else {}
        findings = {'旧生成稿结构': c['recorded_draft_structures'],
                    '范围闸门': {k: v for k,v in gate.items() if k != 'answer_after_deterministic_scope_gate'},
                    '关系守卫警报': c['relation_guard_on_recorded_drafts'],
                    '原段身份': c['recorded_passage_identity_checks'],
                    '新版证据视图重复性': c['new_evidence_view_repeatability'],
                    '子问归属': [{'seq': r['seq'], 'procedure_binding': r['procedure_binding']} for r in c['legacy_claim_reviews']]}
        body.append('<article id="'+esc(c['case_id'])+'"><h2>'+esc(c['case_id'])+' '+esc(c['question'])+'</h2>'
                    '<p>旧录制 HTTP '+esc(c['old_http_status'])+'；旧系统标签 '+esc(c['old_system_label'])+'（非正确率）。</p>'
                    '<h3>实际旧回答（逐字）</h3><pre>'+esc(c['old_actual_answer'] or '本次录制未返回回答。')+'</pre>'
                    '<details><summary>新规则对旧输入的确定性检查</summary><pre>'+esc(json.dumps(findings,ensure_ascii=False,indent=2))+'</pre></details>'
                    '<details><summary>范围闸门后的旧稿（无新模型生成）</summary><pre>'+esc(gate.get('answer_after_deterministic_scope_gate', '本题无可用覆盖审查输入。'))+'</pre></details></article>')
    return '<!doctype html><html lang="zh"><meta charset="utf-8"><title>'+esc(report['title'])+'</title><style>body{font-family:Segoe UI,Microsoft YaHei,sans-serif;max-width:1200px;margin:30px auto;line-height:1.7;color:#153547}h1,h2{color:#087b8c}article{border-top:1px solid #ccd9dd;margin-top:32px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f8fa;padding:18px}table{border-collapse:collapse}th,td{border:1px solid #ccd9dd;padding:6px 12px;text-align:left}.note{background:#fff4dd;padding:18px}</style><h1>'+esc(report['title'])+'</h1><p class="note">'+esc(report['scope'])+'。零新增模型调用、零联网调用、零生产服务调用。旧答案是实测原文；闸门输出和关系警报是对旧输入的确定性处理，不是新版实际回答。</p><table>'+summary+'</table><p>'+esc(' '.join(report['limits']))+'</p>'+''.join(body)+'</html>'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record-dir', type=Path, default=DEFAULT_RECORDS)
    parser.add_argument('--out-prefix', type=Path, default=APP/'exports/P1-P6代码改造_24次录制离线回归与对照_20261020')
    args = parser.parse_args(argv)
    report = build_report(args.record_dir)
    args.out_prefix.parent.mkdir(parents=True, exist_ok=True)
    args.out_prefix.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    args.out_prefix.with_suffix('.html').write_text(render_html(report), encoding='utf-8')
    print(json.dumps({'output_json': str(args.out_prefix.with_suffix('.json')), 'output_html': str(args.out_prefix.with_suffix('.html')), 'summary': report['summary']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
