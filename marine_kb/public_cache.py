"""Persist successfully read primary reference bodies, never live status pages."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,os,uuid


_REVIEWED_ARCHIVE_ROOT = Path(__file__).resolve().parents[1] / 'data' / 'public_references'
# Only these specific, previously acquired public originals may use the offline
# reviewed archive. A caller cannot turn an arbitrary URL or path into evidence.
_REVIEWED_PUBLIC_PDFS = {
    'https://zjjcmspublic.oss-cn-hangzhou-zwynet-d01-a.internet.cloud.zj.gov.cn/'
    'jcms_files/jcms1/web3776/site/attach/0/7dd1311585f7463ea281d33dfbaf286d.pdf':
    'ningbo_public_catastrophe_2024_2026',
}


def _archive_file(name):
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise ValueError('archive filename must be a basename')
    root = _REVIEWED_ARCHIVE_ROOT.resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise ValueError('archive file escaped the fixed archive directory')
    return path


def _reviewed_archive_page(row):
    """Read only verified original excerpts; preserve partial and version scope."""
    url = row.get('url', '')
    archive_id = _REVIEWED_PUBLIC_PDFS.get(url)
    if archive_id is None:
        return None
    try:
        manifest_bytes = _archive_file('manifest.json').read_bytes()
        manifest = json.loads(manifest_bytes)
        if (not isinstance(manifest, dict) or manifest.get('schema_version') != 1
                or not isinstance(manifest.get('sources'), list)
                or any(not isinstance(r, dict) for r in manifest['sources'])):
            return None
        records = [r for r in manifest['sources'] if r.get('id') == archive_id and r.get('url') == url]
        if len(records) != 1:
            return None
        record = records[0]
        document = _archive_file(record['pdf_file']).read_bytes()
        excerpt_bytes = _archive_file(record['excerpt_file']).read_bytes()
        body = excerpt_bytes.decode('utf-8')
        document_hash = hashlib.sha256(document).hexdigest()
        body_hash = hashlib.sha256(excerpt_bytes).hexdigest()
        if (not document.startswith(b'%PDF-') or len(body) < 30
                or document_hash != record['pdf_sha256']
                or body_hash != record['excerpt_sha256']):
            return None
        passages = record['verified_passages']
        if not isinstance(passages, list) or not passages or any(
                not isinstance(p, dict) or not isinstance(p.get('page'), int) or p['page'] < 1
                or p.get('locator') != 'page:' + str(p['page'])
                or not p.get('text') or p['text'] not in body
                or hashlib.sha256(p['text'].encode('utf-8')).hexdigest() != p.get('sha256')
                for p in passages):
            return None
        return {
            **row, 'title': record['title'], 'page_title': record['title'],
            'url': url, 'final_url': url, 'read_status': 'partial', 'read_error': None,
            'content': body, 'content_chars': len(body), 'content_sha256': body_hash,
            'extraction_method': 'reviewed_official_pdf_excerpt',
            'coverage_caveat': record['coverage_caveat'], 'truncated': False,
            'published_at': record.get('published_at'), 'signed_at': record.get('signed_at'),
            'effective_from': record.get('effective_from'),
            'coverage_start': record.get('coverage_start'), 'coverage_end': record.get('coverage_end'),
            'jurisdiction': record['jurisdiction'], 'edition_current_verified': False,
            'read_at': record['acquired_at'], 'read_from_cache': False,
            'read_from_reviewed_archive': True,
            'reviewed_archive_id': archive_id,
            'reviewed_archive_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
            'reviewed_archive_pdf_sha256': document_hash,
            'reviewed_archive_excerpt_sha256': body_hash,
            'reviewed_archive_passages': passages,
            'reviewed_archive_limitations': record['limitations'],
            'document_links': [{'url': url, 'title': record['title']}],
        }
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        # A damaged archive is a failed evidence read, never a fabricated success.
        # Fall back to the ordinary reader, which retains its access controls.
        return None


class PublicReferenceReader:
    REFERENCE_SCOPES={'general_safety','versioned_standard','research_method','technical_reference','annual_statistics'}
    def __init__(self,reader,root,max_age_seconds=3600):
        self.reader,self.root,self.max_age_seconds=reader,Path(root),max_age_seconds
        self.root.mkdir(parents=True,exist_ok=True)

    def _path(self,url):return self.root/(hashlib.sha256(url.encode()).hexdigest()+'.json')

    def enrich(self,rows):
        from .web_supplement import trusted_url
        output={};pending=[]
        for row in rows:
            url=row.get('url','')
            cacheable=row.get('_primary_scope') in self.REFERENCE_SCOPES
            archived = _reviewed_archive_page(row) if cacheable and trusted_url(url,True) else None
            if archived:
                output[url] = archived
                continue
            cached=None
            if cacheable and trusted_url(url,True):
                try:
                    cached=json.loads(self._path(url).read_text(encoding='utf8'))
                    age=(datetime.now(timezone.utc)-datetime.fromisoformat(cached['saved_at'])).total_seconds()
                    body=cached['page']['content']
                    if not (0<=age<=self.max_age_seconds and cached['url']==url
                            and trusted_url(cached['page'].get('final_url') or url,True)
                            and hashlib.sha256(body.encode()).hexdigest()==cached['body_sha256']):cached=None
                except (OSError,ValueError,TypeError,KeyError):cached=None
            if cached:
                output[url]={**row,**cached['page'],'read_from_cache':True,'cache_age_seconds':round(age,1)}
            else:pending.append(row)
        if pending:
            for page in self.reader.enrich(pending):
                url=page['url'];output[url]=page
                if (page.get('_primary_scope') not in self.REFERENCE_SCOPES
                        or page.get('read_status') not in {'success','partial'}
                        or not trusted_url(page.get('final_url') or url,True)
                        or len(page.get('content',''))<30):continue
                record={'url':url,'saved_at':datetime.now(timezone.utc).isoformat(),
                        'body_sha256':hashlib.sha256(page['content'].encode()).hexdigest(),'page':page}
                path=self._path(url);temp=path.with_name(path.stem+'.'+uuid.uuid4().hex+'.writing')
                try:
                    temp.write_text(json.dumps(record,ensure_ascii=False),encoding='utf8');os.replace(temp,path)
                except OSError:
                    try:temp.unlink(missing_ok=True)
                    except OSError:pass
        return [output.get(row['url'],dict(row,read_status='not_attempted',content='')) for row in rows]
