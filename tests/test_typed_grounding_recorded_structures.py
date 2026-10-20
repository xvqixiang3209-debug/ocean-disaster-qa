"""Structural replay of actual P0 outputs, not a new typed-model evaluation.

Only the private recording files are read. No live model, reader, HTTP service,
or generated replacement response is used. Missing private recordings skip
these optional tests in an exported code-only checkout.
"""
import json
from pathlib import Path

import pytest

from marine_kb.model_json import model_json
from marine_kb.replay import _unwire
from marine_kb.typed_grounding import prepare_draft


ROOT = Path(__file__).resolve().parents[1] / 'tmp/replay/p0_20261020_005001_b167cdca'
CASES = ['M1', 'M2', 'M3', 'C1', 'C2', 'C3', 'A1', 'A2', 'A3', 'G1', 'G2', 'G3'] + [f'{q}_extra_{i}' for q in ('G3','A2','C3','M1') for i in (1,2,3)]


def interactions(case):
    p = ROOT / (case + '.cassette.jsonl')
    if not p.exists():
        pytest.skip('private P0 recordings are not part of a code-only snapshot')
    for line in p.read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        if row.get('record_type') == 'interaction' and row.get('kind') == 'model':
            yield row


def planning(payload):
    parts = payload.get('subquestions') or []
    if not parts:
        parts = [{'id': 1, 'question': payload.get('question', '')}]
    return [{'fid': 'f'+str(p['id']), 'id': p['id'], 'kind': 'explanation', 'question': p['question'],
             'objects': [], 'required_outputs': ['explanation'], 'allowed_gaps': ['evidence_not_found']} for p in parts]


@pytest.mark.parametrize('case', CASES)
def test_actual_generation_outputs_only_split_into_exact_contiguous_atoms(case):
    drafts = 0
    failures = 0
    for row in interactions(case):
        if row.get('outcome') != 'return':
            failures += 1
            continue
        req = _unwire(row['request'])
        args = req.get('args', [])
        if len(args) < 3:
            continue
        try:
            payload = json.loads(args[2])
            response = _unwire(row['response'])
            value = model_json(response[0] if isinstance(response, tuple) else response)
        except (ValueError, TypeError, KeyError):
            continue
        if not isinstance(value, dict) or not isinstance(value.get('answer'), str) or not value['answer']:
            continue
        parsed = prepare_draft(value, planning(payload))
        drafts += 1
        for atom in parsed['atoms']:
            assert atom['text'] == parsed['draft']['answer'][atom['start']:atom['end']]
            assert atom['end'] > atom['start']
            assert len(set(atom['candidate_facet_ids'])) == len(atom['candidate_facet_ids'])
    if case == 'G3_extra_3':
        assert failures > 0 and drafts == 0
    else:
        assert drafts > 0


def test_actual_g3_removed_heading_retains_winter_subject_provenance():
    recorded = None
    for row in interactions('G3_extra_2'):
        req = _unwire(row['request'])
        args = req.get('args', [])
        if len(args) < 3:
            continue
        payload = json.loads(args[2])
        if 'claims' in payload and any('户报' in c.get('text','') for c in payload['claims']):
            recorded = payload
            review = model_json(_unwire(row['response'])[0])
            break
    assert recorded is not None
    # This is the exact failed recovery input reconstructed from its recorded
    # existing claims; no new judge decision or replacement answer is made up.
    raw = {'answer': '\n'.join(c['text'] for c in recorded['claims'])}
    fs = [{'fid': 'f1', 'question': '是不是一回事？', 'kind': 'distinction', 'objects': ['过渡期生活救助','冬春救助','巨灾保险赔付']},
          {'fid': 'f2', 'question': '过渡期生活救助依据什么？', 'kind': 'basis_document', 'objects': ['过渡期生活救助']},
          {'fid': 'f3', 'question': '冬春救助依据什么？', 'kind': 'basis_document', 'objects': ['冬春救助']},
          {'fid': 'f4', 'question': '巨灾保险赔付依据什么？', 'kind': 'basis_document', 'objects': ['巨灾保险赔付']}]
    parsed = prepare_draft(raw, fs)
    procedure = next(a for a in parsed['atoms'] if '户报' in a['text'])
    assert procedure['facet_ids'] == ['f3']
    assert procedure['context_subject'] == ['冬春救助']
    # The actual old judge rejected the preceding winter heading and accepted
    # the procedure. New ownership survives independently of this deletion.
    assert next(r for r in review['claims'] if r['id'] == 5)['status'] == 'unsupported'
    assert next(r for r in review['claims'] if r['id'] == 6)['status'] == 'supported'


def test_actual_legacy_judge_outputs_are_not_silently_cast_to_typed_opinions():
    from marine_kb.typed_grounding import apply_review
    fs = [{'fid':'f1', 'question':'程序是什么？', 'kind':'procedure','objects':[]}]
    with pytest.raises(ValueError, match='incomplete_review'):
        apply_review({'answer':'县级审核后发放[资料1]。'}, {'claims':[{'id':1,'status':'supported','refs':[{'citation':1,'passage_id':1}]}]}, [], fs, lambda *args: None)
