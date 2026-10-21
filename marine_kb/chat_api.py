"""Small local question interface, with a token separate from admin actions."""
import hmac
import ipaddress
import secrets
import re
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, request
from .conversation import ConversationStore, structured_case_question
from .qa_observability import trace_request, attach_trace, observe_failure
from .answer_trace import current_trace


def create_chat_blueprint(qa, typhoon_components=None):
    bp = Blueprint('marine_assistant', __name__, url_prefix='/api/assistant')
    token = secrets.token_urlsafe(32)
    conversations = ConversationStore()
    bp.conversations = conversations

    def trace_fields():
        active = current_trace()
        return {'trace': active.to_dict()} if active is not None else {}
    root=Path(__file__).resolve().parents[1]
    tracked=('marine_kb/qa.py','marine_kb/chat_api.py','marine_kb/business_validation.py','marine_kb/quantities.py',
             'marine_kb/guidance.py','forecast_runtime/service.py','forecast_runtime/routing.py',
             'forecast_runtime/answer_facts.py','forecast_runtime/analogue.py','forecast_runtime/recreation.py','server.py',
             'forecast_runtime/objective.py','forecast_runtime/objective_api.py','forecast_runtime/objective_report.py',
             'forecast_runtime/objective_bridge.py','forecast_runtime/objective_report_api.py',
             'forecast_runtime/analogue_delivery.py','marine_kb/objective_context.py',
             'marine_kb/warning_knowledge.py','marine_kb/data/warning_concepts.json',
             'typhoon/service.py','typhoon/validation.py',
             'marine_kb/web_supplement.py','marine_kb/evidence_selection.py','marine_kb/evidence_applicability.py',
             'marine_kb/retrieval.py','marine_kb/query_intents.py','forecast_runtime/task_plan.py',
             'forecast_runtime/activity_locations.py','forecast_runtime/activity_sources.py')
    tracked=tracked+('marine_kb/question_contract.py','marine_kb/public_sources.py','marine_kb/model_json.py',
                     'typhoon/web_reader.py','typhoon/web_search.py','marine_kb/grounded_calculations.py','marine_kb/governance.py',
                     'marine_kb/public_cache.py','marine_kb/bootstrap.py','marine_kb/coverage_review.py',
                     'forecast_runtime/storage.py','forecast_runtime/sync.py','marine_kb/claim_grounding.py','forecast_runtime/impact_inputs.py',
                     'marine_kb/research_excerpt.py','marine_kb/intent_lenses.py','marine_kb/citation_repair.py','marine_kb/conversation.py',
                     'marine_kb/version_claims.py','marine_kb/answer_completeness.py','forecast_runtime/historical_comparison.py',
                     'forecast_runtime/official_warnings.py','forecast_runtime/api.py',
                     'marine_kb/crag.py','marine_kb/history_statistics.py','marine_kb/public_service_intents.py',
                     'marine_kb/answer_trace.py','marine_kb/qa_observability.py','marine_kb/replay.py','model_layer.py',
                     'data/public_references/manifest.json','data/public_references/ningbo_public_catastrophe_2024_2026.pdf',
                     'data/public_references/ningbo_public_catastrophe_2024_2026_excerpt.txt')
    tracked=tracked+tuple('marine_kb/'+name+'.py' for name in ('phase2','facet_contract','scope_gate',
        'evidence_units','typed_grounding','facet_delivery','recovery','relation_guard','answer_pipeline','protected_tool_delivery'))
    tracked=tracked+('data/domain/relation_lexicon.json',)
    build={'version':(root/'VERSION.txt').read_text(encoding='utf-8').split('Version: ',1)[-1].splitlines()[0],
           'runtime_source_sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in tracked}}
    from .phase2 import enabled,mode
    if any(enabled(n) for n in ('MARINE_FACET_CONTRACT','MARINE_EVIDENCE_UNITS','MARINE_SCOPE_GATE','MARINE_CONNECTOR_V2')) or mode('MARINE_TYPED_GROUNDING')!='off':
        build['phase2']={n:enabled(n) for n in ('MARINE_FACET_CONTRACT','MARINE_EVIDENCE_UNITS','MARINE_SCOPE_GATE','MARINE_CONNECTOR_V2')}
        build['phase2'].update(typed_grounding=mode('MARINE_TYPED_GROUNDING'),relation_guard=mode('MARINE_RELATION_GUARD'),
                               max_model_calls=10,max_answer_seconds=60)

    def owner():
        cookie = request.cookies.get('marine_assistant_owner', '')
        return cookie if re.fullmatch(r'[A-Za-z0-9_-]{32,64}', cookie) else None

    @bp.before_request
    def local_boundary():
        try:
            local = ipaddress.ip_address(request.remote_addr or '').is_loopback
        except ValueError:
            local = False
        if not local or urlsplit(request.host_url).hostname not in {'127.0.0.1', 'localhost', '::1'}:
            return jsonify(error='请在运行服务的电脑上打开问答页面。'), 403
        if request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return jsonify(error='请从问答页面重新提交。'), 403
        if request.method != 'GET' and not hmac.compare_digest(request.headers.get('X-Assistant-Token', ''), token):
            return jsonify(error='页面连接已更新，请刷新后重试。'), 403

    @bp.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @bp.get('/session')
    def session():
        response = jsonify(token=token)
        if not owner():
            response.set_cookie('marine_assistant_owner', secrets.token_urlsafe(32), httponly=True,
                                samesite='Strict', secure=request.is_secure, max_age=7200, path='/api/assistant')
        return response

    @bp.get('/status')
    def status():
        # Captured once when this process loads, so a new file on disk cannot
        # make an old running process masquerade as the patched service.
        return jsonify(build)

    @bp.delete('/conversations/<conversation_id>')
    def reset(conversation_id):
        try:
            conversations.delete(owner() or '', conversation_id)
        except KeyError:
            return jsonify(error='对话已清空或失效。'), 404
        return jsonify(cleared=True)

    @bp.post('/ask')
    @trace_request
    def ask():
        if (request.content_length or 0) > 16000:
            return jsonify(error='问题太长，请控制在2000字以内。'), 413
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get('question'), str):
            return jsonify(error='请输入需要查询的问题。'), 400
        question = body['question'].strip()
        if not 1 <= len(question) <= 2000:
            return jsonify(error='问题应为1至2000字。'), 400
        if not owner():
            return jsonify(error='页面连接已更新，请刷新后重试。'), 403
        conversation_id = body.get('conversation_id')
        if conversation_id is not None and (not isinstance(conversation_id, str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{20,64}', conversation_id)):
            return jsonify(error='对话标识无效，请新建对话。'), 400
        selected_sid = body.get('selected_sid') or None
        selected = None
        if selected_sid:
            if not isinstance(selected_sid, str) or len(selected_sid)>64 or typhoon_components is None:
                return jsonify(error='所选台风无效，请重新选择。'), 400
            storm = typhoon_components.repositories.historical.get_storm(selected_sid, include_track=False)
            if not storm:
                return jsonify(error='没有找到所选历史台风，请重新选择。'), 404
            selected = {key:storm.get(key) for key in ('sid','name','season')}
        try:
            conversation_id, conversation = conversations.get(owner(), conversation_id)
        except KeyError:
            return jsonify(error='对话已失效，请新建对话后继续。'), 404
        except RuntimeError:
            return jsonify(error='对话服务繁忙，请稍后重试。'), 503
        if not conversation.lock.acquire(blocking=False):
            return jsonify(error='上一条问题仍在处理，请稍候。'), 409
        try:
            if conversation.sid != selected_sid:
                conversation.turns.clear()
                conversation.sid = selected_sid
            # Model, external-document permissions and live-source rules are
            # server-owned. The simple UI cannot override them in its payload.
            options = {'allow_web':True}
            plan = None
            if hasattr(qa, 'prepare_question'):
                plan = qa.prepare_question(question, history=conversation.turns, selected_context=selected)
                options['question_plan'] = plan
            elif conversation.turns:
                options['history'] = list(conversation.turns)
            if selected and plan is None:
                options.update(sid=selected_sid, selected_context=selected)
            if (plan and plan.get('sid') and structured_case_question(plan['normalized_question'])
                    and typhoon_components is not None):
                request_id = body.get('match_request_id')
                if not isinstance(request_id, str) or not 1<=len(request_id)<=128:
                    return jsonify(error='请先在地图中检索并选择历史台风。'), 400
                if getattr(qa,'crag_enabled',False):
                    from .crag import answer_selected_case
                    sessions=typhoon_components.conversations.sessions
                    analogue=sessions.select(request_id,plan['sid'])
                    current=(sessions.get(request_id) or {}).get('result',{}).get('current') or {}
                    result=answer_selected_case(qa,plan,analogue,current)
                else:
                    result = typhoon_components.conversations.ask(request_id, plan['sid'], plan['normalized_question'])
                result.setdefault('answer_meta', {}).update(normalized_question=plan['normalized_question'],
                    conversation_context_used=plan['context_used'], history_messages_used=plan['history_messages_used'],sid=plan['sid'])
            else:
                result = qa.answer(question, **options)
            if (result.get('answer_meta') or {}).get('used_mode') == 'model_error':
                failure=result.get('answer_meta') or {}
                code=str(failure.get('validation_error') or failure.get('error_type') or 'unknown')
                # Only a bounded error identifier is logged, never questions,
                # model requests, private documents, tokens or credentials.
                code=code if re.fullmatch(r'[A-Za-z0-9_]{1,80}',code) else 'unclassified'
                current_app.logger.warning('Assistant generation failed: %s',code)
                return jsonify(error=result.get('answer') or 'DeepSeek暂时无法完成回答，请稍后重试。',
                               conversation_id=conversation_id, **trace_fields()), 503
            attach_trace(result)
            conversations.append(conversation, question, result)
        except KeyError:
            return jsonify(error='台风检索会话已失效，请重新检索并选择台风。'), 404
        except Exception as exc:
            observe_failure('request', exc)
            return jsonify(error='暂时无法完成查询，请稍后重试。', **trace_fields()), 503
        finally:
            conversation.lock.release()
        sources = sorted(result.get('rag_sources', []) + result.get('web_sources', []),
                         key=lambda row: row.get('citation_id', 0))
        return jsonify(answer=result.get('answer', ''), sources=sources,
            answer_kind=('general_explanation' if (result.get('answer_meta') or {}).get('general_knowledge_used') else
                         'tool_grounded' if (result.get('answer_meta') or {}).get('business_evidence_id') and sources else 'knowledge'),
            mode=(result.get('answer_meta') or {}).get('used_mode', 'unknown'),
            conversation_id=conversation_id, turn_count=len(conversation.turns),
            context_used=(result.get('answer_meta') or {}).get('conversation_context_used', False),
            resolved_question=(result.get('answer_meta') or {}).get('normalized_question',question),
            trial=(result.get('answer_meta') or {}).get('business_trial',False),
            business_state=(result.get('answer_meta') or {}).get('business_state'),
            objective_artifacts=(result.get('answer_meta') or {}).get('objective_artifacts',[]),
            objective_dataset_id=(result.get('answer_meta') or {}).get('objective_dataset_id'),
            map_action=(result.get('answer_meta') or {}).get('map_action'),
            answerability=(result.get('answer_meta') or {}).get('answerability'),
            subquestion_coverage=(result.get('answer_meta') or {}).get('subquestion_coverage',[]),
            missing_information=(result.get('answer_meta') or {}).get('missing_information',[]),
            evidence_diagnostics={key:(result.get('answer_meta') or {}).get(key) for key in (
                'route','grounded_answerability','local_evidence_count','blocked_evidence_count',
                'web_search_executed','web_search_queries','web_search_states','web_body_candidate_count','web_body_read_failures',
                'web_excluded_sources','evidence_sent_count','corrective_retrieval_attempted',
                'corrective_new_sources','corrective_search_summary','combined_validation_error','coverage_review','general_knowledge_used',
                'claim_attribution_recovery','numeric_supported_partial_recovery','citation_format_repairs',
                'recovered_completeness_review','crag_audit','answer_policy','local_retrieval_executed',
                'phase2','scope_gate','typed_shadow','answer_budget','turn_state')},
            validation={'computed_table_fallback':bool((result.get('answer_meta') or {}).get('computed_table_fallback')),
                        'error':(result.get('answer_meta') or {}).get('validation_error'),
                        'details':(result.get('answer_meta') or {}).get('validation_diagnostics')},
            data_url=('/api/forecast/evidence/'+result['answer_meta']['business_evidence_id']) if (result.get('answer_meta') or {}).get('business_evidence_id') else None,
            selected_sid=(result.get('answer_meta') or {}).get('sid'),
            **({'trace':result['answer_meta']['trace']} if (result.get('answer_meta') or {}).get('trace') else {}))

    return bp
