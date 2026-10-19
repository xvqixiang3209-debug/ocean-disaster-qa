"""Read-only batch preflight of pending files; not professional approval."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

APP = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--database', type=Path, default=APP / 'data/marine_kb/knowledge.sqlite3')
    cli.add_argument('--output', type=Path)
    args = cli.parse_args()
    stamp = datetime.now().astimezone()
    output = args.output or APP / 'docs' / ('待审核资料技术预审_' + stamp.strftime('%Y%m%d_%H%M%S') + '.json')
    raw_root = args.database.resolve().parent / 'files/raw'
    db = sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro', uri=True, timeout=60)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    rows = db.execute("SELECT * FROM sources WHERE status='pending_review' ORDER BY category,filename").fetchall()
    # Freeze catalogue records, then check them again before reporting.
    before = [(row['source_id'],row['sha256'],row['status'],row['updated_at']) for row in rows]
    items, hashed = [], {}
    for row in rows:
        metadata = json.loads(row['metadata'])
        issues = []
        path = Path(row['raw_path'])
        try:
            resolved = path.resolve(strict=True)
            safe = resolved.is_relative_to(raw_root.resolve()) and resolved.is_file() and not path.is_symlink()
            if not safe:
                issues.append('原件路径需核验')
            else:
                if resolved not in hashed:
                    hashed[resolved] = (resolved.stat().st_size, sha256(resolved))
                size, digest = hashed[resolved]
                if size != row['bytes'] or digest != row['sha256']:
                    issues.append('原件大小或哈希不一致')
        except OSError:
            issues.append('原件缺失或不可读')
        chunk = db.execute("SELECT count(*),sum(CASE WHEN trim(text)='' OR trim(locator)='' THEN 1 ELSE 0 END) FROM chunks WHERE source_id=?", (row['source_id'],)).fetchone()
        if chunk[0] != row['chunk_count'] or chunk[1]:
            issues.append('切片数量、正文或定位异常')
        if row['category'] == '模式结果':
            group = '模式表：待单位、时间、基准及站点字典'
        elif row['category'] == '空间数据':
            group = '空间数据：待岸段映射与阈值依据'
        elif metadata.get('is_draft'):
            group = '草案标记：需核对本文件版本或引用关系'
        elif (metadata.get('requires_human_review') or metadata.get('quarantined_pages')
              or metadata.get('complete_text_read') is False or metadata.get('conversion')
              or row['quality_status'] == 'partial' or metadata.get('tables_require_review')):
            group = '文字质量：待原页、OCR或转换结果核验'
        elif row['category'] == '标准规范':
            group = '文字可读：待标准版本与业务适用性核定'
        else:
            group = '文字可读：待专业内容核定'
        metadata_checks = []
        if metadata.get('requires_human_review'): metadata_checks.append('OCR或内容需人工复核')
        if metadata.get('quarantined_pages'): metadata_checks.append('存在隔离页')
        if metadata.get('complete_text_read') is False: metadata_checks.append('全文读取未确认')
        if metadata.get('conversion'): metadata_checks.append('存在格式转换')
        if metadata.get('is_draft'): metadata_checks.append('草案标记待核实')
        if row['category'] == '标准规范': metadata_checks.append('正式版本、有效期与适用范围未核定')
        items.append({'source_id':row['source_id'], 'filename':row['filename'], 'category':row['category'],
            'sha256':row['sha256'], 'raw_integrity_passed':not any('原件' in value for value in issues),
            'chunk_registration_passed':not any('切片' in value for value in issues),
            'chunk_count':chunk[0], 'quality_status':row['quality_status'], 'group':group,
            'technical_issues':issues, 'remaining_checks':metadata_checks,
            'review_status':row['status'], 'professional_approved':False})
    after = [tuple(row) for row in db.execute("SELECT source_id,sha256,status,updated_at FROM sources WHERE status='pending_review' ORDER BY category,filename")]
    db.close()
    report = {'generated_at':stamp.isoformat(timespec='seconds'), 'scope':'原件完整性、切片登记与现有质量标记的批量技术预审；未审定标准有效性或专业结论。',
        'pending_count':len(items), 'unique_files_checked':len(hashed),
        'raw_integrity_passed':sum(row['raw_integrity_passed'] for row in items),
        'chunk_registration_passed':sum(row['chunk_registration_passed'] for row in items),
        'groups':dict(Counter(row['group'] for row in items)), 'catalogue_unchanged':before == after,
        'approval_actions':0, 'external_calls':0,
        'note':'技术通过不等于批准；单位和基准、正式版本及OCR核对没有证据时不填已完成。普通用户回答过程不要求审核。',
        'items':items}
    if output.exists(): raise ValueError('预审报告已存在，请指定新输出路径')
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines = ['# 待审核资料技术预审', '', report['scope'], '',
        f"时间：{report['generated_at']}。待审{len(items)}条，核验唯一原件{len(hashed)}个。",
        f"原件完整性通过{report['raw_integrity_passed']}条；切片登记通过{report['chunk_registration_passed']}条。",
        '批准操作0条，外部调用0次。下面是后续审核分组，不代表专业审批通过。', '',
        '| 后续处理组 | 数量 |', '| --- | ---: |']
    lines += [f'|{label}|{count}|' for label,count in report['groups'].items()]
    lines += ['', '普通用户直接提问和阅读回答；参考来源仅用于按需核对。', '',
        '标准需核定正式版本与适用范围；OCR/隔离页需对照原页；结构化表需单位、时间、基准和站点字典。',
        '“草案标记”还需区分本文件是草案，还是正文仅引用了另一份征求意见稿，不能未经检查一律批准或拒绝。', '']
    output.with_suffix('.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({key:report[key] for key in ('pending_count','unique_files_checked','raw_integrity_passed','chunk_registration_passed','groups','catalogue_unchanged','approval_actions','external_calls')},ensure_ascii=False,indent=2))
    print('report='+str(output))


if __name__ == '__main__':
    main()
