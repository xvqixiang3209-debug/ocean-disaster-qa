"""Recheck recorded typed judgements offline; never fabricate new opinions.

This is a deterministic stage regression, not a new complete QA/model run.
Private cassettes are read-only; the report contains hashes/counts, not prompts.
"""
import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from marine_kb import typed_grounding, relation_guard, quantities
from marine_kb.model_json import model_json
from marine_kb.qa import _decode_model_answer
from marine_kb.replay import _unwire


def load_baseline(source, name):
    path = source/'marine_kb'/(name+'.py')
    spec = importlib.util.spec_from_file_location('marine_kb._grounding_baseline_'+name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_model_response(wire):
    if wire.get('outcome') != 'return':
        return None
    value = _unwire(wire['response'])
    return model_json(value[0] if isinstance(value, tuple) else value)


def identity(atoms):
    return [(a['aid'], a['start'], a['end'], a['text']) for a in atoms]


def recorded_body_bank(wires):
    """Only exact acquired source_text; never stitch snippets into a body."""
    bank = {}
    def walk(value):
        if isinstance(value, dict):
            text = value.get('source_text')
            if isinstance(text, str) and text:
                bank[hashlib.sha256(text.encode('utf-8')).hexdigest()] = text
            for child in value.values():
                walk(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                walk(child)
    for row in wires:
        if (row.get('record_type') == 'interaction' and row.get('kind') == 'public_retrieval'
                and row.get('outcome') == 'return'):
            walk(_unwire(row['response']))
    return bank


def restore_evidence_bodies(evidence, bank):
    restored, missing, rows = [], [], []
    for item in evidence:
        row = dict(item)
        digest = row.get('body_sha256') or (row.get('doc_card') or {}).get('body_sha256')
        body = row.get('text')
        if isinstance(body, str) and digest == hashlib.sha256(body.encode('utf-8')).hexdigest():
            rows.append(row)
            continue
        if digest in bank:
            row['text'] = bank[digest]
            restored.append(digest)
        else:
            missing.append(digest)
        rows.append(row)
    return rows, restored, missing


def replay_case(path, old_typed, old_guard, old_quantities):
    history = []
    typed = []
    counts = dict(reviews=0, replayed_judgements=0, needs_new_judge=0,
                  old_health_source_alarms=0, new_health_source_alarms=0,
                  old_section_locator_quantities=0, new_section_locator_quantities=0,
                  scope_restored_direct_atoms=0, retained_atoms=0, dropped_atoms=0,
                  recorded_synthesis_missing_proof=0, quoted_formulas_protected=0,
                  restored_body_count=0, unmatched_body_count=0)
    lexicon = relation_guard.load_lexicon()
    wires = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    body_bank = recorded_body_bank(wires)
    for wire in wires:
        if wire.get('record_type') != 'interaction' or wire.get('kind') != 'model':
            continue
        args = _unwire(wire['request']).get('args', [])
        if len(args) < 3:
            continue
        try:
            payload = json.loads(args[2])
            result = parse_model_response(wire)
        except (ValueError, TypeError, KeyError):
            continue
        if payload.get('atoms'):
            counts['reviews'] += 1
            fs = payload.get('facet_contract') or []
            evidence, restored, missing = restore_evidence_bodies(payload.get('evidence') or [], body_bank)
            counts['restored_body_count'] += len(restored)
            counts['unmatched_body_count'] += len(missing)
            atoms = payload['atoms']
            entry = {'stage': (wire.get('context') or {}).get('stage'),
                     'draft_sha256': payload.get('draft_sha256'), 'recorded_atoms': len(atoms),
                     'restored_body_sha256': restored, 'unmatched_body_sha256': missing}
            for module, prefix in ((old_guard, 'old'), (relation_guard, 'new')):
                issues = module.check(atoms, fs, evidence, lexicon=lexicon)
                counts[prefix+'_health_source_alarms'] += sum(i['code'] == 'relation_health_source_required' for i in issues)
            for atom in atoms:
                if '第' in atom['text'] and '节' in atom['text']:
                    for module, prefix in ((old_quantities, 'old'), (quantities, 'new')):
                        counts[prefix+'_section_locator_quantities'] += sum(q[1] == '节' for q in module.quantities(atom['text']))
            rows = result.get('atoms') if isinstance(result, dict) else None
            if rows:
                counts['recorded_synthesis_missing_proof'] += sum(r.get('status') == 'synthesis' and
                    (not r.get('premises') or not r.get('inference')) for r in rows)
            matched = None
            for candidate in reversed(history):
                try:
                    parsed = old_typed.prepare_draft(candidate, fs)
                except ValueError:
                    continue
                if identity(parsed['atoms']) == identity(atoms):
                    matched = candidate
                    break
            if matched is None:
                counts['needs_new_judge'] += 1
                entry.update(status='needs_new_judge', reason='original_generation_raw_not_exactly_matched')
                typed.append(entry)
                continue
            new_atoms = typed_grounding.prepare_draft(matched, fs)['atoms']
            if identity(new_atoms) != identity(atoms):
                counts['needs_new_judge'] += 1
                entry.update(status='needs_new_judge', reason='parser_changed_exact_atom_boundaries', new_atoms=len(new_atoms))
                typed.append(entry)
                continue
            if not rows:
                counts['needs_new_judge'] += 1
                entry.update(status='needs_new_judge', reason='recorded_typed_response_missing')
                typed.append(entry)
                continue
            def decode(value, sources):
                return _decode_model_answer(json.dumps(value, ensure_ascii=False), sources)
            try:
                current = typed_grounding.apply_review(matched, result, evidence, fs, decode)
                accepted = current['accepted_atoms']
                counts['replayed_judgements'] += 1
                counts['retained_atoms'] += len(accepted)
                counts['dropped_atoms'] += sum(not d.get('retained') for d in current['decisions'])
                restored = 0
                bycitation = {int(e.get('citation') or i): e for i, e in enumerate(evidence, 1)}
                for atom in accepted:
                    if atom['type'] != 'direct_fact':
                        continue
                    selected = [{**q, 'source': bycitation[q['citation']]} for q in atom['evidence_quotes']]
                    if typed_grounding._grounded_display(atom, selected) != old_typed._grounded_display(atom, selected):
                        restored += 1
                counts['scope_restored_direct_atoms'] += restored
                entry.update(status='replayed_exact_recorded_judgement', retained=len(accepted),
                             scope_labels_restored=restored,
                             decisions=[{k: d.get(k) for k in ('aid', 'retained', 'type', 'drop_rule', 'facet_ids')} for d in current['decisions']],
                             output_sha256=hashlib.sha256(current['answer'].encode()).hexdigest())
            except (ValueError, TypeError, KeyError) as exc:
                entry.update(status='deterministic_validation_error', error=str(exc))
            typed.append(entry)
        if isinstance(result, dict) and isinstance(result.get('answer'), str):
            history.append(result)
            # Quoted formulas are measured on original generations, never by
            # stitching together already split/judged atoms from a request.
            try:
                import re
                formulas = re.findall(r'“[^”]*[；;][^”]*”', result['answer'])
                if formulas:
                    fs = [{'fid': 'f1', 'question': '', 'kind': 'explanation', 'objects': []}]
                    old_atoms = old_typed.prepare_draft(result, fs)['atoms']
                    new_atoms = typed_grounding.prepare_draft(result, fs)['atoms']
                    counts['quoted_formulas_protected'] += sum(
                        not any(f in a['text'] for a in old_atoms) and any(f in a['text'] for a in new_atoms) for f in formulas)
            except ValueError:
                pass
    return {'case': path.name.removesuffix('.cassette.jsonl'), 'counts': counts, 'reviews': typed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--baseline-source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.baseline_source or args.run_dir/'source_snapshot'
    old = {name: load_baseline(source, name) for name in ('typed_grounding', 'relation_guard', 'quantities')}
    cases = [replay_case(path, old['typed_grounding'], old['relation_guard'], old['quantities'])
             for path in sorted(args.run_dir.glob('*.cassette.jsonl'))]
    if not cases:
        raise ValueError('recording_cassettes_missing')
    totals = {key: sum(c['counts'][key] for c in cases) for key in cases[0]['counts']}
    report = {'method': 'exact_recorded_atom_judgement_deterministic_recheck', 'model_calls': 0,
              'new_end_to_end_answers_generated': False, 'case_count': len(cases), 'totals': totals, 'cases': cases,
              'limitations': ['Counts are review occurrences, not unique answer accuracy.',
                             'Changed atom boundaries are not assigned replacement judgements.',
                             'Recorded synthesis without proof remains unapproved; changed prompts need a future authorized live run.',
                             'Baseline imports use current shared supporting modules; this is a validator-stage diagnostic, not frozen complete QA replay.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'case_count': len(cases), 'totals': totals}, ensure_ascii=False))


if __name__ == '__main__':
    main()
