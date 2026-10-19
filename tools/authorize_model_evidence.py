"""Record an explicit owner's consent for currently usable reference text only."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from marine_kb.model_authorization import evidence_key, text_digest
from marine_kb.retrieval import usable_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    entries = {}
    counts = {'new_library_chunks': 0, 'legacy_chunks': 0}
    for legacy in (False, True):
        path = APP / ('typhoon/data/typhoon_rag.sqlite3' if legacy else 'data/marine_kb/knowledge.sqlite3')
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            query = ("SELECT c.document_id,c.chunk_id,c.content,d.filename,d.source_kind,c.metadata_json FROM knowledge_chunks c JOIN knowledge_documents d USING(document_id) WHERE d.status='ready'" if legacy else
                     "SELECT c.source_id,c.chunk_id,c.text,s.filename,s.category,s.status,s.metadata FROM chunks c JOIN sources s USING(source_id) WHERE s.status IN ('approved','indexed')")
            for row in db.execute(query):
                item = dict(row)
                if legacy:
                    item.update(source_id='legacy:' + item['document_id'], chunk_id='legacy:' + str(item['chunk_id']),
                                text=item['content'], category=item['source_kind'], status='indexed', metadata=json.loads(item['metadata_json']))
                else:
                    item['metadata'] = json.loads(item['metadata'])
                item['title'] = item['filename']
                if usable_source(item):
                    entries[evidence_key(item)] = text_digest(item['text'])
                    counts['legacy_chunks' if legacy else 'new_library_chunks'] += 1
    report = {'schema_version': 1, 'authorized_at': datetime.now().astimezone().isoformat(timespec='seconds'),
              'authorized_by': '项目负责人（本次对话）',
              'authorization': '不能说只返回摘录啊，要deepseek组织语言并且根据用户的提问回答出来',
              'scope': '为专业问答发送必要的现有可检索资料片段；不包含待审核、草案、数值表、链接目录；新导入或内容改变后不自动继承授权。',
              'counts': counts, 'entries': entries}
    destination = APP / 'data/marine_kb/model_evidence_authorizations.json'
    if args.apply:
        if destination.exists():
            raise RuntimeError('授权文件已存在；请先核对现有授权，不自动覆盖。')
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'applied': args.apply, **counts, 'manifest': str(destination)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
