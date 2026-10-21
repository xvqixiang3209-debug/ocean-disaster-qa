"""Review groups are display-only and classify every pending marine record once."""
import json

import pytest

from marine_kb.review_groups import (
    GROUP_IDS, VERSION_BLOCKERS, classify_review_group, review_progress,
)


@pytest.mark.parametrize(('metadata', 'expected'), [
    ({'structured_archive_state': 'raw_values_recovered'}, 'structured'),
    ({'structured_archive_state': 'coast_versions_recovered'}, 'structured'),
    ({'approved_excerpt_source_ids': ['approved-one']}, 'partial'),
    ({'version_audit_20261021': {'version_status': 'official_current'},
      'edition_current_verified': True}, 'fulltext'),
    ({'version_audit_20261021': {'version_status': 'event_specific_archive_not_current_general_rule'}}, 'fulltext'),
    ({'version_audit_20261021': {'version_status': 'official_published_current_replaces_2022'},
      'formal_replacement_source_ids': ['approved-replacement'], 'is_draft': False}, 'fulltext'),
    ({'is_draft': True}, 'version'),
    ({'is_draft': True, 'edition_current_verified': True}, 'fulltext'),
    ({'edition_current_verified': False}, 'fulltext'),
    ({}, 'fulltext'), (None, 'fulltext'), ('{broken-json', 'fulltext'),
    ({'version_audit_20261021': {'version_status': []}, 'source_type': {}}, 'fulltext'),
])
def test_classification_uses_review_work_remaining(metadata, expected):
    result = classify_review_group('pending_review', metadata)
    assert result['id'] == expected
    assert result['id'] in GROUP_IDS and result['label'] and result['reason']


@pytest.mark.parametrize('version_status', sorted(VERSION_BLOCKERS))
@pytest.mark.parametrize('audit_key', ['version_audit_20261021', 'version_audit'])
def test_explicit_version_gaps_are_grouped_for_both_retained_and_generic_audits(version_status, audit_key):
    metadata = {audit_key: {'version_status': version_status},
                'approved_excerpt_source_ids': ['approved-one']}
    result = classify_review_group('pending_review', metadata)
    assert result['id'] == 'version' and '节选' in result['reason']
    metadata['structured_archive_state'] = 'raw_values_recovered'
    assert classify_review_group('pending_review', metadata)['id'] == 'structured'


@pytest.mark.parametrize('status', ['approved', 'indexed', 'queued', 'rejected', 'error'])
def test_only_pending_review_is_grouped(status):
    assert classify_review_group(status, {'is_draft': True}) is None


def test_legacy_never_gets_a_group_or_progress_even_with_marine_metadata():
    metadata = {'structured_archive_state': 'raw_values_recovered'}
    assert classify_review_group('pending_review', metadata, library='legacy') is None
    assert classify_review_group('pending_review', metadata, source_id='legacy:one') is None
    assert review_progress('pending_review', metadata, library='legacy') == ''


def test_progress_is_safe_generated_text_and_retains_card_excerpt_and_replacement_scope():
    secret = 'FIXTURE_SECRET_DO_NOT_EXPOSE'
    metadata = {'api_key': secret, 'scope_note': secret, 'raw_path': 'E:/private/' + secret,
                'source_type': 'official_document_card'}
    assert review_progress('approved', metadata) == '已核文档身份；不含正文条款'
    metadata['source_type'] = 'official_standard_excerpt'
    assert review_progress('approved', metadata) == '已核原页节选；原文件全文状态另列'
    metadata.update(source_type='unknown', approved_excerpt_source_ids=[secret],
                    formal_replacement_source_ids=[secret], edition_current_verified=True)
    progress = review_progress('pending_review', metadata)
    assert '已核节选 1 份' in progress and '正式替代正文已入库' in progress
    assert secret not in json.dumps({'group': classify_review_group('pending_review', metadata), 'progress': progress})


def test_approved_normative_body_does_not_claim_full_pdf_transcription():
    progress = review_progress('approved', {'normative_body_verified': True, 'complete_text_read': False})
    assert '规范性业务正文及附录已核' in progress
    assert '非业务前置/参考内容保留在原件' in progress
    assert '全文已核' not in progress


def test_archived_progress_explains_replacement_and_retained_original():
    progress = review_progress('archived', {'formal_replacement_source_ids': ['replacement']})
    assert '正式替代正文已入库' in progress and '旧原件与切片留档' in progress
    assert '不用于默认问答' in progress
