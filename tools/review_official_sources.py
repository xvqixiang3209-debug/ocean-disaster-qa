"""Apply an authorized, reversible batch of version-scoped technical reviews.

The project owner confirmed the imported files are official sources and asked
for this batch processing. No professional scoring, current operational
authority, source external permission or numeric dictionary is invented.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import unicodedata
from contextlib import closing

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from marine_kb.governance import draft_markers
from marine_kb.ingestion import split_units
from marine_kb.parsers import parse_file

CURRENT_VERIFIED = {
    'GB/T 13972-2010': 'https://std.samr.gov.cn/gb/search/gbDetailed?id=71F772D7D578D3A7E05397BE0A0AB82A',
    'GB/T 14914.2-2019': 'https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=F8927B290745336329854190DAB8F8A6',
    'GB/T 33666-2017': 'https://std.samr.gov.cn/gb/search/gbDetailed?id=71F772D818B9D3A7E05397BE0A0AB82A',
}


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda:handle.read(4*1024*1024),b''): value.update(block)
    return value.hexdigest()


def standard_identity(filename):
    text = unicodedata.normalize('NFKC', filename).replace('—','-')
    text = re.sub(r'GB[-_/ ]?T[ +]*', 'GB/T ', text, flags=re.I)
    text = re.sub(r'HY[-_/ ]?T[ +]*', 'HY/T ', text, flags=re.I)
    text = re.sub(r'DB33[ /_]*T[ +]*', 'DB33/T ', text, flags=re.I)
    found = re.search(r'((?:GB|HY|DB33)/T)\s*(\d+(?:\.\d+)?)-(\d{4})', text, re.I)
    return f'{found[1].upper()} {found[2]}-{found[3]}' if found else None


def candidate(row, chunks, raw_root):
    metadata = json.loads(row['metadata'])
    if row['status'] != 'pending_review': return None, None
    if row['category'] in {'模式结果','空间数据'}: return None, '结构化数据口径待补'
    if not chunks or row['quality_status'] != 'parsed': return None, '正文不可用或部分缺失'
    if (metadata.get('requires_human_review') or metadata.get('quarantined_pages')
        or metadata.get('complete_text_read') is False or metadata.get('conversion')
        or metadata.get('embedded_media_count') or metadata.get('embedded_image_count')
        or metadata.get('tables_require_review') or metadata.get('ocr',{}).get('recovered_pages')):
        return None, 'OCR、隔离页、图像或转换结果需要核对'
    draft = draft_markers(row['relative_path'], '\n'.join(chunk['text'] for chunk in chunks[:8]))
    if draft['is_draft']: return None, '草案或报批版本保留'
    path = Path(row['raw_path']).resolve(strict=True)
    if not path.is_relative_to(raw_root.resolve()) or Path(row['raw_path']).is_symlink():
        return None, '原件路径无效'
    if path.stat().st_size != row['bytes'] or digest(path) != row['sha256']:
        return None, '原件完整性异常'
    fresh = parse_file(path)
    if fresh['status'] != 'parsed' or fresh['metadata'].get('quarantined_pages') or fresh['metadata'].get('requires_human_review'):
        return None, '重新解析发现质量问题'
    rebuilt = split_units(fresh['units'])
    if [(c['locator'],c['text']) for c in rebuilt] != [(c['locator'],c['text']) for c in chunks]:
        return None, '重新解析与已存文本不一致，需要修复后再处理'
    text = '\n'.join(chunk['text'] for chunk in chunks)
    links_only = bool(re.sub(r'https?://\S+|\s+', '', text) == '')
    if '网页链接' in row['filename'] or links_only:
        return {'target_status':'indexed','review_scope':'reference_directory','standard_id':None,
                'draft_info':draft,'note':'官方来源已由项目负责人确认；此文件仅为链接目录，不作为事实或标准条款依据。'}, None
    return {'target_status':'approved',
        'review_scope':'versioned_reference' if row['category']=='标准规范' else 'historical_reference',
        'standard_id':standard_identity(row['filename']), 'draft_info':draft,
        'note':'官方来源由项目负责人确认；原件SHA256、大小、完整文字提取及切片与原件重解析一致性通过技术核验。按所列文档版本用于参考问答；不代表表格公式逐项专业审定、当前预警或所有版本现行有效。'}, None


def prepare(database):
    db = sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM sources WHERE status='pending_review' ORDER BY category,filename").fetchall()
    admitted, remaining = [], []
    for row in rows:
        chunks = [dict(c) for c in db.execute('SELECT locator,text FROM chunks WHERE source_id=? ORDER BY chunk_id',(row['source_id'],))]
        try:
            result, reason = candidate(row,chunks,database.parent/'files/raw')
        except Exception as exc:
            result, reason = None, '检查失败：'+type(exc).__name__
        identity = {'source_id':row['source_id'],'filename':row['filename'],'sha256':row['sha256'],
                    'previous_updated_at':row['updated_at'],'previous_metadata':json.loads(row['metadata']),
                    'previous_warnings':json.loads(row['warnings']),'previous_status':row['status']}
        if result: admitted.append({**identity,**result})
        else: remaining.append({'source_id':row['source_id'],'filename':row['filename'],'reason':reason})
    db.close()
    return {'admitted':admitted,'remaining':remaining}


def apply(database, plan, run_id):
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    backup = database.parent/'review_backups'/run_id/'knowledge.sqlite3'
    backup.parent.mkdir(parents=True,exist_ok=False)
    with closing(sqlite3.connect(backup)) as destination: db.backup(destination)
    stamp = datetime.now(timezone.utc).isoformat()
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''CREATE TABLE IF NOT EXISTS source_origin_confirmations(
            source_id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,confirmed_by TEXT NOT NULL,
            statement TEXT NOT NULL,confirmed_at TEXT NOT NULL,batch_id TEXT NOT NULL)''')
        for row in db.execute('SELECT source_id,sha256 FROM sources').fetchall():
            db.execute('INSERT OR IGNORE INTO source_origin_confirmations VALUES(?,?,?,?,?,?)',
                (row['source_id'],row['sha256'],'项目负责人（本次对话明确确认）','用户确认所提供文件来自官方；该声明不等于文本、时效及数据口径审定。',stamp,run_id))
        for item in plan['admitted']:
            row = db.execute('SELECT * FROM sources WHERE source_id=?',(item['source_id'],)).fetchone()
            if row['status'] != item['previous_status'] or row['updated_at'] != item['previous_updated_at'] or row['sha256'] != item['sha256']:
                raise RuntimeError('资料在预览后发生变化，整批未应用')
            metadata = json.loads(row['metadata'])
            metadata.update({'official_origin_confirmed':True,'official_origin_basis':'project_owner_statement',
                'review_method':'official_batch_technical','review_scope':item['review_scope'],
                'review_batch_id':run_id,'reviewed_at':stamp,'review_note':item['note'],
                'is_draft':item['draft_info']['is_draft'],'references_draft':item['draft_info']['references_draft'],
                'edition_current_verified':item['standard_id'] in CURRENT_VERIFIED,
                'standard_id':item['standard_id']})
            if metadata['edition_current_verified']:
                metadata.update({'edition_status':'current','edition_verification_url':CURRENT_VERIFIED[item['standard_id']],
                                 'edition_checked_at':'2026-10-15'})
            elif item['previous_metadata'].get('is_draft') and not metadata['is_draft']:
                metadata['edition_status'] = 'historical'
            if item['review_scope'] == 'reference_directory': metadata['source_type'] = 'reference_links'
            warnings = list(item['previous_warnings'])
            if item['previous_metadata'].get('is_draft') and not metadata['is_draft']:
                warnings = [value for value in warnings if value != '草案/报批/上报版本不能自动认定现行正式规则']
                warnings.append('该正式历史通知引用了另一份征求意见稿；引用关系不改变本文件版本，不把被引用草案作为现行规则。')
            db.execute('UPDATE sources SET status=?,metadata=?,warnings=?,updated_at=? WHERE source_id=?',
                       (item['target_status'],json.dumps(metadata,ensure_ascii=False),json.dumps(warnings,ensure_ascii=False),stamp,item['source_id']))
            db.execute('INSERT INTO review_log(source_id,previous_status,status,note,created_at) VALUES(?,?,?,?,?)',
                       (item['source_id'],row['status'],item['target_status'],item['note'],stamp))
        db.commit()
        return {'backup':str(backup),'origin_confirmed':db.execute('SELECT count(*) FROM source_origin_confirmations').fetchone()[0],
                'status_counts':dict(db.execute('SELECT status,count(*) FROM sources GROUP BY status').fetchall())}
    except Exception:
        db.rollback()
        raise
    finally: db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    database = APP/'data/marine_kb/knowledge.sqlite3'
    plan = prepare(database)
    run_id = 'official-'+datetime.now().strftime('%Y%m%d-%H%M%S')
    report = {'batch_id':run_id,'authorization':'项目负责人确认官方来源并明确要求代为完成批量处理。',
              'approval_scope':'技术审核后准入版本化参考问答；专业金标准、当前业务签发及外发权限分别管理。',
              'applied':args.apply,'plan':plan,'current_status_evidence':CURRENT_VERIFIED}
    if args.apply: report['result'] = apply(database,plan,run_id)
    destination = APP/'docs'/('官方资料批量处理_'+run_id+'.json')
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'applied':args.apply,'approved':sum(item['target_status']=='approved' for item in plan['admitted']),
                      'reference_directories':sum(item['target_status']=='indexed' for item in plan['admitted']),
                      'remaining':len(plan['remaining']),'report':str(destination),**report.get('result',{})},ensure_ascii=False,indent=2))


if __name__ == '__main__': main()
