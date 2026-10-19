"""Small deterministic calculations with explicit user and source inputs."""
from decimal import Decimal
import hashlib,re
from urllib.parse import urlparse

CHINESE={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}


def _wind_inputs(question):
    """Only explicit m/s inputs under the named two-minute regional scheme."""
    if not re.search(r'热带气旋|台风',question) or not re.search(r'等级|分级|归类|哪一类',question):return None
    if not re.search(r'中国|西北太平洋|南海',question):return None
    if re.search(r'阵风|瞬时|南太平洋|澳大利亚|北大西洋|印度洋|东北太平洋|[kK][mM]/[hH]|公里|千米|节|[kK][tT]',question):return None
    durations=re.findall(r'(?<!\d)(\d+(?:\.\d+)?)\s*(?:分钟|min)\s*(?:最大)?平均风',question,re.I)
    if not durations or any(Decimal(value)!=2 for value in durations):return None
    number=r'\d+(?:\.\d+)?'
    unit=r'(?:米每秒|米[/／]秒|m\s*[/／]\s*s)'
    match=re.search(r'平均风速\s*(?:分别|为|是|取|等于|[:：=])*\s*('+number+r'(?:\s*'+unit+r')?(?:\s*[、，,和及]\s*'+number+r'(?:\s*'+unit+r')?)*)\s*'+unit,question,re.I)
    if not match:return None
    values=re.findall(number,match[1])
    if not 1<=len(values)<=12:return None
    # Do not classify a range as two independent measurements or silently use
    # the first of several differently specified input groups.
    if re.search(number+r'\s*(?:到|至|[~～])\s*'+number+r'|[≥≤<>]|负|(?<!\d)-\d',question) or len(re.findall('平均风速',question))!=1:return None
    return values


def _wind_table(hit):
    """Parse the selected official standard's table; no stored thresholds."""
    meta=hit.get('metadata') or {}
    if meta.get('source_type')!='public_web':return None
    parsed=urlparse(str(hit.get('url') or ''));host=parsed.hostname or ''
    if parsed.scheme!='https' or not (host=='cma.gov.cn' or host.endswith('.cma.gov.cn')):return None
    text=str(hit.get('text') or '')
    if not re.search(r'GB\s*/?\s*T\s*32935',str(hit.get('title') or '')+' '+text,re.I):return None
    heading=re.search(r'表\s*1\s*西北太平洋和南海热带气旋等级划分',text)
    if not heading:return None
    tail=text[heading.start():]
    end=re.search(r'\n\s*(?:5\.2\b|表\s*2\b)',tail)
    quote=tail[:end.start()] if end else tail
    if not re.search(r'底层中心附近最大平均风速\s*[（(]\s*2\s*(?:min|分钟)\s*平均风\s*[）)]',quote,re.I):return None
    # This parser reads the first numeric column. Explicit order is mandatory
    # so kt or km/h columns cannot accidentally supply an m/s threshold.
    if not re.search(r'(?m)^\s*[|]?\s*m/s\s*(?:[|]\s*)?kt\s*(?:[|]\s*)?km/h\s*[|]?\s*$',quote,re.I):return None
    rows=[];number=r'\d+(?:\.\d+)?'
    for line in quote.splitlines():
        cleaned=line.strip().strip('|').replace('|',' ').strip()
        match=re.match(r'^([\u4e00-\u9fff]{2,12})\s+(?:(≥|>=)\s*('+number+r')|('+number+r')\s*[~～—–-]\s*('+number+r'))(?=\s|$)',cleaned)
        if not match:continue
        lower=Decimal(match[3] or match[4]);upper=Decimal(match[5]) if match[5] else None
        if upper is not None and upper<lower:return None
        rows.append({'label':match[1],'lower':lower,'upper':upper,'row_quote':line.strip()})
    if len(rows)<2 or len({r['label'] for r in rows})!=len(rows):return None
    for left,right in zip(rows,rows[1:]):
        if left['upper'] is None or left['upper']>=right['lower']:return None
    return quote.strip(),rows


def _add_wind_classification(question,hits):
    inputs=_wind_inputs(question)
    if inputs is None:return hits
    candidates=[]
    for index,hit in enumerate(hits,1):
        table=_wind_table(hit)
        if not table:continue
        quote,rows=table;classified=[]
        for raw in inputs:
            value=Decimal(raw)
            matches=[row for row in rows if row['lower']<=value and (row['upper'] is None or value<=row['upper'])]
            if len(matches)!=1:break  # no rounding across the source's gaps
            row=matches[0]
            expression=(str(row['lower'])+' m/s ≤ '+raw+' m/s'+(' ≤ '+str(row['upper'])+' m/s' if row['upper'] is not None else ''))
            classified.append({'input':raw,'unit':'m/s','category':row['label'],
                               'lower':str(row['lower']),'upper':str(row['upper']) if row['upper'] is not None else None,
                               'expression':expression,'source_row':row['row_quote']})
        if len(classified)==len(inputs):candidates.append((index,hit,quote,classified))
    if not candidates:return hits
    # Conflicting versions/tables are not resolved by selecting whichever one
    # the retriever happened to rank first.
    signatures={tuple((row['category'],row['lower'],row['upper']) for row in item[3]) for item in candidates}
    if len(signatures)!=1:return hits
    index,hit,quote,classified=candidates[0]
    key=hashlib.sha256((question+hit['source_id']+quote).encode()).hexdigest()[:24]
    calculation={'kind':'source_interval_classification','region':'西北太平洋和南海','average_minutes':2,
        'unit':'m/s','user_inputs':inputs,'results':classified,'rounding_applied':False,
        'source_id':hit['source_id'],'source_url':hit.get('url'),'source_locator':hit.get('locator'),
        'source_quote':quote,'source_quote_sha256':hashlib.sha256(quote.encode()).hexdigest()}
    text='原文依据：'+hit['title']+'，表1。适用范围为西北太平洋和南海；按底层中心附近最大平均风速（2分钟平均风）划分。用户给定值是待分类输入，不是程序观测到的台风实况。\n'
    text+='所用表1原文：\n'+quote+'\n用户输入与区间判断（不舍入、不转换平均时距）：\n'
    text+='| 输入风速（m/s，2分钟平均） | 等级 | 所用风速下界（m/s） | 上界（m/s） |\n|---|---|---|---|\n'
    text+='\n'.join('| '+row['input']+' | '+row['category']+' | '+row['lower']+' | '+(row['upper'] if row['upper'] is not None else '无上界')+' |' for row in classified)+'\n'
    text+='\n'.join('用户输入'+row['input']+' m/s；'+row['expression']+'；归类为'+row['category']+'。' for row in classified)
    text+='\n以上仅为该表口径的等级归类，不用于阵风或其他平均时距，也不表明已核实某个台风的实际强度。'
    result=list(hits)
    result.append({'source_id':'calc:'+key,'chunk_id':key,'title':'热带气旋等级原表区间程序归类',
        'filename':'已选官方标准原表与明确用户输入区间比较','text':text,
        'locator':'表1原文与逐值十进制区间比较','status':'approved','category':'程序计算','url':hit.get('url'),
        'metadata':{'source_type':'program_calculation','external_allowed':True,
                    'derived_from_citation':index,'calculation':calculation},
        'retrieval_details':{'direct_evidence':True}})
    return result


def add_public_calculations(question,hits):
    return _add_wind_classification(question,_add_household_calculation(question,hits))


def _add_household_calculation(question,hits):
    if not re.search(r'储备|应急物资|水和粮|饮用水',question):return hits
    # Several explicitly mentioned households require separate object binding;
    # never use the first family's size as the combined population.
    if re.search(r'邻居|隔壁|两家|几个家庭|多户|另一家',question):return hits
    pattern=r'([一二两三四五六七八九十]|\d{1,2})口(?:之)?家|(?:一家|家庭共|我们家|家里|家中|全家)(?:一共|共|有|住着)?\s*([一二两三四五六七八九十]|\d{1,2})\s*(?:口|人|个人)|([一二两三四五六七八九十]|\d{1,2})人(?:的)?家庭'
    positions={match.start(index) for match in re.finditer(pattern,question)
               for index,value in enumerate(match.groups(),1) if value}
    positions.update(m.start(1) for m in re.finditer(r'(?<!\d)([一二两三四五六七八九十]|\d{1,2})\s*(?:个人|口人|人)',question))
    if len(positions)>1:return hits  # unresolved old/new counts must not choose the first
    count=re.search(pattern,question)
    if count:raw=next(value for value in count.groups() if value)
    else:
        from .intent_lenses import HOUSEHOLD
        counts=re.findall(r'(?<!\d)([一二两三四五六七八九十]|\d{1,2})\s*(?:个人|口人|人)(?![\u4e00-\u9fff]{0,2}次)',question)
        if not re.search(HOUSEHOLD,question) or len(counts)!=1:return hits
        raw=counts[0]
    people=int(raw) if raw.isdigit() else CHINESE[raw]
    if not 1<=people<=99:return hits
    result=list(hits)
    for index,hit in enumerate(hits,1):
        if hit.get('metadata',{}).get('source_type')!='public_web':continue
        text=re.sub(r'\s+','',hit.get('text',''))
        match=re.search(r'保障每人(\d+)天基本饮水需求[，,]?至少(\d+(?:\.\d+)?)升[/／]人',text)
        if not match:continue
        days=int(match[1]);per_person=Decimal(match[2]);total=people*per_person
        key=hashlib.sha256((question+hit['source_id']+match[0]).encode()).hexdigest()[:24]
        expression=f'{people} × {per_person} 升/人 = {total} 升'
        result.append({'source_id':'calc:'+key,'chunk_id':key,'title':'家庭饮用水储备量程序换算',
            'filename':'明确人数与官方清单口径换算','text':
                f'原文依据：{hit["title"]}；{match[0]}。用户明确家庭人数为{people}人。'
                f'按该清单的{days}天每人总量口径计算，家庭至少{total}升，计算式：{expression}。'
                f'这是{days}天总量下限，不是每天{per_person}升的要求；食品仅按原文保障相应天数，不推造重量或热量。',
            'locator':'原文输入与十进制乘法结果','status':'approved','category':'程序计算','url':hit.get('url'),
            'metadata':{'source_type':'program_calculation','external_allowed':True,
                'derived_from_citation':index,'calculation':{'people':people,'days':days,
                    'per_person_total_litres':str(per_person),'family_total_litres':str(total),'expression':expression}},
            'retrieval_details':{'direct_evidence':True}})
        break
    return result
