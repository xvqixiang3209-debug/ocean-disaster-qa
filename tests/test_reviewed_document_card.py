from datetime import datetime, timedelta, timezone

from marine_kb.evidence_units import build_doc_card


def hit(**changes):
    metadata = {'effective_date': '2021-06-01', 'edition_current_verified': True,
                'edition_checked_at': datetime.now(timezone.utc).date().isoformat(),
                'edition_verification_url': 'https://openstd.samr.gov.cn/verified'}
    metadata.update(changes)
    return {'source_id': 'card', 'title': 'GB/T 39418-2020 风暴潮等级',
            'text': '标准名称：风暴潮等级。标准号：GB/T 39418-2020。', 'metadata': metadata}


def test_registry_metadata_reaches_typed_card():
    card = build_doc_card(hit()).to_dict()
    assert card['effective_from'] == '2021-06-01'
    assert card['version_status'] == 'current_verified'
    assert 'checked=' in card['derivation']['version_status']


def test_unverified_or_stale_flag_does_not_certify_current():
    for metadata in [dict(edition_current_verified=False), dict(edition_checked_at='2000-01-01'),
                     dict(edition_verification_url=None), dict(edition_checked_at='invalid')]:
        assert build_doc_card(hit(**metadata)).version_status == 'version_unverified'


def test_missing_effective_date_does_not_guess_from_publication():
    card = build_doc_card(hit(effective_date=None, publication_date='2020-11-19'))
    assert card.effective_from is None
