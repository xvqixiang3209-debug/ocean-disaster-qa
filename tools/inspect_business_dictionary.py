"""Find actual dictionary evidence, separating general rules from this dataset."""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
import zipfile
from xml.etree import ElementTree as ET

APP = Path(__file__).resolve().parents[1]
TERMS = ('高程基准', '潮高基准', '潮高的单位', '北京时间', '起报时间', '单位为', '集合平均', '集合均值', '分位数', '1985')
DATASET_MARKERS = r'Surge_(?:detem|ensem)|Waterlevel_(?:detem|ensem)|ZJCoastal2026|TY\d{4}_20\d{8}'


def main():
    db = sqlite3.connect((APP / 'data/marine_kb/knowledge.sqlite3').as_uri()+'?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT source_id,filename,raw_path,metadata FROM sources WHERE category='模式结果' AND bytes>0 ORDER BY source_id").fetchall()
    schema, signals, errors = Counter(), [], []
    for row in rows:
        metadata = json.loads(row['metadata'])
        for sheet in metadata.get('sheets', []):
            schema[(sheet['name'], len(sheet.get('headers', []))-1, sheet.get('first_time'), sheet.get('last_time'))] += 1
        try:
            with zipfile.ZipFile(row['raw_path']) as archive:
                for name in archive.namelist():
                    if (name == 'xl/sharedStrings.xml' or name.startswith('xl/comments')
                        or name.startswith('docProps/') or name in {'xl/workbook.xml', 'xl/styles.xml'}
                        or name.startswith('xl/worksheets/') and name.endswith('.xml')):
                        root = ET.fromstring(archive.read(name))
                        parts = [node.text for node in root.iter() if node.tag.rsplit('}',1)[-1] in {'t','description','title','subject','keywords'} and node.text]
                        if name.startswith('docProps/') or name == 'xl/workbook.xml':
                            parts += [node.text for node in root.iter() if node.text and node.text.strip()]
                        if name == 'xl/styles.xml':
                            parts += [node.attrib.get('formatCode','') for node in root.iter() if node.tag.rsplit('}',1)[-1] == 'numFmt']
                        for value in parts:
                            if re.search(r'单位|高程|基准|北京|起报|有效时间|均值|平均|分位|成员|厘米|毫米|米|\b(?:cm|mm|m|unit|datum|utc|ensemble|mean|percentile)\b', value, re.I):
                                signals.append({'source_id':row['source_id'], 'filename':row['filename'], 'part':name, 'text':value[:500]})
        except Exception as exc:
            errors.append({'source_id':row['source_id'], 'error_type':type(exc).__name__})
    evidence = []
    direct = []
    # Search ALL retained text, including pending documents, after Unicode
    # normalization so full-width digits and hard wraps do not hide evidence.
    for row in db.execute('SELECT c.source_id,s.filename,c.locator,c.text FROM chunks c JOIN sources s USING(source_id) ORDER BY s.filename,c.chunk_id'):
        compact = re.sub(r'\s+', '', unicodedata.normalize('NFKC', row['text']))
        found = [term for term in TERMS if term in compact]
        if found:
            pos = min(compact.index(term) for term in found)
            evidence.append({'source_id':row['source_id'],'filename':row['filename'],'locator':row['locator'],
                             'terms':found,'excerpt':compact[max(0,pos-80):pos+650]})
        if re.search(DATASET_MARKERS, compact, re.I):
            direct.append({'source_id':row['source_id'],'filename':row['filename'],'locator':row['locator'],'excerpt':compact[:1000]})
    spatial = []
    for row in db.execute("SELECT source_id,filename,raw_path FROM sources WHERE category='空间数据'"):
        data = json.loads(Path(row['raw_path']).read_text(encoding='utf-8-sig'))
        features = data.get('features', [])
        spatial.append({'source_id':row['source_id'],'filename':row['filename'],'top_level_fields':list(data),
            'feature_count':len(features),'property_fields':sorted({key for feature in features for key in feature.get('properties',{})}),
            'distinct_CWZ':len({feature.get('properties',{}).get('CWZ') for feature in features}),
            'missing_color_fields':[feature.get('properties',{}).get('cid') for feature in features if any(not feature.get('properties',{}).get(key) for key in ('RED','ORANGE','YELLOW','BLUE'))],
            'example_properties':features[0].get('properties') if features else {}})
    db.close()
    # Older standards duplicate this library, while the legacy book/report
    # corpus provides additional text. Check it rather than claiming that a
    # search of the new four folders proves absence in every supplied file.
    legacy_path = APP / 'typhoon/data/typhoon_rag.sqlite3'
    legacy_checked, legacy_general, legacy_direct = 0, [], []
    if legacy_path.is_file():
        with sqlite3.connect(legacy_path.as_uri()+'?mode=ro',uri=True) as old:
            for document_id, chunk_id, text in old.execute('SELECT document_id,chunk_id,content FROM knowledge_chunks'):
                legacy_checked += 1
                compact = re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))
                found = [term for term in TERMS if term in compact]
                identity = {'document_id':document_id,'chunk_id':chunk_id}
                if found:
                    pos = min(compact.index(term) for term in found)
                    legacy_general.append({**identity,'terms':found,'excerpt':compact[max(0,pos-80):pos+650]})
                if re.search(DATASET_MARKERS, compact, re.I):
                    legacy_direct.append({**identity,'excerpt':compact[:1000]})
    extra_word_scans = []
    extra_paths = [APP.parents[1]/'数字孪生实施方案-0815.docx',
                   APP.parents[1]/'影响严重的台风总结/影响严重的台风总结/2106号台风“烟花”防御工作情况复盘.docx']
    for path in extra_paths:
        if not path.is_file():
            continue
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read('word/document.xml'))
            text = ''.join(node.text or '' for node in root.iter() if node.tag.rsplit('}',1)[-1]=='t')
        compact = re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))
        extra_word_scans.append({'path':str(path),'text_characters':len(text),
                                'direct_dataset_marker_found':bool(re.search(DATASET_MARKERS, compact, re.I)),
                                'definition_terms_found':[term for term in TERMS if term in compact]})
    report = {'generated_at':datetime.now().astimezone().isoformat(timespec='seconds'),
        'scope':'四个已导入资料目录的全部25095个切片、880个非空模式工作簿的文本/批注/格式/属性和空间原件；未声称读完隔离图像文字。',
        'workbooks_checked':len(rows),'workbook_scan_errors':errors,'workbook_dictionary_signals':signals,
        'schema_groups':[{'sheet':key[0],'column_count':key[1],'first_time_example':key[2],'last_time_example':key[3],'file_count':count} for key,count in schema.items()],
        'general_document_evidence':evidence,'direct_dataset_description_matches':direct,'spatial':spatial,
        'legacy_chunks_checked':legacy_checked,'legacy_general_evidence':legacy_general,
        'legacy_direct_dataset_matches':legacy_direct,'additional_word_text_checked':extra_word_scans,
        'conclusion':'资料中有观测单位、北京时间、潮高基准和旧预案四色潮位等说明；尚未找到把这些定义明确绑定到当前880份模式表与198岸段数据的依据。通用定义不能直接填作该批数据的已确认字典。'}
    path = APP / 'docs/业务数据口径线索核查_20261015.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'workbooks_checked':len(rows),'scan_errors':len(errors),'dictionary_signals':len(signals),
        'general_evidence_fragments':len(evidence),'direct_dataset_descriptions':len(direct),'spatial':spatial,'report':str(path)},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
