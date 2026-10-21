from types import SimpleNamespace
from marine_kb.conversation import ConversationStore
from marine_kb.qa import _safe_metadata
from marine_kb.warning_knowledge import answer, source_allowed
from forecast_runtime.service import ForecastRuntime


def test_verified_program_answers_available_for_reference_resolution():
    for marker in ('analogue_delivery_verified','objective_delivery_verified'):
        store=ConversationStore();_, conversation=store.get('owner')
        store.append(conversation,'查询',{'answer':'仅用于追问指代，仍须重新检索',
            'answer_meta':{'used_mode':'business_data',marker:True}})
        assert conversation.turns[-1]['answer_context_allowed'] is True


def test_unverified_program_answer_does_not_gain_context_permission():
    store=ConversationStore();_, conversation=store.get('owner')
    store.append(conversation,'查询',{'answer':'未核对',
        'answer_meta':{'used_mode':'business_data','analogue_delivery_verified':False}})
    assert conversation.turns[-1]['answer_context_allowed'] is False


def test_analogue_source_context_rechecked_against_stored_result_hash():
    # The model answer is never the provenance; the server's original tool
    # result and validated delivery contract must still exist and match.
    runtime=object.__new__(ForecastRuntime)
    source={'source_id':'forecast:'+'a'*24,'metadata':{'analogue_program_result':True,'program_result_sha256':'b'*64}}
    record={'kind':'analogue','analogue_delivery':{'result_sha256':'b'*64,'contract':{'verified':True}}}
    runtime.store=SimpleNamespace(state=lambda key:record)
    assert runtime.evidence_allowed(source)
    record['analogue_delivery']['contract']['verified']=False
    assert not runtime.evidence_allowed(source)
    record['analogue_delivery']['contract']['verified']=True
    source['metadata']['program_result_sha256']='c'*64
    assert not runtime.evidence_allowed(source)
    runtime.store=SimpleNamespace(state=lambda key:None)
    assert not runtime.evidence_allowed(source)


def test_public_analogue_metadata_retains_only_needed_provenance():
    metadata=_safe_metadata({'metadata':{'analogue_program_result':True,'program_result_sha256':'x','secret':'hidden'}})
    assert metadata=={'analogue_program_result':True,'program_result_sha256':'x'}


def test_official_concept_answer_context_rechecks_current_source_card():
    citation=answer('什么是海浪预警？')['web_sources'][0]
    assert source_allowed(citation)
    citation['metadata']['sha256']='changed'
    assert not source_allowed(citation)
