import json
import shutil
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from marine_kb import public_cache


URL = next(iter(public_cache._REVIEWED_PUBLIC_PDFS))


def blocked_reader():
    reader = Mock()
    reader.enrich.side_effect = lambda rows: [dict(row, read_status='blocked', content='',
                                                  read_error='test access denial') for row in rows]
    return reader


def test_reviewed_original_preserves_scope_pages_hashes_and_partial_status(tmp_path):
    reader = blocked_reader()
    page = public_cache.PublicReferenceReader(reader, tmp_path).enrich([
        {'url': URL, '_primary_scope': 'versioned_standard'}])[0]
    assert reader.enrich.call_count == 0
    assert page['read_status'] == 'partial'
    assert page['extraction_method'] == 'reviewed_official_pdf_excerpt'
    assert page['read_from_reviewed_archive'] and not page['read_from_cache']
    assert page['edition_current_verified'] is False
    assert page['published_at'] is None
    assert page['coverage_start'] == '2024-01-01'
    assert page['effective_from'] == '2024-04-30'
    assert '住房进水水位线>150厘米' in page['content']
    assert '防台、防汛Ⅲ级' in page['content']
    assert len(page['reviewed_archive_manifest_sha256']) == 64
    assert len(page['reviewed_archive_pdf_sha256']) == 64
    assert page['content_sha256'] == page['reviewed_archive_excerpt_sha256']
    assert {p['page'] for p in page['reviewed_archive_passages']} == {2, 3, 4, 5, 6, 7, 8, 9, 11}
    assert all(p['text'] in page['content'] for p in page['reviewed_archive_passages'])
    assert '不是整份PDF或OCR全文' in page['coverage_caveat']


@pytest.fixture
def copied_archive(tmp_path, monkeypatch):
    target = tmp_path / 'archive'
    shutil.copytree(public_cache._REVIEWED_ARCHIVE_ROOT, target)
    monkeypatch.setattr(public_cache, '_REVIEWED_ARCHIVE_ROOT', target)
    return target


@pytest.mark.parametrize('tamper', ['pdf', 'excerpt', 'passage', 'path', 'schema', 'duplicate', 'invalid_record'])
def test_damaged_or_redirected_archive_falls_back_to_access_controlled_reader(copied_archive, tmp_path, tamper):
    manifest_path = copied_archive / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    record = manifest['sources'][0]
    if tamper == 'pdf':
        (copied_archive / record['pdf_file']).write_bytes(b'%PDF-1.4 changed')
    elif tamper == 'excerpt':
        (copied_archive / record['excerpt_file']).write_text('原文被篡改', encoding='utf-8')
    elif tamper == 'passage':
        record['verified_passages'][0]['text'] = '篡改段落'
    elif tamper == 'path':
        record['excerpt_file'] = '../outside.txt'
    elif tamper == 'schema':
        manifest['schema_version'] = 999
    elif tamper == 'duplicate':
        manifest['sources'].append(dict(record))
    elif tamper == 'invalid_record':
        manifest['sources'] = ['malformed source']
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    reader = blocked_reader()
    page = public_cache.PublicReferenceReader(reader, tmp_path / 'cache').enrich([
        {'url': URL, '_primary_scope': 'versioned_standard'}])[0]
    assert page['read_status'] == 'blocked'
    assert not page.get('read_from_reviewed_archive')
    assert reader.enrich.call_count == 1


@pytest.mark.parametrize('row', [
    {'url': URL + '?other=1', '_primary_scope': 'versioned_standard'},
    {'url': URL},
    {'url': URL, '_primary_scope': 'live_status'},
])
def test_archive_requires_exact_url_and_static_reference_scope(tmp_path, row):
    reader = blocked_reader()
    page = public_cache.PublicReferenceReader(reader, tmp_path).enrich([row])[0]
    assert page['read_status'] == 'blocked' and reader.enrich.call_count == 1


def test_ordinary_reference_cache_still_expires_after_one_hour(tmp_path):
    reader = Mock()
    reader.enrich.side_effect = lambda rows: [dict(row, read_status='success',
        content='真实官方正文，包含统计制度和可复核的原文段落，保留来源与读取时间。',
        read_at='2026-10-18T01:00:00+00:00') for row in rows]
    cache = public_cache.PublicReferenceReader(reader, tmp_path)
    row = {'url': 'https://www.mem.gov.cn/example.html', '_primary_scope': 'technical_reference'}
    cache.enrich([row])
    assert cache.enrich([row])[0]['read_from_cache']
    assert reader.enrich.call_count == 1
    file = cache._path(row['url'])
    data = json.loads(file.read_text(encoding='utf-8'))
    data['saved_at'] = (datetime.now(timezone.utc) - timedelta(seconds=3601)).isoformat()
    file.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    cache.enrich([row])
    assert reader.enrich.call_count == 2
