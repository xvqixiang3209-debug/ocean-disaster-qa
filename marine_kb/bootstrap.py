"""Compose the new workbench without altering the historical databases."""
from pathlib import Path
import hashlib
import json
import os
from .storage import KnowledgeStore
from .ingestion import IngestionService
from .qa import MarineQuestionService
from .api import create_blueprint
from .model_authorization import EvidenceAuthorization
from .web_supplement import OfficialWebSupplement
from .public_cache import PublicReferenceReader


def install(app, typhoon_components, web_service, model_caller):
    data = Path(app.root_path) / "data" / "marine_kb"
    os.environ.setdefault('MARINE_KB_WORD_CACHE', str(data / 'office_cache'))
    os.environ.setdefault('MARINE_LOCAL_OCR_ENABLED', 'true')
    store = KnowledgeStore(data / "knowledge.sqlite3")
    store.semantic_index = None
    try:
        from .semantic import LocalSemanticIndex
        store.semantic_index = LocalSemanticIndex(store.database, data / "semantic")
    except ImportError:
        pass
    ingestion = IngestionService(store, data / "files", typhoon_components.settings.sqlite_database_path)
    model_name = os.getenv('MARINE_QA_MODEL', typhoon_components.settings.report_model)
    try:
        from config import MODELS
        model_config = MODELS.get(model_name) or {}
    except (ImportError, AttributeError):
        model_config = {}
    model_configured = all(model_config.get(key) for key in ("api_key", "base_url", "code"))
    external_enabled = os.getenv("MARINE_EXTERNAL_MODEL_ENABLED", "false").lower() == "true"
    authorization = EvidenceAuthorization(data / 'model_evidence_authorizations.json')
    from typhoon.web_search import WebSearchClient
    from typhoon.web_reader import WebPageReader
    web_enabled = os.getenv('MARINE_WEB_ENABLED', 'false').lower() == 'true'
    supplement = OfficialWebSupplement(
        WebSearchClient(enabled=web_enabled, provider='auto', timeout_seconds=8, max_results=4),
        PublicReferenceReader(WebPageReader(enabled=web_enabled, timeout_seconds=8, max_pages=6, max_chars=64000, max_bytes=3000000,
                      allow_official_documents=True),data/'public_reference_cache'),
        enabled=web_enabled)
    qa = MarineQuestionService(store, legacy_rag=typhoon_components.rag, web_service=web_service,
                               model_caller=model_caller, model_name=model_name,
                               external_enabled=external_enabled,
                               model_configured=model_configured, model_provider=model_config.get("provider", ""),
                               evidence_authorizer=authorization.allows,
                               require_model_answer=os.getenv('MARINE_REQUIRE_MODEL_ANSWER', 'false').lower() == 'true',
                               structured_answers=external_enabled, web_supplement=supplement,
                               search_policy='always',
                               crag_enabled=True,
                               general_explanations=os.getenv('MARINE_GENERAL_EXPLANATIONS', 'true').lower()=='true')
    qa.coverage_review_enabled=os.getenv('MARINE_COVERAGE_REVIEW_ENABLED','true').lower()=='true'
    qa.historical_database=Path(typhoon_components.settings.sqlite_database_path)
    probe_path = Path(app.root_path) / 'data/model_connection_test.json'
    try:
        probe = json.loads(probe_path.read_text(encoding='utf-8'))
        public_config = {key: model_config.get(key, '') for key in ('provider', 'base_url', 'code')}
        fingerprint = hashlib.sha256(json.dumps(public_config, sort_keys=True).encode()).hexdigest()
        if probe.get('configuration_id') == fingerprint:
            qa.connection_probe_record = {key: probe.get(key) for key in ('state', 'checked_at', 'documents_sent', 'actual_model')}
    except (OSError, ValueError, TypeError):
        pass
    app.register_blueprint(create_blueprint(store, ingestion, qa))
    app.extensions["marine_kb"] = {"store": store, "ingestion": ingestion, "qa": qa}
    app.extensions["marine_kb"]["selected_local_qa"] = MarineQuestionService(store)
    ingestion.recover()
    return qa
