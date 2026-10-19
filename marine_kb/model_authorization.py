"""Consent for the existing corpus is bound to exact retained chunk bytes."""
import hashlib
import json
from pathlib import Path


def evidence_key(item):
    return str(item.get('source_id', '')) + '|' + str(item.get('chunk_id', ''))


def text_digest(text):
    return hashlib.sha256(str(text).encode('utf-8')).hexdigest()


class EvidenceAuthorization:
    def __init__(self, path):
        self.entries = {}
        self.authorized_at = None
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'))
            if data.get('schema_version') == 1 and isinstance(data.get('entries'), dict):
                self.entries = data['entries']
                self.authorized_at = data.get('authorized_at')
        except (OSError, ValueError, TypeError):
            pass

    def allows(self, item):
        digest = item.get('_evidence_digest') or text_digest(item.get('text') or item.get('content') or '')
        return self.entries.get(evidence_key(item)) == digest
