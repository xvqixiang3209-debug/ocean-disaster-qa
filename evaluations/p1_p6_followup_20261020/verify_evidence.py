"""Verify this public artifact's statistics and file hashes; no model/network."""
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'))


def main():
    grounding = read('deterministic_grounding.json')
    assert grounding['case_count'] == len(grounding['cases']) == 24
    for key, value in grounding['totals'].items():
        assert value == sum(case['counts'][key] for case in grounding['cases']), key
    decisions = [d for case in grounding['cases'] for review in case['reviews']
                 for d in review.get('decisions', [])]
    assert grounding['totals']['retained_atoms'] == sum(d['retained'] for d in decisions)
    assert grounding['totals']['dropped_atoms'] == sum(not d['retained'] for d in decisions)
    assert grounding['totals']['needs_new_judge'] == sum(
        r['status'] == 'needs_new_judge' for c in grounding['cases'] for r in c['reviews'])
    delivery = read('deterministic_delivery_summary.json')
    assert delivery['case_count'] == len(delivery['cases']) == 24
    for source, total in [('old_facets', 'old_facet_status_counts'),
                          ('new_facets_on_same_recorded_atoms', 'new_facet_status_counts')]:
        assert dict(Counter(r['status'] for c in delivery['cases'] for r in c[source])) == delivery[total]
    assert delivery['changed_case_count'] == sum(bool(c['changes']) for c in delivery['cases'])
    scope = read('deterministic_scope_summary.json')
    assert scope['case_count'] == len(scope['cases']) == 24
    assert scope['scope_inputs_replayed'] == sum(c['scope_inputs_replayed'] for c in scope['cases'])
    assert scope['initial_kept_changed'] == sum(c['initial_kept_changed'] for c in scope['cases'])
    validation = read('verification_summary.json')['validation']
    assert validation['junit_tests_including_subtests'] == validation['pytest_items'] + validation['successful_subtests']
    assert validation['failures'] == validation['errors'] == validation['skipped'] == 0
    for line in (ROOT / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        expected, name = line.split('  ', 1)
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    print('PASS: aggregate counts, deterministic decisions, 24-case coverage, final test totals and artifact SHA256.')


if __name__ == '__main__':
    main()
