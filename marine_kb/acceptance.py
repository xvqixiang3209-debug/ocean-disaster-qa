"""Human review of frozen development candidates; never automatic scoring."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


CRITERIA = ("fact_correct", "complete", "citations_supported", "time_scope_correct")


class MonthOneAcceptance:
    def __init__(self, store, directory):
        self.store = store
        self.directory = Path(directory)
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS month1_reviews (
                    id INTEGER PRIMARY KEY, case_id TEXT NOT NULL,
                    answer_sha256 TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS month1_review_case ON month1_reviews(case_id,id);
            ''')

    def snapshot(self):
        manifest_path = self.directory / 'month1_acceptance_candidates.json'
        if not manifest_path.is_file():
            return {"available": False, "items": [], "summary": {"case_count": 0}}
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw)
        manifest_sha = hashlib.sha256(raw).hexdigest()
        report_path = self.directory / 'month1_acceptance_results.json'
        report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.is_file() else {}
        report_matches = report.get('candidate_manifest_sha256') == manifest_sha
        answers = {item['id']: item for item in report.get('candidates', [])} if report_matches else {}
        with self.store.connect() as db:
            reviews = [dict(row) for row in db.execute('SELECT * FROM month1_reviews ORDER BY id DESC')]
        items = []
        for case in manifest['cases']:
            result = answers.get(case['id'], {})
            # Bind every review to the exact response and citations the human
            # inspected. A regenerated response invalidates earlier scores.
            answer = {key: result.get(key) for key in ('answer', 'sources', 'answer_meta')}
            answer_sha = hashlib.sha256(json.dumps(answer, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            history = [{**json.loads(row['payload']), 'created_at': row['created_at'],
                        'current': row['manifest_sha256'] == manifest_sha and row['answer_sha256'] == answer_sha}
                       for row in reviews if row['case_id'] == case['id']]
            latest = history[0] if history else None
            current = latest if latest and latest['current'] else None
            items.append({**case, **answer, 'answer_sha256': answer_sha, 'manifest_sha256': manifest_sha,
                          'technical_checks_passed': result.get('technical_checks_passed'),
                          'review': current, 'review_history': history[:20],
                          'review_stale': bool(latest and current is None)})
        confirmed = sum(bool(item['review'] and item['review']['sample_confirmed']) for item in items)
        passed = sum(bool(item['review'] and item['review']['verdict'] == 'passed') for item in items)
        reviewed = sum(item['review'] is not None for item in items)
        return {"available": True, "generated_at": report.get('generated_at') if report_matches else None,
                "manifest_sha256": manifest_sha, "items": items,
                "summary": {"case_count": len(items), "sample_confirmed_count": confirmed,
                    "reviewed_count": reviewed, "passed_count": passed, "target_passed": 18,
                    "target_met": len(items) == 20 and confirmed == 20 and passed >= 18},
                "note": "开发候选集；须先核定样本及来源，再由业务人员逐题评分。此结果不代表160题整体覆盖率或第三个月独立评测。"}

    def review(self, case_id, body):
        if not isinstance(body, dict):
            raise ValueError('验收记录应为JSON对象')
        snapshot = self.snapshot()
        case = next((item for item in snapshot['items'] if item['id'] == case_id), None)
        if case is None:
            raise KeyError(case_id)
        if body.get('answer_sha256') != case['answer_sha256'] or body.get('manifest_sha256') != case['manifest_sha256']:
            raise ValueError('候选题或回答已更新，请刷新后重新核验')
        payload = {key: str(body.get(key) or '').strip() for key in
                   ('reviewer', 'reference_answer', 'evidence_note', 'note', 'verdict')}
        for key, minimum, maximum in (('reviewer', 2, 80), ('reference_answer', 4, 6000),
                                     ('evidence_note', 4, 2000), ('note', 4, 2000)):
            if not minimum <= len(payload[key]) <= maximum:
                raise ValueError('请完整填写验收人、参考答案、出处定位与验收意见，并遵守输入长度限制')
        if payload['verdict'] not in {'passed', 'failed', 'awaiting_data'}:
            raise ValueError('验收结果无效')
        payload['sample_confirmed'] = body.get('sample_confirmed') is True
        criteria = body.get('criteria') or {}
        if not isinstance(criteria, dict):
            raise ValueError('验收指标应为JSON对象')
        payload['criteria'] = {key: criteria.get(key) is True for key in CRITERIA}
        if payload['verdict'] == 'passed':
            if not case.get('answer') or not case.get('sources'):
                raise ValueError('缺少回答或证据，不能记为完整正确')
            if not payload['sample_confirmed'] or not all(payload['criteria'].values()):
                raise ValueError('通过须核定样本与来源，并确认事实、完整性、引用及时间口径全部合格')
        with self.store.connect() as db:
            db.execute('INSERT INTO month1_reviews(case_id,answer_sha256,manifest_sha256,payload,created_at) VALUES(?,?,?,?,?)',
                       (case_id, case['answer_sha256'], case['manifest_sha256'], json.dumps(payload, ensure_ascii=False),
                        datetime.now(timezone.utc).isoformat()))
        return self.snapshot()
