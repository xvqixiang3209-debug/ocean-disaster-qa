"""P1–P6 opt-in orchestration. One contract, one evidence view, local recovery.

No raw recorded v1 response is treated as a response to a new v2 prompt.
The legacy pipeline remains available for a controlled rollout.
"""
import copy
import json
import re

from .phase2 import enabled, mode, current_budget
from .qa_observability import event, evidence_view, observe_failure, model_stage


def _refs(answer):
    return {int(n) for n in re.findall(r'\[资料(\d+)\]', answer)}


def _model_view(evidence):
    return [{k:v for k,v in e.items() if k != 'text'} for e in evidence]


def _decode(value, evidence):
    from .qa import _decode_model_answer
    return _decode_model_answer(json.dumps(value, ensure_ascii=False), evidence)


def repair_payload(payload, failed, registry, evidence, frozen, relation_feedback=None):
    """A repair keeps global IDs but can only write the selected user facets.

    Never reuse the whole-question layout or supply frozen answer prose as a
    draft to repeat. Retained text lives in the recovery engine, not this prompt.
    """
    from .answer_design import answer_design
    from .question_contract import question_contract
    targets = copy.deepcopy(failed)
    registry = copy.deepcopy(registry)
    allowed = {str(f.get('fid') or f.get('id')) for f in targets}
    known = {str(f.get('fid') or f.get('id')) for f in registry}
    if not allowed or not allowed <= known:
        raise ValueError('invalid_repair_target_fids')
    question = '\n'.join(str(f.get('question') or f.get('text') or '') for f in targets)
    value = copy.deepcopy(payload)
    # These parent plans carry stale sibling coverage/object assignments. The
    # canonical registry and bounded evidence replace them for this repair.
    for key in ('evidence_coverage','corrective_retrieval_plan','corrective_retrieval_instruction'):
        value.pop(key, None)
    value.update(question=question, subquestions=targets, facet_contract=registry,
        target_fids=sorted(allowed), read_only_fids=sorted(known-allowed),
        question_contract=question_contract(question),
        answer_design=answer_design(question,targets), evidence=_model_view(evidence),
        frozen_siblings=[{'aid':a.get('aid'),'facet_ids':a.get('facet_ids',[]),
                          'content_sha256':__import__('hashlib').sha256(str(a.get('text','')).encode()).hexdigest()}
                         for a in frozen],
        repair_instruction=('只回答target_fids所列子问。facet_contract保留完整身份注册表，read_only_fids禁止补写。'
                            '只按本次answer_design布局生成，已核对内容由程序保留，不复述兄弟子问。'
                            '每个输出的子问ID必须属于target_fids；证据不足时保留该子问具体缺口，不编造。'),
        relation_feedback=relation_feedback or [])
    return value


def enforce_relations(result, facets, evidence):
    from .relation_guard import check
    from .facet_delivery import evaluate_delivery, publishable, render_with_gaps
    setting=mode('MARINE_RELATION_GUARD')
    if setting == 'off':return result
    issues=check([a for a in result['accepted_atoms'] if a.get('type') in
                  {'direct_fact','calculation','synthesis'}],facets,evidence)
    result={**result,'relation_review':{'mode':setting,'issues':issues}}
    if setting == 'shadow' or not issues:return result
    rejected={i['aid'] for i in issues}
    # A synthesis relying on a rejected premise must also be withheld.
    while True:
        more={a['aid'] for a in result['accepted_atoms'] if rejected & set(a.get('premises') or [])}
        if more <= rejected:break
        rejected |= more
    accepted=[a for a in result['accepted_atoms'] if a['aid'] not in rejected]
    rows=evaluate_delivery(facets,accepted)
    result.update(accepted_atoms=accepted,delivery=rows,publishable=publishable(facets,rows),
        answerability='partial',render_mode='with_gaps',
        answer=render_with_gaps(facets,accepted,evidence,rows=rows))
    result['evidence_quotes']=[r for a in accepted for r in a.get('evidence_quotes',[])]
    result['decisions']=list(result['decisions'])+[
        {'aid':aid,'retained':False,'drop_rule':'relation_guard_recheck_required'} for aid in sorted(rejected)]
    return result


def withhold_disputed_atoms(result, issues):
    """A late critic cannot rewrite facts or silently publish a disputed claim.

    Only an exact overlapping atom is withheld, together with syntheses that
    depend on it. Unrelated siblings and original wording remain untouched.
    This is an unresolved disagreement, never a new unsupported model verdict.
    """
    compact=lambda text:re.sub(r'\s+|[*#]', '', str(text)).strip('。；;')
    rejected=set();matches=[];ambiguous=[]
    for issue in issues or []:
        if issue.get('kind') not in {'unsupported_claim','contradiction'}:continue
        span=compact(issue.get('text',''))
        if len(span)<4:continue
        target_id=issue.get('target_id')
        target=compact(issue.get('target_text',''))
        if target_id and (not str(target_id).startswith('a') or not target):
            ambiguous.append({'issue':dict(issue),'reason':'answer_review_target_not_resolved'});continue
        candidates=[]
        for atom in result.get('accepted_atoms',[]):
            if atom.get('origin') == 'verified_tool_record':
                continue
            if atom.get('type') not in {'direct_fact','calculation','synthesis'}:continue
            alternatives=[compact(atom.get(key,'')) for key in ('text','display_text')]
            overlap=any(len(text)>=4 and (span in text or text in span) for text in alternatives)
            in_target=not target_id or any(len(text)>=4 and (text in target or target in text) for text in alternatives)
            if overlap and in_target:candidates.append(atom)
        # A short phrase shared by multiple sibling facts is not a unique
        # location, even when a critic supplies it as an exact substring.
        whole=[a for a in candidates if any(compact(a.get(key,'')) and compact(a.get(key,'')) in span
                                           for key in ('text','display_text'))]
        if len(candidates)>1 and len(whole)!=len(candidates):
            ambiguous.append({'issue':dict(issue),'candidate_atom_ids':[a['aid'] for a in candidates],
                              'reason':'review_span_not_unique'});continue
        for atom in candidates:
            rejected.add(atom['aid']);matches.append({'aid':atom['aid'],'issue':dict(issue)})
    while True:
        more={a['aid'] for a in result.get('accepted_atoms',[])
              if rejected & set(a.get('premises') or [])}
        if more <= rejected:break
        rejected |= more
    accepted=[a for a in result.get('accepted_atoms',[]) if a['aid'] not in rejected]
    return {**result,'accepted_atoms':accepted,
            'disputed_atom_withholding':{'matches':matches,'ambiguous':ambiguous,'withheld_atom_ids':sorted(rejected),
                                        'scope':'exact conflicting spans; not an expert adjudication'},
            'decisions':list(result.get('decisions') or [])+[
                {'aid':aid,'retained':False,'drop_rule':'final_review_disputed_pending_recheck'} for aid in sorted(rejected)]}


def ground(qa,model,raw,evidence,facets):
    from .typed_grounding import review_check, review_atoms, apply_review,resolve_judge_conflicts, PROMPT
    check=review_check(raw,_model_view(evidence),facets)
    evidence_view('typed_grounding',_model_view(evidence))
    review=review_atoms(check,lambda payload:qa._call_model(model,PROMPT,json.dumps(payload,ensure_ascii=False)))
    def allow_recheck(stage):
        budget=current_budget()
        return budget is not None and budget.remaining()>0 and budget.calls < budget.max_calls
    conflict=resolve_judge_conflicts(raw,review,_model_view(evidence),facets,
        lambda payload:qa._call_model(model,PROMPT,json.dumps(payload,ensure_ascii=False)),budget=allow_recheck)
    review=conflict['review']
    result=apply_review(raw,review,evidence,facets,_decode)
    # These fields are capabilities of server-verified tool records. They are
    # never accepted from a draft or a model judgement, even if a future draft
    # parser starts carrying additional JSON keys.
    for atom in result.get('accepted_atoms',[]):
        for key in ('origin','tool_fact','tool_fact_hash'):
            atom.pop(key,None)
    result['judge_consistency']={k:v for k,v in conflict.items() if k!='review'}
    return enforce_relations(result,facets,evidence)


def review_scope(qa,model,raw,evidence,facets,question):
    from .model_json import model_json
    from .coverage_review import PROMPT,decode_review,review_targets
    value=model_json(raw)
    answer=str(value.get('answer') or '')
    missing=value.get('missing_information') or []
    missing=[m for m in missing if isinstance(m,str)]
    targets=review_targets(answer,missing)
    payload={'question':question,'facet_contract':facets,'answer':answer,
             'missing_information':missing,'subquestions':value.get('subquestion_coverage') or facets,
             'review_targets':targets,'cited_passages':_model_view(evidence)}
    evidence_view('coverage_review',payload['cited_passages'])
    review={'verdict':'unavailable','issues':[]}
    if getattr(qa,'coverage_review_enabled',False):
        try:
            review=decode_review(qa._call_model(model,PROMPT,json.dumps(payload,ensure_ascii=False)),answer,missing,targets)
        except Exception as exc:
            observe_failure('coverage_review',exc)
            review['error_type']=type(exc).__name__
    target_map={r['id']:r['text'] for r in targets}
    for issue in review['issues']:
        if issue.get('target_id') in target_map:
            issue['target_text']=target_map[issue['target_id']]
    gate=None
    if enabled('MARINE_SCOPE_GATE'):
        from .scope_gate import gate_answer
        gate=gate_answer(answer,missing,facets,review['issues'],
                         subquestion_coverage=value.get('subquestion_coverage'))
        value.update(answer=gate['answer'],missing_information=gate['kept'])
        # Removal is editorial; it never certifies completeness.
        for row in value.get('subquestion_coverage') or []:
            own=row.get('missing_information') or []
            replacements={r['original']:r['replacement'] for r in gate.get('replacements',[])}
            removed={r.get('text') for r in gate['removed']}
            row['missing_information']=[replacements.get(m,m) for m in own
                                        if replacements.get(m,m) is not None and m not in removed]
    return json.dumps(value,ensure_ascii=False),review,gate


def organized_v2(qa,question,model,hits,sources,evidence,payload,meta,boundary_reason=''):
    from .qa import STRUCTURED_ANSWER_PROMPT,_sources_with_selected_passages
    from .facet_contract import build_contract
    from .facet_delivery import render_with_gaps,evaluate_delivery
    from .recovery import recover_locally
    from .business_validation import validate_business_delivery
    from .model_json import model_json
    facets=build_contract(question,payload.get('subquestions'),meta.get('turn_state'))
    payload={**payload,'subquestions':facets,'facet_contract':facets,
             'typed_delivery_instruction':'逐个子问交付所需内容，允许依据多个有据前提作明确标注的比较归纳。文件身份与正文条款分开引用；元数据passage_id=0只支持身份。保留条件、否定、主语和适用对象。'}
    audit={'version':2,'facet_contract':facets,'scope_review':None,'scope_gate':None}
    from .protected_tool_delivery import verified_tool_atoms,merge_tool_atoms,resolve_tool_gaps
    tool_atoms,evidence,tool_audit=verified_tool_atoms(meta,evidence,facets)
    audit['protected_tool_delivery']=tool_audit
    grounded=None
    raw=None
    try:
        raw=qa._call_model(model,STRUCTURED_ANSWER_PROMPT,json.dumps(payload,ensure_ascii=False))
        raw,review,gate=review_scope(qa,model,raw,evidence,facets,question)
        audit.update(scope_review=review,scope_gate=gate)
        grounded=ground(qa,model,raw,evidence,facets)
        grounded=withhold_disputed_atoms(grounded,review.get('issues') or [])
        audit['initial_disputed_atom_withholding']=grounded.get('disputed_atom_withholding')
        if grounded.get('disputed_atom_withholding',{}).get('withheld_atom_ids'):
            from .facet_delivery import publishable
            rows=evaluate_delivery(facets,grounded['accepted_atoms'])
            grounded.update(delivery=rows,publishable=publishable(facets,rows),answerability='partial',
                            answer=render_with_gaps(facets,grounded['accepted_atoms'],evidence,rows=rows))
    except Exception as exc:
        observe_failure('typed_answer',exc)
        audit['error_type']=type(exc).__name__
        if isinstance(exc,ValueError):audit['error_code']=str(exc)[:100]
    if grounded is None:
        grounded={'accepted_atoms':[],'decisions':[],'evidence_quotes':[],
                  'delivery':evaluate_delivery(facets,[]),'publishable':False,
                  'answerability':'partial','render_mode':'with_gaps'}
        grounded['answer']=render_with_gaps(facets,[],evidence,rows=grounded['delivery'])

    # An unavailable generator is an infrastructure failure, never a fact answer.
    if raw is None:
        return {'answer':'DeepSeek暂时未能生成回答，请稍后重试。','rag_sources':[],'web_sources':[],
                'answer_meta':{**meta,'used_mode':'model_error','route':'model_unavailable',
                               'phase2':audit,'generation_used':False}}

    grounded=merge_tool_atoms(grounded,tool_atoms,facets)

    def permits(stage):
        budget=current_budget()
        return budget is not None and budget.remaining()>0 and budget.calls < budget.max_calls

    local_scope = []

    def retrieve(failed,existing):
        # Search user facets only: no private evidence or model draft sent to search.
        queries=[f['question'] for f in failed]
        repair_question='\n'.join(queries)
        local_meta={}
        local=qa._collect_local_hits(repair_question,repair_question,queries,failed,local_meta,
                                     meta.get('dynamic_question',False),meta.get('sid'))
        public,_=qa._fetch_public(repair_question,failed,meta.get('dynamic_question',False),bool(meta.get('web_allowed',False)))
        from .evidence_selection import select_evidence
        from .evidence_applicability import assess_page
        candidates=[]
        for hit in select_evidence(repair_question,[h for h in local+public if qa._can_send(h)],failed,limit=4):
            page={'title':hit.get('title'),'content':hit.get('source_text') or hit.get('text'),
                  'metadata':hit.get('metadata') or {}}
            apps=[assess_page(repair_question,page,meta.get('dynamic_question',False),subquestion=f['question']) for f in failed]
            if apps and all(a.get('use')=='excluded' for a in apps):continue
            candidates.append({**hit,'metadata':{**hit.get('metadata',{}),'applicability':{'facets':apps}}})
        def identity(e):
            card=e.get('doc_card') or {}
            return (card.get('source_id'),card.get('url'),e.get('body_sha256'),e.get('title'),
                    card.get('jurisdiction'),card.get('issued_at'),card.get('version_status'))
        known={identity(e) for e in existing}
        from .evidence_units import evidence_view as canonical_view,doc_card_sha256,digest
        new=[]
        cards=qa._public_sources(repair_question,candidates)
        for hit,card in zip(candidates,cards):
            row=canonical_view([hit])[0]
            if identity(row) in known:continue
            n=len(existing)+len(new)+1
            row['citation']=n;row['doc_card']['citation']=n
            row['doc_card_sha256']=doc_card_sha256(row['doc_card'])
            for p in row['passages']:p['citation']=n
            row['view_sha256']=digest(row['doc_card_sha256']+'\n'+'\n'.join(str(p['id'])+':'+p['sha256'] for p in row['passages']))
            row['metadata']={**card['metadata'],'external_effective_allowed':True}
            card.update(citation=f'资料{n}',citation_id=n)
            sources.append(card);new.append(row);known.add(identity(row))
        return new

    def rewrite(failed,frozen,existing,rejected,*,facet_registry=None,target_fids=None):
        value=repair_payload(payload,failed,facet_registry or facets,existing,frozen,
                             grounded.get('relation_review',{}).get('issues',[]))
        with model_stage('facet_regenerate'):
            rewritten=qa._call_model(model,STRUCTURED_ANSWER_PROMPT,json.dumps(value,ensure_ascii=False))
        with model_stage('facet_scope_review'):
            rewritten,local_review,local_gate=review_scope(qa,model,rewritten,existing,facets,value['question'])
        local_scope.append({'target_fids':value['target_fids'],'review':local_review,
                            'gate':local_gate,'missing_information':model_json(rewritten).get('missing_information') or []})
        return rewritten

    if any(r['status']!='full' for r in grounded['delivery']):
        grounded=recover_locally(grounded,evidence,facets,targeted_retrieve=retrieve,
            regenerate=rewrite,ground=lambda r,e,f:ground(qa,model,r,e,f),budget=permits)
        evidence=grounded.get('evidence',evidence)
    from .facet_delivery import output_labels,render_answer,publishable
    # All later publication decisions use the final atom set; no initial
    # coverage snapshot or earlier model "full" survives a local rewrite.
    grounded=enforce_relations(grounded,facets,evidence)
    audit['local_scope_reviews']=local_scope
    # A generator's genuine missing inputs cannot be erased by the delivery floor.
    scope_missing=(audit.get('scope_gate') or {}).get('kept')
    if scope_missing is None:
        try:scope_missing=model_json(raw).get('missing_information') or []
        except Exception:scope_missing=[]
    scope_missing=list(scope_missing)
    for local_review in local_scope:
        scope_missing.extend(m for m in local_review['missing_information'] if isinstance(m,str))
    scope_missing=list(dict.fromkeys(scope_missing))
    if enabled('MARINE_SCOPE_GATE'):
        from .scope_gate import reconcile_gap_resolution,gate_atoms
        all_issues=list((audit.get('scope_review') or {}).get('issues') or [])
        for record in local_scope:
            all_issues.extend(record['review'].get('issues') or [])
        if local_scope:
            # A rewritten answer is a new publication candidate. Review that
            # merged view rather than publishing an initial stale gap snapshot.
            merged=json.dumps({'answer':grounded['answer'],'missing_information':scope_missing,
                               'subquestion_coverage':grounded['delivery']},ensure_ascii=False)
            if getattr(qa,'coverage_review_enabled',False) and not permits('merged_scope_review'):
                merged_review={'verdict':'unavailable','issues':[],'error_code':'coverage_budget_exhausted'}
                from .scope_gate import gate_answer
                merged_gate=gate_answer(grounded['answer'],scope_missing,facets,[])
            else:
                with model_stage('merged_scope_review'):
                    _,merged_review,merged_gate=review_scope(qa,model,merged,evidence,facets,question)
            audit.update(merged_scope_review=merged_review,merged_scope_gate=merged_gate)
            all_issues.extend(merged_review.get('issues') or [])
            grounded=withhold_disputed_atoms(grounded,merged_review.get('issues') or [])
            if merged_gate:
                scope_missing=merged_gate['kept']
        atom_scope=gate_atoms(grounded['accepted_atoms'],facets,all_issues)
        audit['final_gap_atom_scope']={k:v for k,v in atom_scope.items() if k!='accepted_atoms'}
        grounded['accepted_atoms']=atom_scope['accepted_atoms']
        resolution=reconcile_gap_resolution(scope_missing,facets,grounded['accepted_atoms'])
        audit['gap_resolution']={k:v for k,v in resolution.items() if k!='accepted_atoms'}
        scope_missing=resolution['kept']
        grounded['accepted_atoms']=resolution['accepted_atoms']
    grounded=merge_tool_atoms(grounded,tool_atoms,facets)
    audit['tool_field_replacements']=grounded.get('tool_field_replacements',[])
    atoms=grounded['accepted_atoms']
    tool_resolution=resolve_tool_gaps(scope_missing,facets,atoms)
    scope_missing=tool_resolution['kept']
    audit['tool_gap_resolution']=tool_resolution
    resolved_text={row['text'] for row in tool_resolution['resolved']}
    if resolved_text:
        grounded['accepted_atoms']=[a for a in atoms if a.get('type')!='gap' or
            str(a.get('text','')).strip('。；;') not in resolved_text]
        atoms=grounded['accepted_atoms']
    rows=evaluate_delivery(facets,atoms)
    grounded.update(delivery=rows,publishable=publishable(facets,rows),
                    evidence_quotes=[ref for a in atoms for ref in a.get('evidence_quotes',[])])
    grounded=enforce_relations(grounded,facets,evidence)
    atoms=grounded['accepted_atoms'];rows=grounded['delivery']
    missing=[f['question']+'：仍缺少'+'、'.join(output_labels(r['missing_outputs']))
             for f,r in zip(facets,rows) if r['status']!='full']
    missing=list(dict.fromkeys(missing+[m for m in scope_missing if isinstance(m,str)]))
    complete=bool(rows) and all(r['status']=='full' for r in rows) and grounded['publishable'] and not missing
    from .protected_tool_delivery import tool_data_card,tool_display_atoms
    data_card=tool_data_card(atoms)
    if data_card:
        display_atoms,display_audit=tool_display_atoms(atoms,facets)
        audit['tool_display_deduplication']=display_audit
    else:display_atoms=atoms
    answer=render_answer(facets,display_atoms) if complete else render_with_gaps(facets,display_atoms,evidence,rows=rows)
    if data_card:answer=data_card+('\n\n'+answer if answer else '')
    # A genuinely missing input remains visible even when all prose slots were
    # filled. Typed gap atoms may already display it; avoid repeating those.
    visible=[m for m in scope_missing if isinstance(m,str) and m and m not in answer]
    if visible:answer+='\n\n仍需核实：'+'；'.join(visible)
    business_error=None
    try:validate_business_delivery(answer,meta,_refs(answer))
    except ValueError as exc:
        business_error=str(exc);complete=False
        answer+='\n\n本次回答尚未完整交付程序计算结果，数值结论暂不作为完整业务答复。'
    if boundary_reason:
        complete=False;answer+='\n\n'+boundary_reason
    final_review=audit.get('merged_scope_review') or {}
    latest_review=final_review or (audit.get('scope_review') or {})
    unresolved=[i for i in latest_review.get('issues',[]) if i.get('kind') in {'unsupported_claim','contradiction'}]
    review_unavailable=bool(local_scope and getattr(qa,'coverage_review_enabled',False) and final_review.get('verdict')=='unavailable')
    if unresolved or review_unavailable:
        complete=False
        withholding=grounded.get('disputed_atom_withholding') or audit.get('initial_disputed_atom_withholding') or {}
        note=(('部分内容的复核意见不一致，已暂缓发布争议内容并保留其他已核对信息。'
               if withholding.get('withheld_atom_ids') else
               '部分内容的复核意见不一致，争议范围尚待核对，当前回答不作为完整结论。') if unresolved
              else '局部补答后的覆盖复核尚未完成，已保留核对过的内容。')
        answer+='\n\n'+note;missing.append(note)
    grounded.update(answer=answer,answerability='full' if complete else 'partial',
                    render_mode='grounded' if complete else 'with_gaps')
    audit.update({k:grounded.get(k) for k in ('method','delivery','decisions','publishable',
        'render_mode','draft_sha256','recovery_trace','frozen_atom_sha256','relation_review','judge_consistency',
        'disputed_atom_withholding')})
    audit.update(business_validation_error=business_error,unresolved_final_review=unresolved)
    status=grounded['answerability']
    citations=_refs(answer)
    decoded={'evidence_quotes':grounded['evidence_quotes']}
    used=_sources_with_selected_passages([s for s in sources if s['citation_id'] in citations],evidence,decoded)
    event('typed_publication',publishable=grounded['publishable'],delivery=grounded['delivery'],
          decisions=grounded.get('decisions'),relation_review=grounded.get('relation_review'))
    return {'answer':answer,'rag_sources':[s for s in used if s.get('source_type')!='public_web'],
            'web_sources':[s for s in used if s.get('source_type')=='public_web'],
            'answer_meta':{**meta,'used_mode':'llm' if citations else 'no_evidence','generation_used':True,
                'model':model,'answerability':status,'subquestions':facets,'facet_contract':facets,
                'missing_information':missing,'subquestion_coverage':grounded['delivery'],
                'evidence_coverage':payload.get('evidence_coverage'),'phase2':audit}}
