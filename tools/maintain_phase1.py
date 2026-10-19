"""Explicit local maintenance commands; never approve sources automatically."""
from pathlib import Path
import argparse
from collections import Counter
import json
import os
import sys
APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from marine_kb.storage import KnowledgeStore
from marine_kb.ingestion import IngestionService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['report','repair-empty','ocr','semantic','classify'])
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()
    data = APP / 'data' / 'marine_kb'
    store = KnowledgeStore(data / 'knowledge.sqlite3')
    if args.action == 'report':
        status = store.status()
        status.pop('latest_jobs', None)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        with store.connect() as db:
            warnings = Counter()
            for row in db.execute("SELECT warnings,category,quality_status FROM sources WHERE status='pending_review'"):
                warnings.update((row[1],w) for w in json.loads(row[0]))
            print(json.dumps(warnings.most_common(15), ensure_ascii=False, indent=2))
        return
    if args.action == 'semantic':
        from marine_kb.semantic import LocalSemanticIndex
        result = LocalSemanticIndex(store.database, data / 'semantic').rebuild()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.action == 'classify':
        from marine_kb.storage import now
        count = 0
        with store.connect() as db:
            rows = db.execute("SELECT * FROM sources WHERE status='pending_review' AND chunk_count>0 AND quality_status IN ('parsed','partial') AND category NOT IN ('标准规范','模式结果','空间数据')").fetchall()
            for row in rows:
                metadata = json.loads(row['metadata'])
                if metadata.get('is_draft') or metadata.get('ocr',{}).get('requires_human_review') or metadata.get('ocr',{}).get('recovered_pages',0):
                    continue
                if db.execute("SELECT 1 FROM chunks WHERE source_id=? AND metadata LIKE '%local_rapidocr%' LIMIT 1",(row['source_id'],)).fetchone():
                    continue
                warnings = json.loads(row['warnings'])
                warnings.append('历史参考资料，文本提取可用；图片/表格版式未审定，不代表全文完整或当前实况')
                db.execute("UPDATE sources SET status='indexed',warnings=?,updated_at=? WHERE source_id=?", (json.dumps(warnings,ensure_ascii=False),now(),row['source_id']))
                count += 1
        print(f'Classified {count} text-readable historical sources as unreviewed reference, not approved.')
        return
    service = IngestionService(store, data / 'files', APP / 'typhoon/data/typhoon_history.sqlite3')
    with store.connect() as db:
        if args.action == 'repair-empty':
            rows = db.execute("SELECT * FROM sources WHERE bytes=0").fetchall()
        else:
            os.environ['MARINE_LOCAL_OCR_ENABLED'] = 'true'
            os.environ['MARINE_LOCAL_OCR_MAX_PAGES'] = '30'
            os.environ['MARINE_LOCAL_OCR_TIMEOUT'] = '120'
            rows = db.execute("SELECT * FROM sources WHERE status='needs_ocr' AND lower(filename) LIKE '%.pdf' ORDER BY category='标准规范' DESC,bytes ASC").fetchall()
    if args.limit:
        rows = rows[:args.limit]
    results = []
    for index,row in enumerate(rows,1):
        entry = {'path':row['raw_path'],'relative_path':row['relative_path'],'sha256':row['sha256'],'bytes':row['bytes']}
        result = service.import_entry(entry, force=True)
        results.append(result)
        print(json.dumps({'index':index,'total':len(rows),**result}, ensure_ascii=False), flush=True)
    (data / f'maintenance-{args.action}.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    service.pool.shutdown(wait=True)


if __name__ == '__main__':
    main()
