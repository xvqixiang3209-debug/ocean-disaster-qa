"""Bounded facet-local recovery; validated siblings are immutable.

All IO is injected. Production callers must pass their per-answer budget to
the callbacks; offline tests can use structural fixtures without network IO.
"""
import copy
import hashlib
import inspect
import json
import re

from .facet_delivery import clean_text, evaluate_delivery, publishable, render_answer, render_with_gaps, semantic_match


def _fingerprint(atom):
    return hashlib.sha256(json.dumps(atom, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _callback(callback, args, *, registry, target_fids):
    """Optional keyword context without breaking legacy positional callbacks."""
    parameters = inspect.signature(callback).parameters
    variadic = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values())
    kwargs = {}
    for name, value in (('facet_registry', registry), ('target_fids', target_fids)):
        if variadic or name in parameters:
            kwargs[name] = copy.deepcopy(value)
    return callback(*args, **kwargs)


def _claim_key(atom):
    # Ignore citation/numbering/spacing variations, but preserve all factual
    # numbers, negations and facet/scope identity. No fuzzy fact merging.
    text = re.sub(r'^\s*\d+(?:[.．](?!\d)|、)\s*', '', clean_text(atom.get('text', '')))
    text = re.sub(r'\s+', '', text).strip('。；;')
    return (text, atom.get('type'), tuple(sorted(atom.get('facet_ids') or [])),
            tuple(sorted(atom.get('context_subject') or [])))


def recover_locally(initial, evidence, facets, *, targeted_retrieve=None,
                    regenerate=None, ground=None, budget=None,
                    max_searches=1, max_rewrites=1):
    """At most one targeted search and one rewrite, restricted to missing facets.

    Callback shapes: retrieve(failed_facets, evidence) -> new evidence rows;
    regenerate(failed_facets, frozen_atoms, evidence, rejected) -> raw draft;
    ground(raw, evidence, full_facet_registry) -> typed-grounding result.
    Callbacks accepting ``facet_registry``/``target_fids`` receive those keyword
    arguments too. ``target_fids`` restricts edits, never the binding universe.
    ``budget``
    exposes ``take(stage)`` or is a callable, and can deny every optional step.
    No callback may edit a validated sibling; attempts are detected and ignored.
    """
    registry = copy.deepcopy(list(facets))
    registry_sha256 = _fingerprint(registry)
    accepted = copy.deepcopy(initial.get('accepted_atoms', []))
    frozen_ids = {a['aid'] for a in accepted}
    frozen = {a['aid']: _fingerprint(a) for a in accepted}
    original_evidence = copy.deepcopy(evidence)
    current_evidence = copy.deepcopy(evidence)
    trace = []
    rows = evaluate_delivery(registry, accepted)
    by_fid = {r['fid']: r for r in rows}
    failed = [f for f in registry if by_fid.get(str(f.get('fid') or f.get('id')), {}).get('status') != 'full']
    target_fids = [str(f.get('fid') or f.get('id')) for f in failed]

    def take(stage):
        if budget is None:
            # A caller without a budget can run deterministic rendering only.
            return False
        try:
            return bool(budget.take(stage) if hasattr(budget, 'take') else budget(stage))
        except (ValueError, TypeError, KeyError, RuntimeError):
            return False

    if failed and targeted_retrieve and max_searches > 0 and take('facet_search'):
        try:
            new = targeted_retrieve(copy.deepcopy(failed), copy.deepcopy(current_evidence))
            if isinstance(new, list):
                # Source identity must remain stable: appending a source is OK,
                # replacing an existing source under its citation is not.
                known = {int(e.get('citation') or i+1): e for i, e in enumerate(current_evidence)}
                for item in new:
                    citation = item.get('citation')
                    if type(citation) is int and citation in known:
                        if item != known[citation]:
                            trace.append({'stage': 'facet_search', 'error': 'source_identity_changed'})
                        continue
                    current_evidence.append(copy.deepcopy(item))
                trace.append({'stage': 'facet_search', 'new_sources': len(current_evidence)-len(original_evidence)})
        except Exception as exc:
            trace.append({'stage': 'facet_search', 'error': type(exc).__name__})
    if failed and regenerate and ground and max_rewrites > 0 and take('facet_regen'):
        try:
            callback_frozen = copy.deepcopy(accepted)
            raw = _callback(regenerate, (copy.deepcopy(failed), callback_frozen,
                            copy.deepcopy(current_evidence), copy.deepcopy(initial.get('decisions', []))),
                            registry=registry, target_fids=target_fids)
            if any(_fingerprint(a) != frozen.get(a['aid']) for a in callback_frozen) or {a['aid'] for a in callback_frozen} != frozen_ids:
                raise ValueError('frozen_sibling_mutated')
            if not take('facet_ground'):
                raise ValueError('facet_ground_budget_exhausted')
            result = _callback(ground, (raw, copy.deepcopy(current_evidence), copy.deepcopy(registry)),
                               registry=registry, target_fids=target_fids)
            allowed = set(target_fids)
            candidates = result.get('accepted_atoms', [])
            local_ids = {a['aid']: f'local1_{n}_{a["aid"]}' for n,a in enumerate(candidates, 1)}
            by_facet = {str(f.get('fid') or f.get('id')): f for f in registry}
            known_claims = {_claim_key(a): a['aid'] for a in accepted}
            filtered = []
            for n, original in enumerate(candidates, 1):
                atom = copy.deepcopy(original)
                if not set(atom.get('facet_ids', [])) <= allowed or not atom.get('facet_ids'):
                    trace.append({'stage': 'facet_regen', 'error': 'rewritten_sibling_rejected', 'aid': atom.get('aid')})
                    continue
                if any(not semantic_match(atom, by_facet[fid], registry) for fid in atom['facet_ids']):
                    trace.append({'stage': 'facet_regen', 'error': 'foreign_object_or_intent_rejected', 'aid': atom.get('aid')})
                    continue
                key = _claim_key(atom)
                if key in known_claims:
                    local_ids[atom['aid']] = known_claims[key]
                    trace.append({'stage': 'facet_regen', 'error': 'duplicate_fact_omitted', 'aid': atom.get('aid'), 'retained_aid': known_claims[key]})
                    continue
                known_claims[key] = local_ids[atom['aid']]
                filtered.append(atom)
            # Drop the whole unsupported inference dependency chain, not only
            # its first dangling node. A later synthesis may depend on an
            # earlier synthesis removed because a sibling premise was barred.
            while True:
                available_ids = frozen_ids | {local_ids[a['aid']] for a in filtered}
                dangling = [a for a in filtered if a.get('type') == 'synthesis' and
                            any(local_ids.get(aid, aid) not in available_ids for aid in a.get('premises', []))]
                if not dangling:
                    break
                rejected_ids = {a['aid'] for a in dangling}
                for atom in dangling:
                    trace.append({'stage': 'facet_regen', 'error': 'rejected_synthesis_premise', 'aid': atom.get('aid')})
                filtered = [a for a in filtered if a['aid'] not in rejected_ids]
            for atom in filtered:
                # Every new parse has local IDs. Namespace all of them and all
                # premise pointers together, so a synthesis cannot accidentally
                # point to an unrelated frozen atom with the same old aid.
                atom['aid'] = local_ids[atom['aid']]
                if atom.get('premises'):
                    atom['premises'] = [local_ids.get(aid, aid) for aid in atom['premises']]
                for comparison in atom.get('inference', {}).get('comparisons', []):
                    comparison['premise_id'] = local_ids.get(comparison.get('premise_id'), comparison.get('premise_id'))
                accepted.append(copy.deepcopy(atom))
            trace.append({'stage': 'facet_regen', 'accepted_atoms': len(accepted)-len(frozen),
                          'target_fids': target_fids, 'facet_registry_sha256': registry_sha256})
        except Exception as exc:
            trace.append({'stage': 'facet_regen', 'error': str(exc) if isinstance(exc, ValueError) else type(exc).__name__})
    # Validate the retained immutable copies again before publication.
    if any(_fingerprint(a) != frozen[a['aid']] for a in accepted if a['aid'] in frozen_ids):
        raise ValueError('frozen_sibling_mutated')
    if _fingerprint(registry) != registry_sha256:
        raise ValueError('facet_registry_mutated')
    rows = evaluate_delivery(registry, accepted)
    ok = publishable(registry, rows)
    complete = ok and all(r['status'] == 'full' for r in rows)
    answer = render_answer(registry, accepted) if complete else render_with_gaps(registry, accepted, current_evidence, rows=rows)
    quotes = list({(r['citation'], r.get('passage_id'), r['quote']): r for a in accepted for r in a.get('evidence_quotes', [])}.values())
    return {**initial, 'answer': answer, 'answerability': 'full' if complete else 'partial',
            'accepted_atoms': accepted, 'delivery': rows, 'publishable': ok,
            'render_mode': 'grounded' if complete else 'with_gaps',
            'evidence_quotes': quotes, 'recovery_trace': trace, 'evidence': current_evidence,
            'frozen_atom_sha256': frozen}


recover = recover_locally
