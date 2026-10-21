"""Read-only federated management catalogue; document stores stay independent."""
from contextlib import ExitStack, closing
import heapq
import json
from pathlib import Path
from .review_groups import classify_review_group, review_progress, validate_review_group

LABELS={'marine':'海洋专业资料','legacy':'历史台风资料'}
COUNT_NOTE='来源记录跨库未去重，不是独立有效文档数；历史库仅展示可查看的ready文本记录。'


def _integer(value,name):
    try:
        if isinstance(value,bool) or isinstance(value,float) and not value.is_integer():raise ValueError()
        number=int(value)
        if number<1:raise ValueError()
        return number
    except (ValueError,TypeError,OverflowError) as exc:raise ValueError(name+'必须为正整数') from exc


class LibraryCatalog:
    def __init__(self,store,legacy_rag=None):
        self.store,self.legacy=store,legacy_rag

    def summary(self):
        with self.store.connect() as db:
            row=db.execute('SELECT count(*) AS n,coalesce(sum(chunk_count),0) AS chunks FROM sources').fetchone()
        items=[{'id':'marine','label':LABELS['marine'],'record_count':row['n'],'total_registered':row['n'],
                'chunk_count':row['chunks'],'available':True,'read_only':False,
                'original_available':True,'originals_available':True}]
        legacy={'id':'legacy','label':LABELS['legacy'],'record_count':None,'chunk_count':None,
                'total_registered':None,'available':False,'read_only':True,'original_available':False,'originals_available':False}
        if self.legacy is not None:
            try:
                with closing(self.legacy._connect()) as db:
                    row=db.execute("SELECT count(*) AS total,sum(CASE WHEN status='ready' THEN 1 ELSE 0 END) AS n,"
                        "coalesce(sum(CASE WHEN status='ready' THEN chunk_count ELSE 0 END),0) AS chunks FROM knowledge_documents").fetchone()
                legacy.update(record_count=row['n'] or 0,total_registered=row['total'],chunk_count=row['chunks'],available=True)
            except Exception as exc:legacy['error_type']=type(exc).__name__
        items.append(legacy)
        return {'libraries':items,'query_mode':'joint_local_retrieval' if legacy['available'] else 'marine_only',
                'count_note':COUNT_NOTE}

    def list_sources(self,*,library='all',q='',status='',category='',page=1,limit=40,review_group=''):
        if library not in {'all','marine','legacy'}:raise ValueError('未知的资料库')
        review_group=validate_review_group(review_group)
        page,limit=_integer(page,'页码'),min(100,_integer(limit,'每页条数'))
        term=str(q or '').strip()[:300].replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        common=[];values=[]
        if term:
            common.append("(filename LIKE ? ESCAPE '\\' OR relative_path LIKE ? ESCAPE '\\')")
            values.extend(['%'+term+'%']*2)
        for column,value in [('status',status),('category',category)]:
            if value:common.append(column+'=?');values.append(value)
        if review_group:common.append('review_group_id(status,metadata)=?');values.append(review_group)
        where=' WHERE '+' AND '.join(common) if common else ''
        counts={'marine':0,'legacy':0};errors=[]
        with ExitStack() as stack:
            db=stack.enter_context(self.store.connect())
            counts['marine']=db.execute('SELECT count(*) FROM sources'+where,values).fetchone()[0]
            streams=[]
            if library in {'all','marine'}:
                cursor=db.execute('SELECT source_id,filename,relative_path,raw_path,category,status,quality_status,chunk_count,created_at,sha256,metadata,'
                    'coalesce(julianday(created_at),0) AS sort_time FROM sources'+where+' ORDER BY sort_time DESC,source_id',values)
                streams.append((self._marine(row) for row in cursor))
            if self.legacy is not None:
                try:
                    old=stack.enter_context(closing(self.legacy._connect()))
                    # Metadata is data, never a filesystem path or a source of
                    # write permissions. Invalid historical JSON stays inert.
                    old_where=["status='ready'"];old_values=[]
                    if review_group:old_where.append('0=1')
                    if term:
                        old_where.append("(filename LIKE ? ESCAPE '\\' OR CASE WHEN json_valid(metadata_json) THEN "
                            "coalesce(json_extract(metadata_json,'$.relative_path'),'') ELSE '' END LIKE ? ESCAPE '\\')")
                        old_values.extend(['%'+term+'%']*2)
                    if status and status!='indexed':old_where.append('0=1')
                    if category:old_where.append('source_kind=?');old_values.append(category)
                    clause=' WHERE '+' AND '.join(old_where)
                    counts['legacy']=old.execute('SELECT count(*) FROM knowledge_documents'+clause,old_values).fetchone()[0]
                    if library in {'all','legacy'}:
                        cursor=old.execute('SELECT document_id,filename,source_kind,chunk_count,imported_at,sha256,metadata_json,'
                            'coalesce(julianday(imported_at),0) AS sort_time FROM knowledge_documents'+clause+' ORDER BY sort_time DESC,document_id',old_values)
                        streams.append((self._old(row) for row in cursor))
                except Exception as exc:
                    counts['legacy']=None;errors.append({'library':'legacy','error_type':type(exc).__name__})
            elif library in {'all','legacy'}:
                counts['legacy']=None;errors.append({'library':'legacy','error_type':'NotConfigured'})
            # Merge two ordered cursors, retaining only one page in memory.
            rows=heapq.merge(*streams,key=lambda row:(-row['_sort_time'],row['source_id']))
            offset=(page-1)*limit;items=[]
            for index,row in enumerate(rows):
                if index<offset:continue
                if len(items)>=limit:break
                row.pop('_sort_time',None);items.append(row)
        total=sum(counts[k] or 0 for k in ('marine','legacy')) if library=='all' else counts[library] or 0
        return {'items':items,'total':total,'page':page,'page_size':limit,'has_next':page*limit<total,
                'library':library,'counts':counts,'library_errors':errors,'count_note':COUNT_NOTE}

    @staticmethod
    def _marine(row):
        item={k:row[k] for k in ('source_id','filename','relative_path','category','status','quality_status','chunk_count','created_at','sha256')}
        return {**item,'library':'marine','library_label':LABELS['marine'],'read_only':False,
                'review_group':classify_review_group(row['status'],row['metadata']),
                'review_progress':review_progress(row['status'],row['metadata']),
                'original_available':Path(row['raw_path']).is_file(),'_sort_time':row['sort_time']}

    @staticmethod
    def _old(row):
        try:metadata=json.loads(row['metadata_json'] or '{}')
        except (ValueError,TypeError):metadata={}
        if not isinstance(metadata,dict):metadata={}
        relative=metadata.get('relative_path')
        return {'source_id':'legacy:'+row['document_id'],'filename':row['filename'],
                'relative_path':relative if isinstance(relative,str) else row['filename'],
                'category':row['source_kind'],'status':'indexed','quality_status':'legacy_text',
                'chunk_count':row['chunk_count'],'created_at':row['imported_at'],'sha256':row['sha256'],
                'library':'legacy','library_label':LABELS['legacy'],'read_only':True,'original_available':False,
                'review_group':None,'review_progress':'',
                '_sort_time':row['sort_time']}
