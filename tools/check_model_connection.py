"""Explicit, document-free probe of the configured model API.

Use --probe only with authorization for the external API request. This tool
does not open a knowledge database or change per-document permissions.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from config import MODELS
from marine_kb.qa import MarineQuestionService
from model_layer import call_model
from typhoon.settings import TyphoonSettings


def configuration_id(config):
    # Used only to avoid displaying a result from a different endpoint/model.
    public = {key: config.get(key, '') for key in ('provider', 'base_url', 'code')}
    return hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--output', type=Path, default=APP / 'data/model_connection_test.json')
    args = parser.parse_args()
    alias = TyphoonSettings.from_env().report_model
    config = MODELS.get(alias) or {}
    configured = all(config.get(key) for key in ('api_key', 'base_url', 'code'))
    service = MarineQuestionService(None, model_caller=call_model, model_name=alias,
        external_enabled=args.probe, model_configured=configured, model_provider=config.get('provider', ''))
    if not args.probe:
        print(json.dumps(service.model_status(), ensure_ascii=False, indent=2))
        return
    try:
        result = service.test_model_connection()
    except Exception as exc:
        result = {'state': 'failed', 'tested': True, 'error_type': exc.__class__.__name__,
                  'note': '模型连接测试失败；未发送业务资料。异常详情和密钥不写入报告。'}
    result.update({'checked_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'configuration_id': configuration_id(config), 'actual_model': config.get('code'),
        'documents_sent': 0, 'knowledge_database_opened': False,
        'persistent_external_switch_changed': False})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['state'] == 'connected' else 1)


if __name__ == '__main__':
    main()
