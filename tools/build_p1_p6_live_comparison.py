"""Build a local before/after report from exactly 24 recorded QA transactions.

No model, server, reader, or network call is made. Raw prompts and source bodies
stay in private cassettes. Old judge outputs remain explicitly old opinions.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from html import escape
import json
from pathlib import Path
import re
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from marine_kb.model_json import model_json
from marine_kb.replay import ModelCassette, _unwire
from tools.build_p0_baseline_report import (
    CN_ZONE, EXPECTED_IDS, ReportInvariantError, _SECRET, file_sha, iso,
    read_json, require, safe_url, text_sha, token_summary,
)

DEFAULT_BEFORE = ROOT/'tmp/replay/p0_20261020_005001_b167cdca'


def _cards(rows):
    output=[]
    for row in rows or []:
        if not isinstance(row,dict): continue
        card=row.get('doc_card') or {}
        output.append({'citation_id':row.get('citation_id'),'citation':row.get('citation'),
            'title':row.get('title') or row.get('filename'),'source_type':row.get('source_type'),
            'url':safe_url(row.get('url')),
            'supporting_passage_ids':[p.get('passage_id') for p in row.get('supporting_passages') or [] if isinstance(p,dict)],
            'doc_card':{k:card.get(k) for k in ('title','issuer','doc_no','published_at','issued_at',
                'effective_from','effective_to','jurisdiction','version_status','retrieved_at','body_sha256',
                'acquisition_method','reference_recheck_required')} if isinstance(card,dict) and card else None})
    return output


def _model_rows(records, calls):
    models=[]
    for record in records:
        if record['kind']!='model': continue
        request=_unwire(record['request'])
        args=request.get('args',[]) if isinstance(request,dict) else []
        payload=None
        if len(args)>=3:
            try: payload=json.loads(args[2])
            except (ValueError,TypeError): pass
        response=None
        usage=None
        if record.get('outcome')=='return':
            returned=_unwire(record['response'])
            if isinstance(returned,tuple):
                returned,usage=returned[0],returned[1] if len(returned)>1 else None
            try: response=model_json(returned)
            except (ValueError,TypeError): pass
        kwargs=request.get('kwargs') or {} if isinstance(request,dict) else {}
        options={k:kwargs.get(k) for k in ('temperature','timeout','max_retries','max_tokens','return_usage') if k in kwargs}
        models.append({'seq':record['seq'],'model_alias':args[0] if args and isinstance(args[0],str) else None,
            'options':options,'stage':calls[len(models)].get('stage') if len(models)<len(calls) else 'unavailable',
            'payload':payload if isinstance(payload,dict) else {},
            'response':response if isinstance(response,dict) else {},
            'usage':usage,'error':record.get('error')})
    return models


def _question_rows(parts):
    return [{k:p.get(k) for k in ('id','fid','question','kind','objects','places','place_role','required_outputs','allowed_gaps')}
            if isinstance(p,dict) else {'question':str(p)} for p in parts or []]


def _recorded_evidence_view(model):
    payload=model['payload']
    rows=payload.get('evidence')
    key='evidence'
    if not isinstance(rows,list):rows=payload.get('cited_passages');key='cited_passages'
    if not isinstance(rows,list):return None
    sources=[]
    for number,row in enumerate(rows,1):
        if not isinstance(row,dict):continue
        card=row.get('doc_card')
        computed=text_sha(json.dumps(card,ensure_ascii=False,sort_keys=True,separators=(',',':'))) if isinstance(card,dict) else None
        passages=[]
        for p in row.get('passages') or []:
            if not isinstance(p,dict):continue
            actual=text_sha(str(p.get('text') or ''))
            passages.append({'id':p.get('id',p.get('passage_id')),'sha256':actual,
                'declared_hash_matches':p.get('sha256')==actual if p.get('sha256') else None})
        sources.append({'citation':row.get('citation',number),'body_sha256':row.get('body_sha256'),
            'doc_card_sha256':computed,'declared_doc_card_hash_matches':row.get('doc_card_sha256')==computed if row.get('doc_card_sha256') else None,
            'passages':passages,'full_body_field_present':'text' in row,
            'full_body_field_characters':len(str(row.get('text') or ''))})
    return {'seq':model['seq'],'stage':model['stage'],'payload_key':key,'sources':sources,
            'view_sha256':text_sha(json.dumps(sources,ensure_ascii=False,sort_keys=True,separators=(',',':'))),
            'provenance':'hashes_computed_from_actual_recorded_request_only_no_raw_body_export'}


def _analysis(models, reply):
    diagnostic=reply.get('evidence_diagnostics') or {}
    phase2=diagnostic.get('phase2') or {}
    drafts=[]; judges=[]; coverage=[]; typed_inputs=[]
    for model in models:
        payload,value=model['payload'],model['response']
        if isinstance(value.get('answer'),str) and payload.get('evidence') is not None:
            drafts.append({'seq':model['seq'],'stage':model['stage'],'answer_sha256':text_sha(value['answer']),
                'answer_characters':len(value['answer']),
                'requested_subquestions':_question_rows(payload.get('facet_contract') or payload.get('subquestions')),
                'model_reported_answerability':value.get('answerability'),
                'model_reported_subquestion_coverage':value.get('subquestion_coverage') or [],
                'provenance':'actual_recorded_generation_response_not_quality_verdict'})
        if isinstance(value.get('issues'),list) and 'verdict' in value:
            coverage.append({'seq':model['seq'],'stage':model['stage'],'verdict':value['verdict'],
                             'issues':value['issues'],'provenance':'actual_recorded_critic_opinion_not_program_fact'})
        if isinstance(payload.get('claims'),list) and isinstance(value.get('claims'),list):
            claims={str(c.get('id')):c for c in payload['claims'] if isinstance(c,dict)}
            judges.append({'seq':model['seq'],'provenance':'actual_recorded_legacy_judge_opinion_not_v2_judgment',
                'decisions':[{'id':v.get('id'),'status':v.get('status'),'reason':v.get('reason'),
                    'claim_sha256':text_sha(str(claims.get(str(v.get('id')),{}).get('text') or ''))}
                    for v in value['claims'] if isinstance(v,dict)]})
        if isinstance(payload.get('atoms'),list):
            typed_inputs.append({'seq':model['seq'],'draft_sha256':payload.get('draft_sha256'),
                                 'atoms':payload['atoms']})
    decisions=phase2.get('decisions') or []
    decision_map={d.get('aid'):d for d in decisions if isinstance(d,dict)}
    matched=[row for row in typed_inputs if row['draft_sha256'] and row['draft_sha256']==phase2.get('draft_sha256')]
    composites=[]
    # Only bind an atom to a recorded program decision when its draft hash is
    # unambiguous. Reused local atom IDs cannot identify a different draft.
    if len(matched)==1:
        groups={}
        for atom in matched[0]['atoms']:
            if isinstance(atom,dict): groups.setdefault(atom.get('claim_id'),[]).append(atom)
        for claim_id,atoms in groups.items():
            if len(atoms)<2: continue
            resolved=[(a,decision_map.get(a.get('aid'))) for a in atoms]
            composites.append({'draft_seq':matched[0]['seq'],'claim_id':claim_id,
                'kept_atoms':sum(d is not None and d.get('retained') is True for _,d in resolved),
                'dropped_atoms':sum(d is not None and d.get('retained') is False for _,d in resolved),
                'atoms':[{'aid':a.get('aid'),'text':str(a.get('text') or '')[:800],
                          'facet_ids':a.get('facet_ids') or [],'retained':d.get('retained') if d else None,
                          'drop_rule':d.get('drop_rule') if d else None} for a,d in resolved],
                'provenance':'actual_program_decisions_matched_to_exact_recorded_draft'})
    scope=phase2.get('scope_gate') or diagnostic.get('scope_gate') or {}
    typed_delivery=phase2.get('delivery')
    delivery=typed_delivery if isinstance(typed_delivery,list) else reply.get('subquestion_coverage') or []
    reviewer_unasked=[{'seq':r['seq'],**issue} for r in coverage for issue in r['issues']
                     if isinstance(issue,dict) and issue.get('kind')=='unasked_gap']
    return {'drafts':drafts,'program_decisions':decisions,'composite_clause_outcomes':composites,
        'recorded_evidence_views':[view for model in models if (view:=_recorded_evidence_view(model)) is not None],
        'composite_analysis_state':'matched_program_draft' if len(matched)==1 else 'unavailable_or_ambiguous',
        'subquestion_delivery':delivery,
        'subquestion_delivery_provenance':'typed_program_minimum_delivery_not_fact_accuracy' if typed_delivery is not None else 'legacy_model_self_report_unverified',
        'unasked_gaps':{'reviewer_opinions':reviewer_unasked,'program_removed':scope.get('removed') or [],
            'program_kept':scope.get('kept') or [],'program_decisions':scope.get('decisions') or [],
            'state':'program_gate_recorded' if scope else 'critic_opinions_only_unverified'},
        'recovery_trace':phase2.get('recovery_trace') or [],'relation_review':phase2.get('relation_review'),
        'judge_consistency':phase2.get('judge_consistency'),
        'program_publishable':phase2.get('publishable'),'program_render_mode':phase2.get('render_mode'),
        'legacy_judge_opinions':judges,'recorded_coverage_opinions':coverage,
        'quality_status':'requires_codex_readback_not_independent_expert_acceptance_no_automatic_quality_score'}


def inspect_case(run_dir, case):
    key=case['case_id']
    result=read_json(run_dir/(key+'.result.json'))
    started=read_json(run_dir/(key+'.started.json'))
    require(result.get('case')==case,'result_plan_mismatch:'+key)
    require(started.get('case_id')==key and started.get('attempt')==1 and
            started.get('automatic_transaction_retry') is False,'transaction_attempt_mismatch:'+key)
    reply=result.get('reply')
    require(isinstance(reply,dict),'http_reply_not_object:'+key)
    status=result.get('http_status')
    require(type(status) is int,'http_status_missing:'+key)
    answer=reply.get('answer') or ''
    require(isinstance(answer,str),'answer_not_string:'+key)
    if result.get('answer_sha256'):
        require(result['answer_sha256']==text_sha(answer),'recorded_answer_hash_mismatch:'+key)
    cassette_path=run_dir/(key+'.cassette.jsonl')
    cassette=ModelCassette(cassette_path,mode='replay')
    try:
        records=cassette._records
        summary=cassette.summary()
        require(summary['consumed']==0,'report_consumed_recording:'+key)
        trace=reply.get('trace') or {}
        calls=trace.get('model_calls') or []
        models=_model_rows(records,calls)
        # Missing trace must not turn failed/model usage into zero consumption.
        usage_calls=calls
        if len(calls)!=len(models):
            usage_calls=[]
            for model in models:
                usage=model.get('usage') if isinstance(model.get('usage'),dict) else {}
                usage_calls.append({**usage,'error_code':(model.get('error') or {}).get('code')})
        analysis=_analysis(models,reply)
        configurations=list({json.dumps({'model_alias':m['model_alias'],'options':m['options']},sort_keys=True):
                             {'model_alias':m['model_alias'],'options':m['options']} for m in models}.values())
        cassette_meta={'sha256':file_sha(cassette_path),'integrity_verified':True,
            'complete_footer_verified':True,'interactions':len(records),
            'calls_by_kind':dict(Counter(r['kind'] for r in records)),
            'model_calls':len(models),'public_trace_count_matches':len(calls)==len(models),
            'dependency_errors':[{'seq':r['seq'],'kind':r['kind'],'type':r['error'].get('type'),
                                  'code':r['error'].get('code')} for r in records if r.get('outcome')=='error'],
            'consumed':0,'raw_prompts_or_reader_bodies_exported':False}
    finally: cassette.close(complete=False)
    start,end=iso(started['started_at']),iso(result['completed_at'])
    require(end>=start,'negative_case_latency:'+key)
    return {'id':key,'question':case['question'],'http_status':status,'http_failure':status>=400 or status<100,
        'http_error':reply.get('error'),'answer':answer,'answer_sha256':text_sha(answer),
        'answer_characters':len(answer),'system_answerability':reply.get('answerability'),
        'answer_kind':reply.get('answer_kind'),'missing_information':reply.get('missing_information') or [],
        'sources':_cards(reply.get('sources')),'validation':reply.get('validation') or {},
        'started_at':started['started_at'],'completed_at':result['completed_at'],
        'latency_seconds':round((end-start).total_seconds(),3),
        'token_usage':token_summary(usage_calls),'model_calls':len(models),
        'model_call_trace':calls,'calls_by_stage':dict(Counter(m['stage'] for m in models)),
        'recorded_model_configurations':configurations,
        'answer_budget':(reply.get('evidence_diagnostics') or {}).get('answer_budget'),
        'program_errors':{
            'phase2_error_type':((reply.get('evidence_diagnostics') or {}).get('phase2') or {}).get('error_type'),
            'phase2_error_code':((reply.get('evidence_diagnostics') or {}).get('phase2') or {}).get('error_code'),
            'stage_failures':[e for e in trace.get('events') or [] if isinstance(e,dict) and e.get('event')=='stage_failure']},
        'diagnostics':analysis,'cassette':cassette_meta,
        'artifact_hashes':{'result':file_sha(run_dir/(key+'.result.json')),
                           'started':file_sha(run_dir/(key+'.started.json'))}}


def inspect_run(run_dir, *, current=False):
    run_dir=Path(run_dir).resolve()
    plan=read_json(run_dir/'run_plan.json');complete=read_json(run_dir/'run_completed.json')
    cases=plan.get('cases')
    require(isinstance(cases,list) and tuple(c.get('case_id') for c in cases)==EXPECTED_IDS,'expected_exact_24_case_ids')
    require((complete.get('qa_transactions_attempted',complete.get('qa_transactions')))==24 and
            complete.get('automatic_transaction_retries')==0,'run_incomplete_or_retried')
    for suffix in ('.started.json','.result.json','.cassette.jsonl'):
        require({p.name[:-len(suffix)] for p in run_dir.glob('*'+suffix)}==set(EXPECTED_IDS),
                'missing_or_extra_artifacts:'+suffix)
    rows=[inspect_case(run_dir,case) for case in cases]
    source_snapshot=run_dir/'run_source_snapshot.json'
    snapshot=read_json(source_snapshot) if source_snapshot.is_file() else {}
    stability_path=run_dir/'source_stability.json'
    stability=read_json(stability_path) if stability_path.is_file() else complete.get('source_stability')
    hashes=snapshot.get('source_sha256') or {}
    checked=[];mismatched=[];unavailable=[]
    for name,wanted in hashes.items():
        path=(ROOT/name).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file(): unavailable.append(name);continue
        checked.append(name)
        if file_sha(path)!=wanted:mismatched.append(name)
    captured_checked=[];captured_mismatch=[];captured_missing=[]
    captured_root=(run_dir/'source_snapshot').resolve()
    if captured_root.is_dir():
        for name,wanted in hashes.items():
            path=(captured_root/name).resolve()
            if not path.is_relative_to(captured_root) or not path.is_file():captured_missing.append(name);continue
            captured_checked.append(name)
            if file_sha(path)!=wanted:captured_mismatch.append(name)
    after_manifest=run_dir/'run_source_snapshot.after.json'
    before_after_hash_equal=None
    if after_manifest.is_file():
        before_after_hash_equal=hashes==read_json(after_manifest).get('source_sha256')
    flag_names={'MARINE_FACET_CONTRACT','MARINE_EVIDENCE_UNITS','MARINE_SCOPE_GATE','MARINE_CONNECTOR_V2',
                'MARINE_TYPED_GROUNDING','MARINE_RELATION_GUARD','MARINE_FORECAST_WORKER','MARINE_WARNING_WORKER','MARINE_TRACE'}
    flags={k:v for k,v in (plan.get('flags') or snapshot.get('flags') or {}).items() if k in flag_names}
    receipt=None
    for candidate in (run_dir/'preview_status.json',run_dir/'preview_receipt.json'):
        if not candidate.is_file():continue
        value=read_json(candidate)
        status=value.get('status') or {}
        receipt={'file_sha256':file_sha(candidate),'url':safe_url(value.get('url')),
                 'phase2':status.get('phase2'),'runtime_source_sha256':status.get('runtime_source_sha256'),
                 'sha256_matches_current_files':value.get('sha256_matches_current_files'),
                 'no_qa_submitted':value.get('no_qa_submitted'),'test_transport':value.get('test_transport'),
                 'scope_note':'预览状态是另一独立服务的已保存核查；本次问答通过独立Flask测试客户端执行，并非请求8770端口。'}
        break
    return {'run_id':run_dir.name,'plan_sha256':file_sha(run_dir/'run_plan.json'),
        'completion_sha256':file_sha(run_dir/'run_completed.json'),'cases':cases,'items':rows,
        'runtime':{'flags':flags,'snapshot_sha256':file_sha(source_snapshot) if snapshot else None,
            'captured_source_hashes':hashes,'before_after_source_stability':stability,
            'before_after_manifest_hash_maps_equal':before_after_hash_equal,
            'captured_snapshot_bytes_checked':len(captured_checked),'captured_snapshot_mismatches':captured_mismatch,
            'captured_snapshot_missing':captured_missing,
            'captured_snapshot_bytes_verified':not captured_mismatch and not captured_missing if captured_checked else None,
            'saved_preview_receipt':receipt,
            'current_files_checked':len(checked),'current_files_changed':mismatched,'current_files_unavailable':unavailable,
            'current_files_match_capture':not mismatched and not unavailable if hashes else None,
            'scope_note':'新版记录前后哈希及当下文件另列；旧版文件在改造后变化是预期现象，不据此否定旧录制。'},
        'current_run':current}


def _summary(run):
    rows=run['items'];calls=[call for row in rows for call in row['model_call_trace']]
    token_fields=('prompt_tokens','completion_tokens','total_tokens')
    usage={'calls':sum(r['model_calls'] for r in rows),
           'usage_reported_calls':sum(r['token_usage']['usage_reported_calls'] for r in rows),
           'unknown_usage_call_count':sum(r['token_usage']['unknown_usage_call_count'] for r in rows)}
    for name in token_fields:
        usage['known_'+name]=sum(r['token_usage']['known_'+name] for r in rows)
        usage[name]=usage['known_'+name] if all(r['token_usage'][name] is not None for r in rows) else None
    def label(value):
        return value if isinstance(value,str) else 'redacted_or_unavailable' if value else None
    phase_errors=[label(row['program_errors'].get('phase2_error_code') or row['program_errors'].get('phase2_error_type')) for row in rows]
    stage_errors=[label((event.get('fields') or {}).get('error_code') or (event.get('fields') or {}).get('error_type'))
                  for row in rows for event in row['program_errors']['stage_failures']]
    recovery_errors=[str(step['error']) for row in rows for step in row['diagnostics']['recovery_trace'] if step.get('error')]
    dependency_errors=[e for row in rows for e in row['cassette']['dependency_errors'] if e.get('kind')=='model']
    budget_rows=[row for row in rows if (row.get('answer_budget') or {}).get('exhausted')]
    def case_errors(row):
        return ([e.get('code') for e in row['cassette']['dependency_errors'] if e.get('kind')=='model']+
                [row['program_errors'].get('phase2_error_code'),row['program_errors'].get('phase2_error_type')]+
                [step.get('error') for step in row['diagnostics']['recovery_trace']]+
                [(e.get('fields') or {}).get('error_code') for e in row['program_errors']['stage_failures']])
    error_audit={'http_failure_transactions':sum(row['http_failure'] for row in rows),
        'model_dependency_error_calls':len(dependency_errors),
        'model_dependency_error_types':dict(Counter(str(e.get('type')) for e in dependency_errors)),
        'model_error_transactions':[row['id'] for row in rows if any(e.get('kind')=='model' for e in row['cassette']['dependency_errors'])],
        'phase2_caught_error_counts':dict(Counter(code for code in phase_errors if code)),
        'stage_failure_error_counts':dict(Counter(code for code in stage_errors if code)),
        'recovery_step_error_counts':dict(Counter(recovery_errors)),
        'budget_explicitly_exhausted_transactions':[row['id'] for row in budget_rows],
        'budget_error_code_transactions':[row['id'] for row in rows if any('budget' in str(e).lower() for e in case_errors(row) if e)],
        'typed_incomplete_error_transactions':[row['id'] for row in rows if any('incomplete' in str(e).lower() and ('typed' in str(e).lower() or 'review' in str(e).lower()) for e in case_errors(row) if e)],
        'http_2xx_with_recorded_internal_error':[row['id'] for row in rows if 200<=row['http_status']<300 and any(case_errors(row))],
        'program_publishable_false_transactions':[row['id'] for row in rows if row['diagnostics']['program_publishable'] is False],
        'scope_note':'HTTP 200仅表示接口返回。模型异常、被捕获的校验错误、恢复错误和预算停止分列；partial无异常也不能当作完整正确回答。'}
    latencies=[r['latency_seconds'] for r in rows]
    return {'transactions':len(rows),'http_status_counts':dict(Counter(str(r['http_status']) for r in rows)),
        'nonempty_answers':sum(bool(r['answer'].strip()) for r in rows),
        'model_calls':sum(r['model_calls'] for r in rows),'token_usage':usage,
        'case_latency_seconds':{r['id']:r['latency_seconds'] for r in rows},
        'total_case_seconds':round(sum(r['latency_seconds'] for r in rows),3),
        'latency_p50_seconds':round(median(latencies),3) if latencies else None,
        'latency_max_seconds':max(latencies) if latencies else None,
        'wall_time_over_60_seconds':[{'id':r['id'],'latency_seconds':r['latency_seconds']} for r in rows if r['latency_seconds']>60],
        'budget_time_note':'60秒是阻止后续步骤/约束在途请求的预算，非严格的整题墙钟上限；实际HTTP事务耗时如实列出。',
        'error_audit':error_audit,
        'system_labels_not_correctness':dict(Counter(str(r['system_answerability']) for r in rows)),
        'calls_by_stage':dict(Counter(str(c.get('stage')) for c in calls)),
        'recorded_model_aliases':sorted({str(config['model_alias']) for row in rows
                                        for config in row['recorded_model_configurations']}),
        'trace_cassette_counts_all_match':all(r['cassette']['public_trace_count_matches'] for r in rows)}


def build_report(before_dir,run_dir,human_review=None,code_diagnosis=None):
    before=inspect_run(before_dir);after=inspect_run(run_dir,current=True)
    old={row['id']:row for row in before['items']}
    current={row['id']:row for row in after['items']}
    require(all(row['question']==old[row['id']]['question'] for row in after['items']),'old_new_questions_differ')
    reviews={};review_provenance=None
    if human_review:
        paths=human_review if isinstance(human_review,(tuple,list)) else [human_review]
        files=[]
        for path in paths:
            review=read_json(path)
            entries=review.get('cases',review.get('items',[]))
            require(isinstance(entries,list),'human_review_cases_invalid')
            for entry in entries:
                key=entry.get('case_id',entry.get('id'))
                require(key in old and key not in reviews,'human_review_id_invalid')
                require(not entry.get('question') or entry['question']==old[key]['question'],'human_review_question_mismatch')
                for hash_key in ('answer_sha256','actual_answer_sha256'):
                    require(not entry.get(hash_key) or entry[hash_key]==current[key]['answer_sha256'],
                            'codex_review_answer_hash_mismatch:'+str(key))
                require(entry.get('http_status') is None or entry['http_status']==current[key]['http_status'],
                        'codex_review_http_status_mismatch:'+str(key))
                require(not entry.get('run_id') or entry['run_id']==after['run_id'],
                        'codex_review_run_mismatch:'+str(key))
                reviews[key]={k:entry.get(k) for k in ('case_id','question','review_status','subquestion_findings',
                    'retention_findings','unasked_gap_findings','summary','overall_outcome','review_method',
                    'before_after_assessment','limits','answer_sha256','actual_answer_sha256','http_status','run_id') if k in entry}
            files.append({'file':Path(path).name,'sha256':file_sha(path),'cases':len(entries),
                          'method':review.get('method'),'limits':review.get('limits')})
        review_provenance={'files':files,'reviewed_cases':len(reviews),'label':'Codex逐项审读（非独立专家验收）'}
    items=[{'id':new['id'],'base_id':new['id'].split('_extra_')[0],'question':new['question'],
            'before':old[new['id']],'after':new,'independent_review':reviews.get(new['id']),
            'comparison':{'answer_unchanged':new['answer_sha256']==old[new['id']]['answer_sha256'],
                'model_call_delta':new['model_calls']-old[new['id']]['model_calls'],
                'latency_delta_seconds':round(new['latency_seconds']-old[new['id']]['latency_seconds'],3),
                'quality_conclusion':'codex_readback_supplied' if new['id'] in reviews else 'unverified'}}
           for new in after['items']]
    report={'schema_version':1,'generated_at':datetime.now(CN_ZONE).isoformat(timespec='seconds'),
        'title':'P1–P6：24次真实问答改造前后对照','before_run':before['run_id'],'after_run':after['run_id'],
        'scope':'同一12题各1次，G3、A2、C3、M1各追加3次，共24次独立问答；保留所有失败事务，不补跑、不选择最佳样本。',
        'limitations':['系统标签、引用数、HTTP成功和程序最低交付检查均不代表事实正确率或专家验收。',
            '审读是Codex逐项审读（非人类审核、专家验收或独立盲评）；保留可回查证据及不确定结论。',
            '这是两批真实联网问答的非受控前后对照，并非冻结同一网页/证据的因果A/B试验；问题相同，检索结果、官方网页、缓存、运行时刻和模型采样均可能改变。不能把差异全部归因代码。',
            '模型配置只报告录制边界可核对的DeepSeek模型别名及请求参数；相同别名不证明提供方权重或服务版本完全相同。',
            '旧模型的逐句、覆盖判定仅以旧意见展示，没有转换成新版typed-v2判定。',
            '复合句只在程序决策可唯一绑定到录制草稿哈希时分析；缺少或歧义绑定标为不可核定。',
            'token按实际usage统计；未知保持null并列已知小计，不估算调用费用。',
            '本工具没有模型、网络、生产服务调用；原始提示词、完整网页正文和私有cassette没有复制到报告。'],
        'expert_accepted':False,'summaries':{'before':_summary(before),'after':_summary(after)},
        'runtime':{'before':before['runtime'],'after':after['runtime']},
        'integrity':{'same_24_questions':True,'all_cassette_integrity_verified':True,
            'all_failed_transactions_preserved':True,'all_cassettes_consumed':0,
            'report_model_calls':0,'report_network_calls':0,
            'before_plan_sha256':before['plan_sha256'],'after_plan_sha256':after['plan_sha256']},
        'independent_review_provenance':review_provenance,'items':items}
    report['codex_outcome_counts']={
        'label':'Codex逐项审读结论计数（非正确率或专家验收）',
        'counts':dict(Counter((r['independent_review'] or {}).get('overall_outcome') or 'unverifiable' for r in items))}
    if code_diagnosis:
        diagnosis=read_json(code_diagnosis)
        report['code_diagnosis']={'file':Path(code_diagnosis).name,'sha256':file_sha(code_diagnosis),
            'label':'Codex代码与录制追踪诊断（非独立专家验收）',
            **{k:diagnosis.get(k) for k in ('review_mode','no_model_calls','runtime_modified','reviewed_cases',
                'source_matches_live_before_snapshot','source_sha256','code_references','findings')}}
    report['repeat_groups']=[{'base_id':base,'case_ids':[r['id'] for r in items if r['base_id']==base],
        'before_answer_hashes':{r['id']:r['before']['answer_sha256'] for r in items if r['base_id']==base},
        'after_answer_hashes':{r['id']:r['after']['answer_sha256'] for r in items if r['base_id']==base},
        'note':'逐次保留，不选最佳回答；哈希差异本身不能证明改善或模型随机性原因。'} for base in ('G3','A2','C3','M1')]
    false_full=[row['id'] for row in items if row['after']['system_answerability']=='full' and
                any(p.get('delivery')=='missing' for p in (row.get('independent_review') or {}).get('subquestion_findings',[]))]
    report['publication_conclusion']={
        'status':'needs_revision' if false_full else 'not_expert_accepted',
        'codex_confirmed_full_with_missing_subquestion':false_full,
        'note':('本轮仍未达到稳定完整回答目标。系统full标签里仍有关键子问未回答的实测反例；其他记录不能因此自动判为正确。'
                if false_full else '尚未完成业务正确性验收；没有此类已核定反例不代表所有回答正确。'),
        'scope':'只汇总已绑定实际回答的Codex审读；不是专家验收或总体正确率。'}
    require(not _SECRET.search(json.dumps(report,ensure_ascii=False)),'credential_pattern_in_report')
    return report


def html_report(report):
    def pretty(value):return '<pre>'+escape(json.dumps(value,ensure_ascii=False,indent=2))+'</pre>'
    out=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>'+escape(report['title'])+'</title>',
         '<style>body{max-width:1250px;margin:30px auto;padding:0 22px;font:16px/1.7 system-ui;color:#153645}h1,h2{color:#087e8b}table{border-collapse:collapse;width:100%}td,th{border:1px solid #bdd2d6;padding:9px;vertical-align:top}pre,.answer{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f7f8;padding:15px}.compare{display:grid;grid-template-columns:1fr 1fr;gap:15px}.notice{background:#fff4db;padding:18px}summary{cursor:pointer;font-weight:bold}details{margin:16px 0}@media(max-width:850px){.compare{grid-template-columns:1fr}}</style>',
         '<h1>'+escape(report['title'])+'</h1><p class="notice">'+escape(report['scope'])+'</p>',
         '<p class="notice"><strong>本轮结论：'+escape(report.get('publication_conclusion',{}).get('note','尚未完成质量核验。'))+'</strong><br>已确认full但漏答的事务：'+escape('、'.join(report.get('publication_conclusion',{}).get('codex_confirmed_full_with_missing_subquestion',[])) or '尚无已核定记录')+'。</p>',
         '<ul>'+''.join('<li>'+escape(v)+'</li>' for v in report['limitations'])+'</ul>',
         '<h2>运行统计与真实token</h2>'+pretty(report['summaries']),
         '<details><summary>运行开关、源码哈希与录制完整性</summary>'+pretty(report['runtime'])+pretty(report['integrity'])+'</details>',
         '<p>Codex逐项审读（非独立专家验收）：'+escape(str(report['independent_review_provenance'] or '尚未合并，以下改进质量结论待审读。'))+'</p>',
         '<table><tr><th>事务</th><th>HTTP 前→后</th><th>调用 前→后</th><th>系统标签 前→后（非正确率）</th><th>Codex审读结论</th></tr>']
    outcomes={'improved':'改善','mixed':'有改善也有退化','regressed':'退化','unverifiable':'未核定','unchanged':'无明显变化'}
    for row in report['items']:
        b,a=row['before'],row['after'];review=row['independent_review'] or {}
        summary=review.get('summary') or '尚未合并逐项审读。'
        if not isinstance(summary,str):summary=json.dumps(summary,ensure_ascii=False)
        summary=re.sub(r'\s+',' ',summary)
        conclusion=outcomes.get(review.get('overall_outcome'),'未核定')+'：'+summary[:170]+('…' if len(summary)>170 else '')
        cells=['<a href="#'+row['id']+'">'+row['id']+'</a>',escape(f'{b["http_status"]} → {a["http_status"]}'),
               f'{b["model_calls"]} → {a["model_calls"]}',escape(f'{b["system_answerability"]} → {a["system_answerability"]}'),
               escape(conclusion)]
        out.append('<tr>'+''.join('<td>'+c+'</td>' for c in cells)+'</tr>')
    out.append('</table>')
    out.append('<h2>逐项审读概览</h2>'+pretty(report.get('codex_outcome_counts')))
    if report.get('code_diagnosis'):
        diagnosis=report['code_diagnosis']
        out.append('<h2>仍需解决的通用问题</h2><p>以下为冻结版本的代码与真实录制追踪；运行期间没有修改实现。</p>')
        for finding in diagnosis.get('findings') or []:
            if not isinstance(finding,dict):continue
            out.append('<h3>'+escape(str(finding.get('id') or '')+' '+str(finding.get('title') or ''))+'</h3><p>'+escape(str(finding.get('mechanism') or ''))+'</p>')
        out.append('<details><summary>通用原因、代码位置及证据</summary>'+pretty(diagnosis)+'</details>')
    for row in report['items']:
        b,a=row['before'],row['after']
        out.extend(['<section id="'+row['id']+'"><h2>'+escape(row['id']+' '+row['question'])+'</h2>',
            '<div class="compare"><div><h3>改造前实际回答</h3><p>HTTP '+str(b['http_status'])+'</p><div class="answer">'+escape(b['answer'] or '[无最终回答]')+'</div></div>',
            '<div><h3>新版实际回答</h3><p>HTTP '+str(a['http_status'])+'</p><div class="answer">'+escape(a['answer'] or '[无最终回答]')+'</div></div></div>',
            '<details open><summary>Codex逐项审读（非独立专家验收）</summary>'+pretty(row['independent_review'] or {'state':'unverified','note':'尚未合并Codex逐项审读，不根据标签或引用数推断已改进。'})+'</details>',
            '<details><summary>子问归属、复合句保留/删除、额外缺项程序诊断</summary><h3>改造前</h3>'+pretty(b['diagnostics'])+'<h3>改造后</h3>'+pretty(a['diagnostics'])+'</details>',
            '<details><summary>实际来源、缺项、调用、token及失败</summary>'+pretty({'before':{k:b[k] for k in ('sources','missing_information','token_usage','latency_seconds','model_call_trace','recorded_model_configurations','answer_budget','cassette','validation','http_error')},
                'after':{k:a[k] for k in ('sources','missing_information','token_usage','latency_seconds','model_call_trace','recorded_model_configurations','answer_budget','program_errors','cassette','validation','http_error')}})+'</details></section>'])
    out.append('<p>报告生成于 '+escape(report['generated_at'])+'；原始录制只存本机。</p></html>')
    return '\n'.join(out)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before-dir',type=Path,default=DEFAULT_BEFORE)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--human-review',type=Path,action='append')
    parser.add_argument('--code-diagnosis',type=Path)
    parser.add_argument('--output-prefix',type=Path)
    args=parser.parse_args(argv)
    report=build_report(args.before_dir,args.run_dir,args.human_review,args.code_diagnosis)
    prefix=args.output_prefix or ROOT/'exports'/('P1-P6_24次真实问答前后对照_'+args.run_dir.name)
    prefix.parent.mkdir(parents=True,exist_ok=True)
    prefix.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    prefix.with_suffix('.html').write_text(html_report(report),encoding='utf-8')
    print(json.dumps({'json':str(prefix.with_suffix('.json')),'html':str(prefix.with_suffix('.html')),
                      'transactions':len(report['items']),'report_model_calls':0,'report_network_calls':0},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
